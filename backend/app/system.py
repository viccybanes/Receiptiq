"""Hardware probes and explicit, restartable local model setup. No setup runs on import."""

import csv
import ctypes
import hashlib
import io
import json
import os
import platform
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import zipfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from filelock import FileLock, Timeout

from .db import data_directory
from .ocr import install_models, models_ready

RUNTIME_URL = "http://127.0.0.1:11435"
_processes = {}
_threads = {}
_job_memory = {}
_mutex = threading.RLock()


def manifest():
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
    return json.loads((base / "packaging" / "runtime-manifest.json").read_text(encoding="utf-8"))


def _command(arguments):
    try:
        result = subprocess.run(arguments, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                timeout=12, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        return result.stdout.strip() if result.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def _memory_gb():
    if os.name == "nt":
        class Memory(ctypes.Structure):
            _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [
                (name, ctypes.c_ulonglong) for name in
                ("total", "available", "page_total", "page_available", "virtual_total", "virtual_available", "extended")]
        memory = Memory()
        memory.length = ctypes.sizeof(memory)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(memory)):
            return round(memory.total / 1024**3, 1)
    try:
        return round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1024**3, 1)
    except (AttributeError, OSError, ValueError):
        return None


def _cpu_name():
    if os.name == "nt":
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as key:
                return winreg.QueryValueEx(key, "ProcessorNameString")[0].strip()
        except OSError:
            pass
    return platform.processor() or "Unknown CPU"


def capabilities(root=None):
    root = Path(root or data_directory()).resolve()
    existing = root
    while not existing.exists():
        existing = existing.parent
    gpus = []
    # nvidia-smi reports actual VRAM; Win32_VideoController.AdapterRAM truncates many cards above 4 GB.
    smi = shutil.which("nvidia-smi")
    if not smi and os.name == "nt":
        candidate = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "nvidia-smi.exe"
        smi = str(candidate) if candidate.exists() else None
    if smi:
        output = _command([smi, "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader,nounits"])
        for row in csv.reader(io.StringIO(output)):
            if len(row) == 3:
                try:
                    gpus.append({"name": row[0].strip(), "vram_gb": round(float(row[1]) / 1024, 1),
                                 "driver": row[2].strip()})
                except ValueError:
                    pass
    ram = _memory_gb()
    free = round(shutil.disk_usage(existing).free / 1024**3, 1)
    vram = max((gpu["vram_gb"] for gpu in gpus), default=0)
    recommendation = "4b" if (ram or 0) >= 16 and vram >= 6 and free >= 14 else (
        "2b" if (ram or 0) >= 8 and vram >= 4 and free >= 10 else "ocr-only")
    return {"os": platform.platform(), "cpu": _cpu_name(), "cpu_count": os.cpu_count(),
            "ram_gb": ram, "free_disk_gb": free, "nvidia": gpus, "workspace": str(root),
            "recommended_profile": recommendation, "profiles": manifest()["profiles"],
            "recommendation_note": "Conservative hardware estimate. Run the sample check; speed and accuracy vary.",
            "workspace_location": _read_json(_launch_file(), {})}


def _read_json(path, default=None):
    try:
        with _mutex:
            return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    with _mutex:
        try:
            temporary.write_text(json.dumps(value, indent=2), encoding="utf-8")
            for attempt in range(40):
                try:
                    temporary.replace(path)
                    return
                except PermissionError:
                    if attempt == 39:
                        raise
                    # Windows scanners and readers can briefly deny atomic replacement.
                    time.sleep(0.025)
        finally:
            temporary.unlink(missing_ok=True)


def _request(path, body=None, timeout=5):
    request = urllib.request.Request(RUNTIME_URL + path, data=None if body is None else json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
    return urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=timeout)


def _installed_models():
    try:
        with _request("/api/tags") as response:
            return [entry["name"] for entry in json.load(response).get("models", [])]
    except (OSError, ValueError, KeyError):
        return []


def _runtime_directory(root):
    return Path(root) / "runtimes" / ("ollama-" + manifest()["ollama"]["version"])


def setup_status(root):
    root = Path(root).resolve()
    job = _job_memory.get(str(root)) or _read_json(root / "setup-job.json", {})
    thread = _threads.get(str(root))
    if job.get("state") in ("queued", "running") and not (thread and thread.is_alive()):
        try:
            with FileLock(str(root / "setup.lock"), timeout=0):
                job = {**job, "state": "interrupted", "message": "Setup paused when Receiptor closed. Retry to resume downloads."}
        except Timeout:
            pass  # Another Receiptor setup process still owns the durable job.
    owned = _processes.get(str(root))
    running = bool(owned and owned.poll() is None)
    return {"ocr_ready": models_ready(root), "runtime_installed": (_runtime_directory(root) / "ollama.exe").is_file(),
            "runtime_running": running, "runtime_version": manifest()["ollama"]["version"],
            "runtime_url": RUNTIME_URL, "models": _installed_models() if running else [], "job": job,
            "profile": _read_json(root / "setup-profile.json", {}).get("profile", "ocr-only")}


def _download_runtime(root, progress):
    specification = manifest()["ollama"]
    target = _runtime_directory(root)
    if (target / "ollama.exe").is_file() and (target / "verified.json").is_file():
        return target
    if os.name != "nt" or platform.machine().lower() not in ("amd64", "x86_64"):
        raise RuntimeError("This managed runtime supports 64-bit Windows on Intel/AMD only.")
    downloads = root / "runtimes" / "downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    archive = downloads / ("ollama-" + specification["version"] + ".zip.part")
    offset = archive.stat().st_size if archive.exists() else 0
    if offset < specification["download_bytes"]:
        request = urllib.request.Request(specification["url"], headers={"Range": f"bytes={offset}-"} if offset else {})
        with urllib.request.urlopen(request, timeout=60) as response:
            if response.status != 206:
                offset = 0
            with archive.open("ab" if offset else "wb") as stream:
                while chunk := response.read(1024 * 1024):
                    stream.write(chunk)
                    offset += len(chunk)
                    if offset > specification["download_bytes"]:
                        raise RuntimeError("Runtime download exceeded the pinned release size.")
                    progress("runtime-download", offset, specification["download_bytes"])
    digest = hashlib.sha256()
    with archive.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != specification["sha256"]:
        archive.unlink(missing_ok=True)
        raise RuntimeError("Runtime SHA-256 verification failed. Retry to download a fresh official archive.")
    staging = target.with_name(target.name + ".extracting")
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir()
    try:
        with zipfile.ZipFile(archive) as bundle:
            if sum(entry.file_size for entry in bundle.infolist()) > 8 * 1024**3:
                raise RuntimeError("Runtime archive is larger than the allowed installation size.")
            for entry in bundle.infolist():
                destination = (staging / entry.filename).resolve()
                if not destination.is_relative_to(staging.resolve()) or ":" in entry.filename:
                    raise RuntimeError("Unsafe runtime archive path.")
            bundle.extractall(staging)
        if not (staging / "ollama.exe").is_file():
            raise RuntimeError("Runtime archive is missing ollama.exe.")
        _write_json(staging / "verified.json", specification)
        if target.exists():
            shutil.rmtree(target)
        staging.replace(target)
        archive.unlink(missing_ok=True)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return target


def ensure_runtime(root):
    """Start only an already installed runtime; never downloads or adopts another Ollama process."""
    root = Path(root).resolve()
    key = str(root)
    with _mutex:
        existing = _processes.get(key)
        if existing and existing.poll() is None:
            return RUNTIME_URL
        executable = _runtime_directory(root) / "ollama.exe"
        if not executable.is_file():
            raise RuntimeError("Install a vision profile in Setup before using local vision.")
        # A private fixed port is intentional: refusal keeps an unrelated Ollama untouched.
        with socket.socket() as probe:
            try:
                probe.bind(("127.0.0.1", 11435))
            except OSError as exc:
                raise RuntimeError("Receiptor's private model port 11435 is busy. Close the other Receiptor instance.") from exc
        environment = os.environ.copy()
        # Go's os.UserHomeDir uses USERPROFILE on Windows; keep Ollama keys/config separate too.
        runtime_user = root / "runtimes" / "ollama-user"
        runtime_user.mkdir(parents=True, exist_ok=True)
        environment.update({"OLLAMA_HOST": "127.0.0.1:11435", "OLLAMA_MODELS": str(root / "models" / "ollama"),
                            "USERPROFILE": str(runtime_user),
                            "OLLAMA_NO_CLOUD": "1", "OLLAMA_NUM_PARALLEL": "1", "OLLAMA_MAX_LOADED_MODELS": "1",
                            "OLLAMA_CONTEXT_LENGTH": "4096", "OLLAMA_ORIGINS": "http://127.0.0.1:11435"})
        process = subprocess.Popen([str(executable), "serve"], env=environment, cwd=executable.parent,
                                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        _processes[key] = process
    for _ in range(120):
        if process.poll() is not None:
            raise RuntimeError("The managed model runtime could not start. Check free disk, GPU driver, and retry.")
        try:
            with _request("/api/version", timeout=1) as response:
                if json.load(response).get("version") == manifest()["ollama"]["version"]:
                    return RUNTIME_URL
        except (OSError, ValueError):
            time.sleep(0.25)
    stop_runtime(root)
    raise RuntimeError("The managed model runtime did not become ready. Retry setup.")


def stop_runtime(root):
    """Only terminate the process handle created by this backend, including its owned model runners."""
    with _mutex:
        process = _processes.pop(str(Path(root).resolve()), None)
    if process and process.poll() is None:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True,
                           creationflags=subprocess.CREATE_NO_WINDOW, timeout=15)
        else:
            process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()


def start_setup(root, profile):
    """Explicit user action starts one persistent job; retry reuses partial runtime/model downloads."""
    root = Path(root).resolve()
    profiles = manifest()["profiles"]
    if profile not in profiles:
        raise ValueError("Choose ocr-only, 2b, or 4b.")
    root.mkdir(parents=True, exist_ok=True)
    with _mutex:
        active = _threads.get(str(root))
        if active and active.is_alive():
            return setup_status(root)
        if shutil.disk_usage(root).free < profiles[profile]["minimum_free_gb"] * 1024**3:
            raise ValueError(f"This profile needs at least {profiles[profile]['minimum_free_gb']} GB free disk space.")
        job = {"id": str(uuid4()), "profile": profile, "state": "queued", "stage": "queued", "completed": 0,
               "total": 0, "message": "Setup queued", "updated_at": datetime.now(timezone.utc).isoformat()}
        _job_memory[str(root)] = job

        def run():
            def progress(stage, completed=0, total=0, message=None):
                job.update(state="running", stage=stage, completed=completed, total=total,
                           message=message or stage.replace("-", " "), updated_at=datetime.now(timezone.utc).isoformat())
                _write_json(root / "setup-job.json", job)
            try:
                with FileLock(str(root / "setup.lock"), timeout=0):
                    _write_json(root / "setup-job.json", job)
                    progress("ocr-models")
                    install_models(root)
                    model = profiles[profile]["model"]
                    if model:
                        _download_runtime(root, progress)
                        progress("runtime-start")
                        ensure_runtime(root)
                        progress("model-download")
                        for download_model in (model, manifest()["embedding_model"]):
                            with _request("/api/pull", {"model": download_model, "stream": True}, timeout=1800) as response:
                                success = False
                                for line in response:
                                    event = json.loads(line)
                                    if event.get("error"):
                                        raise RuntimeError("Model download failed. Check connection/disk space and retry.")
                                    progress("model-download", event.get("completed", 0), event.get("total", 0),
                                             str(event.get("status", "Downloading model"))[:180])
                                    success = event.get("status") == "success"
                                if not success or download_model not in _installed_models():
                                    raise RuntimeError("Model installation did not complete. Retry to resume.")
                    _write_json(root / "setup-profile.json", {"profile": profile, "model": model})
                    job.update(state="complete", stage="complete", message="Assets installed. Run a sample receipt to check this PC.")
                    _write_json(root / "setup-job.json", job)
            except Timeout:
                # Do not overwrite the job belonging to another live setup process.
                _job_memory.pop(str(root), None)
            except Exception as exc:
                # Never include prompts, receipt text, environment variables, or raw HTTP bodies in state/logs.
                message = str(exc) if isinstance(exc, (RuntimeError, ValueError)) else "Setup failed. Check connection, free disk and permissions, then retry."
                job.update(state="failed", message=message[:250])
                try:
                    _write_json(root / "setup-job.json", job)
                except OSError:
                    job.update(message="Setup could not save progress. Check folder permissions and retry.")
        thread = threading.Thread(target=run, name="receiptiq-setup", daemon=True)
        _threads[str(root)] = thread
        thread.start()
    return setup_status(root)


def _launch_file():
    return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "ReceiptIQ-launch.json"


def _location_paths(source, target):
    source, target = Path(source).resolve(), Path(target)
    if not target.is_absolute() or target.drive.startswith("\\\\"):
        raise ValueError("Choose an absolute folder on a local drive.")
    target = target.resolve()
    if os.name == "nt" and ctypes.windll.kernel32.GetDriveTypeW(str(target.anchor)) == 4:
        raise ValueError("SQLite workspaces must stay on a local drive, not a network share.")
    if source == target or source.is_relative_to(target) or target.is_relative_to(source):
        raise ValueError("The new workspace must be separate from the current folder and its parents/subfolders.")
    return source, target


def stage_location(root, target):
    """Queue an explicit move to an empty folder. All existing workspace files remain untouched."""
    if os.environ.get("RECEIPTIQ_DATA_DIR"):
        raise ValueError("RECEIPTIQ_DATA_DIR currently overrides workspace selection. Remove that override first.")
    source, target = _location_paths(root, target)
    if not (source / "receiptiq.sqlite3").is_file():
        raise ValueError("The current workspace database is missing.")
    if target.exists() and (not target.is_dir() or any(target.iterdir())):
        raise ValueError("Choose a new or completely empty destination folder.")
    configuration = _launch_file()
    configuration.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(configuration) + ".lock", timeout=0):
        current = _read_json(configuration, {})
        if not isinstance(current, dict) or current.get("format", 1) != 1:
            raise ValueError("Unsupported workspace-location preferences.")
        current.update(format=1, active_workspace=str(source), pending={"id": uuid4().hex,
                       "source": str(source), "target": str(target)})
        current.pop("location_error", None)
        _write_json(configuration, current)
    return {"staged": True, "restart_required": True, "target": str(target),
            "message": "Close and restart Receiptor to copy this workspace. The original folder is retained for rollback."}


def _file_hash(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _workspace_fingerprint(root):
    return {name: _file_hash(root / name) for name in ("receiptiq.sqlite3", "receiptiq.sqlite3-wal")
            if (root / name).is_file()}


def _check_location_database(root):
    from .backups import validate_workspace

    validate_workspace(root)


def _location_files(source):
    files = []
    for name in ("originals", "pages", "attachments", "exports", "models", "runtimes", "backups"):
        for path in (source / name).rglob("*"):
            if path.is_symlink() or path.resolve() != path.absolute():
                raise ValueError("Workspace contains a file link. Keep the source intact and choose a regular local folder.")
            if path.is_file():
                files.append(path)
            if len(files) > 100_000:
                raise ValueError("Workspace location changes support up to 100,000 files.")
    for name in ("settings.json", "setup-profile.json", "setup-job.json", "google-api-key.dpapi"):
        path = source / name
        if path.is_file():
            if path.is_symlink() or path.resolve() != path.absolute():
                raise ValueError("Workspace settings cannot be symbolic links.")
            files.append(path)
    return files


def _copy_location(source, target, pending):
    marker = target / ".receiptiq-location.json"
    if marker.is_file():
        saved = _read_json(marker, {})
        if saved.get("id") != pending["id"] or saved.get("source_fingerprint") != _workspace_fingerprint(source):
            raise ValueError("The source changed after an interrupted copy. Choose a different empty destination.")
        _check_location_database(target)
        for relative, details in saved.get("files", {}).items():
            path = (target / relative).resolve()
            if not path.is_relative_to(target) or not path.is_file() or path.stat().st_size != details["bytes"] or _file_hash(path) != details["sha256"]:
                raise ValueError("The interrupted workspace copy could not be verified. Choose a different empty folder.")
        return
    if target.exists() and (not target.is_dir() or any(target.iterdir())):
        raise ValueError("Destination is no longer empty. Existing destination files were preserved.")
    files = _location_files(source)
    required = sum(path.stat().st_size for path in files) + (source / "receiptiq.sqlite3").stat().st_size + 128 * 1024**2
    if shutil.disk_usage(target.parent).free < required:
        raise ValueError("The destination drive needs more free disk space for a complete verified copy.")
    staging = Path(tempfile.mkdtemp(prefix=".receiptiq-location-" + pending["id"] + "-", dir=target.parent)).resolve()
    try:
        with closing(sqlite3.connect((source / "receiptiq.sqlite3").as_uri() + "?mode=ro", uri=True)) as original:
            with closing(sqlite3.connect(staging / "receiptiq.sqlite3")) as snapshot:
                original.backup(snapshot)
        checksums = {}
        for path in files:
            relative = path.relative_to(source)
            destination = staging / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            # These files are immutable. Mutable settings/cache manifests always get independent copies.
            immutable = relative.parts[0] in ("originals", "attachments", "exports", "backups") or (
                relative.parts[:3] == ("models", "ollama", "blobs")) or (
                len(relative.parts) > 1 and relative.parts[0] == "runtimes" and relative.parts[1].startswith("ollama-")
                and relative.parts[1] != "ollama-user")
            linked = False
            if immutable:
                try:
                    os.link(path, destination)
                    linked = True
                except OSError:
                    pass
            if not linked:
                shutil.copy2(path, destination)
            digest = _file_hash(path)
            if destination.stat().st_size != path.stat().st_size or (not linked and _file_hash(destination) != digest):
                raise ValueError("Workspace copy verification failed. The original workspace remains active.")
            checksums[relative.as_posix()] = {"bytes": destination.stat().st_size, "sha256": digest}
        _check_location_database(staging)
        database = staging / "receiptiq.sqlite3"
        checksums["receiptiq.sqlite3"] = {"bytes": database.stat().st_size, "sha256": _file_hash(database)}
        _write_json(staging / ".receiptiq-location.json", {"id": pending["id"], "files": checksums,
                    "source_fingerprint": _workspace_fingerprint(source)})
        if target.exists():
            target.rmdir()  # Only an empty, explicitly selected destination can be promoted.
        staging.rename(target)
    finally:
        if staging.exists():
            # Only this newly created sibling staging directory is eligible for cleanup.
            if staging.parent != target.parent.resolve() or not staging.name.startswith(".receiptiq-location-" + pending["id"] + "-"):
                raise RuntimeError("Unsafe workspace-copy cleanup path.")
            shutil.rmtree(staging)


def apply_pending_location():
    """Called by the launcher before Store opens. Failure keeps the original workspace active."""
    if os.environ.get("RECEIPTIQ_DATA_DIR"):
        return Path(os.environ["RECEIPTIQ_DATA_DIR"]).resolve()
    configuration = _launch_file()
    configuration.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(configuration) + ".lock", timeout=0):
        current = _read_json(configuration, {})
        if not isinstance(current, dict) or current.get("format", 1) != 1:
            raise ValueError("Unsupported workspace-location preferences.")
        active = Path(current.get("active_workspace") or configuration.parent / "ReceiptIQ").resolve()
        pending = current.get("pending")
        if not pending:
            return active
        try:
            source, target = _location_paths(pending["source"], pending["target"])
            if source != active or not isinstance(pending.get("id"), str) or len(pending["id"]) != 32:
                raise ValueError("Invalid pending workspace location.")
            if not all(character in "0123456789abcdef" for character in pending["id"]):
                raise ValueError("Invalid workspace-copy identifier.")
            target.parent.mkdir(parents=True, exist_ok=True)
            with FileLock(str(source.parent / ("." + source.name + ".restore.lock")), timeout=0), \
                    FileLock(str(target.parent / ("." + target.name + ".restore.lock")), timeout=0), \
                    FileLock(str(source / "workspace.lock"), timeout=0), FileLock(str(source / "setup.lock"), timeout=0):
                _copy_location(source, target, pending)
                current.update(active_workspace=str(target))
                current.pop("pending", None)
                current.pop("location_error", None)
                _write_json(configuration, current)
                return target
        except (OSError, ValueError, KeyError, TypeError, Timeout, sqlite3.Error) as exc:
            # Source data is never deleted; a failed/cancelled request can be retried in Settings.
            current.pop("pending", None)
            current["active_workspace"] = str(active)
            reason = str(exc) if isinstance(exc, ValueError) else "Check destination permissions/free disk and close other Receiptor/setup processes, then retry with an empty folder."
            current["location_error"] = "The original workspace remains active. " + reason
            try:
                _write_json(configuration, current)
            except OSError:
                pass
            return active
