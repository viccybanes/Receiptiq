"""Expense wording, cent allocation, owned photos, and portable CSV round trips."""

import copy
import csv
import io
import json
import sqlite3
import zipfile
from decimal import Decimal

import pytest
from test_workflow import client as client_fixture
from test_workflow import image_bytes, ready_receipt, verify

from app.db import Store
from app.extraction import allocate_discount, parse, reconcile, validate
from app.service import COLUMNS

client = client_fixture


def save(client, receipt):
    response = client.patch("/api/v1/receipts/" + receipt["id"],
                            json={"revision": receipt["revision"], "data": receipt["data"]})
    assert response.status_code == 200, response.text
    return response.json()


def csv_bytes(rows):
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=COLUMNS)
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8-sig")


def test_receipt_codes_order_duplicates_units():
    texts = ["SHOP", "TRANS #: 00-0045", "SI #: 00-0029", "TRANS #: 00-0045",
             "Receipt No: R-009", "Rice 2 bag x 50.00 90.00", "Total 90.00"]
    data, meta = parse([{"text": text, "id": str(i), "page": 0, "box": [0, i * 30, 500, i * 30 + 15]}
                        for i, text in enumerate(texts)])
    assert data["receipt_number"] == "00-0045 / 00-0029 / R-009"
    assert meta["receipt_number"]["line_ids"] == ["1", "2", "4"]
    assert data["items"][0]["unit"] == "bag"
    assert any("possible discount" in issue["message"] for issue in validate(data))
    assert not reconcile(data)["can_allocate"]  # net Amount already matches Total


@pytest.mark.parametrize("text,expected", [
    ("Rice 2 bag x 50.00 100.00", "bag"),
    ("Rice 2 x 50.00 100.00", "unit"),
    ("Rice 100.00", "unit"),
])
def test_ocr_missing_units_use_default(text, expected):
    data, _ = parse([{"text": text, "id": "line", "page": 0, "box": [0, 0, 500, 15]}])
    assert data["items"][0]["unit"] == expected


def test_discount_allocation_exact_cents_and_never_automatic(client):
    receipt, _ = ready_receipt(client)
    receipt["data"]["total"] = "100.01"
    receipt = save(client, receipt)
    assert receipt["data"]["items"][0]["line_total"] == "100.00"
    assert receipt["reconciliation"]["possible_discount"] == "24.99"
    response = client.post(f"/api/v1/receipts/{receipt['id']}/discount-preview",
                           json={"revision": receipt["revision"], "data": receipt["data"]})
    assert response.status_code == 200, response.text
    proposal = response.json()["data"]
    assert sum(Decimal(item["line_total"]) for item in proposal["items"]) == Decimal("100.01")
    assert [item["line_total"] for item in proposal["items"]] == ["80.01", "20.00"]
    assert not validate(proposal)
    assert "24.99" in proposal["notes"]
    assert client.store.receipt(receipt["id"])["data"]["total"] == "100.01"
    assert client.store.receipt(receipt["id"])["data"]["items"][0]["line_total"] == "100.00"
    receipt["data"] = proposal
    receipt = save(client, receipt)
    assert verify(client, receipt).status_code == 200
    stale = client.post(f"/api/v1/receipts/{receipt['id']}/discount-preview",
                        json={"revision": 0, "data": proposal})
    assert stale.status_code == 409
    for total, quantity in [("200", "2"), ("-1", "2"), ("100", "0"), ("100", "")]:
        invalid = copy.deepcopy(proposal)
        invalid["total"], invalid["items"][0]["quantity"] = total, quantity
        with pytest.raises(ValueError):
            allocate_discount(invalid)
    overage = copy.deepcopy(proposal)
    overage["total"] = "200"
    assert any("missing items" in issue["message"] for issue in validate(overage))
    one_cent = copy.deepcopy(proposal)
    one_cent["total"] = "100.00"
    assert reconcile(one_cent)["can_allocate"]
    assert not validate(allocate_discount(one_cent))
    extreme = copy.deepcopy(proposal)
    extreme["total"] = "50"
    extreme["items"][0]["quantity"] = "0.000000000001"
    with pytest.raises(ValueError, match="precision"):
        allocate_discount(extreme)


def test_stable_item_numbers_and_new_expense_fields(client):
    receipt, _ = ready_receipt(client)
    numbers = {item["id"]: item["item_number"] for item in receipt["data"]["items"]}
    assert len(set(numbers.values())) == 2
    receipt["data"]["items"].reverse()
    receipt["data"]["items"][0].update(unit="pc", item_type="Equipment")
    receipt["data"]["notes"] = "Check warranty"
    receipt = save(client, receipt)
    assert {item["id"]: item["item_number"] for item in receipt["data"]["items"]} == numbers
    assert receipt["data"]["items"][0]["unit"] == "pc"
    duplicate = copy.deepcopy(receipt["data"])
    duplicate["items"].append(duplicate["items"][0])
    assert client.patch("/api/v1/receipts/" + receipt["id"],
                        json={"revision": receipt["revision"], "data": duplicate}).status_code == 422
    other, _ = ready_receipt(client)
    other["data"]["items"][0]["id"] = receipt["data"]["items"][0]["id"]
    assert client.patch("/api/v1/receipts/" + other["id"],
                        json={"revision": other["revision"], "data": other["data"]}).status_code == 422


def test_zip_export_import_with_receipt_and_optional_item_photo(client):
    receipt, _ = ready_receipt(client)
    photo = client.post(f"/api/v1/receipts/{receipt['id']}/photos",
                        files={"file": ("item.png", image_bytes())})
    assert photo.status_code == 201, photo.text
    photo_id = photo.json()["id"]
    assert client.get(f"/api/v1/receipts/{receipt['id']}/photos/{photo_id}").content == image_bytes()
    other, _ = ready_receipt(client)
    other["data"]["receipt_photo_id"] = photo_id
    assert client.patch("/api/v1/receipts/" + other["id"],
                        json={"revision": other["revision"], "data": other["data"]}).status_code == 422
    assert client.get(f"/api/v1/receipts/{other['id']}/photos/{photo_id}").status_code == 404
    receipt["data"]["items"][0].update(photo_id=photo_id, unit="bag")
    receipt["data"]["notes"] = "Optional item photo attached."
    receipt = save(client, receipt)
    assert verify(client, receipt).status_code == 200
    exported = client.post("/api/v1/exports",
                           json={"receipt_ids": [receipt["id"]], "include_photos": True})
    assert exported.status_code == 201, exported.text
    response = client.get("/api/v1/exports/" + exported.json()["id"] + "/download")
    assert response.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        rows = list(csv.DictReader(io.StringIO(archive.read("receipts.csv").decode("utf-8-sig"))))
        assert list(rows[0]) == COLUMNS
        assert not {"Tax", "Subtotal", "Discount"} & set(rows[0])
        assert rows[0]["Type"] == "Materials" and rows[0]["Unit"] == "bag"
        assert archive.read(rows[0]["Receipt photo"]) == image_bytes()
        assert archive.read(rows[0]["Photo of item purchased"]) == image_bytes()
        assert rows[1]["Photo of item purchased"] == ""
    imported = client.post("/api/v1/imports", files={"file": ("expenses.zip", response.content)})
    assert imported.status_code == 201, imported.text
    assert imported.json()["receipt_count"] == 1 and not imported.json()["warnings"]
    # Structured imports only render previews, even if no OCR model is available.
    class NoOCR:
        def recognize(self, path):
            raise AssertionError("CSV imports must not run OCR")
    client.worker.engine = NoOCR()
    assert client.worker.process_one()
    row = client.store.one("SELECT id FROM receipts WHERE batch_id=?", (imported.json()["id"],))
    result = client.store.receipt(row["id"])
    assert result["status"] == "needs_review" and result["verified_revision"] is None
    assert result["data"]["supplier"] == receipt["data"]["supplier"]
    assert result["data"]["notes"] == receipt["data"]["notes"]
    assert result["data"]["items"][0]["photo_id"]
    assert result["pages"] and result["evidence"]["lines"] == []
    assert result["data"]["items"][0]["item_number"] != receipt["data"]["items"][0]["item_number"]
    assert client.post("/api/v1/exports", json={"receipt_ids": [result["id"]]}).status_code == 409


@pytest.mark.parametrize("with_photo", [False, True])
def test_csv_import_selected_photos_or_missing_photo_review(client, with_photo):
    row = {"Purchase date": "19/10/2026", "Vendor": "Hardware", "Receipt number": "00-0045 / 00-0029",
           "Receipt photo": "receipt.png", "Description": "Nails", "Quantity": "2", "Unit": "bag",
           "Unit price": "50", "Amount": "90", "Type": "Materials"}
    files = [("file", ("expense.csv", csv_bytes([row])))]
    if with_photo:
        files.append(("photos", ("receipt.png", image_bytes(), "image/png")))
    response = client.post("/api/v1/imports", files=files)
    assert response.status_code == 201, response.text
    assert bool(response.json()["warnings"]) is not with_photo
    assert client.worker.process_one()
    record = client.store.one("SELECT id FROM receipts WHERE batch_id=?", (response.json()["id"],))
    receipt = client.store.receipt(record["id"])
    assert receipt["status"] == "needs_review"
    assert receipt["data"]["receipt_date"] == "2026-10-19"
    assert receipt["data"]["total"] == "90"
    assert "Total inferred" in receipt["data"]["notes"]
    assert bool(receipt["pages"]) is with_photo
    if not with_photo:
        assert client.post(f"/api/v1/receipts/{receipt['id']}/process", json={}).status_code == 422


def test_bad_imports_rollback_and_photo_content_validation(client):
    row = {"Receipt ID": "same", "Purchase date": "2026-10-19", "Vendor": "Hardware", "Total": "10",
           "Description": "Bolt", "Quantity": "1", "Unit price": "10", "Amount": "10", "Receipt photo": "receipt.png"}
    before = set(client.store.root.rglob("*"))
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("expenses.csv", csv_bytes([row, {**row, "Vendor": "Conflicting"}]))
        archive.writestr("receipt.png", image_bytes())
    assert client.post("/api/v1/imports", files={"file": ("bad.zip", stream.getvalue())}).status_code == 422
    assert not client.store.read("SELECT * FROM receipts")
    assert not client.store.read("SELECT * FROM batches")
    assert not [path for path in set(client.store.root.rglob("*")) - before if path.is_file()]
    for unsafe in ("../outside.png", "/absolute.png", "C:/outside.png"):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            archive.writestr("expenses.csv", csv_bytes([row]))
            archive.writestr(unsafe, image_bytes())
        assert client.post("/api/v1/imports", files={"file": ("bad.zip", stream.getvalue())}).status_code == 422
    assert client.post("/api/v1/imports", files={"file": ("bad.csv", b"bad,headers\n1,2")}).status_code == 422
    receipt, _ = ready_receipt(client)
    assert client.post(f"/api/v1/receipts/{receipt['id']}/photos",
                       files={"file": ("fake.png", b"not an image")}).status_code == 422
    assert not list((client.store.root / "attachments").iterdir())


def test_v1_migration_backup_keeps_verified_data_and_export(client):
    receipt, _ = ready_receipt(client)
    assert verify(client, receipt).status_code == 200
    exported = client.post("/api/v1/exports", json={"receipt_ids": [receipt["id"]]}).json()
    export_path = "/api/v1/exports/" + exported["id"] + "/download"
    old_export = client.get(export_path).content
    with client.store.connect() as connection:
        header = {key: value for key, value in receipt["data"].items()
                  if key not in ("items", "notes", "receipt_photo_id")}
        connection.execute("UPDATE receipts SET data_json=? WHERE id=?", (json.dumps(header), receipt["id"]))
        evidence = json.loads(connection.execute("SELECT result_json FROM extraction_runs WHERE receipt_id=?",
                                                 (receipt["id"],)).fetchone()[0])
        evidence["draft"] = {**header, "items": [{key: item[key] for key in
                                                ("description", "quantity", "unit_price", "line_total")}
                                               for item in receipt["data"]["items"]]}
        connection.execute("UPDATE extraction_runs SET result_json=? WHERE receipt_id=?",
                           (json.dumps(evidence), receipt["id"]))
        for index, item in enumerate(receipt["data"]["items"]):
            old_item = {key: item[key] for key in ("description", "quantity", "unit_price", "line_total")}
            connection.execute("UPDATE receipt_items SET data_json=? WHERE receipt_id=? AND position=?",
                               (json.dumps(old_item), receipt["id"], index))
        connection.execute("DROP TABLE attachments")
        connection.execute("DROP TABLE line_item_numbers")
        connection.execute("ALTER TABLE jobs DROP COLUMN kind")
        connection.execute("DELETE FROM schema_migrations WHERE version=2")
        connection.execute("PRAGMA user_version=1")
    migrated = Store(client.store.root)
    result = migrated.receipt(receipt["id"])
    assert result["status"] == "verified" and result["verified_revision"] == receipt["revision"]
    assert result["data"]["supplier"] == receipt["data"]["supplier"]
    assert result["data"]["items"][0]["item_number"] > 0
    assert migrated.one("PRAGMA user_version")["user_version"] == 3
    assert result["evidence"]["draft"]["notes"] == ""
    assert result["evidence"]["draft"]["items"][0]["id"]
    assert client.get(export_path).content == old_export
    backups = list((migrated.root / "backups").glob("before-v2-*.sqlite3"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
        assert connection.execute("SELECT status FROM receipts").fetchone()[0] == "verified"
    migrated.execute("DELETE FROM receipt_items WHERE receipt_id=?", (receipt["id"],))
    assert client.post("/api/v1/exports", json={"receipt_ids": [receipt["id"]]}).status_code == 409
    assert client.get(export_path).content == old_export


def test_whole_quantities_preserve_drafts_but_block_fractional_verification(client):
    receipt, _ = ready_receipt(client)
    receipt['data']['items'][0]['quantity'] = '2.000000'
    receipt = save(client, receipt)
    assert receipt['data']['items'][0]['quantity'] == '2'
    receipt['data']['items'][0]['quantity'] = '1.5'
    receipt = save(client, receipt)
    assert receipt['data']['items'][0]['quantity'] == '1.5'
    assert any(issue['field'] == 'items.0.quantity' and issue['blocking']
               for issue in validate(receipt['data']))
    assert verify(client, receipt).status_code == 422
