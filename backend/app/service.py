"""Application rules: revision-safe review and reproducible verified exports."""

import csv
import io
import json
from decimal import Decimal

from fastapi import HTTPException

from .db import now, uid
from .extraction import receipt_issues

COLUMNS = ["receipt_id", "supplier", "receipt_number", "receipt_date", "currency",
           "item_description", "quantity", "unit_price", "line_total", "subtotal", "tax", "discount", "total"]
TEMPLATE = {"id": "default", "name": "Receipt line items", "version": 1,
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
        revision = receipt["revision"] + 1
        metadata = receipt["field_meta"]
        for key, value in data.items():
            if key == "items":
                if value != receipt["data"]["items"]:
                    metadata = {key: value for key, value in metadata.items() if not key.startswith("items.")}
            elif value != receipt["data"].get(key):
                metadata[key] = {"source": "manual", "line_ids": metadata.get(key, {}).get("line_ids", [])}
        store.write_data(connection, receipt_id, data)
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


def csv_rows(receipts):
    rows = []
    for receipt in receipts:
        data = receipt["data"]
        for item in data["items"] or [{}]:
            row = {"receipt_id": receipt["id"], **{key: value for key, value in data.items() if key != "items"},
                   "item_description": item.get("description", ""),
                   "quantity": item.get("quantity", ""), "unit_price": item.get("unit_price", ""),
                   "line_total": item.get("line_total", "")}
            for key in ("supplier", "receipt_number", "item_description"):
                row[key] = safe_text(row[key])
            rows.append({key: row.get(key, "") for key in COLUMNS})
    return rows


def export_selection(store, ids):
    if len(ids) != len(set(ids)):
        raise HTTPException(422, "Select each receipt only once.")
    receipts = [require_receipt(store, receipt_id) for receipt_id in ids]
    if any(r["status"] != "verified" or r["verified_revision"] != r["revision"] for r in receipts):
        raise HTTPException(409, "Every selected receipt must have its current revision verified.")
    return receipts


def generate_export(store, ids):
    export_id = uid()
    relative = f"exports/{export_id}.csv"
    path = store.file(relative)
    try:
        with store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            receipts = export_selection(store, ids)
            buffer = io.StringIO(newline="")
            writer = csv.DictWriter(buffer, fieldnames=COLUMNS, lineterminator="\r\n")
            writer.writeheader()
            writer.writerows(csv_rows(receipts))
            path.write_bytes(buffer.getvalue().encode("utf-8-sig"))
            connection.execute("INSERT INTO exports VALUES(?,?,?,?)",
                               (export_id, relative, json.dumps(TEMPLATE), now()))
            connection.executemany("INSERT INTO export_receipts VALUES(?,?,?,?)", [
                (export_id, r["id"], r["revision"], json.dumps(r["data"])) for r in receipts])
        return {"id": export_id, "receipt_count": len(receipts), "row_count": len(csv_rows(receipts)),
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
