"""Validated CSV mappings, never expressions, with immutable export-ready definitions."""

import copy
import csv
import io
import json
import re
from datetime import date
from uuid import uuid4

COLUMNS = ["Receipt ID", "Purchase date", "Vendor", "Receipt number", "Receipt photo", "Notes",
           "Item number", "Description", "Quantity", "Unit", "Unit price", "Amount", "Type",
           "Photo of item purchased", "Total"]
DEFAULT_TEMPLATE = {"id": "default", "name": "Expense line items", "version": 2,
                    "columns": COLUMNS, "row_mode": "line_item", "encoding": "utf-8-sig", "delimiter": ",",
                    "date_format": "ISO", "decimal_separator": ".", "required_fields": []}
NUMERIC_FIELDS = {"Item number", "Quantity", "Unit price", "Amount", "Total"}
ITEM_KEYS = {"Item number": "item_number", "Description": "description", "Quantity": "quantity",
             "Unit": "unit", "Unit price": "unit_price", "Amount": "line_total", "Type": "item_type",
             "Photo of item purchased": "photo_id"}
RECEIPT_KEYS = {"Purchase date": "receipt_date", "Vendor": "supplier", "Receipt number": "receipt_number",
                "Receipt photo": "receipt_photo_id", "Notes": "notes", "Total": "total"}
NUMBER = re.compile(r"-?[0-9]+(?:[.,][0-9]+)?\Z")


class TemplateConflict(ValueError):
    pass


def safe_text(value):
    text = str(value) if value is not None else ""
    leading = re.sub(r"^[\s\ufeff\x00]+", "", text)
    return "'" + text if leading.startswith(("=", "+", "-", "@")) else text


def validate_template(payload):
    if not isinstance(payload, dict):
        raise ValueError("Template must be an object.")
    unknown = set(payload) - set(DEFAULT_TEMPLATE)
    if unknown:
        raise ValueError("Unknown template settings: " + ", ".join(sorted(unknown)))
    template = {**copy.deepcopy(DEFAULT_TEMPLATE), **copy.deepcopy(payload)}
    template["id"] = payload.get("id", str(uuid4()))
    if not isinstance(template["id"], str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", template["id"]):
        raise ValueError("Template ID must contain 1 to 64 letters, digits, underscores, or hyphens.")
    name = template["name"]
    if not isinstance(name, str) or not name.strip() or len(name) > 120:
        raise ValueError("Template name must contain 1 to 120 characters.")
    template["name"] = name.strip()
    if isinstance(template["version"], bool) or not isinstance(template["version"], int) or template["version"] < 1:
        raise ValueError("Template version must be a positive integer.")
    settings = {"row_mode": ("line_item", "receipt"), "delimiter": (",", ";", "\t"),
                "encoding": ("utf-8-sig", "utf-8"), "date_format": ("ISO", "DDMMYYYY", "MMDDYYYY"),
                "decimal_separator": (".", ",")}
    for key, options in settings.items():
        if template[key] not in options:
            raise ValueError(f"Invalid {key}.")
    columns = template["columns"]
    if not isinstance(columns, list) or not 1 <= len(columns) <= 40:
        raise ValueError("Choose 1 to 40 CSV columns.")
    normalized, headers = [], set()
    for column in columns:
        if isinstance(column, str):
            column = {"header": column, "field": column}
        if not isinstance(column, dict) or set(column) not in ({"header", "field"}, {"header", "constant"}):
            raise ValueError("Each column needs a header and exactly one field or constant.")
        header = column["header"]
        if not isinstance(header, str) or not header.strip() or len(header) > 120 or any(c in header for c in "\r\n\t\x00"):
            raise ValueError("Column headers must contain 1 to 120 characters on one line.")
        header = header.strip()
        if header.casefold() in headers or safe_text(header) != header:
            raise ValueError("Column headers must be distinct and must not be spreadsheet formulas.")
        headers.add(header.casefold())
        if "field" in column:
            if not isinstance(column["field"], str) or column["field"] not in COLUMNS:
                raise ValueError("Unknown CSV source field.")
        elif not isinstance(column["constant"], str) or len(column["constant"]) > 4000 or "\x00" in column["constant"]:
            raise ValueError("Literal constants must be text of at most 4000 characters.")
        normalized.append({**column, "header": header})
    template["columns"] = normalized
    required = template["required_fields"]
    if not isinstance(required, list) or len(required) > len(COLUMNS) or any(
            not isinstance(field, str) or field not in COLUMNS for field in required):
        raise ValueError("Required fields must be selected from the CSV source fields.")
    if len(set(required)) != len(required):
        raise ValueError("Select each required field once.")
    return template


def required_issues(receipts, template):
    template = validate_template(template)
    issues = []
    for receipt in receipts:
        data = receipt["data"]
        for field in template["required_fields"]:
            if field == "Receipt ID":
                values = [receipt["id"]]
            elif field in ITEM_KEYS:
                values = [item.get(ITEM_KEYS[field], "") for item in data["items"]] or [""]
            elif field == "Receipt photo" and receipt.get("original_mime", "").startswith(("image/", "application/pdf")):
                values = [data.get("receipt_photo_id") or receipt.get("document_id", "")]
            else:
                values = [data.get(RECEIPT_KEYS[field], "")]
            for position, value in enumerate(values):
                if value is None or not str(value).strip():
                    issues.append({"id": f"template:{receipt['id']}:{field}:{position}", "receipt_id": receipt["id"],
                                   "field": field, "message": f"{field} is required by this CSV template.",
                                   "blocking": True})
    return issues


def render_rows(receipts, store, template, base_rows):
    template = validate_template(template)
    source_rows = base_rows(receipts, store) if callable(base_rows) else base_rows
    if template["row_mode"] == "receipt":
        grouped = {}
        for row in source_rows:
            grouped.setdefault(row["Receipt ID"], []).append(row)
        source_rows = []
        for receipt in receipts:
            items = grouped.get(receipt["id"], [])
            if not items:
                continue
            row = dict(items[0])
            for field in ITEM_KEYS:
                row[field] = ""
            row["Description"] = "; ".join(str(item.get("Description", "")) for item in items
                                           if item.get("Description"))
            row["Amount"] = row.get("Total", "")
            source_rows.append(row)
    rows = []
    for source in source_rows:
        row = {}
        for column in template["columns"]:
            field = column.get("field")
            value = str(source.get(field, "") if field else column["constant"])
            if field == "Purchase date" and value and template["date_format"] != "ISO":
                pattern = "%d/%m/%Y" if template["date_format"] == "DDMMYYYY" else "%m/%d/%Y"
                value = date.fromisoformat(value).strftime(pattern)
            if field in NUMERIC_FIELDS and NUMBER.fullmatch(value):
                value = value.replace(".", template["decimal_separator"])
            else:
                value = safe_text(value)
            row[column["header"]] = value
        rows.append(row)
    return rows


def serialize_rows(rows, template):
    template = validate_template(template)
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=[column["header"] for column in template["columns"]],
                            delimiter=template["delimiter"], lineterminator="\r\n", extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        safe = {}
        for column in template["columns"]:
            value = str(row.get(column["header"], ""))
            safe[column["header"]] = value if column.get("field") in NUMERIC_FIELDS and NUMBER.fullmatch(value) else safe_text(value)
        writer.writerow(safe)
    return buffer.getvalue().encode(template["encoding"])


def get_template(store, template_id="default"):
    if template_id == "default":
        return validate_template(DEFAULT_TEMPLATE)
    row = store.one("SELECT definition_json FROM csv_templates WHERE id=?", (template_id,))
    if not row:
        return None
    return validate_template(json.loads(row["definition_json"]))


def list_templates(store):
    return [get_template(store)] + [validate_template(json.loads(row["definition_json"])) for row in store.read(
        "SELECT definition_json FROM csv_templates WHERE id!='default' ORDER BY id")]


def save_template(store, payload, expected_version=None):
    template = validate_template(payload)
    if template["id"] == "default":
        raise ValueError("The default template is immutable. Save a copy with a different ID.")
    with store.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        existing = connection.execute("SELECT version FROM csv_templates WHERE id=?", (template["id"],)).fetchone()
        if existing:
            if expected_version is None and "version" not in payload:
                raise TemplateConflict("Provide the current template version before saving.")
            expected = template["version"] if expected_version is None else expected_version
            if isinstance(expected, bool) or expected != existing["version"]:
                raise TemplateConflict("Template changed. Reload before saving.")
            template["version"] = existing["version"] + 1
        else:
            if expected_version not in (None, 0):
                raise TemplateConflict("Template no longer exists. Save a new copy.")
            template["version"] = 1
        connection.execute("INSERT INTO csv_templates VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET "
                           "version=excluded.version,definition_json=excluded.definition_json",
                           (template["id"], template["version"], json.dumps(template)))
    return template


def delete_template(store, template_id, expected_version):
    if template_id == "default":
        raise ValueError("The default template cannot be deleted.")
    with store.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        existing = connection.execute("SELECT version FROM csv_templates WHERE id=?", (template_id,)).fetchone()
        if not existing:
            return False
        if isinstance(expected_version, bool) or expected_version != existing["version"]:
            raise TemplateConflict("Template changed. Reload before deleting.")
        connection.execute("DELETE FROM csv_templates WHERE id=?", (template_id,))
    return True
