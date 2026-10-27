"""Synthetic review clues never repair numbers, delete purchases, or imply accuracy."""

import copy

import pytest

from app.extraction import validate
from app.models import Item, blank_data


def data_with(amounts):
    data = blank_data()
    data.update(supplier="Test", receipt_date="2026-01-14", total="200")
    data["items"] = [Item(description=f"Part {i}", quantity="2", unit_price=str(price),
                          line_total=str(amount)).model_dump()
                     for i, (price, amount) in enumerate(zip((7, 11, 19, 31, 43), amounts))]
    return data


@pytest.mark.parametrize("amounts,rows", [([22, 38, 62, 62], {0, 1, 2}),
                                        ([14, 14, 22, 38], {1, 2, 3})])
def test_repeated_adjacent_amount_pattern_is_flagged_without_repair(amounts, rows):
    data = data_with(amounts)
    before = copy.deepcopy(data)
    issues = validate(data)
    flagged = {int(issue["field"].split(".")[1]) for issue in issues
               if "shifted Amount column" in issue["message"]}
    assert flagged == rows
    assert data == before
    assert all(not issue["blocking"] for issue in issues if "shifted Amount" in issue["message"])


@pytest.mark.parametrize("amounts", [[14, 22, 38, 62], [22, 38, 37, 61], [13, 21, 37, 61],
                                    ["", 38, 62, 62], ["bad", 38, 62, 62], [-22, -38, -62, -62]])
def test_valid_discounts_sparse_matches_and_invalid_values_do_not_claim_shift(amounts):
    data = data_with(amounts)
    before = copy.deepcopy(data)
    assert not any("shifted Amount column" in issue["message"] for issue in validate(data))
    assert data == before


def test_identical_purchases_remain_distinct_without_shift_warning():
    data = data_with([14, 14, 14, 14])
    for item in data["items"]:
        item["unit_price"] = "7"
    assert not any("shifted Amount column" in issue["message"] for issue in validate(data))
    assert len(data["items"]) == 4


@pytest.mark.parametrize("description,suspicious", [("VATable Sales", True), ("VAT-Exempt Sales", True),
    ("Zero-Rated Sales", True), ("TOTAL AMOUNT DUE", True), ("Received the amount of", True),
    ("VAT", True), ("Total adhesive", False), ("VAT inspection service", False), ("Payment terminal", False)])
def test_summary_label_review_does_not_delete_or_rewrite_item(description, suspicious):
    data = data_with([14])
    data["items"][0]["description"] = description
    before = copy.deepcopy(data)
    assert any("summary or payment label" in issue["message"] for issue in validate(data)) == suspicious
    assert data == before
