"""API acceptance checks use a deterministic engine; separate smoke tests run real Paddle."""

import csv
import io
import threading
import time

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.extraction import decimal, parse, receipt_issues, validate
from app.main import create_app
from app.models import blank_data
from app.worker import Worker


def image_bytes():
    output = io.BytesIO()
    Image.new("RGB", (800, 1100), "white").save(output, "PNG")
    return output.getvalue()


LINES = ["NORTHSIDE MARKET", "Receipt No: NS-001", "2026-10-19 PHP",
         "Coffee 2 x 50.00 100.00", "Bread 1 x 25.00 25.00", "Subtotal 125.00", "Tax 0.00", "Total 125.00"]


class Engine:
    def recognize(self, image):
        return [{"text": text, "score": 0.99, "box": [20, i * 40 + 20, 600, i * 40 + 45]}
                for i, text in enumerate(LINES)]


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path, start_worker=False, engine=Engine())
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        client.headers["Authorization"] = "Bearer " + client.get("/api/v1/session").json()["token"]
        client.store = app.state.store
        client.worker = app.state.worker
        yield client


def batch(client, count=1):
    response = client.post("/api/v1/batches", json={"name": "Acceptance batch", "files": [f"receipt-{i}.png" for i in range(count)]})
    assert response.status_code == 201, response.text
    return response.json()


def upload(client, group, index=0, content=None, auto_ocr=True):
    response = client.post("/api/v1/receipts", params={"batch_id": group["id"], "upload_id": group["files"][index]["id"], "auto_ocr": auto_ocr},
                           files={"file": ("receipt.png", image_bytes() if content is None else content, "image/png")})
    return response


def ready_receipt(client):
    group = batch(client)
    accepted = upload(client, group)
    assert accepted.status_code == 202, accepted.text
    assert client.worker.process_one()
    receipt = client.get("/api/v1/receipts/" + accepted.json()["id"]).json()
    assert receipt["status"] == "needs_review"
    return receipt, group


def verify(client, receipt):
    return client.post(f"/api/v1/receipts/{receipt['id']}/verify", json={
        "revision": receipt["revision"], "reviewer": "QA", "acknowledged": [], "reason": ""})


def test_ocr_parse_decimal_validation():
    lines = [{**line, "page": 0, "id": str(i)} for i, line in enumerate(Engine().recognize(None))]
    data, metadata = parse(lines)
    assert data["supplier"] == "NORTHSIDE MARKET"
    assert data["total"] == "125.00"
    assert data["receipt_date"] == "2026-10-19"
    assert len(data["items"]) == 2
    assert data["items"][0]["quantity"] == "2"
    assert not validate(data)
    assert metadata["total"]["line_ids"]
    assert decimal("0.1") + decimal("0.2") == decimal("0.3")
    for value in ("NaN", "Infinity", "1e10", "1,000.00", "=1+1"):
        with pytest.raises(ValueError):
            decimal(value)
    data["receipt_date"] = "10/11/2026"
    data["total"] = "124.00"
    issues = validate(data)
    assert any(issue["field"] == "receipt_date" and issue["blocking"] for issue in issues)
    assert any(issue["field"] == "item_sum" and not issue["blocking"] for issue in issues)


def test_security_and_limits(client):
    assert client.get("/api/v1/receipts", headers={"Authorization": ""}).status_code == 401
    assert client.get("/api/v1/session", headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.get("/api/v1/session", headers={"Host": "evil.example"}).status_code == 403
    assert client.get("/api/v1/session", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert client.post("/api/v1/batches", json={"files": []}).status_code == 422
    assert client.get("/api/v1/receipts?limit=10000").status_code == 422
    response = client.post("/api/v1/receipts", headers={"Content-Length": str(30 * 1024 * 1024)}, content=b"")
    assert response.status_code == 413


def test_upload_independent_failure_and_idempotency(client):
    group = batch(client, 3)
    first = upload(client, group)
    assert first.status_code == 202
    duplicate = upload(client, group)
    assert duplicate.json()["id"] == first.json()["id"]
    bad = upload(client, group, 1, b"not an image")
    assert bad.status_code == 422
    third = upload(client, group, 2)
    assert third.status_code == 202
    while client.worker.process_one():
        pass
    detail = client.get("/api/v1/batches/" + group["id"]).json()
    assert detail["counts"] == {"needs_review": 2}
    assert detail["files"][1]["state"] == "failed"
    assert len(client.store.read("SELECT * FROM receipts")) == 2


def test_verification_export_edit_invalidation_and_history(client):
    receipt, group = ready_receipt(client)
    selected = {"receipt_ids": [receipt["id"]]}
    assert client.post("/api/v1/exports", json=selected).status_code == 409
    assert verify(client, receipt).status_code == 200
    preview = client.post("/api/v1/exports/preview", json=selected)
    assert preview.status_code == 200 and preview.json()["row_count"] == 2
    exported = client.post("/api/v1/exports", json=selected)
    assert exported.status_code == 201, exported.text
    csv_before = client.get("/api/v1/exports/" + exported.json()["id"] + "/download").content
    rows = list(csv.DictReader(io.StringIO(csv_before.decode("utf-8-sig"))))
    assert rows[0]["Vendor"] == "NORTHSIDE MARKET"
    assert rows[0]["Amount"] == "100.00"
    receipt["data"]["supplier"] = "Changed supplier"
    edit = client.patch("/api/v1/receipts/" + receipt["id"],
                        json={"revision": receipt["revision"], "data": receipt["data"]})
    assert edit.status_code == 200, edit.text
    assert edit.json()["verified_revision"] is None
    assert client.get("/api/v1/export-queue").json() == []
    assert client.post("/api/v1/exports", json=selected).status_code == 409
    assert verify(client, receipt).status_code == 409
    assert client.patch("/api/v1/receipts/" + receipt["id"],
                        json={"revision": receipt["revision"], "data": receipt["data"]}).status_code == 409
    assert client.get("/api/v1/exports/" + exported.json()["id"] + "/download").content == csv_before
    assert len(client.get("/api/v1/receipts/" + receipt["id"] + "/history").json()) == 2


def test_warning_resolution_and_csv_injection_unicode(client):
    receipt, _ = ready_receipt(client)
    receipt["data"]["supplier"] = "=HYPERLINK(\"bad\")"
    receipt["data"]["receipt_number"] = "  +COMMAND"
    receipt["data"]["items"] = [{"description": "Café, rice\n\"large\"", "quantity": "", "unit_price": "", "line_total": "125.00"}]
    edit = client.patch("/api/v1/receipts/" + receipt["id"],
                        json={"revision": receipt["revision"], "data": receipt["data"]}).json()
    assert verify(client, edit).status_code == 422
    response = client.post("/api/v1/receipts/" + receipt["id"] + "/verify", json={
        "revision": edit["revision"], "reviewer": "AB",
        "acknowledged": [issue["id"] for issue in edit["issues"]], "reason": "Summary amount checked against original."})
    assert response.status_code == 200, response.text
    exported = client.post("/api/v1/exports", json={"receipt_ids": [receipt["id"]]}).json()
    data = client.get("/api/v1/exports/" + exported["id"] + "/download").content
    row = next(csv.DictReader(io.StringIO(data.decode("utf-8-sig"))))
    assert row["Vendor"].startswith("'=") and row["Receipt number"].startswith("'+")
    assert row["Description"] == 'Café, rice\n"large"'
    assert row["Quantity"] == ""


def test_low_ocr_score_requires_review_not_probability():
    data = blank_data()
    receipt = {"data": data, "field_meta": {"supplier": {"source": "ocr", "line_ids": ["x"]}},
               "evidence": {"lines": [{"id": "x", "score": 0.4}]}}
    assert any(issue["id"] == "supplier.ocr" for issue in receipt_issues(receipt))
    receipt["field_meta"]["supplier"]["source"] = "manual"
    assert not any(issue["id"] == "supplier.ocr" for issue in receipt_issues(receipt))


def test_reprocessing_preserves_manual_values(client):
    receipt, _ = ready_receipt(client)
    receipt["data"]["supplier"] = "Human correction"
    updated = client.patch("/api/v1/receipts/" + receipt["id"],
                           json={"revision": receipt["revision"], "data": receipt["data"]}).json()
    assert verify(client, updated).status_code == 200
    assert client.post("/api/v1/receipts/" + receipt["id"] + "/process", json={"rotation": 90}).status_code == 202
    assert client.get("/api/v1/export-queue").json() == []
    assert client.worker.process_one()
    result = client.get("/api/v1/receipts/" + receipt["id"]).json()
    assert result["data"]["supplier"] == "Human correction"
    assert result["evidence"]["draft"]["supplier"] == "NORTHSIDE MARKET"


def test_cancel_retry_and_manual_verification_not_retried(client):
    group = batch(client, 2)
    ids = [upload(client, group, i).json()["id"] for i in range(2)]
    assert client.post("/api/v1/batches/" + group["id"] + "/cancel").status_code == 200
    assert not client.worker.process_one()
    assert client.post("/api/v1/batches/" + group["id"] + "/retry-failed").status_code == 200
    assert client.worker.process_one()
    assert client.worker.process_one()
    assert not client.worker.process_one()
    receipt = client.get("/api/v1/receipts/" + ids[0]).json()
    assert verify(client, receipt).status_code == 200
    assert client.post("/api/v1/batches/" + group["id"] + "/retry-failed").status_code == 200
    assert not client.worker.process_one()


@pytest.mark.parametrize("count", [50, 100])
def test_batch_stress_restart_and_review_during_processing(client, count):
    group = batch(client, count)
    for index in range(count):
        assert upload(client, group, index).status_code == 202
    for _ in range(5):
        assert client.worker.process_one()
    ready = client.store.receipt(client.store.read("SELECT id FROM receipts WHERE status='needs_review'")[0]["id"])
    assert verify(client, ready).status_code == 200
    selected = {"receipt_ids": [ready["id"]]}
    assert client.post("/api/v1/exports", json=selected).status_code == 201
    job = client.store.one("SELECT * FROM jobs WHERE state='queued' LIMIT 1")
    client.store.execute("UPDATE jobs SET state='processing' WHERE id=?", (job["id"],))
    client.store.execute("UPDATE receipts SET status='processing' WHERE id=?", (job["receipt_id"],))
    restarted = Worker(client.store, Engine())
    restarted.start()
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        remaining = client.store.one("SELECT COUNT(*) AS n FROM jobs WHERE state IN ('queued','processing')")["n"]
        if not remaining:
            break
        time.sleep(0.05)
    restarted.close()
    assert remaining == 0
    counts = client.get("/api/v1/batches/" + group["id"]).json()["counts"]
    assert counts == {"needs_review": count - 1, "verified": 1}
    assert len(client.store.read("SELECT * FROM receipts")) == count
    assert client.store.receipt(ready["id"])["status"] == "verified"


def test_concurrent_upload_retries_only_create_one_receipt(client):
    group = batch(client)
    outcomes = []
    threads = [threading.Thread(target=lambda: outcomes.append(upload(client, group).status_code)) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert outcomes == [202, 202, 202]
    assert len(client.store.read("SELECT * FROM receipts")) == 1


def test_summary_only_validation_does_not_invent_values():
    data = blank_data()
    assert data["total"] == "" and data["currency"] == "PHP" and data["items"] == []
    issues = validate(data)
    assert len([issue for issue in issues if issue["blocking"]]) == 4


def test_pdf_upload_and_multi_page_review(client):
    output = io.BytesIO()
    first, second = Image.new("RGB", (600, 800), "white"), Image.new("RGB", (600, 800), "white")
    first.save(output, "PDF", save_all=True, append_images=[second], resolution=120)
    group = batch(client)
    result = upload(client, group, content=output.getvalue())
    assert result.status_code == 202, result.text
    assert client.worker.process_one()
    receipt = client.get("/api/v1/receipts/" + result.json()["id"]).json()
    assert receipt["status"] == "needs_review"
    assert len(receipt["pages"]) == 2
    page = receipt["pages"][0]
    assert client.get(f"/api/v1/receipts/{receipt['id']}/pages/{page['id']}").status_code == 200
    assert client.get(f"/api/v1/receipts/{receipt['id']}/original").content.startswith(b"%PDF-")


def test_ocr_reprocessing_changes_revision_and_rejects_stale_review(client):
    receipt, _ = ready_receipt(client)
    assert client.post("/api/v1/receipts/" + receipt["id"] + "/process", json={"rotation": 0}).status_code == 202
    assert client.worker.process_one()
    current = client.store.receipt(receipt["id"])
    assert current["revision"] > receipt["revision"]
    assert verify(client, receipt).status_code == 409


def test_failed_ocr_can_be_verified_manually_and_is_not_retried(client):
    class BrokenEngine:
        def recognize(self, image):
            raise RuntimeError("Model unavailable")
    group = batch(client)
    receipt_id = upload(client, group).json()["id"]
    client.worker.engine = BrokenEngine()
    client.worker.process_one()
    receipt = client.store.receipt(receipt_id)
    assert receipt["status"] == "failed" and receipt["pages"]
    data, _ = parse([{**line, "page": 0, "id": str(i)} for i, line in enumerate(Engine().recognize(None))])
    result = client.patch("/api/v1/receipts/" + receipt_id, json={"revision": receipt["revision"], "data": data})
    assert result.status_code == 200
    assert verify(client, result.json()).status_code == 200
    client.post("/api/v1/batches/" + group["id"] + "/retry-failed")
    assert not client.worker.process_one()


def test_upload_preview_then_explicit_ocr(client):
    group = batch(client)
    result = client.post('/api/v1/receipts', params={
        'batch_id': group['id'], 'upload_id': group['files'][0]['id']},
        files={'file': ('receipt.png', image_bytes(), 'image/png')})
    assert result.status_code == 202
    receipt_id = result.json()['id']
    assert client.worker.process_one()
    receipt = client.get('/api/v1/receipts/' + receipt_id).json()
    assert receipt['pages']
    assert receipt['data']['supplier'] == ''
    assert receipt['evidence']['lines'] == []
    assert client.post('/api/v1/receipts/' + receipt_id + '/process', json={'rotation': 0}).status_code == 202
    assert client.worker.process_one()
    receipt = client.get('/api/v1/receipts/' + receipt_id).json()
    assert receipt['data']['supplier'] == 'NORTHSIDE MARKET'
    assert receipt['evidence']['lines']
