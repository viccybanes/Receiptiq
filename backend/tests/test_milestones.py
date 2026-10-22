"""Milestone API behavior against real SQLite, files, and queued workers; no model downloads."""

import copy
import csv
import io
import json
import sqlite3

import pypdfium2 as pdfium
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from test_workflow import Engine, batch, image_bytes, ready_receipt, upload, verify
from test_workflow import client as client_fixture

from app.db import Store
from app.main import create_app

client = client_fixture


@pytest.fixture(autouse=True)
def local_embeddings(monkeypatch):
    monkeypatch.setattr("app.retrieval.OllamaEmbedder.embed", lambda self, text: [1, 0])


@pytest.fixture
def fake_vision(client, monkeypatch):
    def extract(images, lines, **options):
        assert len(images) == 1 and images[0].is_file() and lines
        draft = copy.deepcopy(options["current"])
        draft["supplier"] = "VISION MARKET"
        return {"draft": draft, "provenance": {"source": "local_vision"},
                "model": options["model"], "field_meta": {"supplier": {"source": "local_vision"}}}

    monkeypatch.setattr(client.worker.vision, "extract", extract)


def queue_vision(client, receipt):
    response = client.post(f"/api/v1/receipts/{receipt['id']}/vision", json={
        "revision": receipt["revision"], "page_id": receipt["pages"][0]["id"],
        "model": "qwen3-vl:2b-instruct", "crop": [0, 0, 1, 1]})
    assert response.status_code == 202, response.text
    return response.json()["id"]


def selected(receipt, template_id="default"):
    return {"receipt_ids": [receipt["id"]], "template_id": template_id}


def ready_pdf(client):
    stream = io.BytesIO()
    with Image.new("RGB", (80, 100), "white") as first, Image.new("RGB", (80, 100), "white") as second:
        first.save(stream, "PDF", resolution=72, save_all=True, append_images=[second])
    group = client.post("/api/v1/batches", json={"name": "Two-page source", "files": ["two-pages.pdf"]}).json()
    accepted = client.post("/api/v1/receipts", params={"batch_id": group["id"], "upload_id": group["files"][0]["id"], "auto_ocr": True},
                           files={"file": ("two-pages.pdf", stream.getvalue(), "application/pdf")})
    assert accepted.status_code == 202, accepted.text
    assert client.worker.process_one()
    receipt = client.get("/api/v1/receipts/" + accepted.json()["id"]).json()
    assert receipt["status"] == "needs_review" and len(receipt["pages"]) == 2
    return receipt


def test_vision_queue_keeps_verified_export_eligibility_and_is_idempotent(client):
    receipt, _ = ready_receipt(client)
    assert verify(client, receipt).status_code == 200
    job_id = queue_vision(client, receipt)
    assert queue_vision(client, receipt) == job_id
    queued = client.get("/api/v1/receipts/" + receipt["id"]).json()
    assert queued["status"] == "verified" and queued["verified_revision"] == receipt["revision"]
    assert queued["data"] == receipt["data"] and queued["vision_job"]["state"] == "queued"
    assert client.post("/api/v1/exports/preview", json=selected(receipt)).status_code == 200
    assert client.post(f"/api/v1/receipts/{receipt['id']}/vision", json={
        "revision": receipt["revision"] + 1, "page_id": receipt["pages"][0]["id"]}).status_code == 409
    assert client.post(f"/api/v1/receipts/{receipt['id']}/vision", json={
        "revision": receipt["revision"], "page_id": receipt["pages"][0]["id"], "crop": [0.8, 0, 0.2, 1]}).status_code == 422


def test_vision_processing_stores_candidate_until_explicit_apply(client, fake_vision):
    receipt, _ = ready_receipt(client)
    assert verify(client, receipt).status_code == 200
    job_id = queue_vision(client, receipt)
    assert client.worker.process_one()
    unchanged = client.get("/api/v1/receipts/" + receipt["id"]).json()
    assert unchanged["status"] == "verified" and unchanged["data"] == receipt["data"]
    assert unchanged["evidence"]["draft"]["supplier"] == "NORTHSIDE MARKET"
    candidate = unchanged["candidate"]
    assert candidate["draft"]["supplier"] == "VISION MARKET" and candidate["base_revision"] == receipt["revision"]
    assert candidate["provenance"]["source"] == "local_vision"
    assert client.get("/api/v1/jobs/" + job_id).json()["state"] == "done"
    applied = client.post(f"/api/v1/receipts/{receipt['id']}/candidates/{candidate['id']}/apply",
                          json={"revision": receipt["revision"]})
    assert applied.status_code == 200, applied.text
    assert applied.json()["data"]["supplier"] == "VISION MARKET"
    assert applied.json()["revision"] == receipt["revision"] + 1
    assert applied.json()["status"] == "needs_review" and applied.json()["verified_revision"] is None
    assert client.post("/api/v1/exports", json=selected(receipt)).status_code == 409


def test_stale_vision_candidate_cannot_overwrite_new_manual_edit(client, fake_vision):
    receipt, _ = ready_receipt(client)
    queue_vision(client, receipt)
    assert client.worker.process_one()
    candidate = client.store.receipt(receipt["id"])["candidate"]
    receipt["data"]["notes"] = "A later manual correction"
    saved = client.patch("/api/v1/receipts/" + receipt["id"], json={
        "revision": receipt["revision"], "data": receipt["data"]})
    assert saved.status_code == 200, saved.text
    for revision in (receipt["revision"], saved.json()["revision"]):
        rejected = client.post(f"/api/v1/receipts/{receipt['id']}/candidates/{candidate['id']}/apply",
                               json={"revision": revision})
        assert rejected.status_code == 409
    assert client.store.receipt(receipt["id"])["data"] == saved.json()["data"]


def test_full_page_vision_on_multipage_receipt_preserves_other_page_items_when_applied(client, monkeypatch):
    from app.vision import VisionReceipt, _merge

    receipt = ready_pdf(client)
    previous = copy.deepcopy(receipt["data"]["items"])

    def extract(images, lines, **options):
        assert len(images) == 1 and options["preserve_items"] and options["crop"] is None
        proposed = VisionReceipt(items=[{"description": previous[0]["description"], "line_total": "90.00"}])
        draft, _ = _merge(proposed, options["current"], options["crop"], options["preserve_items"])
        return {"draft": draft, "model": options["model"]}

    monkeypatch.setattr(client.worker.vision, "extract", extract)
    queued = client.post(f"/api/v1/receipts/{receipt['id']}/vision", json={
        "revision": receipt["revision"], "page_id": receipt["pages"][0]["id"]})
    assert queued.status_code == 202, queued.text
    assert client.worker.process_one()
    candidate = client.store.receipt(receipt["id"])["candidate"]
    applied = client.post(f"/api/v1/receipts/{receipt['id']}/candidates/{candidate['id']}/apply",
                          json={"revision": receipt["revision"]})
    assert applied.status_code == 200, applied.text
    items = applied.json()["data"]["items"]
    assert items[0] == {**previous[0], "line_total": "90.00"}
    assert items[1:] == previous[1:]


def test_failed_vision_keeps_verified_manual_export(client, monkeypatch):
    receipt, _ = ready_receipt(client)
    assert verify(client, receipt).status_code == 200

    def unavailable(*args, **kwargs):
        raise RuntimeError("Local model is not installed")

    monkeypatch.setattr(client.worker.vision, "extract", unavailable)
    job_id = queue_vision(client, receipt)
    assert client.worker.process_one()
    assert client.get("/api/v1/jobs/" + job_id).json()["state"] == "failed"
    current = client.store.receipt(receipt["id"])
    assert current["status"] == "verified" and current["data"] == receipt["data"] and current["candidate"] is None
    assert client.post("/api/v1/exports", json=selected(receipt)).status_code == 201


@pytest.mark.parametrize("processing", [False, True])
def test_cancel_vision_does_not_cancel_receipt(client, fake_vision, monkeypatch, processing):
    receipt, _ = ready_receipt(client)
    assert verify(client, receipt).status_code == 200
    job_id = queue_vision(client, receipt)
    if processing:
        extract = client.worker.vision.extract

        def cancel_during_extraction(*args, **kwargs):
            response = client.post(f"/api/v1/jobs/{job_id}/cancel")
            assert response.status_code == 200 and response.json()["state"] == "cancelled"
            return extract(*args, **kwargs)

        monkeypatch.setattr(client.worker.vision, "extract", cancel_during_extraction)
        assert client.worker.process_one()
    else:
        response = client.post(f"/api/v1/jobs/{job_id}/cancel")
        assert response.status_code == 200 and response.json()["state"] == "cancelled"
    assert not client.worker.process_one()
    unchanged = client.store.receipt(receipt["id"])
    assert unchanged["status"] == "verified" and unchanged["candidate"] is None
    assert client.post("/api/v1/exports", json=selected(receipt)).status_code == 201


def test_verification_opt_in_indexes_and_opt_out_purges_live_examples(client):
    source, _ = ready_receipt(client)
    opted = client.post(f"/api/v1/receipts/{source['id']}/verify", json={
        "revision": source["revision"], "reviewer": "QA", "retrieval_allowed": True})
    assert opted.status_code == 200, opted.text
    job = client.store.one("SELECT * FROM jobs WHERE receipt_id=? AND kind='index'", (source["id"],))
    assert job["state"] == "queued"
    assert client.worker.process_one()
    assert client.store.one("SELECT * FROM retrieval_entries WHERE receipt_id=?", (source["id"],))
    target, _ = ready_receipt(client)
    examples = client.get(f"/api/v1/receipts/{target['id']}/suggestions").json()["examples"]
    assert examples[0]["receipt_id"] == source["id"] and examples[0]["revision"] == source["revision"]
    assert client.store.receipt(target["id"])["data"] == target["data"]
    opted_out = client.post(f"/api/v1/receipts/{source['id']}/review-options", json={
        "revision": source["revision"], "retrieval_allowed": False})
    assert opted_out.status_code == 200 and opted_out.json()["status"] == "verified"
    assert not client.store.read("SELECT * FROM retrieval_entries")
    assert client.get(f"/api/v1/receipts/{target['id']}/suggestions").json()["examples"] == []


def test_assigned_second_review_invalidates_export_and_requires_assignee(client):
    receipt, _ = ready_receipt(client)
    assert verify(client, receipt).status_code == 200
    assigned = client.post(f"/api/v1/receipts/{receipt['id']}/review-options", json={
        "revision": receipt["revision"], "assignee": "Morgan", "second_review": True})
    assert assigned.status_code == 200, assigned.text
    assert assigned.json()["status"] == "needs_review" and assigned.json()["verified_revision"] is None
    assert not client.get("/api/v1/export-queue").json()
    filtered = client.get("/api/v1/receipts", params={"assignee": "Morgan", "second_review": True}).json()
    assert [row["id"] for row in filtered] == [receipt["id"]]
    assert verify(client, receipt).status_code == 422
    assert client.post("/api/v1/exports", json=selected(receipt)).status_code == 409
    approved = client.post(f"/api/v1/receipts/{receipt['id']}/verify", json={
        "revision": receipt["revision"], "reviewer": "Morgan"})
    assert approved.status_code == 200 and approved.json()["second_review"] == 0
    assert client.post("/api/v1/exports", json=selected(receipt)).status_code == 201


def test_template_api_custom_preview_csv_and_immutable_export_snapshot(client):
    receipt, _ = ready_receipt(client)
    assert verify(client, receipt).status_code == 200
    created = client.post("/api/v1/templates", json={"id": "finance", "name": "Finance", "columns": [
        {"header": "Supplier", "field": "Vendor"}, {"header": "Net cost", "field": "Amount"},
        {"header": "Department", "constant": "Ops"}], "delimiter": ";", "encoding": "utf-8"})
    assert created.status_code == 201, created.text
    template = created.json()
    preview = client.post("/api/v1/exports/preview", json=selected(receipt, "finance"))
    assert preview.status_code == 200, preview.text
    assert preview.json()["columns"] == ["Supplier", "Net cost", "Department"]
    assert preview.json()["rows"][0] == {"Supplier": "NORTHSIDE MARKET", "Net cost": "100.00", "Department": "Ops"}
    exported = client.post("/api/v1/exports", json=selected(receipt, "finance"))
    assert exported.status_code == 201, exported.text
    export_id = exported.json()["id"]
    original = client.get(f"/api/v1/exports/{export_id}/download").content
    assert original.startswith(b"Supplier;Net cost;Department\r\n")
    assert len(list(csv.DictReader(io.StringIO(original.decode()), delimiter=";"))) == 2
    updated = client.patch("/api/v1/templates/finance", json={**template, "name": "Updated", "delimiter": ","})
    assert updated.status_code == 200 and updated.json()["version"] == 2
    assert client.patch("/api/v1/templates/finance", json=template).status_code == 409
    assert any(row["id"] == "finance" for row in client.get("/api/v1/templates").json())
    snapshot = client.store.one("SELECT template_json FROM exports WHERE id=?", (export_id,))
    assert json.loads(snapshot["template_json"])["version"] == 1
    assert json.loads(snapshot["template_json"])["delimiter"] == ";"
    assert client.get(f"/api/v1/exports/{export_id}/download").content == original
    assert client.get("/api/v1/templates/finance").json()["version"] == 2
    assert client.delete("/api/v1/templates/finance", params={"version": 1}).status_code == 409
    assert client.delete("/api/v1/templates/finance", params={"version": 2}).status_code == 200
    assert client.get("/api/v1/templates/finance").status_code == 404
    assert client.delete("/api/v1/templates/default", params={"version": 2}).status_code == 422
    assert client.get(f"/api/v1/exports/{export_id}/download").content == original


def test_template_required_fields_block_preview_and_export(client):
    receipt, _ = ready_receipt(client)
    assert verify(client, receipt).status_code == 200
    created = client.post("/api/v1/templates", json={"id": "photos", "name": "Photos required",
        "columns": ["Vendor", "Total"], "required_fields": ["Photo of item purchased"]})
    assert created.status_code == 201, created.text
    for endpoint in ("/api/v1/exports/preview", "/api/v1/exports"):
        response = client.post(endpoint, json=selected(receipt, "photos"))
        assert response.status_code == 422 and response.json()["detail"]["issues"][0]["blocking"]
    assert not client.store.read("SELECT * FROM exports")
    assert not list((client.store.root / "exports").iterdir())


def test_exact_duplicate_uploads_remain_separate_and_report_sources(client):
    group = batch(client, 2)
    first, second = upload(client, group, 0), upload(client, group, 1)
    assert first.status_code == second.status_code == 202
    assert first.json()["id"] != second.json()["id"]
    assert client.worker.process_one() and client.worker.process_one()
    rows = client.get("/api/v1/receipts", params={"batch_id": group["id"]}).json()
    assert len(rows) == 2 and all(row["duplicate_count"] == 1 for row in rows)
    detail = client.get("/api/v1/receipts/" + first.json()["id"]).json()
    assert detail["duplicates"][0]["id"] == second.json()["id"]


def test_pdf_split_creates_queued_groups_retaining_source_and_export_snapshot(client):
    receipt = ready_pdf(client)
    approved = client.post(f"/api/v1/receipts/{receipt['id']}/verify", json={
        "revision": receipt["revision"], "reviewer": "QA", "reason": "Both PDF pages checked",
        "acknowledged": [issue["id"] for issue in receipt["issues"] if not issue["blocking"]]})
    assert approved.status_code == 200, approved.text
    exported = client.post("/api/v1/exports", json=selected(receipt))
    assert exported.status_code == 201, exported.text
    export_id = exported.json()["id"]
    prior_csv = client.get(f"/api/v1/exports/{export_id}/download").content
    source_document = client.store.one("SELECT * FROM documents WHERE id=?", (receipt["document_id"],))
    source_bytes = client.store.file(source_document["relative_path"]).read_bytes()
    response = client.post(f"/api/v1/receipts/{receipt['id']}/split", json={
        "revision": receipt["revision"], "groups": [[0], [1]]})
    assert response.status_code == 201, response.text
    children = response.json()["receipt_ids"]
    assert len(children) == len(set(children)) == 2
    for child in children:
        current = client.store.receipt(child)
        assert current["status"] == "queued" and current["revision"] == 0 and current["verified_revision"] is None
        assert client.store.one("SELECT * FROM jobs WHERE receipt_id=?", (child,))["state"] == "queued"
        document = client.store.one("SELECT * FROM documents WHERE id=?", (current["document_id"],))
        pdf = pdfium.PdfDocument(client.store.file(document["relative_path"]))
        try:
            assert len(pdf) == 1
        finally:
            pdf.close()
    archived = client.store.receipt(receipt["id"])
    assert archived["status"] == "split" and archived["verified_revision"] is None
    assert archived["data"] == receipt["data"]
    assert client.store.file(source_document["relative_path"]).read_bytes() == source_bytes
    assert all(row["id"] != receipt["id"] for row in client.get("/api/v1/receipts").json())
    assert client.get("/api/v1/receipts", params={"status": "split"}).json()[0]["id"] == receipt["id"]
    assert client.get(f"/api/v1/exports/{export_id}/download").content == prior_csv
    assert client.store.one("SELECT snapshot_json FROM export_receipts WHERE export_id=?", (export_id,))


def test_split_source_mutations_are_rejected_without_changing_retained_data(client):
    receipt = ready_pdf(client)
    path = f"/api/v1/receipts/{receipt['id']}"
    split = client.post(path + "/split", json={"revision": receipt["revision"], "groups": [[0], [1]]})
    assert split.status_code == 201, split.text
    tables = ("receipts", "receipt_items", "documents", "batches", "batch_files", "jobs", "attachments",
              "extraction_runs", "review_events", "export_queue", "retrieval_entries")
    before = {table: client.store.read(f"SELECT * FROM {table}") for table in tables}
    files = {str(file.relative_to(client.store.root)): file.read_bytes()
             for folder in ("originals", "pages", "attachments", "exports")
             for file in (client.store.root / folder).rglob("*") if file.is_file()}
    revision = {"revision": receipt["revision"]}
    mutations = [
        ("PATCH", path, {"json": {**revision, "data": {**receipt["data"], "notes": "A stale tab edit"}}}),
        ("POST", path + "/process", {"json": {}}),
        ("POST", path + "/review-options", {"json": {**revision, "second_review": True, "assignee": "QA"}}),
        ("POST", path + "/verify", {"json": {**revision, "reviewer": "QA"}}),
        ("POST", path + "/vision", {"json": {**revision, "page_id": receipt["pages"][0]["id"]}}),
        ("POST", path + "/candidates/missing/apply", {"json": revision}),
        ("POST", path + "/photos", {"files": {"file": ("photo.png", image_bytes(), "image/png")}}),
        ("POST", path + "/split", {"json": {**revision, "groups": [[0], [1]]}}),
    ]
    for method, endpoint, options in mutations:
        response = client.request(method, endpoint, **options)
        assert response.status_code == 409, (endpoint, response.text)
    assert before == {table: client.store.read(f"SELECT * FROM {table}") for table in tables}
    assert client.store.receipt(receipt["id"])["status"] == "split"
    assert files == {str(file.relative_to(client.store.root)): file.read_bytes()
                     for folder in ("originals", "pages", "attachments", "exports")
                     for file in (client.store.root / folder).rglob("*") if file.is_file()}
    assert client.get(path).status_code == 200
    assert client.get(path + "/original").status_code == 200
    assert client.post("/api/v1/exports", json=selected(receipt)).status_code == 409


@pytest.mark.parametrize("groups", [[[0], [0]], [[0]], [[], [0, 1]]])
def test_pdf_split_invalid_groups_leave_database_and_files_unchanged(client, groups):
    receipt = ready_pdf(client)
    before = {table: client.store.read(f"SELECT * FROM {table}") for table in ("receipts", "documents", "batches", "jobs")}
    files = {path.name for path in (client.store.root / "originals").iterdir()}
    response = client.post(f"/api/v1/receipts/{receipt['id']}/split", json={
        "revision": receipt["revision"], "groups": groups})
    assert response.status_code == 422, response.text
    assert before == {table: client.store.read(f"SELECT * FROM {table}") for table in before}
    assert files == {path.name for path in (client.store.root / "originals").iterdir()}


def test_desktop_token_protects_session_health_and_api(tmp_path, monkeypatch):
    token = "desktop-test-session-token-" + "x" * 32
    monkeypatch.setenv("RECEIPTIQ_DESKTOP", "1")
    monkeypatch.setenv("RECEIPTIQ_SESSION_TOKEN", token)
    app = create_app(tmp_path / "desktop", start_worker=False, engine=Engine())
    with TestClient(app, base_url="http://127.0.0.1:8765") as desktop:
        for route in ("/api/v1/session", "/api/v1/health", "/api/v1/receipts"):
            assert desktop.get(route).status_code == 401
            assert desktop.get(route, headers={"Authorization": "Bearer wrong"}).status_code == 401
            assert desktop.get(route, headers={"Authorization": "Bearer " + token}).status_code == 200
        assert desktop.get("/api/v1/session", headers={"Authorization": "Bearer " + token}).json()["token"] == token


def test_short_desktop_token_is_rejected_before_opening_workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("RECEIPTIQ_SESSION_TOKEN", "too-short")
    with pytest.raises(RuntimeError, match="at least 32"):
        create_app(tmp_path / "short-token", start_worker=False, engine=Engine())
    assert not (tmp_path / "short-token" / "receiptiq.sqlite3").exists()


def test_setup_and_capabilities_routes_use_explicit_profile_without_downloading(client, monkeypatch):
    calls = []
    monkeypatch.setattr("app.features.capabilities", lambda root: {"recommended_profile": "2b", "ram_gb": 16})
    monkeypatch.setattr("app.features.setup_status", lambda root: {"state": "ready", "profile": "ocr-only"})
    monkeypatch.setattr("app.features.start_setup", lambda root, profile: calls.append((root, profile)) or {"state": "queued"})
    assert client.get("/api/v1/system/capabilities").json()["recommended_profile"] == "2b"
    assert client.get("/api/v1/setup/status").json() == {"state": "ready", "profile": "ocr-only"}
    response = client.post("/api/v1/setup/install", json={"profile": "4b"})
    assert response.status_code == 202 and response.json()["state"] == "queued"
    assert calls == [(client.store.root, "4b")]
    assert client.post("/api/v1/setup/install", json={"profile": "remote"}).status_code == 422

    def busy(*args):
        raise RuntimeError("Setup already running")

    monkeypatch.setattr("app.features.start_setup", busy)
    assert client.post("/api/v1/setup/install", json={"profile": "2b"}).status_code == 409


def test_backup_routes_download_and_stage_restore_without_swapping_active_workspace(client, monkeypatch):
    backup_path = client.store.root / "backups" / "workspace-test.zip"

    def backup(store):
        assert store is client.store
        backup_path.write_bytes(b"backup fixture")
        return backup_path

    def stage(root, temporary):
        assert root == client.store.root and temporary.read_bytes() == b"restore fixture"
        return {"restart_required": True, "state": "staged"}

    monkeypatch.setattr("app.features.create_backup", backup)
    monkeypatch.setattr("app.features.stage_restore", stage)
    created = client.post("/api/v1/backups")
    assert created.status_code == 201 and created.json()["id"] == backup_path.name
    assert client.get("/api/v1/backups").json()[0]["name"] == backup_path.name
    assert client.get(f"/api/v1/backups/{backup_path.name}/download").content == b"backup fixture"
    response = client.post("/api/v1/backups/restore", files={"file": ("saved.zip", b"restore fixture", "application/zip")})
    assert response.status_code == 202 and response.json()["restart_required"]
    assert not list((client.store.root / "tmp").glob("*.restore"))
    assert client.get("/api/v1/health").status_code == 200

    def malformed(*args):
        raise ValueError("Invalid backup manifest")

    monkeypatch.setattr("app.features.stage_restore", malformed)
    rejected = client.post("/api/v1/backups/restore", files={"file": ("bad.zip", b"bad", "application/zip")})
    assert rejected.status_code == 422 and "manifest" in rejected.json()["detail"]
    assert not list((client.store.root / "tmp").glob("*.restore"))
    monkeypatch.setattr("app.features.create_backup", malformed)
    rejected = client.post("/api/v1/backups")
    assert rejected.status_code == 422 and "manifest" in rejected.json()["detail"]


def test_schema_two_migration_preserves_verified_receipt_items_and_export_and_backs_up(client):
    receipt, _ = ready_receipt(client)
    assert verify(client, receipt).status_code == 200
    exported = client.post("/api/v1/exports", json=selected(receipt))
    assert exported.status_code == 201, exported.text
    items = client.store.read("SELECT * FROM receipt_items")
    snapshots = client.store.read("SELECT * FROM export_receipts")
    with sqlite3.connect(client.store.path) as connection:
        connection.execute("DROP TABLE retrieval_entries")
        for column in ("assignee", "second_review", "retrieval_allowed"):
            connection.execute("ALTER TABLE receipts DROP COLUMN " + column)
        connection.execute("ALTER TABLE jobs DROP COLUMN payload_json")
        connection.execute("DELETE FROM schema_migrations WHERE version=3")
        connection.execute("PRAGMA user_version=2")
    migrated = Store(client.store.root)
    current = migrated.receipt(receipt["id"])
    assert current["status"] == "verified" and current["verified_revision"] == receipt["revision"]
    assert current["data"] == receipt["data"] and current["retrieval_allowed"] == current["second_review"] == 0
    assert migrated.read("SELECT * FROM receipt_items") == items
    assert migrated.read("SELECT * FROM export_receipts") == snapshots
    assert migrated.one("PRAGMA user_version")["user_version"] == 3
    backups = list((client.store.root / "backups").glob("before-v3-*.sqlite3"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
        assert connection.execute("SELECT status FROM receipts WHERE id=?", (receipt["id"],)).fetchone()[0] == "verified"
        assert "retrieval_allowed" not in {row[1] for row in connection.execute("PRAGMA table_info(receipts)")}
    assert client.get(f"/api/v1/exports/{exported.json()['id']}/download").status_code == 200
