"""Backups preserve SQLite state and files; hostile archives never reach an active workspace."""

import json
import shutil
import sqlite3
import zipfile

import pytest
from filelock import FileLock

from app.backups import apply_staged_restore, create_backup, stage_restore
from app.db import Store, now
from app.models import blank_data


def workspace(root):
    store = Store(root)
    (root / "originals" / "sample.png").write_bytes(b"immutable original bytes")
    (root / "attachments" / "photo.png").write_bytes(b"attachment bytes")
    (root / "exports" / "saved.csv").write_bytes(b"name,total\nsample,5\n")
    (root / "settings.json").write_text('{"reviewer":"Local reviewer"}')
    with store.connect() as connection:
        connection.execute("INSERT INTO batches(id,name,created_at) VALUES('batch','Backup test',?)", (now(),))
        connection.execute("INSERT INTO batch_files(id,batch_id,name) VALUES('upload','batch','sample.png')")
        connection.execute("INSERT INTO documents VALUES('doc','sample.png','originals/sample.png','hash','image/png',?)", (now(),))
        connection.execute("INSERT INTO receipts(id,document_id,batch_id,upload_id,status,data_json,created_at,updated_at) "
                           "VALUES('receipt','doc','batch','upload','draft',?,?,?)", (json.dumps(blank_data()), now(), now()))
        connection.execute("INSERT INTO jobs(id,receipt_id,batch_id,state,stage,created_at,updated_at) "
                           "VALUES('pending','receipt','batch','pending','queued',?,?)", (now(), now()))
    return store


def test_backup_roundtrip_pending_jobs_and_model_exclusion(tmp_path):
    root = tmp_path / "workspace"
    store = workspace(root)
    (root / "models" / "large-model.bin").write_bytes(b"model cache")
    archive = create_backup(store)
    with zipfile.ZipFile(archive) as bundle:
        assert "originals/sample.png" in bundle.namelist()
        assert not any(name.startswith(("models/", "runtimes/", "backups/", "tmp/")) for name in bundle.namelist())
    store.execute("UPDATE batches SET name='After backup'")
    result = stage_restore(root, archive)
    assert result["restart_required"]
    assert store.one("SELECT name FROM batches")["name"] == "After backup"
    with FileLock(str(root / "workspace.lock")):
        with pytest.raises(ValueError, match="Close the active"):
            apply_staged_restore(root)
    applied = apply_staged_restore(root)
    restored = Store(root)
    assert restored.one("SELECT name FROM batches")["name"] == "Backup test"
    assert restored.one("SELECT state FROM jobs")["state"] == "pending"
    assert (root / "originals" / "sample.png").read_bytes() == b"immutable original bytes"
    assert (root / "attachments" / "photo.png").read_bytes() == b"attachment bytes"
    assert (root / "models" / "large-model.bin").read_bytes() == b"model cache"
    assert json.loads((root / "settings.json").read_text())["reviewer"] == "Local reviewer"
    with sqlite3.connect(applied["previous_workspace"] + "/receiptiq.sqlite3") as previous:
        assert previous.execute("SELECT name FROM batches").fetchone()[0] == "After backup"
    assert apply_staged_restore(root) == {"restored": False}


@pytest.mark.parametrize("name", ["../outside.txt", "originals/../../outside", "C:/outside", "/outside", "originals\\escape", "originals/CON.txt", "originals/photo.png:stream", "originals/trailing. "])
def test_malicious_paths_rejected(tmp_path, name):
    root = tmp_path / "workspace"
    root.mkdir()
    archive = tmp_path / "hostile.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr(name, "hostile")
    with pytest.raises(ValueError):
        stage_restore(root, archive)
    assert list(root.iterdir()) == []
    assert not (tmp_path / "outside.txt").exists()


def test_restore_version_checksum_collision_and_bomb_guards(tmp_path, monkeypatch):
    from app import backups

    root = tmp_path / "workspace"
    store = workspace(root)
    good = create_backup(store)
    for damage in ("schema", "checksum", "collision"):
        archive = tmp_path / (damage + ".zip")
        with zipfile.ZipFile(good) as source, zipfile.ZipFile(archive, "w") as target:
            for name in source.namelist():
                content = source.read(name)
                if name == "backup.json" and damage == "schema":
                    data = json.loads(content)
                    data["schema"] = 999
                    content = json.dumps(data).encode()
                if name == "originals/sample.png" and damage == "checksum":
                    content = b"tampered"
                target.writestr(name, content)
            if damage == "collision":
                target.writestr("ORIGINALS/sample.png", b"collision")
        with pytest.raises(ValueError):
            stage_restore(root, archive)
    monkeypatch.setattr(backups, "MAX_EXPANDED", 10)
    with pytest.raises(ValueError, match="restore limit"):
        stage_restore(root, good)
    assert store.one("SELECT name FROM batches")["name"] == "Backup test"


def test_failed_swap_retains_original_workspace(tmp_path, monkeypatch):
    root = tmp_path / "workspace"
    store = workspace(root)
    archive = create_backup(store)
    stage_restore(root, archive)
    monkeypatch.setattr(shutil, "copytree", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError):
        apply_staged_restore(root)
    assert Store(root).one("SELECT name FROM batches")["name"] == "Backup test"


@pytest.mark.parametrize("promoted", [False, True])
def test_restart_recovers_each_interrupted_directory_swap(tmp_path, promoted):
    root = tmp_path / "workspace"
    store = workspace(root)
    (root / "models" / "weights.bin").write_bytes(b"cached weights")
    archive = create_backup(store)
    store.execute("UPDATE batches SET name='Before interrupted restore'")
    stage_restore(root, archive)
    pending = json.loads((tmp_path / ".workspace.restore.json").read_text())
    previous = tmp_path / pending["previous"]
    staging = tmp_path / pending["staging"]
    root.replace(previous)
    if promoted:
        staging.replace(root)
    else:
        root.mkdir()  # The application creates the workspace path before applying a pending restore.
    result = apply_staged_restore(root)
    assert result["restored"]
    assert Store(root).one("SELECT name FROM batches")["name"] == "Backup test"
    assert (root / "models" / "weights.bin").read_bytes() == b"cached weights"
    assert previous.is_dir()
    assert not (tmp_path / ".workspace.restore.json").exists()


def test_backup_and_restore_refuse_missing_referenced_original(tmp_path):
    root = tmp_path / "workspace"
    store = workspace(root)
    archive = create_backup(store)
    incomplete = tmp_path / "incomplete.zip"
    with zipfile.ZipFile(archive) as original, zipfile.ZipFile(incomplete, "w") as output:
        for name in original.namelist():
            if name == "originals/sample.png":
                continue
            content = original.read(name)
            if name == "backup.json":
                metadata = json.loads(content)
                del metadata["files"]["originals/sample.png"]
                content = json.dumps(metadata).encode()
            output.writestr(name, content)
    with pytest.raises(ValueError, match="missing"):
        stage_restore(root, incomplete)
    (root / "originals" / "sample.png").unlink()
    with pytest.raises(ValueError, match="missing"):
        create_backup(store)
    assert store.one("SELECT state FROM jobs")["state"] == "pending"
