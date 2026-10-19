"""Hardware recommendations and explicit/retryable setup never assume a GPU or download on startup."""

import hashlib
import io
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from filelock import FileLock

from app import system
from app.db import Store, now


def test_capabilities_uses_actual_nvidia_vram(monkeypatch, tmp_path):
    monkeypatch.setattr(system, "_memory_gb", lambda: 32)
    monkeypatch.setattr(system.shutil, "which", lambda name: "nvidia-smi")
    monkeypatch.setattr(system, "_command", lambda args: "NVIDIA RTX test, 8192, 596.21")
    monkeypatch.setattr(system.shutil, "disk_usage", lambda path: SimpleNamespace(free=100 * 1024**3))
    result = system.capabilities(tmp_path)
    assert result["nvidia"][0]["vram_gb"] == 8
    assert result["recommended_profile"] == "4b"
    monkeypatch.setattr(system, "_command", lambda args: "")
    assert system.capabilities(tmp_path)["recommended_profile"] == "ocr-only"
    monkeypatch.setattr(system, "_command", lambda args: "GPU, 4096, driver")
    assert system.capabilities(tmp_path)["recommended_profile"] == "2b"
    monkeypatch.setattr(system.shutil, "disk_usage", lambda path: SimpleNamespace(free=1024**3))
    assert system.capabilities(tmp_path)["recommended_profile"] == "ocr-only"


def test_status_marks_previous_running_job_interrupted(tmp_path):
    (tmp_path / "setup-job.json").write_text(json.dumps({"state": "running", "profile": "2b"}))
    result = system.setup_status(tmp_path)
    assert result["job"]["state"] == "interrupted"
    assert not result["runtime_running"]


def test_setup_is_explicit_persistent_and_retryable(monkeypatch, tmp_path):
    monkeypatch.setattr(system, "install_models", lambda root: None)
    monkeypatch.setattr(system, "_download_runtime", lambda root, progress: None)
    monkeypatch.setattr(system, "ensure_runtime", lambda root: system.RUNTIME_URL)
    monkeypatch.setattr(system, "_installed_models", lambda: ["qwen3-vl:2b-instruct", "embeddinggemma:300m-qat-q4_0"])
    calls = []

    def request(path, body=None, timeout=5):
        calls.append(body["model"])
        return io.BytesIO(b'{"status":"pulling","completed":5,"total":10}\n{"status":"success"}\n')

    monkeypatch.setattr(system, "_request", request)
    assert not system.setup_status(tmp_path)["job"]
    system.start_setup(tmp_path, "2b")
    system._threads[str(tmp_path.resolve())].join(5)
    assert system.setup_status(tmp_path)["job"]["state"] == "complete"
    assert calls == ["qwen3-vl:2b-instruct", "embeddinggemma:300m-qat-q4_0"]
    assert json.loads((tmp_path / "setup-profile.json").read_text())["profile"] == "2b"
    with pytest.raises(ValueError):
        system.start_setup(tmp_path, "cloud")
    monkeypatch.setattr(system, "install_models", lambda root: (_ for _ in ()).throw(OSError("disk full")))
    system.start_setup(tmp_path, "ocr-only")
    system._threads[str(tmp_path.resolve())].join(5)
    assert system.setup_status(tmp_path)["job"]["state"] == "failed"
    assert "disk full" not in (tmp_path / "setup-job.json").read_text()


def test_setup_refuses_low_disk_and_reuses_active_job(monkeypatch, tmp_path):
    monkeypatch.setattr(system.shutil, "disk_usage", lambda path: SimpleNamespace(free=1024))
    with pytest.raises(ValueError, match="free disk"):
        system.start_setup(tmp_path, "4b")
    assert not (tmp_path / "setup-job.json").exists()


def test_stop_runtime_never_touches_unowned_process(tmp_path, monkeypatch):
    monkeypatch.setattr(system.subprocess, "run", lambda *args, **kwargs: pytest.fail("Unowned runtime killed"))
    system.stop_runtime(tmp_path)


def test_pinned_runtime_manifest():
    data = system.manifest()
    assert data["ollama"]["version"] == "0.40.6"
    assert len(data["ollama"]["sha256"]) == 64
    assert data["ollama"]["url"].startswith("https://github.com/ollama/ollama/releases/download/v0.40.6/")
    assert all("cloud" not in str(profile["model"]) for profile in data["profiles"].values())


def test_atomic_json_retries_windows_reader_denial(monkeypatch, tmp_path):
    original = Path.replace
    attempts = []

    def replace(path, target):
        attempts.append(path.name)
        if len(attempts) <= 2:
            raise PermissionError("Windows reader holds file")
        return original(path, target)

    monkeypatch.setattr(Path, "replace", replace)
    system._write_json(tmp_path / "job.json", {"state": "complete"})
    assert len(attempts) == 3
    assert json.loads((tmp_path / "job.json").read_text()) == {"state": "complete"}
    assert not list(tmp_path.glob("*.tmp"))


def test_runtime_resumes_and_checks_digest_before_install(monkeypatch, tmp_path):
    if system.os.name != "nt":
        pytest.skip("Pinned standalone runtime targets Windows")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        bundle.writestr("ollama.exe", b"test runtime bytes")
    content = buffer.getvalue()
    spec = {"version": "test", "url": "https://github.com/ollama/ollama/test.zip",
            "sha256": hashlib.sha256(content).hexdigest(), "download_bytes": len(content)}
    monkeypatch.setattr(system, "manifest", lambda: {"ollama": spec})
    monkeypatch.setattr(system.platform, "machine", lambda: "AMD64")
    partial = tmp_path / "runtimes" / "downloads" / "ollama-test.zip.part"
    partial.parent.mkdir(parents=True)
    partial.write_bytes(content[:12])

    def download(request, timeout=60):
        assert request.get_header("Range") == "bytes=12-"
        response = io.BytesIO(content[12:])
        response.status = 206
        return response

    monkeypatch.setattr(system.urllib.request, "urlopen", download)
    installed = system._download_runtime(tmp_path, lambda *args: None)
    assert (installed / "ollama.exe").read_bytes() == b"test runtime bytes"
    assert (installed / "verified.json").exists()
    # A second workspace with an untrusted completed archive never receives an executable.
    bad_root = tmp_path / "bad"
    bad_partial = bad_root / "runtimes" / "downloads" / "ollama-test.zip.part"
    bad_partial.parent.mkdir(parents=True)
    bad_partial.write_bytes(b"X" * len(content))
    with pytest.raises(RuntimeError, match="SHA-256"):
        system._download_runtime(bad_root, lambda *args: None)
    assert not (bad_root / "runtimes" / "ollama-test" / "ollama.exe").exists()
    assert not bad_partial.exists()


def test_live_external_setup_job_is_not_overwritten(tmp_path):
    state = {"id": "other-process", "state": "running", "profile": "2b"}
    (tmp_path / "setup-job.json").write_text(json.dumps(state))
    with FileLock(str(tmp_path / "setup.lock")):
        assert system.setup_status(tmp_path)["job"]["state"] == "running"
        system.start_setup(tmp_path, "ocr-only")
        system._threads[str(tmp_path.resolve())].join(5)
        assert json.loads((tmp_path / "setup-job.json").read_text()) == state
    assert system.setup_status(tmp_path)["job"]["state"] == "interrupted"


def test_owned_runtime_has_private_environment_and_owned_shutdown(monkeypatch, tmp_path):
    if system.os.name != "nt":
        pytest.skip("Windows runtime lifecycle")
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "ollama.exe").write_bytes(b"fake runtime for lifecycle probe")
    monkeypatch.setattr(system, "_runtime_directory", lambda root: runtime)
    monkeypatch.setattr(system, "_request", lambda *args, **kwargs: io.BytesIO(
        json.dumps({"version": system.manifest()["ollama"]["version"]}).encode()))

    class Probe:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def bind(self, address):
            assert address == ("127.0.0.1", 11435)

    monkeypatch.setattr(system.socket, "socket", lambda: Probe())
    captured = {}
    process = SimpleNamespace(pid=765432, poll=lambda: None, wait=lambda timeout: None)

    def spawn(arguments, **kwargs):
        captured.update(kwargs)
        assert arguments == [str(runtime / "ollama.exe"), "serve"]
        return process

    monkeypatch.setattr(system.subprocess, "Popen", spawn)
    killed = []
    monkeypatch.setattr(system.subprocess, "run", lambda arguments, **kwargs: killed.append(arguments))
    try:
        assert system.ensure_runtime(tmp_path) == "http://127.0.0.1:11435"
        environment = captured["env"]
        assert environment["OLLAMA_NO_CLOUD"] == "1"
        assert environment["OLLAMA_HOST"] == "127.0.0.1:11435"
        assert environment["OLLAMA_MODELS"] == str(tmp_path / "models" / "ollama")
        assert environment["USERPROFILE"] == str(tmp_path / "runtimes" / "ollama-user")
    finally:
        system.stop_runtime(tmp_path)
    if system.os.name == "nt":
        assert killed == [["taskkill", "/PID", "765432", "/T", "/F"]]


def location_workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    monkeypatch.delenv("RECEIPTIQ_DATA_DIR", raising=False)
    source = tmp_path / "source"
    store = Store(source)
    store.execute("INSERT INTO batches(id,name,created_at) VALUES('batch','Preserved workspace',?)", (now(),))
    (source / "originals" / "receipt.png").write_bytes(b"immutable original")
    (source / "pages" / "page.png").write_bytes(b"independent page copy")
    (source / "settings.json").write_text('{"reviewer":"Original reviewer"}')
    return source, store, tmp_path / "destination"


def test_location_change_copies_latest_workspace_and_retains_original(tmp_path, monkeypatch):
    source, store, target = location_workspace(tmp_path, monkeypatch)
    target.mkdir()
    assert system.stage_location(source, str(target))["restart_required"]
    assert list(target.iterdir()) == []
    store.execute("UPDATE batches SET name='Saved after scheduling move'")
    assert system.apply_pending_location() == target
    assert Store(target).one("SELECT name FROM batches")["name"] == "Saved after scheduling move"
    assert Store(source).one("SELECT name FROM batches")["name"] == "Saved after scheduling move"
    assert (target / "originals" / "receipt.png").read_bytes() == b"immutable original"
    (target / "settings.json").write_text('{"reviewer":"New reviewer"}')
    assert "Original reviewer" in (source / "settings.json").read_text()
    current = json.loads(system._launch_file().read_text())
    assert current["active_workspace"] == str(target)
    assert "pending" not in current


def test_failed_location_copy_keeps_active_pointer_and_source(tmp_path, monkeypatch):
    source, store, target = location_workspace(tmp_path, monkeypatch)
    system.stage_location(source, str(target))
    monkeypatch.setattr(system.shutil, "copy2", lambda *args: (_ for _ in ()).throw(OSError("disk full")))
    assert system.apply_pending_location() == source
    current = json.loads(system._launch_file().read_text())
    assert current["active_workspace"] == str(source)
    assert "location_error" in current and "pending" not in current
    assert store.one("SELECT name FROM batches")["name"] == "Preserved workspace"
    assert (source / "originals" / "receipt.png").read_bytes() == b"immutable original"
    assert not target.exists()
    assert not list(tmp_path.glob(".receiptiq-location-*"))


def test_location_change_refuses_overwrite_related_paths_and_running_workspace(tmp_path, monkeypatch):
    source, _, target = location_workspace(tmp_path, monkeypatch)
    for destination in (str(source), str(source / "nested"), str(source.parent), "relative"):
        with pytest.raises(ValueError):
            system.stage_location(source, destination)
    target.mkdir()
    (target / "existing.txt").write_text("user data")
    with pytest.raises(ValueError, match="empty"):
        system.stage_location(source, str(target))
    assert (target / "existing.txt").read_text() == "user data"
    destination = tmp_path / "empty-destination"
    system.stage_location(source, str(destination))
    with FileLock(str(source / "workspace.lock")):
        assert system.apply_pending_location() == source
    assert not destination.exists()
    monkeypatch.setenv("RECEIPTIQ_DATA_DIR", str(source))
    assert system.apply_pending_location() == source
    with pytest.raises(ValueError, match="overrides"):
        system.stage_location(source, str(destination))


def test_location_restart_finishes_verified_promoted_copy(tmp_path, monkeypatch):
    source, _, target = location_workspace(tmp_path, monkeypatch)
    system.stage_location(source, str(target))
    pending = json.loads(system._launch_file().read_text())["pending"]
    # Simulate power loss after destination promotion and before the global pointer commit.
    system._copy_location(source, target, pending)
    assert json.loads(system._launch_file().read_text())["active_workspace"] == str(source)
    assert system.apply_pending_location() == target
    assert Store(target).one("SELECT name FROM batches")["name"] == "Preserved workspace"


def test_location_pointer_write_failure_preserves_source_selection(tmp_path, monkeypatch):
    source, _, target = location_workspace(tmp_path, monkeypatch)
    system.stage_location(source, str(target))
    original = system._write_json
    failures = []

    def write(path, data):
        if path == system._launch_file() and data.get("active_workspace") == str(target):
            failures.append(True)
            raise OSError("read-only preferences file")
        return original(path, data)

    monkeypatch.setattr(system, "_write_json", write)
    assert system.apply_pending_location() == source
    assert failures
    assert json.loads(system._launch_file().read_text())["active_workspace"] == str(source)
    assert (source / "receiptiq.sqlite3").exists()
