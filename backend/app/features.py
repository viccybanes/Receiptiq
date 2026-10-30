"""Vision proposals and providers, review handoff, setup, backups, and PDF grouping."""

import hashlib
import json
from pathlib import Path
from threading import Lock

import pypdfium2 as pdfium
from fastapi import File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from filelock import FileLock
from starlette.concurrency import run_in_threadpool

from .backups import MAX_ARCHIVE, create_backup, list_backups, stage_restore
from .db import now, uid
from .extraction import receipt_issues, reconcile
from .google_key import KEY_FILE, save_google_key
from .google_setup import GoogleSetupInput, google_setup_status, save_google_setup, test_google_connection
from .models import (
    LocationInput,
    ReviewOptions,
    RevisionInput,
    SetupInput,
    SplitInput,
    VisionInput,
    blank_data,
)
from .retrieval import Retrieval
from .service import edit_receipt, require_mutable_receipt, require_receipt
from .system import capabilities, setup_status, stage_location, start_setup
from .templates import TemplateConflict, delete_template, get_template, save_template
from .vision import VisionError
from .vision_settings import (
    VisionSettings,
    public_vision_settings,
    require_google_enabled,
    save_vision_settings,
    selected_vision,
)


def view_receipt(store, receipt_id):
    result = require_receipt(store, receipt_id)
    result["issues"] = receipt_issues(result)
    result["reconciliation"] = reconcile(result["data"])
    result["vision_settings"] = public_vision_settings(store.root)
    return result


def install_routes(app, store, worker):
    google_test_lock = Lock()

    @app.get("/api/v1/vision/settings")
    def vision_settings():
        return public_vision_settings(store.root)

    @app.patch("/api/v1/vision/settings")
    def update_vision_settings(body: VisionSettings):
        try:
            return save_vision_settings(store.root, body)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    async def read_google_key(request, *, allow_saved=False):
        # Parse this write-only secret separately: validation responses must never echo its input.
        try:
            raw = bytearray()
            async for chunk in request.stream():
                raw.extend(chunk)
                if len(raw) > 4096:
                    raise ValueError()
            body = json.loads(raw)
            if allow_saved and body == {}:
                return None
            if not isinstance(body, dict) or set(body) != {"api_key"} or not isinstance(body["api_key"], str):
                raise ValueError()
            return body["api_key"]
        except (ValueError, UnicodeError):
            raise HTTPException(422, "Enter a Google AI Studio API key.") from None

    @app.put("/api/v1/vision/google-key")
    async def update_google_key(request: Request):
        key = await read_google_key(request)
        try:
            # A restored backup can remember Google consent but deliberately has no key.
            # Re-entering a missing key must not resume restored cloud jobs by itself.
            if not (store.root / KEY_FILE).is_file():
                save_vision_settings(store.root, VisionSettings(provider="local", google_consent=False))
            with FileLock(str(store.root / "settings.lock"), timeout=5):
                save_google_key(store.root, key)
            return public_vision_settings(store.root)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None

    @app.delete("/api/v1/vision/google-key")
    def delete_google_key():
        return save_vision_settings(store.root, VisionSettings(provider="local", google_consent=False), clear_key=True)

    @app.post("/api/v1/vision/google-key/test")
    async def test_google_key(request: Request):
        key = await read_google_key(request, allow_saved=True)
        if not google_test_lock.acquire(blocking=False):
            raise HTTPException(409, "A Google connection test is already running. Wait for it to finish.")
        try:
            return await run_in_threadpool(test_google_connection, store.root, key)
        except (ValueError, VisionError) as exc:
            raise HTTPException(422, str(exc)) from None
        finally:
            google_test_lock.release()

    @app.get("/api/v1/vision/google-setup")
    def google_setup():
        return google_setup_status(store.root)

    @app.post("/api/v1/vision/google-setup")
    def update_google_setup(body: GoogleSetupInput):
        try:
            return save_google_setup(store.root, body.status)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None

    @app.get("/api/v1/system/capabilities")
    def hardware():
        return capabilities(store.root)

    @app.post("/api/v1/system/location", status_code=202)
    def location(body: LocationInput):
        try:
            return stage_location(store.root, body.target)
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/api/v1/setup/status")
    def setup():
        return setup_status(store.root)

    @app.post("/api/v1/setup/install", status_code=202)
    def install(body: SetupInput):
        try:
            return start_setup(store.root, body.profile)
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/v1/templates", status_code=201)
    def new_template(body: dict):
        try:
            return save_template(store, body)
        except TemplateConflict as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/api/v1/templates/{template_id}")
    def template(template_id: str):
        result = get_template(store, template_id)
        if not result:
            raise HTTPException(404, "Template not found.")
        return result

    @app.delete("/api/v1/templates/{template_id}")
    def remove_template(template_id: str, version: int):
        try:
            if not delete_template(store, template_id, version):
                raise HTTPException(404, "Template not found.")
            return {"deleted": True}
        except TemplateConflict as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.patch("/api/v1/templates/{template_id}")
    def update_template(template_id: str, body: dict):
        if body.get("id") != template_id:
            raise HTTPException(422, "Template ID must match its address.")
        return new_template(body)

    @app.post("/api/v1/receipts/{receipt_id}/vision", status_code=202)
    def vision(receipt_id: str, body: VisionInput):
        with store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            receipt = require_mutable_receipt(store, receipt_id)
            if receipt["revision"] != body.revision:
                raise HTTPException(409, "Save and review the latest receipt before requesting vision.")
            if receipt["status"] in ("queued", "processing"):
                raise HTTPException(409, "Wait for the receipt image to be ready.")
            if receipt["data"]["receipt_photo_id"] != receipt["evidence"].get("photo_id", ""):
                raise HTTPException(409, "Run OCR on the replacement photo before requesting vision.")
            if not connection.execute("SELECT 1 FROM document_pages WHERE receipt_id=? AND id=?",
                                      (receipt_id, body.page_id)).fetchone():
                raise HTTPException(404, "Receipt page not found.")
            existing = connection.execute("SELECT id FROM jobs WHERE receipt_id=? AND kind='vision' "
                                          "AND state IN ('queued','processing')", (receipt_id,)).fetchone()
            if existing:
                return {"id": existing["id"], "accepted": True}
            selection = selected_vision(store.root, body.model, provider=body.provider, google_model=body.google_model)
            if selection["provider"] == "google":
                try:
                    require_google_enabled(store.root)
                except ValueError as exc:
                    raise HTTPException(409, str(exc)) from exc
            job_id = uid()
            connection.execute("INSERT INTO jobs(id,receipt_id,batch_id,state,stage,created_at,updated_at,"
                               "kind,payload_json) VALUES(?,?,?,?,?,?,?,?,?)",
                               (job_id, receipt_id, receipt["batch_id"], "queued", "Waiting for vision",
                                now(), now(), "vision", json.dumps({**body.model_dump(), **selection})))
        return {"id": job_id, "accepted": True}

    @app.post("/api/v1/receipts/{receipt_id}/candidates/{candidate_id}/apply")
    def apply_candidate(receipt_id: str, candidate_id: str, body: RevisionInput):
        from .models import Edit
        # Check ownership and base revision; the normal edit also rejects intervening changes.
        with store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            receipt = require_mutable_receipt(store, receipt_id)
            candidate = connection.execute("SELECT result_json FROM extraction_runs WHERE id=? AND receipt_id=? "
                                           "AND (engine LIKE 'Ollama%' OR engine LIKE 'Google%')", (candidate_id, receipt_id)).fetchone()
            if not candidate:
                raise HTTPException(404, "Vision suggestion not found.")
            proposal = json.loads(candidate["result_json"])
            if receipt["revision"] != body.revision or proposal["base_revision"] != body.revision:
                raise HTTPException(409, "Receipt changed after this suggestion. Run vision again or copy fields manually.")
            # The outer transaction cannot call edit_receipt's independent writer. Validate under it,
            # then release; edit_receipt's expected revision rejects intervening edits.
        edit_receipt(store, receipt_id, Edit(revision=body.revision, data=proposal["draft"]))
        return view_receipt(store, receipt_id)

    @app.get("/api/v1/receipts/{receipt_id}/suggestions")
    def suggestions(receipt_id: str):
        require_receipt(store, receipt_id)
        retrieval = Retrieval(store)
        examples = retrieval.search(receipt_id)
        return {"examples": examples, "notice": retrieval.last_error}

    @app.post("/api/v1/receipts/{receipt_id}/review-options")
    def review_options(receipt_id: str, body: ReviewOptions):
        with store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            receipt = require_mutable_receipt(store, receipt_id)
            if receipt["revision"] != body.revision:
                raise HTTPException(409, "Receipt changed. Save the latest draft before assigning review.")
            status = receipt["status"]
            verified = receipt["verified_revision"]
            if body.second_review:
                status = status if status in ("queued", "processing") else "needs_review"
                verified = None
                connection.execute("DELETE FROM export_queue WHERE receipt_id=?", (receipt_id,))
            connection.execute("UPDATE receipts SET assignee=?,second_review=?,retrieval_allowed=?,status=?,"
                               "verified_revision=?,updated_at=? WHERE id=?",
                               (body.assignee, body.second_review, body.retrieval_allowed, status, verified, now(), receipt_id))
            connection.execute("DELETE FROM retrieval_entries WHERE receipt_id=?", (receipt_id,))
            if body.retrieval_allowed and status == "verified":
                connection.execute("INSERT INTO jobs(id,receipt_id,batch_id,state,stage,created_at,updated_at,kind) "
                                   "VALUES(?,?,?,?,?,?,?,?)", (uid(), receipt_id, receipt["batch_id"], "queued",
                                                              "Index verified example", now(), now(), "index"))
            store.audit(connection, receipt_id, receipt["revision"], "assignment",
                        after=body.model_dump(), reviewer=body.assignee, reason="Local review handoff")
        return view_receipt(store, receipt_id)

    @app.get("/api/v1/backups")
    def backups():
        return list_backups(store.root)

    @app.post("/api/v1/backups", status_code=201)
    def backup():
        try:
            path = create_backup(store)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"id": Path(path).name, "name": Path(path).name}

    @app.get("/api/v1/backups/{backup_id}/download")
    def backup_download(backup_id: str):
        if Path(backup_id).name != backup_id or not backup_id.endswith(".zip"):
            raise HTTPException(404, "Backup not found.")
        path = store.root / "backups" / backup_id
        if not path.is_file():
            raise HTTPException(404, "Backup not found.")
        return FileResponse(path, media_type="application/zip", filename=backup_id)

    @app.post("/api/v1/backups/restore", status_code=202)
    async def restore(file: UploadFile = File(...)):
        # Restoring never swaps an active workspace. The launcher applies validated staging offline.
        temporary = store.root / "tmp" / (uid() + ".restore")
        try:
            total = 0
            with temporary.open("wb") as output:
                while chunk := await file.read(1024 * 1024):
                    total += len(chunk)
                    if total > MAX_ARCHIVE:
                        raise HTTPException(413, "Backup exceeds the 2 GB upload limit.")
                    output.write(chunk)
            try:
                return await run_in_threadpool(stage_restore, store.root, temporary)
            except (ValueError, RuntimeError) as exc:
                raise HTTPException(422, str(exc)) from exc
        finally:
            temporary.unlink(missing_ok=True)
            await file.close()

    @app.post("/api/v1/receipts/{receipt_id}/split", status_code=201)
    def split(receipt_id: str, body: SplitInput):
        receipt = require_mutable_receipt(store, receipt_id)
        if receipt["revision"] != body.revision or receipt["status"] in ("queued", "processing", "split"):
            raise HTTPException(409, "Save and finish processing before splitting.")
        if receipt["original_mime"] != "application/pdf" or receipt["data"]["receipt_photo_id"]:
            raise HTTPException(422, "Split supports original PDF pages. Images already represent one receipt.")
        document = store.one("SELECT * FROM documents WHERE id=?", (receipt["document_id"],))
        created = []
        source = pdfium.PdfDocument(store.file(document["relative_path"]))
        try:
            indices = [index for group in body.groups for index in group]
            if not all(group for group in body.groups) or sorted(indices) != list(range(len(source))):
                raise HTTPException(422, "Assign every PDF page exactly once, using zero-based page numbers.")
            batch_id = uid()
            with store.connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                current = connection.execute("SELECT revision,status FROM receipts WHERE id=?", (receipt_id,)).fetchone()
                if current["revision"] != body.revision or current["status"] in ("queued", "processing", "split"):
                    raise HTTPException(409, "Receipt changed before splitting.")
                connection.execute("INSERT INTO batches(id,name,created_at) VALUES(?,?,?)",
                                   (batch_id, "Split PDF · " + receipt["original_name"][:150], now()))
                receipt_ids = []
                for number, group in enumerate(body.groups):
                    new_document, new_receipt, upload_id = uid(), uid(), uid()
                    relative = f"originals/{new_document}.pdf"
                    path = store.file(relative)
                    created.append(path)
                    target = pdfium.PdfDocument.new()
                    try:
                        target.import_pages(source, group)
                        target.save(path)
                    finally:
                        target.close()
                    name = f"{Path(receipt['original_name']).stem}-part-{number + 1}.pdf"
                    connection.execute("INSERT INTO documents VALUES(?,?,?,?,?,?)",
                                       (new_document, name, relative, hashlib.sha256(path.read_bytes()).hexdigest(),
                                        "application/pdf", now()))
                    connection.execute("INSERT INTO batch_files(id,batch_id,name,state,receipt_id) VALUES(?,?,?,?,?)",
                                       (upload_id, batch_id, name, "accepted", new_receipt))
                    connection.execute("INSERT INTO receipts(id,document_id,batch_id,upload_id,status,data_json,"
                                       "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                                       (new_receipt, new_document, batch_id, upload_id, "queued",
                                        json.dumps(blank_data()), now(), now()))
                    connection.execute("INSERT INTO jobs(id,receipt_id,batch_id,state,stage,created_at,updated_at,kind) "
                                       "VALUES(?,?,?,?,?,?,?,?)", (uid(), new_receipt, batch_id, "queued",
                                                                  "Preparing preview", now(), now(), "preview"))
                    receipt_ids.append(new_receipt)
                connection.execute("UPDATE receipts SET status='split',verified_revision=NULL,retrieval_allowed=0 "
                                   "WHERE id=?", (receipt_id,))
                connection.execute("DELETE FROM export_queue WHERE receipt_id=?", (receipt_id,))
                connection.execute("DELETE FROM retrieval_entries WHERE receipt_id=?", (receipt_id,))
                store.audit(connection, receipt_id, receipt["revision"], "split", after=receipt_ids,
                            reason="Source retained; pages grouped into new unverified receipts.")
            return {"id": batch_id, "receipt_ids": receipt_ids}
        except BaseException:
            for path in created:
                path.unlink(missing_ok=True)
            raise
        finally:
            source.close()
