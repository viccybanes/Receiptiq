"""Application rules: revision-safe review and reproducible verified exports."""

import csv
import io
import json
import zipfile
from decimal import Decimal

from fastapi import HTTPException

from .db import now, uid
from .extraction import receipt_issues

COLUMNS = ["Receipt ID", "Purchase date", "Vendor", "Receipt number", "Receipt photo", "Notes",
           "Item number", "Description", "Quantity", "Unit", "Unit price", "Amount", "Type",
           "Photo of item purchased", "Total"]
TEMPLATE = {"id": "default", "name": "Expense line items", "version": 2,
            "columns": COLUMNS, "row_mode": "line_item", "encoding": "utf-8-sig", "delimiter": ","}


def require_receipt(store, receipt_id):
    receipt = store.receipt(receipt_id)
    if not receipt:
        raise HTTPException(404, "Receipt not found")
    return receipt


def edit_receipt(store, receipt_id, edit):
    data = edit.data.model_dump()
    with store.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        receipt = require_receipt(store, receipt_id)
        if receipt["revision"] != edit.revision:
            raise HTTPException(409, "Receipt changed. Reload before saving.")
        if data == receipt["data"]:
            return receipt
        photo_ids = [data["receipt_photo_id"]] + [item["photo_id"] for item in data["items"]]
        for photo_id in filter(None, photo_ids):
            if not connection.execute("SELECT 1 FROM attachments WHERE id=? AND receipt_id=?",
                                      (photo_id, receipt_id)).fetchone():
                raise HTTPException(422, "Photo does not belong to this receipt.")
        revision = receipt["revision"] + 1
        metadata = receipt["field_meta"]
        for key, value in data.items():
            if key == "items":
                if value != receipt["data"]["items"]:
                    metadata = {key: value for key, value in metadata.items() if not key.startswith("items.")}
            elif value != receipt["data"].get(key):
                metadata[key] = {"source": "manual", "line_ids": metadata.get(key, {}).get("line_ids", [])}
        try:
            data = store.write_data(connection, receipt_id, data)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        status = receipt["status"] if receipt["status"] in ("queued", "processing") else "needs_review"
        connection.execute("UPDATE receipts SET revision=?,verified_revision=NULL,status=?,field_meta_json=?,"
                           "updated_at=? WHERE id=?", (revision, status, json.dumps(metadata), now(), receipt_id))
        connection.execute("DELETE FROM export_queue WHERE receipt_id=?", (receipt_id,))
        store.audit(connection, receipt_id, revision, "edit", receipt["data"], data)
    return store.receipt(receipt_id)


def verify_receipt(store, receipt_id, verification):
    with store.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        receipt = require_receipt(store, receipt_id)
        if receipt["revision"] != verification.revision:
            raise HTTPException(409, "Receipt changed. Reload and review the latest values.")
        if receipt["status"] in ("queued", "processing"):
            raise HTTPException(409, "Wait for processing or cancel this receipt before verification.")
        issues = receipt_issues(receipt)
        blocked = [issue for issue in issues if issue["blocking"]]
        warnings = {issue["id"] for issue in issues if not issue["blocking"]}
        if blocked or warnings - set(verification.acknowledged) or (warnings and not verification.reason):
            raise HTTPException(422, {"message": "Resolve required fields and acknowledge every warning with a reason.",
                                      "issues": issues})
        connection.execute("UPDATE receipts SET status='verified',verified_revision=revision,error=NULL,updated_at=? "
                           "WHERE id=?", (now(), receipt_id))
        connection.execute("INSERT OR REPLACE INTO export_queue VALUES(?,?)", (receipt_id, receipt["revision"]))
        store.audit(connection, receipt_id, receipt["revision"], "verify", after=receipt["data"],
                    reviewer=verification.reviewer, reason=verification.reason)
    return store.receipt(receipt_id)


def safe_text(value):
    # Spreadsheet applications interpret leading whitespace followed by =,+,-,@ as formulas.
    if value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def photo_reference(store, receipt, photo_id="", original=False):
    if photo_id:
        photo = store.one("SELECT * FROM attachments WHERE id=? AND receipt_id=?", (photo_id, receipt["id"]))
        if not photo:
            raise HTTPException(409, "A selected photo is missing. Attach it again before exporting.")
        return photo["relative_path"]
    if original and receipt.get("original_mime", "").startswith(("image/", "application/pdf")):
        document = store.one("SELECT relative_path FROM documents WHERE id=?", (receipt["document_id"],))
        return document["relative_path"]
    return ""


def csv_rows(receipts, store):
    rows = []
    for receipt in receipts:
        data = receipt["data"]
        for item in data["items"] or [{}]:
            row = {"Receipt ID": receipt["id"], "Purchase date": data["receipt_date"], "Vendor": data["supplier"],
                   "Receipt number": data["receipt_number"],
                   "Receipt photo": photo_reference(store, receipt, data.get("receipt_photo_id", ""), original=True),
                   "Notes": data.get("notes", ""), "Item number": item.get("item_number", ""),
                   "Description": item.get("description", ""), "Quantity": item.get("quantity", ""),
                   "Unit": item.get("unit", ""), "Unit price": item.get("unit_price", ""),
                   "Amount": item.get("line_total", ""), "Type": item.get("item_type", "Materials"),
                   "Photo of item purchased": photo_reference(store, receipt, item.get("photo_id", "")),
                   "Total": data["total"]}
            for key in ("Vendor", "Receipt number", "Notes", "Description", "Unit", "Type"):
                row[key] = safe_text(row[key])
            rows.append({key: row.get(key, "") for key in COLUMNS})
    return rows


def export_selection(store, ids):
    if len(ids) != len(set(ids)):
        raise HTTPException(422, "Select each receipt only once.")
    receipts = [require_receipt(store, receipt_id) for receipt_id in ids]
    if any(r["status"] != "verified" or r["verified_revision"] != r["revision"] for r in receipts):
        raise HTTPException(409, "Every selected receipt must have its current revision verified.")
    if any(issue["blocking"] for receipt in receipts for issue in receipt_issues(receipt)):
        raise HTTPException(409, "A selected receipt needs required expense fields. Open it, correct, and verify again.")
    return receipts


def generate_export(store, ids, include_photos=False):
    export_id = uid()
    relative = f"exports/{export_id}." + ("zip" if include_photos else "csv")
    path = store.file(relative)
    try:
        with store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            receipts = export_selection(store, ids)
            buffer = io.StringIO(newline="")
            writer = csv.DictWriter(buffer, fieldnames=COLUMNS, lineterminator="\r\n")
            writer.writeheader()
            rows = csv_rows(receipts, store)
            writer.writerows(rows)
            content = buffer.getvalue().encode("utf-8-sig")
            if include_photos:
                with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
                    archive.writestr("receipts.csv", content)
                    images = {row[key] for row in rows for key in ("Receipt photo", "Photo of item purchased") if row[key]}
                    for image in sorted(images):
                        archive.write(store.file(image), arcname=image)
            else:
                path.write_bytes(content)
            connection.execute("INSERT INTO exports VALUES(?,?,?,?)",
                               (export_id, relative, json.dumps(TEMPLATE), now()))
            connection.executemany("INSERT INTO export_receipts VALUES(?,?,?,?)", [
                (export_id, r["id"], r["revision"], json.dumps(r["data"])) for r in receipts])
        return {"id": export_id, "receipt_count": len(receipts), "row_count": len(rows),
                "format": "zip" if include_photos else "csv",
                "totals_by_currency": totals(receipts)}
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def totals(receipts):
    result = {}
    for receipt in receipts:
        data = receipt["data"]
        currency = data["currency"]
        result[currency] = result.get(currency, Decimal("0")) + Decimal(data["total"])
    return {currency: str(value) for currency, value in result.items()}
