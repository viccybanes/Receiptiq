"""Conservative parsing. Unknown values remain blank for human review."""

import re
from datetime import date
from decimal import Decimal, InvalidOperation

from .models import blank_data

AMOUNT = r"-?\d[\d,]*\.\d{2}"


def decimal(value):
    if not re.fullmatch(r"-?\d{1,12}(?:\.\d{1,6})?", value):
        raise ValueError("Use a decimal number without currency symbols or grouping separators.")
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError("Invalid decimal number") from exc
    if not number.is_finite():
        raise ValueError("Number must be finite")
    return number


def parse(lines):
    data = blank_data()
    meta = {}
    rows = []
    # Join OCR boxes on the same visual row before interpreting line items.
    for line in sorted(lines, key=lambda line: (line["page"], line["box"][1], line["box"][0])):
        if rows and rows[-1]["page"] == line["page"] and abs(rows[-1]["y"] - line["box"][1]) < 12:
            rows[-1]["text"] += " " + line["text"]
            rows[-1]["ids"].append(line["id"])
        else:
            rows.append({"page": line["page"], "y": line["box"][1], "text": line["text"], "ids": [line["id"]]})

    def assign(key, value, row):
        data[key] = value
        meta[key] = {"source": "ocr", "line_ids": row["ids"]}

    for row in rows:
        text = row["text"].strip()
        if not text:
            continue
        if not data["supplier"] and re.search(r"[A-Za-z]{3}", text) and not re.search(r"receipt|invoice", text, re.I):
            assign("supplier", text, row)
        match = re.search(r"\b(20\d{2})[-/](\d{1,2})[-/](\d{1,2})\b", text)
        if match and not data["receipt_date"]:
            try:
                assign("receipt_date", date(*map(int, match.groups())).isoformat(), row)
            except ValueError:
                pass
        match = re.search(r"(?:receipt|invoice|OR)\s*(?:no\.?|#|number)\s*[:#]?\s*([\w-]+)", text, re.I)
        if match:
            assign("receipt_number", match[1], row)
        currency = re.search(r"\b(PHP|USD|EUR|GBP|JPY|AUD|CAD|SGD)\b", text, re.I)
        if currency:
            assign("currency", currency[1].upper(), row)
        elif "₱" in text:
            assign("currency", "PHP", row)
        amounts = re.findall(AMOUNT, text)
        if not amounts:
            continue
        amount = amounts[-1].replace(",", "")
        key = None
        if re.search(r"\bsub\s*total\b", text, re.I):
            key = "subtotal"
        elif re.search(r"\b(discount|savings)\b", text, re.I):
            key = "discount"
        elif re.search(r"\b(tax|VAT)\b", text, re.I):
            key = "tax"
        elif re.search(r"\b(total|amount due|balance due)\b", text, re.I):
            key = "total"
        if key:
            assign(key, amount, row)
            continue
        if re.search(r"\b(cash|change|tender|payment|card|visa|mastercard)\b", text, re.I):
            continue
        match = re.match(
            r"(.+?)\s+(?:(\d+(?:\.\d+)?)\s*[xX@]\s*(" + AMOUNT + r")\s+)?(" + AMOUNT + r")\s*$", text)
        if match and re.search(r"[A-Za-z]", match[1]) and not re.search(r"\b(date|tel|phone)\b", match[1], re.I):
            index = len(data["items"])
            data["items"].append({"description": match[1].strip(), "quantity": match[2] or "",
                                  "unit_price": (match[3] or "").replace(",", ""),
                                  "line_total": match[4].replace(",", "")})
            for field in data["items"][-1]:
                meta[f"items.{index}.{field}"] = {"source": "ocr", "line_ids": row["ids"]}
    return data, meta


def validate(data):
    issues = []

    def issue(field, message, blocking=False):
        issues.append({"id": field, "field": field, "message": message, "blocking": blocking})

    for key in ("supplier", "receipt_date", "currency", "total"):
        if not data.get(key):
            issue(key, f"Enter {key.replace('_', ' ')} from the receipt.", True)
    if data.get("receipt_date"):
        try:
            if date.fromisoformat(data["receipt_date"]).isoformat() != data["receipt_date"]:
                raise ValueError()
        except ValueError:
            issue("receipt_date", "Use an unambiguous date in YYYY-MM-DD format.", True)
    if data.get("currency") and not re.fullmatch("[A-Z]{3}", data["currency"]):
        issue("currency", "Use a three-letter uppercase currency code, such as PHP.", True)
    numbers = {}
    for key in ("subtotal", "tax", "discount", "total"):
        if data.get(key):
            try:
                numbers[key] = decimal(data[key])
            except ValueError as exc:
                issue(key, str(exc), True)
    items = data.get("items", [])
    item_totals = []
    for i, item in enumerate(items):
        prefix = f"items.{i}"
        if not item["description"]:
            issue(prefix + ".description", f"Item {i + 1} needs a description.", True)
        values = {}
        for key in ("quantity", "unit_price", "line_total"):
            if item[key]:
                try:
                    values[key] = decimal(item[key])
                except ValueError as exc:
                    issue(prefix + "." + key, str(exc), True)
        if "line_total" in values:
            item_totals.append(values["line_total"])
        else:
            issue(prefix + ".line_total", f"Item {i + 1} has no line total.")
        if len(values) == 3 and abs(values["quantity"] * values["unit_price"] - values["line_total"]) > Decimal("0.01"):
            issue(prefix + ".arithmetic", f"Item {i + 1}: quantity × price differs from line total.")
    if not items:
        issue("items", "No item detail. Confirm a summary-only export is appropriate.")
    elif len(item_totals) == len(items):
        if "subtotal" in numbers and abs(sum(item_totals) - numbers["subtotal"]) > Decimal("0.01"):
            issue("item_sum", "Item totals differ from subtotal.")
        elif "subtotal" not in numbers and "total" in numbers:
            expected = sum(item_totals) + numbers.get("tax", 0) - numbers.get("discount", 0)
            if abs(expected - numbers["total"]) > Decimal("0.01"):
                issue("item_sum", "Item totals plus tax minus discount differ from receipt total.")
    if "subtotal" in numbers and "total" in numbers:
        expected = numbers["subtotal"] + numbers.get("tax", 0) - numbers.get("discount", 0)
        if abs(expected - numbers["total"]) > Decimal("0.01"):
            issue("total_arithmetic", "Subtotal plus tax minus discount differs from total; check tax-inclusive amounts.")
    return issues


def receipt_issues(receipt):
    issues = validate(receipt["data"])
    scores = {line["id"]: line["score"] for line in receipt["evidence"]["lines"]}
    for field, evidence in receipt["field_meta"].items():
        if evidence.get("source") != "ocr":
            continue
        if any(scores.get(line_id, 1) < 0.8 for line_id in evidence.get("line_ids", [])):
            issues.append({"id": field + ".ocr", "field": field,
                           "message": f"Check {field.replace('_', ' ')}: OCR had difficulty reading its source.",
                           "blocking": False})
    return issues
