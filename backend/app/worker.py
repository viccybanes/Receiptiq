"""One persistent, bounded worker. SQLite jobs survive application restarts."""

import json
import logging
import threading
import time

from .db import Store, now, uid
from .extraction import parse
from .imaging import pages, preprocess
from .models import ReceiptData
from .ocr import PaddleEngine
from .retrieval import Retrieval
from .vision import VisionEngine

logger = logging.getLogger(__name__)


class Worker:
    def __init__(self, store: Store, engine=None):
        self.store = store
        self.engine = engine or PaddleEngine(store.root)
        self.stop = threading.Event()
        self.thread = None
        self.vision = VisionEngine()
        self.retrieval = Retrieval(store)

    def start(self):
        # App lifecycle holds a workspace lock, so no other instance can own these jobs.
        with self.store.connect() as connection:
            connection.execute("UPDATE jobs SET state='queued',stage='Recovered after restart' WHERE state='processing'")
            connection.execute("UPDATE receipts SET status='queued' WHERE status='processing'")
        self.thread = threading.Thread(target=self.run, name="receipt-ocr", daemon=True)
        self.thread.start()

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=30)
        # If a native inference call is still active, leave the job persisted for restart.

    def run(self):
        while not self.stop.is_set():
            if not self.process_one():
                self.stop.wait(0.4)

    def process_one(self):
        with self.store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            job = connection.execute("SELECT * FROM jobs WHERE state='queued' ORDER BY "
                                     "CASE kind WHEN 'vision' THEN 0 WHEN 'index' THEN 1 ELSE 2 END,created_at "
                                     "LIMIT 1").fetchone()
            if not job:
                return False
            job = dict(job)
            connection.execute("UPDATE jobs SET state='processing',stage='Preparing pages',attempts=attempts+1,"
                               "updated_at=? WHERE id=?", (now(), job["id"]))
            if job["kind"] in ("ocr", "preview"):
                connection.execute("UPDATE receipts SET status='processing',error=NULL WHERE id=?", (job["receipt_id"],))
        try:
            if job["kind"] == "vision":
                self.process_vision(job)
            elif job["kind"] == "index":
                if not self.retrieval.index(job["receipt_id"]):
                    raise ValueError(self.retrieval.last_error or "Example not eligible for retrieval.")
                self.store.execute("UPDATE jobs SET state='done',stage='Verified example indexed',progress=100,"
                                   "updated_at=? WHERE id=?", (now(), job["id"]))
            else:
                self.process(job)
        except Exception as exc:
            logger.exception("Receipt processing failed for job %s", job["id"])
            message = str(exc)[:1000] or "OCR failed. Open the receipt for manual entry or retry."
            with self.store.connect() as connection:
                connection.execute("UPDATE jobs SET state='failed',stage='Processing failed',error=?,updated_at=? "
                                   "WHERE id=? AND state='processing'", (message, now(), job["id"]))
                if job["kind"] in ("ocr", "preview"):
                    connection.execute("UPDATE receipts SET status='failed',error=?,updated_at=? "
                                       "WHERE id=? AND status='processing'", (message, now(), job["receipt_id"]))
        return True

    def cancelled(self, job):
        row = self.store.one("SELECT state FROM jobs WHERE id=?", (job["id"],))
        return self.stop.is_set() or not row or row["state"] == "cancelled"

    def process_vision(self, job):
        receipt = self.store.receipt(job["receipt_id"])
        payload = json.loads(job["payload_json"])
        page = self.store.one("SELECT * FROM document_pages WHERE receipt_id=? AND id=?",
                              (receipt["id"], payload["page_id"]))
        if not page:
            raise ValueError("Receipt page is unavailable. Run OCR or import its image first.")
        if self.cancelled(job):
            return
        examples = self.retrieval.search(receipt["id"])
        self.store.execute("UPDATE jobs SET stage='Reading with local vision',progress=20 WHERE id=?", (job["id"],))
        lines = [line for line in receipt["evidence"]["lines"] if line["page"] == page["page_number"]]
        result = self.vision.extract([self.store.file(page["processed_path"])], lines, examples=examples,
                                     crop=payload.get("crop"), model=payload["model"],
                                     current=receipt["data"], stop=lambda: self.cancelled(job),
                                     preserve_items=len(receipt["pages"]) > 1)
        if self.cancelled(job):
            return
        result["base_revision"] = receipt["revision"]
        result["photo_id"] = receipt["data"]["receipt_photo_id"]
        result["examples"] = examples
        with self.store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            state = connection.execute("SELECT state FROM jobs WHERE id=?", (job["id"],)).fetchone()
            if not state or state["state"] != "processing":
                return
            connection.execute("INSERT INTO extraction_runs VALUES(?,?,?,?,?)",
                               (uid(), receipt["id"], "Ollama / " + result["model"], json.dumps(result), now()))
            connection.execute("UPDATE jobs SET state='done',stage='Vision suggestion ready',progress=100,"
                               "error=NULL,updated_at=? WHERE id=?", (now(), job["id"]))

    def process(self, job):
        started = time.monotonic()
        receipt = self.store.receipt(job["receipt_id"])
        document = self.store.one("SELECT * FROM documents WHERE id=?", (receipt["document_id"],))
        photo_id = receipt["data"].get("receipt_photo_id", "")
        if photo_id:
            document = self.store.one("SELECT * FROM attachments WHERE id=? AND receipt_id=?",
                                      (photo_id, receipt["id"]))
        preview_only = job.get("kind") == "preview"
        lines = []
        directory = self.store.root / "pages" / receipt["id"]
        directory.mkdir(exist_ok=True)
        images = [] if document["mime_type"] == "text/csv" else pages(
            self.store.file(document["relative_path"]), document["mime_type"])
        page_count = 0
        for number, image in enumerate(images):
            page_count = number + 1
            if self.cancelled(job):
                return
            original, processed, transform = preprocess(image, receipt["rotation"])
            image.close()
            original_relative = f"pages/{receipt['id']}/{number}-original.png"
            processed_relative = f"pages/{receipt['id']}/{number}-processed.png"
            original.save(self.store.file(original_relative))
            processed.save(self.store.file(processed_relative))
            width, height = processed.size
            original.close()
            processed.close()
            with self.store.connect() as connection:
                connection.execute(
                    "INSERT INTO document_pages VALUES(?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(receipt_id,page_number) DO UPDATE SET width=excluded.width,height=excluded.height,"
                    "transform_json=excluded.transform_json",
                    (uid(), receipt["id"], number, original_relative, processed_relative,
                     width, height, json.dumps(transform)))
                connection.execute("UPDATE jobs SET stage=?,progress=?,updated_at=? WHERE id=?",
                                   (f"Reading page {number + 1}", min(90, 10 + number * 5), now(), job["id"]))
            if not preview_only:
                for line in self.engine.recognize(self.store.file(processed_relative)):
                    lines.append({**line, "id": f"{number}-{len(lines)}", "page": number})
            if self.cancelled(job):
                return
        data, metadata = (receipt["data"], {}) if preview_only else parse(lines)
        data = ReceiptData.model_validate(data).model_dump()
        result = {"lines": lines, "draft": data, "seconds": round(time.monotonic() - started, 3),
                  "photo_id": photo_id,
                  "notice": "OCR suggestions require human verification."}
        if not preview_only:
            result["examples"] = self.retrieval.search(receipt["id"], query=" ".join(
                line["text"] for line in lines)[:4000])
            if self.retrieval.last_error:
                result["retrieval_notice"] = self.retrieval.last_error
        with self.store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                "SELECT revision, EXISTS(SELECT 1 FROM review_events WHERE receipt_id=receipts.id "
                "AND action IN ('edit','import')) AS manually_edited FROM receipts WHERE id=?", (receipt["id"],)).fetchone()
            state = connection.execute("SELECT state FROM jobs WHERE id=?", (job["id"],)).fetchone()
            if not current or state["state"] == "cancelled":
                return
            connection.execute("DELETE FROM document_pages WHERE receipt_id=? AND page_number>=?",
                               (receipt["id"], page_count))
            connection.execute("INSERT INTO extraction_runs VALUES(?,?,?,?,?)",
                               (uid(), receipt["id"], "CSV preview" if preview_only else "PaddleOCR 3.0.3 / CPU",
                                json.dumps(result), now()))
            # Any human edit wins. Reprocessing still records evidence and an alternative draft.
            if not current["manually_edited"]:
                self.store.write_data(connection, receipt["id"], data)
                connection.execute("UPDATE receipts SET field_meta_json=?,revision=revision+1 WHERE id=?",
                                   (json.dumps(metadata), receipt["id"]))
            connection.execute("UPDATE receipts SET status='needs_review',updated_at=?,error=NULL WHERE id=?",
                               (now(), receipt["id"]))
            connection.execute("UPDATE jobs SET state='done',stage='Ready for review',progress=100,error=NULL,"
                               "updated_at=? WHERE id=?", (now(), job["id"]))
