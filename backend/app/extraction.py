"""Conservative parsing. Unknown values remain blank for human review."""

import re
from datetime import date
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal, InvalidOperation, localcontext

from .layout import group_rows
from .models import blank_data

AMOUNT = r"-?\d[\d,]*\.\d{2}"
MONTH_NUMBERS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}
FIELD_LABELS = {"supplier": "Vendor", "receipt_date": "Purchase date", "receipt_number": "Receipt number",
                "total": "Total", "line_total": "Amount", "quantity": "Quantity", "unit_price": "Unit price"}


def decimal(value):
    if not re.fullmatch(r"-?\d{1,12}(?:\.\d{1,12})?", value):
        raise ValueError("Use a decimal number without currency symbols or grouping separators.")
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError("Invalid decimal number") from exc
    if not number.is_finite():
        raise ValueError("Number must be finite")
    return number


def percentage(value):
    if not re.fullmatch(r"[0-9]{1,3}(?:\.[0-9]{1,6})?", value) or not 0 <= Decimal(value) <= 100:
        raise ValueError("Use a discount from 0 to 100, with up to six decimal places, or leave it blank.")
    return Decimal(value)


def effective_discount(item, data):
    # Blank follows the receipt default; an explicit zero is a manual override.
    return item.get("discount_percent") or data.get("total_discount_percent", "")


def discounted_price(price, percent):
    with localcontext() as context:
        context.prec = 60
        factor = (100 - percentage(percent)) / 100 if percent else Decimal(1)
        return (decimal(price) * factor).quantize(Decimal("0.000000000001"), rounding=ROUND_HALF_UP) + Decimal(0)


def discounted_amount(item, data):
    quantity = decimal(item["quantity"])
    if quantity != quantity.to_integral_value():
        raise ValueError("Quantity must be a whole number.")
    with localcontext() as context:
        context.prec = 60
        return (quantity * discounted_price(item["unit_price"], effective_discount(item, data))).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP) + Decimal(0)


def apply_percentage_discounts(data, previous):
    """Recalculate explicit percentage edits from gross prices, never from already-net Amounts."""
    old_items = {item["id"]: item for item in previous["items"]}
    changed = False
    for item in data["items"]:
        old = old_items.get(item["id"])
        percent = effective_discount(item, data)
        old_percent = effective_discount(old, previous) if old else ""
        try:
            factors_changed = old and any(item[key] != old[key] for key in ("quantity", "unit_price"))
            if (percent != old_percent or (percent and not old) or
                    (percent and factors_changed and decimal(old["line_total"]) == discounted_amount(old, previous))):
                item["line_total"] = f"{discounted_amount(item, data):.2f}"
                changed = True
        except ValueError:
            pass  # Incomplete/invalid drafts remain editable and are blocked by validation.
    if changed and (not data["total"] or data["total"] == previous["total"]):
        try:
            data["total"] = f"{sum(decimal(item['line_total']) for item in data['items']):.2f}"
        except ValueError:
            pass
    return data


def extraction_date(value):
    """Normalize clear dates; ambiguous day/month readings need human review."""
    value = value.strip()
    written = re.fullmatch(
        r"(?:([A-Za-z]+)\.?[\s/-]+(\d{1,2})|(\d{1,2})[\s/-]+([A-Za-z]+)\.?)"
        r"(?:,\s*|[\s/-]+)(\d{4}|\d{2})", value,
    )
    if written:
        month = MONTH_NUMBERS.get((written[1] or written[4]).lower(), 0)
        day = int(written[2] or written[3])
        # Match the numeric-date convention for two-digit receipt years.
        year = int(written[5]) + (2000 if len(written[5]) == 2 else 0)
        try:
            return date(year, month, day).isoformat()
        except ValueError:
            return ""
    if re.fullmatch(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}", value):
        try:
            return date(*map(int, re.split(r"[-/]", value))).isoformat()
        except ValueError:
            return ""
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        pass
    match = re.fullmatch(r"(\d{1,2})[/-](\d{1,2})[/-](\d{2}|\d{4})", value)
    if not match:
        return ""
    first, second, year = map(int, match.groups())
    if first <= 12 and second <= 12 and first != second:
        return ""
    month, day = (second, first) if first > 12 else (first, second)
    # Two-digit receipt years use the application's contemporary 2000-2099 range.
    if len(match[3]) == 2:
        year += 2000
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return ""


def parse(lines):
    data = blank_data()
    meta = {}
    receipt_codes = []
    receipt_code_lines = []
    rows = group_rows(lines)

    def assign(key, value, row):
        data[key] = value
        meta[key] = {"source": "ocr", "line_ids": row["ids"]}

    # Only consider the first page's header before any item/amount rows. OCR
    # cannot classify handwriting, so spelling correction belongs to vision.
    header = [row for row in rows if row["page"] == rows[0]["page"]][:8] if rows else []
    for row in header:
        text = row["text"].strip()
        if re.search(AMOUNT, text):
            break
        if not re.search(r"[A-Za-z]{3}", text) or re.search(
            r"receipt|invoice|\b(?:trans|SI|OR)\s*(?:#|no)|"
            r"\b(?:welcome|thank|date|time|tel|telephone|phone|mobile|TIN|VAT|"
            r"customer|cashier|address|description|quantity|total)\b|"
            r"\b(?:sold|bill|ship)\s+to\b|@|https?://|www\.|"
            r"\b\d+\s+.*\b(?:street|st\.?|road|rd\.?|avenue|ave\.?)\b", text, re.I
        ):
            continue
        assign("supplier", text, row)
        break

    for row in rows:
        text = row["text"].strip()
        if not text:
            continue
        match = re.search(r"\b(?:\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}[/-]\d{1,2}[/-](?:\d{4}|\d{2}))\b", text)
        if match and not data["receipt_date"]:
            normalized = extraction_date(match[0])
            if normalized:
                assign("receipt_date", normalized, row)
        for match in re.finditer(
            r"\b(?:receipt|invoice|trans(?:action)?|SI|OR)\s*(?:no\.?|#|number)\s*[:#]?\s*([A-Za-z0-9][\w-]*)",
            text, re.I,
        ):
            if match[1] not in receipt_codes:
                receipt_codes.append(match[1])
                receipt_code_lines.extend(row["ids"])
            data["receipt_number"] = " / ".join(receipt_codes)
            meta["receipt_number"] = {"source": "ocr", "line_ids": receipt_code_lines.copy()}
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
        if re.search(r"\b(cash|change|tender|payment|card|visa|mastercard|trans|invoice|receipt)\b", text, re.I):
            continue
        match = re.match(
            r"(.+?)\s+(?:(\d+(?:\.\d+)?)\s*(?:([A-Za-z]{1,12})\s+)?[xX@]\s*("
            + AMOUNT + r")\s+)?(" + AMOUNT + r")\s*$", text)
        if match and re.search(r"[A-Za-z]", match[1]) and not re.search(r"\b(date|tel|phone)\b", match[1], re.I):
            index = len(data["items"])
            data["items"].append({"description": match[1].strip(), "quantity": match[2] or "", "unit": match[3] or "unit",
                                  "unit_price": (match[4] or "").replace(",", ""),
                                  "line_total": match[5].replace(",", ""), "item_type": "Materials", "photo_id": ""})
            for field in ("description", "quantity", "unit_price", "line_total"):
                meta[f"items.{index}.{field}"] = {"source": "ocr", "line_ids": row["ids"]}
    return data, meta


def validate(data):
    issues = []

    def issue(field, message, blocking=False):
        issues.append({"id": field, "field": field, "message": message, "blocking": blocking})

    for key in ("supplier", "receipt_date", "total"):
        if not data.get(key):
            issue(key, f"Enter {FIELD_LABELS.get(key, key)} from the receipt.", True)
    if data.get("receipt_date"):
        try:
            if date.fromisoformat(data["receipt_date"]).isoformat() != data["receipt_date"]:
                raise ValueError()
        except ValueError:
            issue("receipt_date", "Use an unambiguous date in YYYY-MM-DD format.", True)
    if data.get("currency") and not re.fullmatch("[A-Z]{3}", data["currency"]):
        issue("currency", "Use a three-letter uppercase currency code, such as PHP.", True)
    numbers = {}
    for key in ("total",):
        if data.get(key):
            try:
                numbers[key] = decimal(data[key])
            except ValueError as exc:
                issue(key, str(exc), True)
    items = data.get("items", [])
    if data.get("total_discount_percent"):
        try:
            percentage(data["total_discount_percent"])
        except ValueError as exc:
            issue("total_discount_percent", str(exc), True)
    item_totals = []
    for i, item in enumerate(items):
        prefix = f"items.{i}"
        percent = effective_discount(item, data)
        valid_discount = True
        if percent:
            try:
                percentage(percent)
            except ValueError as exc:
                valid_discount = False
                if item.get("discount_percent"):
                    issue(prefix + ".discount_percent", str(exc), True)
        if not item["description"]:
            issue(prefix + ".description", f"Item {i + 1} needs a description.", True)
        if item.get("unit") and not re.search(r"[A-Za-z]", item["unit"]):
            issue(prefix + ".unit", "Unit must name a unit such as pcs, mtrs, boxes, gals, or kgs; it cannot be a number.", True)
        values = {}
        for key in ("quantity", "unit_price", "line_total"):
            if item[key]:
                try:
                    values[key] = decimal(item[key])
                    if key == "quantity" and values[key] != values[key].to_integral_value():
                        issue(prefix + ".quantity", "Quantity must be a whole number. Check the original.", True)
                    elif key == "quantity" and abs(values[key]) >= 100:
                        issue(prefix + ".quantity", "Quantity is unusually large. Check the original for extra zeros or a misread decimal; keep it if correct.")
                except ValueError as exc:
                    issue(prefix + "." + key, str(exc), True)
        if "line_total" in values:
            item_totals.append(values["line_total"])
        else:
            issue(prefix + ".line_total", f"Line {i + 1} has no Amount.", True)
        if not item["quantity"] or not item["unit_price"]:
            issue(prefix + ".pricing", f"Item {i + 1}: quantity or unit price is missing. Check the original.", bool(percent))
        if percent and valid_discount and len(values) == 3 and values["quantity"] == values["quantity"].to_integral_value():
            if discounted_amount(item, data) != values["line_total"]:
                issue(prefix + ".arithmetic", f"Line {i + 1}: Amount does not match Quantity × discounted Unit price. "
                      "Check Discount (%), Quantity, and Unit price.", True)
        elif not percent and len(values) == 3 and abs(values["quantity"] * values["unit_price"] - values["line_total"]) > Decimal("0.005"):
            difference = values["quantity"] * values["unit_price"] - values["line_total"]
            message = (f"Line {i + 1}: Amount is {difference:.2f} below Quantity × Unit price; check a possible discount."
                       if difference > 0 else f"Line {i + 1}: Amount exceeds Quantity × Unit price. Check the values.")
            issue(prefix + ".arithmetic", message)
    if not items:
        issue("items", "Add one or more line items.", True)
    elif len(item_totals) == len(items) and "total" in numbers:
        difference = sum(item_totals) - numbers["total"]
        if abs(difference) > Decimal("0.005"):
            message = (f"Line Amounts exceed Total by {difference:.2f}. This may be an implicit discount; "
                       "confirm against the receipt before allocating it." if difference > 0 else
                       f"Total exceeds line Amounts by {-difference:.2f}. Check missing items, prices, or charges.")
            issue("item_sum", message)
    return issues


def reconcile(data):
    try:
        amounts = [decimal(item["line_total"]) for item in data["items"]]
        if not amounts:
            return None
        total = decimal(data["total"])
        difference = sum(amounts) - total
        return {"item_sum": str(sum(amounts)), "total": str(total),
                "possible_discount": str(difference) if difference > Decimal("0.005") else "",
                "can_allocate": not data.get("total_discount_percent") and not any(
                    item.get("discount_percent") for item in data["items"]) and difference > Decimal("0.005") and total >= 0 and all(
                    amount >= 0 and decimal(item["quantity"]) > 0
                    for amount, item in zip(amounts, data["items"]))}
    except (ValueError, KeyError):
        return None


def allocate_discount(data):
    result = reconcile(data)
    if not result or not result["can_allocate"]:
        raise ValueError("A positive difference, nonnegative Amounts, and positive Quantities are needed.")
    target = decimal(data["total"])
    if target != target.quantize(Decimal("0.01")):
        raise ValueError("Total must use no more than two decimal places for allocation.")
    note = f"Confirmed discount {result['possible_discount']} allocated across line items from Total."
    notes = (data.get("notes", "") + "\n" + note).strip()
    if len(notes) > 4000:
        raise ValueError("Shorten Notes before applying the discount.")
    amounts = [decimal(item["line_total"]) for item in data["items"]]
    subtotal = sum(amounts)
    target_cents = int(target * 100)
    shares = [Decimal(target_cents) * amount / subtotal for amount in amounts]
    cents = [int(share.to_integral_value(rounding=ROUND_DOWN)) for share in shares]
    order = sorted(range(len(cents)), key=lambda i: shares[i] - cents[i], reverse=True)
    for index in order[:target_cents - sum(cents)]:
        cents[index] += 1
    for item, amount in zip(data["items"], cents):
        net = Decimal(amount) / 100
        try:
            unit_price = (net / decimal(item["quantity"])).quantize(Decimal("0.000000000001"), rounding=ROUND_HALF_UP)
            decimal(format(unit_price, "f"))
        except (InvalidOperation, ValueError) as exc:
            raise ValueError("This quantity needs more price precision. Adjust this line manually.") from exc
        if (unit_price * decimal(item["quantity"])).quantize(Decimal("0.01")) != net:
            raise ValueError("This quantity needs more price precision. Adjust this line manually.")
        item["unit_price"] = format(unit_price.normalize(), "f")
        item["line_total"] = f"{net:.2f}"
    data["notes"] = notes
    return data


def receipt_issues(receipt):
    issues = validate(receipt["data"])
    scores = {line["id"]: line["score"] for line in receipt["evidence"]["lines"]}
    for field, evidence in receipt["field_meta"].items():
        if field not in ("supplier", "receipt_date", "receipt_number", "total") and not field.startswith("items."):
            continue
        if evidence.get("source") != "ocr":
            continue
        if any(scores.get(line_id, 1) < 0.8 for line_id in evidence.get("line_ids", [])):
            issues.append({"id": field + ".ocr", "field": field,
                           "message": f"Check {FIELD_LABELS.get(field, field.replace('_', ' '))}: OCR had difficulty reading its source.",
                           "blocking": False})
    return issues
