"""Local HTTP API and static frontend host."""

import asyncio
import hashlib
import json
import os
import secrets
import shutil
import sqlite3
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from filelock import FileLock, Timeout
from starlette.concurrency import run_in_threadpool

from .backups import apply_staged_restore
from .db import Store, data_directory, now, uid
from .extraction import allocate_discount, receipt_issues, reconcile
from .features import install_routes, view_receipt
from .imaging import inspect_file
from .models import BatchInput, Edit, ExportInput, Process, Verify, blank_data
from .ocr import models_ready
from .service import (
    edit_receipt,
    export_rows,
    export_selection,
    generate_export,
    require_mutable_receipt,
    require_receipt,
    verify_receipt,
)
from .system import ensure_runtime, stop_runtime
from .templates import list_templates
from .transfer import MAX_BUNDLE, add_photo, import_expenses
from .vision_settings import selected_vision
from .worker import Worker

MAX_UPLOAD = 25 * 1024 * 1024


def create_app(root=None, *, start_worker=True, engine=None):
    root = (Path(root) if root else data_directory()).resolve()
    root.mkdir(parents=True, exist_ok=True)
    apply_staged_restore(root)
    token = os.environ.get("RECEIPTIQ_SESSION_TOKEN") or secrets.token_urlsafe(32)
    if len(token) < 32:
        raise RuntimeError("Desktop session token must contain at least 32 characters.")
    desktop = os.environ.get("RECEIPTIQ_DESKTOP") == "1"
    workspace_lock = FileLock(str(root / "workspace.lock"))
    # Acquire before migrations, under the same sibling lock used by offline restore.
    with FileLock(str(root.parent / ("." + root.name + ".restore.lock")), timeout=0):
        try:
            workspace_lock.acquire(timeout=0)
        except Timeout as exc:
            raise RuntimeError("ReceiptIQ is already using this workspace. Close the other instance.") from exc
        try:
            store = Store(root)
        except BaseException:
            workspace_lock.release()
            raise
    worker = Worker(store, engine)
    upload_lock = asyncio.Lock()

    @asynccontextmanager
    async def lifespan(app):
        try:
            # Incomplete transfers never become accepted receipts.
            for temporary in (store.root / "tmp").glob("*.upload"):
                temporary.unlink(missing_ok=True)
            store.execute("UPDATE batch_files SET state='pending',error='Transfer interrupted. Reselect this file.' "
                          "WHERE state='uploading'")
            if start_worker:
                profile_path = store.root / "setup-profile.json"
                if profile_path.exists():
                    try:
                        if json.loads(profile_path.read_text(encoding="utf-8")).get("model"):
                            ensure_runtime(store.root)
                    except (ValueError, RuntimeError, OSError):
                        app.state.vision_notice = "Local vision is unavailable. Retry setup; OCR and manual entry remain available."
                worker.start()
            yield
        finally:
            worker.close()
            stop_runtime(store.root)
            workspace_lock.release()

    app = FastAPI(title="ReceiptIQ", version="0.4.0", lifespan=lifespan, docs_url=None, redoc_url=None,
                  openapi_url=None)
    app.state.store, app.state.worker = store, worker

    @app.middleware("http")
    async def local_access(request: Request, call_next):
        host = urlsplit("//" + request.headers.get("host", "")).hostname
        if host not in ("127.0.0.1", "localhost"):
            return JSONResponse({"detail": "Local host required"}, 403)
        origin = request.headers.get("origin")
        allowed = {f"http://127.0.0.1:{port}" for port in (5173, 8765)} | {
            f"http://localhost:{port}" for port in (5173, 8765)}
        # Same-origin production works on a custom port; cross-origin dev uses Vite's proxy.
        allowed.add(str(request.base_url).rstrip("/"))
        if request.headers.get("sec-fetch-site") == "cross-site" or (origin and origin not in allowed):
            return JSONResponse({"detail": "Origin not allowed"}, 403)
        if request.url.path.startswith("/api/v1") and (
                desktop or request.url.path not in ("/api/v1/session", "/api/v1/health")):
            supplied = request.headers.get("authorization", "")
            if not secrets.compare_digest(supplied, "Bearer " + token):
                return JSONResponse({"detail": "Local session required. Reload ReceiptIQ."}, 401)
        if request.method in ("POST", "PATCH", "PUT"):
            length = request.headers.get("content-length")
            if length is None or not length.isdigit():
                return JSONResponse({"detail": "Content-Length required"}, 411)
            path = request.url.path
            limit = 2 * 1024 * 1024
            if path == "/api/v1/imports":
                limit = MAX_BUNDLE + 1024 * 1024
            elif path == "/api/v1/backups/restore":
                limit = 2 * 1024**3 + 1024 * 1024
            elif path == "/api/v1/receipts" or path.endswith("/photos"):
                limit = MAX_UPLOAD + 1024 * 1024
            if int(length) > limit:
                return JSONResponse({"detail": "Request too large for this operation."}, 413)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' blob:; style-src 'self' 'unsafe-inline'; "
            "script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        return response

    @app.exception_handler(OSError)
    async def storage_error(request, exc):
        return JSONResponse({"detail": "Local storage failed. Check disk space and folder permissions, then retry."},
                            status_code=507)

    @app.exception_handler(sqlite3.OperationalError)
    async def database_error(request, exc):
        if getattr(exc, "sqlite_errorcode", None) == sqlite3.SQLITE_FULL:
            return JSONResponse({"detail": "Disk is full. Free space, then save or upload again."}, status_code=507)
        if getattr(exc, "sqlite_errorcode", None) in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
            return JSONResponse({"detail": "Workspace is busy. Wait a moment, then retry."}, status_code=503)
        raise exc

    @app.get("/api/v1/session")
    def session():
        return {"token": token}

    @app.get("/api/v1/health")
    def health():
        return {"status": "ok", "ocr_models_ready": models_ready(store.root), "version": "0.4.0",
                "vision_notice": getattr(app.state, "vision_notice", ""),
                "worker_alive": bool(worker.thread and worker.thread.is_alive())}

    @app.post("/api/v1/batches", status_code=201)
    def create_batch(body: BatchInput):
        if any(not name or len(name) > 500 for name in body.files):
            raise HTTPException(422, "Each file needs a name of 1-500 characters.")
        batch_id = uid()
        entries = [{"id": uid(), "name": Path(name.replace("\\", "/")).name} for name in body.files]
        with store.connect() as connection:
            connection.execute("INSERT INTO batches(id,name,created_at) VALUES(?,?,?)",
                               (batch_id, body.name, now()))
            connection.executemany("INSERT INTO batch_files(id,batch_id,name) VALUES(?,?,?)",
                                   [(entry["id"], batch_id, entry["name"]) for entry in entries])
        return {"id": batch_id, "files": entries}

    @app.get("/api/v1/batches")
    def batches():
        return store.read("SELECT b.*, (SELECT COUNT(*) FROM batch_files f WHERE f.batch_id=b.id) AS file_count "
                          "FROM batches b ORDER BY created_at DESC")

    @app.get("/api/v1/batches/{batch_id}")
    def batch(batch_id: str):
        result = store.one("SELECT * FROM batches WHERE id=?", (batch_id,))
        if not result:
            raise HTTPException(404, "Batch not found")
        result["files"] = store.read("SELECT * FROM batch_files WHERE batch_id=?", (batch_id,))
        result["counts"] = {row["status"]: row["count"] for row in store.read(
            "SELECT status,COUNT(*) AS count FROM receipts WHERE batch_id=? GROUP BY status", (batch_id,))}
        return result

    @app.post("/api/v1/receipts", status_code=202)
    async def upload(batch_id: str, upload_id: str, file: UploadFile = File(...), auto_ocr: bool = False):
        async with upload_lock:
            entry = store.one("SELECT * FROM batch_files WHERE id=? AND batch_id=?", (upload_id, batch_id))
            if not entry:
                raise HTTPException(404, "Upload entry not found")
            existing = store.one("SELECT id FROM receipts WHERE upload_id=?", (upload_id,))
            if existing:
                await file.close()
                return {"id": existing["id"], "accepted": True, "already_uploaded": True}
            if batch(batch_id)["cancelled"]:
                raise HTTPException(409, "Batch cancelled. Use Retry failed or create a new batch.")
            temporary = store.root / "tmp" / (uid() + ".upload")
            original_path = None
            store.execute("UPDATE batch_files SET state='uploading',error=NULL WHERE id=?", (upload_id,))
            try:
                size, digest = 0, hashlib.sha256()
                with temporary.open("wb") as stream:
                    while chunk := await file.read(1024 * 1024):
                        size += len(chunk)
                        if size > MAX_UPLOAD:
                            raise ValueError("File exceeds the 25 MB limit.")
                        digest.update(chunk)
                        stream.write(chunk)
                if not size:
                    raise ValueError("File is empty.")
                mime = await run_in_threadpool(inspect_file, temporary)
                receipt_id, document_id, job_id = uid(), uid(), uid()
                relative = f"originals/{document_id}" + {"image/jpeg": ".jpg", "image/png": ".png",
                                                         "application/pdf": ".pdf"}[mime]
                original_path = store.file(relative)
                temporary.replace(original_path)
                with store.connect() as connection:
                    connection.execute("BEGIN IMMEDIATE")
                    cancelled = connection.execute("SELECT cancelled FROM batches WHERE id=?", (batch_id,)).fetchone()
                    if cancelled["cancelled"]:
                        raise ValueError("Batch cancelled while uploading. Retry the batch to continue.")
                    connection.execute("INSERT INTO documents VALUES(?,?,?,?,?,?)",
                                       (document_id, entry["name"], relative, digest.hexdigest(), mime, now()))
                    connection.execute("INSERT INTO receipts(id,document_id,batch_id,upload_id,status,data_json,"
                                       "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                                       (receipt_id, document_id, batch_id, upload_id, "queued",
                                        json.dumps(blank_data()), now(), now()))
                    connection.execute("INSERT INTO jobs(id,receipt_id,batch_id,state,stage,created_at,updated_at,kind,payload_json) "
                                       "VALUES(?,?,?,?,?,?,?,?,?)",
                                       (job_id, receipt_id, batch_id, "queued", "Preparing preview", now(), now(),
                                        "ocr" if auto_ocr else "preview", json.dumps({"auto_vision": not auto_ocr,
                                            "vision": selected_vision(store.root) if not auto_ocr else None})))
                    connection.execute("UPDATE batch_files SET state='accepted',receipt_id=?,error=NULL WHERE id=?",
                                       (receipt_id, upload_id))
                return {"id": receipt_id, "document_id": document_id, "job_id": job_id, "accepted": True}
            except ValueError as exc:
                store.execute("UPDATE batch_files SET state='failed',error=? WHERE id=?", (str(exc), upload_id))
                if original_path:
                    original_path.unlink(missing_ok=True)
                raise HTTPException(422, str(exc)) from exc
            except Exception:
                store.execute("UPDATE batch_files SET state='failed',error='Upload failed. Retry this file.' WHERE id=?",
                              (upload_id,))
                if original_path:
                    original_path.unlink(missing_ok=True)
                raise
            finally:
                temporary.unlink(missing_ok=True)
                await file.close()

    @app.post("/api/v1/batches/{batch_id}/cancel")
    def cancel_batch(batch_id: str):
        batch(batch_id)
        with store.connect() as connection:
            connection.execute("UPDATE batches SET cancelled=1 WHERE id=?", (batch_id,))
            connection.execute("UPDATE jobs SET state='cancelled',stage='Cancelled',updated_at=? "
                               "WHERE batch_id=? AND state IN ('queued','processing')", (now(), batch_id))
            connection.execute("UPDATE receipts SET status='cancelled' WHERE batch_id=? "
                               "AND status IN ('queued','processing')", (batch_id,))
        return batch(batch_id)

    @app.post("/api/v1/batches/{batch_id}/retry-failed")
    def retry_batch(batch_id: str):
        batch(batch_id)
        with store.connect() as connection:
            connection.execute("UPDATE batches SET cancelled=0 WHERE id=?", (batch_id,))
            connection.execute("UPDATE jobs SET state='queued',stage='Waiting for retry',error=NULL,progress=0 "
                               "WHERE batch_id=? AND state IN ('failed','cancelled') AND receipt_id IN "
                               "(SELECT id FROM receipts WHERE status IN ('failed','cancelled')) AND id IN "
                               "(SELECT j.id FROM jobs j WHERE j.kind IN ('ocr','preview') AND j.id=(SELECT j2.id FROM jobs j2 "
                               "WHERE j2.receipt_id=j.receipt_id AND j2.kind IN ('ocr','preview') "
                               "ORDER BY j2.created_at DESC LIMIT 1))", (batch_id,))
            connection.execute("UPDATE receipts SET status='queued',error=NULL WHERE batch_id=? "
                               "AND status IN ('failed','cancelled')", (batch_id,))
        return batch(batch_id)

    @app.get("/api/v1/receipts")
    def receipts(batch_id: str | None = None, status: str | None = None, assignee: str | None = None,
                 second_review: bool = False, offset: int = 0, limit: int = 100):
        if offset < 0 or not 1 <= limit <= 500:
            raise HTTPException(422, "Invalid pagination")
        conditions, args = [], []
        for key, value in (("r.batch_id", batch_id), ("r.status", status)):
            if value:
                conditions.append(key + "=?")
                args.append(value)
        if status != "split":
            conditions.append("r.status<>'split'")
        if assignee is not None:
            conditions.append("r.assignee=?")
            args.append(assignee)
        if second_review:
            conditions.append("r.second_review=1")
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        return store.read(
            "SELECT r.id,r.batch_id,r.status,r.revision,r.verified_revision,r.error,r.updated_at,r.assignee,r.second_review,"
            "(SELECT COUNT(*) FROM documents other WHERE other.sha256=d.sha256 AND other.id<>d.id) AS duplicate_count,"
            "d.original_name,r.data_json,(SELECT stage FROM jobs j WHERE j.receipt_id=r.id "
            "ORDER BY j.created_at DESC LIMIT 1) AS stage FROM receipts r "
            "JOIN documents d ON d.id=r.document_id" + where + " ORDER BY r.created_at LIMIT ? OFFSET ?",
            (*args, limit, offset))

    @app.get("/api/v1/receipts/{receipt_id}")
    def receipt(receipt_id: str):
        return view_receipt(store, receipt_id)

    @app.post("/api/v1/imports", status_code=201)
    async def import_file(file: UploadFile = File(...), photos: list[UploadFile] = File(default=[])):
        try:
            async with upload_lock:
                return await run_in_threadpool(import_expenses, store, file.file, file.filename or "", photos)
        finally:
            await file.close()
            for photo in photos:
                await photo.close()

    @app.post("/api/v1/receipts/{receipt_id}/photos", status_code=201)
    async def upload_photo(receipt_id: str, file: UploadFile = File(...)):
        try:
            require_mutable_receipt(store, receipt_id)
            async with upload_lock:
                return await run_in_threadpool(add_photo, store, receipt_id, file.file, file.filename or "Photo")
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        finally:
            await file.close()

    @app.get("/api/v1/receipts/{receipt_id}/photos/{photo_id}")
    def photo(receipt_id: str, photo_id: str):
        row = store.one("SELECT * FROM attachments WHERE id=? AND receipt_id=?", (photo_id, receipt_id))
        if not row:
            raise HTTPException(404, "Photo not found")
        return FileResponse(store.file(row["relative_path"]), media_type=row["mime_type"])

    @app.post("/api/v1/receipts/{receipt_id}/discount-preview")
    def discount_preview(receipt_id: str, body: Edit):
        result = require_receipt(store, receipt_id)
        if result["revision"] != body.revision:
            raise HTTPException(409, "A newer version was saved. Reload before allocating the discount.")
        try:
            data = allocate_discount(body.data.model_dump())
            return {"data": data, "reconciliation": reconcile(data)}
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.patch("/api/v1/receipts/{receipt_id}")
    def edit(receipt_id: str, body: Edit):
        edit_receipt(store, receipt_id, body)
        return receipt(receipt_id)

    @app.post("/api/v1/receipts/{receipt_id}/validate")
    def validation(receipt_id: str):
        return receipt_issues(require_receipt(store, receipt_id))

    @app.post("/api/v1/receipts/{receipt_id}/verify")
    def verification(receipt_id: str, body: Verify):
        verify_receipt(store, receipt_id, body)
        return receipt(receipt_id)

    @app.get("/api/v1/receipts/{receipt_id}/history")
    def history(receipt_id: str):
        require_receipt(store, receipt_id)
        return store.read("SELECT revision,action,reviewer,reason,created_at FROM review_events "
                          "WHERE receipt_id=? ORDER BY created_at DESC LIMIT 100", (receipt_id,))

    @app.get("/api/v1/receipts/{receipt_id}/original")
    def original(receipt_id: str):
        result = require_receipt(store, receipt_id)
        document = store.one("SELECT * FROM documents WHERE id=?", (result["document_id"],))
        return FileResponse(store.file(document["relative_path"]), media_type=document["mime_type"],
                            filename=document["original_name"])

    @app.get("/api/v1/receipts/{receipt_id}/pages/{page_id}")
    def page(receipt_id: str, page_id: str, variant: str = "processed"):
        if variant not in ("processed", "original"):
            raise HTTPException(422, "Unknown page variant")
        row = store.one("SELECT * FROM document_pages WHERE receipt_id=? AND id=?", (receipt_id, page_id))
        if not row:
            raise HTTPException(404, "Page not found")
        return FileResponse(store.file(row[variant + "_path"]), media_type="image/png")

    @app.post("/api/v1/receipts/{receipt_id}/process", status_code=202)
    def process(receipt_id: str, body: Process):
        with store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            result = require_mutable_receipt(store, receipt_id)
            if result["status"] in ("queued", "processing"):
                raise HTTPException(409, "Receipt already queued")
            if result["original_mime"] == "text/csv" and not result["data"]["receipt_photo_id"]:
                raise HTTPException(422, "Add a receipt photo before running OCR.")
            connection.execute("UPDATE receipts SET status='queued',verified_revision=NULL,rotation=?,error=NULL,"
                               "updated_at=? WHERE id=?", (body.rotation, now(), receipt_id))
            connection.execute("DELETE FROM export_queue WHERE receipt_id=?", (receipt_id,))
            connection.execute("DELETE FROM retrieval_entries WHERE receipt_id=?", (receipt_id,))
            connection.execute("INSERT INTO jobs(id,receipt_id,batch_id,state,stage,created_at,updated_at) "
                               "VALUES(?,?,?,?,?,?,?)", (uid(), receipt_id, result["batch_id"], "queued",
                                                        "Waiting for OCR", now(), now()))
            store.audit(connection, receipt_id, result["revision"], "reprocess",
                        reason=f"Rotation {body.rotation}; manual values preserved")
        return receipt(receipt_id)

    @app.get("/api/v1/jobs/{job_id}")
    def job(job_id: str):
        result = store.one("SELECT * FROM jobs WHERE id=?", (job_id,))
        if not result:
            raise HTTPException(404, "Job not found")
        return result

    @app.post("/api/v1/jobs/{job_id}/cancel")
    def cancel_job(job_id: str):
        result = job(job_id)
        with store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute("SELECT state FROM jobs WHERE id=?", (job_id,)).fetchone()
            if current["state"] not in ("queued", "processing"):
                return result
            connection.execute("UPDATE jobs SET state='cancelled',stage='Cancelled' WHERE id=? "
                               "AND state IN ('queued','processing')", (job_id,))
            if result["kind"] in ("ocr", "preview"):
                connection.execute("UPDATE receipts SET status='cancelled' WHERE id=? "
                                   "AND status IN ('queued','processing')", (result["receipt_id"],))
        return job(job_id)

    @app.get("/api/v1/templates")
    def templates():
        return list_templates(store)

    @app.get("/api/v1/export-queue")
    def export_queue():
        return store.read("SELECT * FROM export_queue")

    @app.post("/api/v1/exports/preview")
    def preview(body: ExportInput):
        with store.connect() as connection:
            connection.execute("BEGIN")
            selected = export_selection(store, body.receipt_ids)
            try:
                template, rows = export_rows(store, selected, body.template_id)
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
        return {"columns": [column["header"] for column in template["columns"]], "rows": rows[:100], "row_count": len(rows),
                "template": template,
                "receipt_count": len(selected)}

    @app.post("/api/v1/exports", status_code=201)
    def export(body: ExportInput):
        try:
            return generate_export(store, body.receipt_ids, body.include_photos, body.template_id, body.template_version)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/api/v1/exports")
    def exports():
        return store.read("SELECT id,created_at FROM exports ORDER BY created_at DESC LIMIT 100")

    @app.get("/api/v1/exports/{export_id}/download")
    def download(export_id: str):
        result = store.one("SELECT * FROM exports WHERE id=?", (export_id,))
        if not result:
            raise HTTPException(404, "Export not found")
        extension = Path(result["relative_path"]).suffix
        return FileResponse(store.file(result["relative_path"]),
                            media_type="application/zip" if extension == ".zip" else "text/csv",
                            filename=f"receiptiq-{export_id[:8]}{extension}")

    @app.delete("/api/v1/receipts/{receipt_id}")
    def delete(receipt_id: str):
        with store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            result = require_receipt(store, receipt_id)
            if result["status"] in ("queued", "processing"):
                raise HTTPException(409, "Cancel processing before deleting this receipt.")
            document = store.one("SELECT * FROM documents WHERE id=?", (result["document_id"],))
            attachments = store.read("SELECT relative_path FROM attachments WHERE receipt_id=?", (receipt_id,))
            connection.execute("UPDATE batch_files SET receipt_id=NULL,state='deleted' WHERE id=?",
                               (result["upload_id"],))
            connection.execute("DELETE FROM receipts WHERE id=?", (receipt_id,))
            connection.execute("DELETE FROM documents WHERE id=?", (result["document_id"],))
        store.file(document["relative_path"]).unlink(missing_ok=True)
        for attachment in attachments:
            store.file(attachment["relative_path"]).unlink(missing_ok=True)
        shutil.rmtree(store.root / "pages" / receipt_id, ignore_errors=True)
        return {"deleted": True}

    install_routes(app, store, worker)
    static = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2])) / "frontend" / "dist"
    override = os.environ.get("RECEIPTIQ_FRONTEND_DIR")
    if override:
        static = Path(override)
    if static.is_dir():
        app.mount("/", StaticFiles(directory=static, html=True), name="frontend")
    return app
