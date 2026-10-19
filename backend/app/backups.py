"""Consistent workspace backups and validated restores applied only while the workspace is closed."""

import hashlib
import json
import os
import shutil
import sqlite3
import stat
import tempfile
import zipfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from uuid import uuid4

from filelock import FileLock, Timeout

BACKUP_VERSION = 1
MAX_SCHEMA = 3
MAX_ARCHIVE = 2 * 1024**3
MAX_EXPANDED = 4 * 1024**3
MAX_FILES = 20000
MAX_BACKUPS = 10
FOLDERS = ("originals", "pages", "attachments", "exports")
SETTINGS = ("settings.json", "setup-profile.json")


def _hash(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def list_backups(root):
    folder = Path(root) / "backups"
    return [{"name": path.name, "bytes": path.stat().st_size,
             "created_at": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()}
            for path in sorted(folder.glob("workspace-*.zip"), key=lambda item: item.stat().st_mtime, reverse=True)]


def create_backup(store):
    """Snapshot SQLite and referenced files under its write reservation, then compress the private snapshot."""
    folder = store.root / "backups"
    folder.mkdir(exist_ok=True)
    destination = folder / ("workspace-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid4().hex[:8] + ".zip")
    temporary = destination.with_suffix(".partial")
    with FileLock(str(store.root / "backup.lock"), timeout=0), tempfile.TemporaryDirectory(dir=folder) as scratch:
        snapshot = Path(scratch) / "receiptiq.sqlite3"
        # A separate read connection backs up a WAL database while the first connection excludes writers.
        with store.connect() as reservation:
            reservation.execute("BEGIN IMMEDIATE")
            with closing(sqlite3.connect(store.path)) as source, closing(sqlite3.connect(snapshot)) as target:
                source.backup(target)
                schema = target.execute("PRAGMA user_version").fetchone()[0]
                if target.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise ValueError("Database check failed; a backup was not created.")
                _check_references(target, store.root)
            files = [(snapshot, "receiptiq.sqlite3")]
            for name in FOLDERS:
                files.extend((path, path.relative_to(store.root).as_posix()) for path in (store.root / name).rglob("*")
                             if path.is_file() and not path.is_symlink())
            files.extend((store.root / name, name) for name in SETTINGS if (store.root / name).is_file())
            if len(files) > MAX_FILES or sum(path.stat().st_size for path, _ in files) > MAX_EXPANDED:
                raise ValueError("Workspace exceeds the 4 GB / 20,000 file backup limit.")
            copied = [(snapshot, "receiptiq.sqlite3")]
            for path, name in files[1:]:
                staged = Path(scratch) / name
                staged.parent.mkdir(parents=True, exist_ok=True)
                if PurePosixPath(name).parts[0] in ("originals", "attachments", "exports"):
                    _link_or_copy(path, staged)
                else:
                    shutil.copy2(path, staged)
                copied.append((staged, name))
        validate_workspace(Path(scratch))
        metadata = {"format": BACKUP_VERSION, "schema": schema,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "files": {name: {"bytes": path.stat().st_size, "sha256": _hash(path)} for path, name in copied}}
        try:
            with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1) as bundle:
                bundle.writestr("backup.json", json.dumps(metadata))
                for path, name in copied:
                    bundle.write(path, name)
            if temporary.stat().st_size > MAX_ARCHIVE:
                raise ValueError("Compressed backup exceeds the 2 GB limit.")
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
    # Only prune completed archives created by this feature; migration/recovery snapshots stay intact.
    for entry in list_backups(store.root)[MAX_BACKUPS:]:
        (folder / entry["name"]).unlink(missing_ok=True)
    return destination


def _allowed_name(name):
    if not name or "\\" in name or ":" in name or "\x00" in name:
        return False
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in (".", "..", "") or part.endswith((".", " ")) for part in name.split("/")):
        return False
    reserved = {"CON", "PRN", "AUX", "NUL"} | {f"{prefix}{number}" for prefix in ("COM", "LPT") for number in range(1, 10)}
    if any(part.split(".")[0].upper() in reserved for part in path.parts):
        return False
    return name in ("backup.json", "receiptiq.sqlite3", *SETTINGS) or path.parts[0] in FOLDERS


def _validate_extract(archive, staging):
    if archive.stat().st_size > MAX_ARCHIVE:
        raise ValueError("Backup exceeds the 2 GB compressed limit.")
    with zipfile.ZipFile(archive) as bundle:
        entries = bundle.infolist()
        if len(entries) > MAX_FILES + 1 or sum(entry.file_size for entry in entries) > MAX_EXPANDED:
            raise ValueError("Backup exceeds the 4 GB / 20,000 file restore limit.")
        names = set()
        for entry in entries:
            name = entry.filename
            mode = entry.external_attr >> 16
            if not _allowed_name(name) or entry.is_dir() or stat.S_ISLNK(mode) or entry.flag_bits & 1:
                raise ValueError("Backup contains an unsafe path, link, directory, or encrypted file.")
            if name.casefold() in names:
                raise ValueError("Backup contains duplicate or case-colliding paths.")
            names.add(name.casefold())
            # Large images and SQLite compress well; pathological >1000:1 inputs are refused.
            if entry.file_size > 1024**2 and entry.file_size > max(entry.compress_size, 1) * 1000:
                raise ValueError("Backup compression ratio is unsafe.")
        if "backup.json" not in names or "receiptiq.sqlite3" not in names:
            raise ValueError("Not a ReceiptIQ workspace backup.")
        if bundle.getinfo("backup.json").file_size > 8 * 1024**2:
            raise ValueError("Backup manifest is too large.")
        metadata = json.loads(bundle.read("backup.json"))
        if not isinstance(metadata, dict) or metadata.get("format") != BACKUP_VERSION or type(metadata.get("schema")) is not int:
            raise ValueError("Unsupported backup format.")
        if metadata["schema"] < 1 or metadata["schema"] > MAX_SCHEMA:
            raise ValueError("This backup needs a different ReceiptIQ version.")
        expected = metadata.get("files")
        if not isinstance(expected, dict) or not all(isinstance(name, str) for name in expected):
            raise ValueError("Invalid backup file manifest.")
        if {name.casefold() for name in expected} != names - {"backup.json"}:
            raise ValueError("Backup manifest does not match its contents.")
        if any(not isinstance(details, dict) for details in expected.values()):
            raise ValueError("Invalid backup file manifest.")
        if shutil.disk_usage(staging).free < sum(entry.file_size for entry in entries) + 128 * 1024**2:
            raise ValueError("Not enough free disk space to stage this restore safely.")
        for entry in entries:
            if entry.filename == "backup.json":
                continue
            destination = staging / entry.filename
            destination.parent.mkdir(parents=True, exist_ok=True)
            with bundle.open(entry) as source, destination.open("xb") as target:
                shutil.copyfileobj(source, target, length=1024 * 1024)
            declared = expected.get(entry.filename, {})
            if destination.stat().st_size != declared.get("bytes") or _hash(destination) != declared.get("sha256"):
                raise ValueError("Backup checksum verification failed.")
    with closing(sqlite3.connect(staging / "receiptiq.sqlite3")) as connection:
        if connection.execute("PRAGMA user_version").fetchone()[0] != metadata["schema"]:
            raise ValueError("Backup database version differs from its manifest.")
    validate_workspace(staging)
    return metadata


def _pending_file(root):
    return root.parent / ("." + root.name + ".restore.json")


def stage_restore(root, archive):
    """Validate and stage only. The running API never replaces its active workspace."""
    root, archive = Path(root).resolve(), Path(archive).resolve()
    with FileLock(str(root.parent / ("." + root.name + ".restore.lock")), timeout=0):
        if _pending_file(root).exists():
            raise ValueError("A restore is already staged. Restart ReceiptIQ to apply it first.")
        staging = Path(tempfile.mkdtemp(prefix="." + root.name + ".restore-", dir=root.parent))
        try:
            metadata = _validate_extract(archive, staging)
            previous = root.name + ".before-restore-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid4().hex[:6]
            pending = {"staging": staging.name, "previous": previous, "format": BACKUP_VERSION, "schema": metadata["schema"]}
            temporary = _pending_file(root).with_suffix("." + uuid4().hex + ".tmp")
            temporary.write_text(json.dumps(pending), encoding="utf-8")
            temporary.replace(_pending_file(root))
        except zipfile.BadZipFile as exc:
            shutil.rmtree(staging)
            raise ValueError("Backup ZIP is damaged or incomplete.") from exc
        except sqlite3.Error as exc:
            shutil.rmtree(staging)
            raise ValueError("Backup database is damaged or is not a supported ReceiptIQ workspace.") from exc
        except BaseException:
            shutil.rmtree(staging)
            raise
    return {"staged": True, "schema": metadata["schema"], "restart_required": True,
            "message": "Backup validated. Close and restart ReceiptIQ to restore. The previous workspace is retained."}


def apply_staged_restore(root):
    """Offline-only two-directory swap with rollback. Models stay outside the backup payload."""
    root = Path(root).resolve()
    pending_file = _pending_file(root)
    if not pending_file.exists():
        return {"restored": False}
    root.mkdir(parents=True, exist_ok=True)
    with FileLock(str(root.parent / ("." + root.name + ".restore.lock")), timeout=0):
        try:
            workspace_lock = FileLock(str(root / "workspace.lock"), timeout=0)
            workspace_lock.acquire()
        except Timeout as exc:
            raise ValueError("Close the active ReceiptIQ workspace before applying a restore.") from exc
        try:
            pending = json.loads(pending_file.read_text(encoding="utf-8"))
            if not isinstance(pending, dict):
                raise ValueError("Invalid restore journal.")
            name = pending.get("staging", "")
            previous = pending.get("previous", "")
            if not isinstance(name, str) or not name.startswith("." + root.name + ".restore-") or Path(name).name != name:
                raise ValueError("Invalid restore staging path.")
            if not isinstance(previous, str) or not previous.startswith(root.name + ".before-restore-") or Path(previous).name != previous:
                raise ValueError("Invalid retained workspace path.")
            staging = root.parent / name
            retained = root.parent / previous
            if retained.is_symlink():
                raise ValueError("Retained workspace cannot be a link.")
            if retained.exists() and not staging.exists() and (root / "receiptiq.sqlite3").is_file():
                # The last process promoted the staged database but exited before clearing its journal.
                validate_workspace(root)
                _reuse_assets(retained, root)
                pending_file.unlink()
                return {"restored": True, "previous_workspace": str(retained), "recovered": True}
            if retained.exists() and staging.exists():
                if (root / "receiptiq.sqlite3").exists():
                    raise ValueError("Restore recovery found two databases. Keep both folders and contact support.")
                # Interrupted between renames: put the original back before retrying the normal swap.
                workspace_lock.release()
                root.replace(root.with_name(root.name + ".incomplete-restore-" + uuid4().hex[:8]))
                retained.replace(root)
                workspace_lock = FileLock(str(root / "workspace.lock"), timeout=0)
                workspace_lock.acquire()
            if not staging.is_dir() or staging.is_symlink():
                raise ValueError("Restore staging folder is missing.")
            validate_workspace(staging)
            # The workspace lock is held in the old directory; Windows cannot rename a folder containing an open lock.
            # A sibling lock excludes cooperating launchers during this short offline swap.
            workspace_lock.release()
            root.replace(retained)
            try:
                staging.replace(root)
                # Reuse downloaded assets without adding gigabytes to backup archives.
                _reuse_assets(retained, root)
                pending_file.unlink()
            except BaseException:
                if root.exists():
                    root.replace(staging)
                retained.replace(root)
                raise
            return {"restored": True, "previous_workspace": str(retained)}
        finally:
            workspace_lock.release()


def _link_or_copy(source, destination):
    if Path(destination).exists() and os.path.samefile(source, destination):
        return destination
    try:
        os.link(source, destination)
        return destination
    except OSError:
        return shutil.copy2(source, destination)


def validate_workspace(folder):
    """Check version, SQLite integrity, and the original/attachment/page/export files referenced by the DB."""
    if not (folder / "receiptiq.sqlite3").is_file():
        raise ValueError("Restored database is missing.")
    with closing(sqlite3.connect(folder / "receiptiq.sqlite3")) as database:
        version = database.execute("PRAGMA user_version").fetchone()[0]
        if not 1 <= version <= MAX_SCHEMA or database.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ValueError("Staged backup is not compatible or valid.")
        if database.execute("PRAGMA foreign_key_check").fetchone():
            raise ValueError("Workspace database has broken references.")
        _check_references(database, folder)


def _check_references(connection, root):
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not {"receipts", "documents", "jobs", "batches", "batch_files", "schema_migrations"} <= tables:
        raise ValueError("Backup is missing required ReceiptIQ tables.")
    for table, columns in (("documents", "relative_path"), ("attachments", "relative_path"),
                           ("document_pages", "original_path,processed_path"), ("exports", "relative_path")):
        if table not in tables:
            continue
        for row in connection.execute(f"SELECT {columns} FROM {table}"):
            for relative in row:
                if not isinstance(relative, str) or not _allowed_name(relative) or PurePosixPath(relative).parts[0] not in FOLDERS:
                    raise ValueError("Workspace contains an unsafe file reference.")
                path = root / relative
                if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
                    raise ValueError("A referenced original, attachment, page or export is missing. Keep the original workspace and repair it before retrying.")


def _reuse_assets(previous, root):
    for folder in ("models", "runtimes", "backups"):
        if (previous / folder).exists():
            shutil.copytree(previous / folder, root / folder, copy_function=_link_or_copy, dirs_exist_ok=True)
