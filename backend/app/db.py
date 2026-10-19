"""SQLite owns jobs, editable values, audit events, and export snapshots."""

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


def uid():
    return str(uuid4())


def now():
    return datetime.now(timezone.utc).isoformat()


def data_directory():
    configured = os.environ.get("RECEIPTIQ_DATA_DIR")
    return Path(configured) if configured else Path(os.environ.get("LOCALAPPDATA", Path.home())) / "ReceiptIQ"


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
 rotation INTEGER NOT NULL DEFAULT 0, error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
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
 error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
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
"""


class Store:
    def __init__(self, root: Path):
        self.root = root.resolve()
        for folder in ("originals", "pages", "exports", "tmp", "models"):
            (self.root / folder).mkdir(parents=True, exist_ok=True)
        self.path = self.root / "receiptiq.sqlite3"
        with self.connect() as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version > 1:
                raise RuntimeError("This workspace needs a newer ReceiptIQ version.")
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(SCHEMA)
            connection.execute("INSERT OR IGNORE INTO schema_migrations VALUES(1)")
            connection.execute("PRAGMA user_version=1")

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
        receipt = self.one("SELECT * FROM receipts WHERE id=?", (receipt_id,))
        if not receipt:
            return None
        receipt["data"] = json.loads(receipt.pop("data_json"))
        receipt["data"]["items"] = [json.loads(row["data_json"]) for row in self.read(
            "SELECT data_json FROM receipt_items WHERE receipt_id=? ORDER BY position", (receipt_id,))]
        receipt["field_meta"] = json.loads(receipt.pop("field_meta_json"))
        receipt["pages"] = self.read(
            "SELECT id,page_number,width,height,transform_json FROM document_pages WHERE receipt_id=? "
            "ORDER BY page_number", (receipt_id,))
        run = self.one("SELECT result_json FROM extraction_runs WHERE receipt_id=? ORDER BY created_at DESC LIMIT 1",
                       (receipt_id,))
        receipt["evidence"] = json.loads(run["result_json"]) if run else {"lines": []}
        receipt["original_name"] = self.one("SELECT original_name FROM documents WHERE id=?",
                                            (receipt["document_id"],))["original_name"]
        return receipt

    @staticmethod
    def write_data(connection, receipt_id, data):
        header = {key: value for key, value in data.items() if key != "items"}
        connection.execute("UPDATE receipts SET data_json=? WHERE id=?", (json.dumps(header), receipt_id))
        connection.execute("DELETE FROM receipt_items WHERE receipt_id=?", (receipt_id,))
        connection.executemany("INSERT INTO receipt_items VALUES(?,?,?)", [
            (receipt_id, i, json.dumps(item)) for i, item in enumerate(data.get("items", []))])

    @staticmethod
    def audit(connection, receipt_id, revision, action, before=None, after=None, reviewer=None, reason=None):
        connection.execute("INSERT INTO review_events VALUES(?,?,?,?,?,?,?,?,?)", (
            uid(), receipt_id, revision, action, json.dumps(before), json.dumps(after), reviewer, reason, now()))
