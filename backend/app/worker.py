"""One persistent, bounded worker. SQLite jobs survive application restarts."""

import json
import logging
import threading
import time

from .db import Store, now, uid
from .extraction import parse
from .google_vision import GoogleVisionEngine
from .imaging import pages, preprocess
from .layout import layout_assistance_enabled
from .models import ReceiptData
from .ocr import PaddleEngine
from .retrieval import Retrieval
from .vision import ALLOWED_MODELS, DEFAULT_MODEL, MAX_VISION_PAGES, VisionEngine
from .vision_settings import load_vision_settings, require_google_enabled

logger = logging.getLogger(__name__)
MAX_VISION_ATTEMPTS = 3


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
                                     "CASE WHEN kind='vision' AND attempts>0 THEN 3 "
                                     "WHEN kind='vision' THEN 0 WHEN kind='index' THEN 1 ELSE 2 END,"
                                     "attempts,CASE WHEN kind='vision' AND attempts>0 THEN updated_at ELSE created_at END,rowid "
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
            if self.cancelled(job):
                return True
            logger.exception("Receipt processing failed for job %s", job["id"])
            message = str(exc)[:1000] or "OCR failed. Open the receipt for manual entry or retry."
            payload = json.loads(job["payload_json"])
            upload_chain = job["kind"] == "vision" and payload.get("auto_apply") and "fallbacks" in payload
            retry = (job["kind"] == "vision" and not upload_chain and payload.get("provider", "local") == "local"
                     and job["attempts"] + 1 < MAX_VISION_ATTEMPTS)
            stage = (f"Vision failed; retry {job['attempts'] + 1}/{MAX_VISION_ATTEMPTS - 1} after queue finishes"
                     if retry else "Processing failed; retry manually")
            if upload_chain:
                # Revoking cloud consent stops the whole sequence, including queued fallbacks.
                try:
                    require_google_enabled(self.store.root)
                except ValueError:
                    pass
                else:
                    payload.setdefault("failed_models", []).append({"provider": payload["provider"], "model": payload["model"]})
                    if payload["fallbacks"]:
                        failed_model = payload["model"]
                        payload.update(payload["fallbacks"].pop(0))
                        retry = True
                        stage = f"{failed_model} failed; next: {payload['model']} after queue finishes"
                    else:
                        stage = "All upload vision models failed; retry manually"
            with self.store.connect() as connection:
                connection.execute("UPDATE jobs SET state=?,stage=?,error=?,payload_json=?,updated_at=? "
                                   "WHERE id=? AND state='processing'",
                                   ("queued" if retry else "failed", stage, message, json.dumps(payload), now(), job["id"]))
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
        provider = payload.get("provider", "local")
        if provider not in ("local", "google"):
            raise ValueError("Unknown vision provider. Request a new scan from Settings.")
        if provider == "google" or "fallbacks" in payload:
            require_google_enabled(self.store.root)
        engine = GoogleVisionEngine(self.store.root) if provider == "google" else self.vision
        examples = [] if provider == "google" else self.retrieval.search(receipt["id"])
        stage = ("Reading with Google vision" if provider == "google" else "Reading with local vision") + f" ({payload['model']})"
        self.store.execute("UPDATE jobs SET stage=?,progress=20 WHERE id=?", (stage, job["id"]))
        selected_pages = self.store.read("SELECT * FROM document_pages WHERE receipt_id=? ORDER BY page_number",
                                         (receipt["id"],)) if payload.get("auto_apply") else [page]
        image_paths = [self.store.file(row["processed_path"]) for row in selected_pages]
        if len(image_paths) > MAX_VISION_PAGES:
            raise ValueError(f"Choose between 1 and {MAX_VISION_PAGES} receipt pages for vision.")
        page_numbers = {row["page_number"]: index for index, row in enumerate(selected_pages)}
        lines = [{**line, "page": page_numbers[line["page"]]} for line in receipt["evidence"]["lines"]
                 if line["page"] in page_numbers]
        options = load_vision_settings(self.store.root).model_dump(include={"amount_first", "capture_regions"})
        location_notice = ""
        ocr_started = time.monotonic()
        ocr_status = "existing" if lines else "not_requested"
        if (provider == "local" and layout_assistance_enabled()) or options["capture_regions"]:
            missing_pages = [index for index in range(len(image_paths))
                             if not any(line["page"] == index for line in lines)]
            if missing_pages:
                self.store.execute("UPDATE jobs SET stage='Reading OCR layout' WHERE id=?", (job["id"],))
            for index in missing_pages:
                if self.cancelled(job):
                    return
                try:
                    lines.extend({**line, "page": index} for line in self.engine.recognize(image_paths[index]))
                    if ocr_status != "unavailable":
                        ocr_status = "read"
                except Exception:
                    ocr_status = "unavailable"
                    location_notice = " OCR layout assistance was unavailable for some pages; check the image carefully."
                if self.cancelled(job):
                    return
        ocr_seconds = round(time.monotonic() - ocr_started, 3)
        self.store.execute("UPDATE jobs SET stage=? WHERE id=?", (stage, job["id"]))
        result = engine.extract(image_paths, lines, examples=examples,
                                     **options,
                                     crop=payload.get("crop"), model=payload["model"],
                                     current=receipt["data"], stop=lambda: self.cancelled(job),
                                     preserve_items=len(receipt["pages"]) > 1 and not payload.get("auto_apply"))
        if self.cancelled(job):
            return
        if "fallbacks" in payload:
            require_google_enabled(self.store.root)
        if payload.get("failed_models"):
            result.setdefault("provenance", {})["upload_fallbacks"] = payload["failed_models"]
            result["notice"] = (f"Earlier upload scans failed; this result uses {payload['model']}. "
                                + result.get("notice", ""))
        result["ocr_assistance"] = {"status": ocr_status, "seconds": ocr_seconds, "line_count": len(lines)}
        if location_notice:
            result["notice"] = result.get("notice", "") + location_notice
        for region in result.get("source_regions", []):
            region["page_id"] = selected_pages[region["page_index"]]["id"]
        for field, metadata in result.get("field_meta", {}).items():
            region = next((entry for entry in result.get("source_regions", [])
                           if field.startswith(f"items.{entry['item_index']}.")), None)
            if region:
                metadata.update({"source_box": region["box"], "page_id": region["page_id"]})
        result["base_revision"] = receipt["revision"]
        result["photo_id"] = receipt["data"]["receipt_photo_id"]
        result["examples"] = examples
        with self.store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            state = connection.execute("SELECT state FROM jobs WHERE id=?", (job["id"],)).fetchone()
            if not state or state["state"] != "processing":
                return
            stage = "Vision suggestion ready"
            current = connection.execute("SELECT revision,status FROM receipts WHERE id=?", (receipt["id"],)).fetchone()
            if (payload.get("auto_apply") and current and current["status"] == "needs_review"
                    and current["revision"] == receipt["revision"] == payload.get("revision")):
                self.store.write_data(connection, receipt["id"], result["draft"])
                connection.execute("UPDATE receipts SET revision=revision+1,field_meta_json=?,updated_at=? WHERE id=?",
                                   (json.dumps(result.get("field_meta", {})), now(), receipt["id"]))
                self.store.audit(connection, receipt["id"], receipt["revision"] + 1, "auto_vision",
                                 before=receipt["data"], after=result["draft"],
                                 reason="Applied upload vision; human verification required")
                result["auto_applied"] = True
                stage = "Vision applied; ready for review"
            elif payload.get("auto_apply"):
                stage = "Vision suggestion ready; edits preserved"
            connection.execute("INSERT INTO extraction_runs VALUES(?,?,?,?,?)",
                               (uid(), receipt["id"], ("Google / " if provider == "google" else "Ollama / ") + result["model"],
                                json.dumps(result), now()))
            connection.execute("UPDATE jobs SET state='done',stage=?,progress=100,"
                               "error=NULL,updated_at=? WHERE id=?", (stage, now(), job["id"]))

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
                               (uid(), receipt["id"], "Image preview" if preview_only else "PaddleOCR 3.0.3 / CPU",
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

            if preview_only and json.loads(job["payload_json"]).get("auto_vision") and page_count:
                model = DEFAULT_MODEL
                try:
                    profile = json.loads((self.store.root / "setup-profile.json").read_text(encoding="utf-8"))
                    if profile.get("model") in ALLOWED_MODELS:
                        model = profile["model"]
                except (OSError, ValueError):
                    pass
                first_page = connection.execute("SELECT id FROM document_pages WHERE receipt_id=? ORDER BY page_number LIMIT 1",
                                                (receipt["id"],)).fetchone()
                revision = connection.execute("SELECT revision FROM receipts WHERE id=?", (receipt["id"],)).fetchone()["revision"]
                # A manual edit during preview preparation must also prevent automatic application.
                auto_revision = revision if not current["manually_edited"] else -1
                selection = json.loads(job["payload_json"]).get("vision") or {"provider": "local", "model": model}
                payload = {"page_id": first_page["id"], **selection, "revision": auto_revision, "auto_apply": True}
                connection.execute("INSERT INTO jobs(id,receipt_id,batch_id,state,stage,created_at,updated_at,kind,payload_json) "
                                   "VALUES(?,?,?,?,?,?,?,?,?)", (uid(), receipt["id"], job["batch_id"], "queued",
                                   "Waiting for vision", now(), now(), "vision", json.dumps(payload)))
