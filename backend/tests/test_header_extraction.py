"""Header selection must not turn item text or later pages into a vendor."""

import pytest

from app.extraction import parse


def lines(texts, page=0):
    return [{"id": str(i), "text": text, "page": page, "box": [0, i * 30, 400, i * 30 + 20]}
            for i, text in enumerate(texts)]


def test_vendor_skips_receipt_title_contact_and_greeting():
    data, meta = parse(lines(["WELCOME", "SALES INVOICE", "Tel: 1234567", "NORTHSIDE HARDWARE",
                             "PIVUT 1 x 20.00 20.00", "TOTAL 20.00"]))
    assert data["supplier"] == "NORTHSIDE HARDWARE"
    assert meta["supplier"]["line_ids"] == ["3"]
    assert data["items"][0]["description"] == "PIVUT"  # OCR never spell-corrects.


def test_missing_vendor_does_not_fall_back_to_items_or_next_page():
    data, _ = parse(lines(["SALES INVOICE", "PIVUT 1 x 20.00 20.00", "TOTAL 20.00"])
                    + lines(["OTHER BUSINESS"], page=1))
    assert data["supplier"] == ""


def test_vendor_skips_address_and_customer_label():
    data, _ = parse(lines(["123 Main Street", "Sold to: Jane", "PIVUT SUPPLY", "TOTAL 20.00"]))
    assert data["supplier"] == "PIVUT SUPPLY"


def test_empty_receipt_has_no_vendor():
    assert parse([])[0]["supplier"] == ""


@pytest.mark.parametrize('text,expected', [
    ('8/27/26', '2026-08-27'), ('27/8/2026', '2026-08-27'),
    ('2026/8/27', '2026-08-27'), ('2026-08-27', '2026-08-27'),
    ('02/29/24', '2024-02-29'), ('02/29/25', ''), ('08/09/26', ''),
])
def test_ocr_purchase_dates(text, expected):
    assert parse(lines(['Hardware', 'Date: ' + text]))[0]['receipt_date'] == expected
