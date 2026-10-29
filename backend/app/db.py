"""SQLite owns jobs, editable values, audit events, and export snapshots."""

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .models import ReceiptData


def uid():
    return str(uuid4())


def now():
    return datetime.now(timezone.utc).isoformat()


def data_directory():
    configured = os.environ.get("RECEIPTIQ_DATA_DIR")
    if configured:
        return Path(configured)
    local = Path(os.environ.get("LOCALAPPDATA", Path.home()))
    try:
        location = json.loads((local / "ReceiptIQ-launch.json").read_text(encoding="utf-8")).get("active_workspace")
        if location and Path(location).is_absolute():
            return Path(location)
    except (OSError, ValueError, TypeError):
        pass
    return local / "ReceiptIQ"


SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY);
CREATE TABLE IF NOT EXISTS batches(
 id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at TEXT NOT NULL, cancelled INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS batch_files(
 id TEXT PRIMARY KEY, batch_id TEXT NOT NULL REFERENCES batches(id), name TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'pending', error TEXT, receipt_id TEXT
);
CREATE TABLE IF NOT EXISTS documents(
 id TEXT PRIMARY KEY, original_name TEXT NOT NULL, relative_path TEXT NOT NULL,
 sha256 TEXT NOT NULL, mime_type TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS receipts(
 id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id),
 batch_id TEXT NOT NULL REFERENCES batches(id), upload_id TEXT NOT NULL UNIQUE REFERENCES batch_files(id),
 status TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0, verified_revision INTEGER,
 data_json TEXT NOT NULL, field_meta_json TEXT NOT NULL DEFAULT '{}',
 rotation INTEGER NOT NULL DEFAULT 0, error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 assignee TEXT NOT NULL DEFAULT '', second_review INTEGER NOT NULL DEFAULT 0,
 retrieval_allowed INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS receipt_items(
 receipt_id TEXT NOT NULL REFERENCES receipts(id) ON DELETE CASCADE,
 position INTEGER NOT NULL, data_json TEXT NOT NULL, PRIMARY KEY(receipt_id, position)
);
CREATE TABLE IF NOT EXISTS document_pages(
 id TEXT PRIMARY KEY, receipt_id TEXT NOT NULL REFERENCES receipts(id) ON DELETE CASCADE,
 page_number INTEGER NOT NULL, original_path TEXT NOT NULL, processed_path TEXT NOT NULL,
 width INTEGER NOT NULL, height INTEGER NOT NULL, transform_json TEXT NOT NULL,
 UNIQUE(receipt_id, page_number)
);
CREATE TABLE IF NOT EXISTS jobs(
 id TEXT PRIMARY KEY, receipt_id TEXT NOT NULL REFERENCES receipts(id) ON DELETE CASCADE,
 batch_id TEXT NOT NULL REFERENCES batches(id), state TEXT NOT NULL, stage TEXT NOT NULL,
 progress INTEGER NOT NULL DEFAULT 0, attempts INTEGER NOT NULL DEFAULT 0,
 error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'ocr',
 payload_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS jobs_state ON jobs(state, created_at);
CREATE INDEX IF NOT EXISTS receipts_batch ON receipts(batch_id, created_at);
CREATE TABLE IF NOT EXISTS extraction_runs(
 id TEXT PRIMARY KEY, receipt_id TEXT NOT NULL REFERENCES receipts(id) ON DELETE CASCADE,
 engine TEXT NOT NULL, result_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS review_events(
 id TEXT PRIMARY KEY, receipt_id TEXT NOT NULL REFERENCES receipts(id) ON DELETE CASCADE,
 revision INTEGER NOT NULL, action TEXT NOT NULL, before_json TEXT, after_json TEXT,
 reviewer TEXT, reason TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS export_queue(
 receipt_id TEXT PRIMARY KEY REFERENCES receipts(id) ON DELETE CASCADE, verified_revision INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS csv_templates(id TEXT PRIMARY KEY, version INTEGER NOT NULL, definition_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS exports(id TEXT PRIMARY KEY, relative_path TEXT NOT NULL, template_json TEXT NOT NULL,
 created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS export_receipts(
 export_id TEXT NOT NULL REFERENCES exports(id), receipt_id TEXT NOT NULL,
 verified_revision INTEGER NOT NULL, snapshot_json TEXT NOT NULL, PRIMARY KEY(export_id, receipt_id)
);
CREATE TABLE IF NOT EXISTS attachments(
 id TEXT PRIMARY KEY, receipt_id TEXT NOT NULL REFERENCES receipts(id) ON DELETE CASCADE,
 relative_path TEXT NOT NULL, original_name TEXT NOT NULL, mime_type TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS line_item_numbers(
 number INTEGER PRIMARY KEY AUTOINCREMENT,
 receipt_id TEXT NOT NULL REFERENCES receipts(id) ON DELETE CASCADE, item_id TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS retrieval_entries(
 receipt_id TEXT PRIMARY KEY REFERENCES receipts(id) ON DELETE CASCADE,
 verified_revision INTEGER NOT NULL, embedding_model TEXT NOT NULL, text TEXT NOT NULL,
 vector_json TEXT NOT NULL, indexed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS documents_hash ON documents(sha256);
"""


class Store:
    def __init__(self, root: Path):
        self.root = root.resolve()
        for folder in ("originals", "pages", "exports", "tmp", "models", "attachments", "backups"):
            (self.root / folder).mkdir(parents=True, exist_ok=True)
        self.path = self.root / "receiptiq.sqlite3"
        with self.connect() as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version > 3:
                raise RuntimeError("This workspace needs a newer Receiptor version.")
            if version in (1, 2):
                from .backups import create_backup

                create_backup(self)
                target = 2 if version == 1 else 3
                backup = sqlite3.connect(self.root / "backups" / (f"before-v{target}-" + uid() + ".sqlite3"))
                try:
                    connection.backup(backup)
                finally:
                    backup.close()
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(SCHEMA)
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(jobs)")}
            if "kind" not in columns:
                connection.execute("ALTER TABLE jobs ADD COLUMN kind TEXT NOT NULL DEFAULT 'ocr'")
            if "payload_json" not in columns:
                connection.execute("ALTER TABLE jobs ADD COLUMN payload_json TEXT NOT NULL DEFAULT '{}'")
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(receipts)")}
            for name, definition in (("assignee", "TEXT NOT NULL DEFAULT ''"),
                                     ("second_review", "INTEGER NOT NULL DEFAULT 0"),
                                     ("retrieval_allowed", "INTEGER NOT NULL DEFAULT 0")):
                if name not in columns:
                    connection.execute(f"ALTER TABLE receipts ADD COLUMN {name} {definition}")
            if version < 2:
                for row in connection.execute("SELECT id,data_json FROM receipts").fetchall():
                    data = json.loads(row["data_json"])
                    data["items"] = [json.loads(item["data_json"]) for item in connection.execute(
                        "SELECT data_json FROM receipt_items WHERE receipt_id=? ORDER BY position", (row["id"],))]
                    self.write_data(connection, row["id"], data)
            connection.execute("INSERT OR IGNORE INTO schema_migrations VALUES(1)")
            connection.execute("INSERT OR IGNORE INTO schema_migrations VALUES(2)")
            connection.execute("INSERT OR IGNORE INTO schema_migrations VALUES(3)")
            connection.execute("PRAGMA user_version=3")

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def read(self, sql, parameters=()):
        with self.connect() as connection:
            return [dict(row) for row in connection.execute(sql, parameters)]

    def one(self, sql, parameters=()):
        rows = self.read(sql, parameters)
        return rows[0] if rows else None

    def execute(self, sql, parameters=()):
        with self.connect() as connection:
            connection.execute(sql, parameters)

    def file(self, relative):
        path = (self.root / relative).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("Path outside workspace")
        return path

    def receipt(self, receipt_id):
        with self.connect() as connection:
            # Header, items, evidence, and revision must belong to one SQLite snapshot.
            connection.execute("BEGIN")

            def read(sql, parameters=()):
                return [dict(row) for row in connection.execute(sql, parameters)]

            def one(sql, parameters=()):
                rows = read(sql, parameters)
                return rows[0] if rows else None

            receipt = one("SELECT * FROM receipts WHERE id=?", (receipt_id,))
            if not receipt:
                return None
            receipt["data"] = json.loads(receipt.pop("data_json"))
            receipt["data"]["items"] = [json.loads(row["data_json"]) for row in read(
                "SELECT data_json FROM receipt_items WHERE receipt_id=? ORDER BY position", (receipt_id,))]
            receipt["data"] = ReceiptData.model_validate(receipt["data"]).model_dump()
            receipt["field_meta"] = json.loads(receipt.pop("field_meta_json"))
            receipt["pages"] = read(
                "SELECT id,page_number,width,height,transform_json FROM document_pages WHERE receipt_id=? "
                "ORDER BY page_number", (receipt_id,))
            run = one("SELECT result_json FROM extraction_runs WHERE receipt_id=? AND engine NOT LIKE 'Ollama%' "
                      "AND engine NOT LIKE 'Google%' "
                      "ORDER BY created_at DESC LIMIT 1", (receipt_id,))
            receipt["evidence"] = json.loads(run["result_json"]) if run else {"lines": []}
            if "draft" in receipt["evidence"]:
                receipt["evidence"]["draft"] = ReceiptData.model_validate(receipt["evidence"]["draft"]).model_dump()
            candidate = one("SELECT id,result_json,created_at FROM extraction_runs WHERE receipt_id=? "
                            "AND (engine LIKE 'Ollama%' OR engine LIKE 'Google%') ORDER BY created_at DESC LIMIT 1", (receipt_id,))
            receipt["candidate"] = ({**json.loads(candidate["result_json"]), "id": candidate["id"],
                                      "created_at": candidate["created_at"]} if candidate else None)
            receipt["vision_job"] = one(
                "SELECT id,state,stage,error,progress FROM jobs WHERE receipt_id=? AND kind='vision' "
                "ORDER BY created_at DESC LIMIT 1", (receipt_id,))
            receipt["duplicates"] = read(
                "SELECT r.id,d.original_name,r.status FROM receipts r JOIN documents d ON d.id=r.document_id "
                "WHERE d.sha256=(SELECT sha256 FROM documents WHERE id=?) AND r.id<>? LIMIT 10",
                (receipt["document_id"], receipt_id))
            document = one("SELECT original_name,mime_type FROM documents WHERE id=?", (receipt["document_id"],))
            receipt["original_name"], receipt["original_mime"] = document["original_name"], document["mime_type"]
            return receipt

    @staticmethod
    def write_data(connection, receipt_id, data):
        data = ReceiptData.model_validate(data).model_dump()
        data["currency"] = data["currency"] or "PHP"
        for item in data["items"]:
            number = connection.execute("SELECT number,receipt_id FROM line_item_numbers WHERE item_id=?",
                                        (item["id"],)).fetchone()
            if number and number["receipt_id"] != receipt_id:
                raise ValueError("Line item belongs to another receipt.")
            if not number:
                cursor = connection.execute("INSERT INTO line_item_numbers(receipt_id,item_id) VALUES(?,?)",
                                            (receipt_id, item["id"]))
                item["item_number"] = cursor.lastrowid
            else:
                item["item_number"] = number["number"]
        header = {key: value for key, value in data.items() if key != "items"}
        connection.execute("UPDATE receipts SET data_json=? WHERE id=?", (json.dumps(header), receipt_id))
        connection.execute("DELETE FROM receipt_items WHERE receipt_id=?", (receipt_id,))
        connection.executemany("INSERT INTO receipt_items VALUES(?,?,?)", [
            (receipt_id, i, json.dumps(item)) for i, item in enumerate(data.get("items", []))])
        return data

    @staticmethod
    def audit(connection, receipt_id, revision, action, before=None, after=None, reviewer=None, reason=None):
        connection.execute("INSERT INTO review_events VALUES(?,?,?,?,?,?,?,?,?)", (
            uid(), receipt_id, revision, action, json.dumps(before), json.dumps(after), reviewer, reason, now()))
