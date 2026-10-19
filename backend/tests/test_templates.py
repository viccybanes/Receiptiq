"""CSV template persistence, formatting, validation, and spreadsheet safety."""

import csv
import io

import pytest
import test_workflow
from test_workflow import ready_receipt

from app.service import COLUMNS as SCREENSHOT_COLUMNS
from app.service import csv_rows
from app.templates import (
    DEFAULT_TEMPLATE,
    TemplateConflict,
    delete_template,
    get_template,
    list_templates,
    render_rows,
    required_issues,
    save_template,
    serialize_rows,
    validate_template,
)

client = test_workflow.client


def test_default_preserves_screenshot_order_and_csv(client):
    receipt, _ = ready_receipt(client)
    template = get_template(client.store)
    assert [column["header"] for column in template["columns"]] == SCREENSHOT_COLUMNS
    rows = render_rows([receipt], client.store, template, csv_rows)
    assert rows == [{key: str(value) for key, value in row.items()} for row in csv_rows([receipt], client.store)]
    content = serialize_rows(rows, template)
    assert content.startswith(b"\xef\xbb\xbf")
    assert content.decode("utf-8-sig").splitlines()[0] == ",".join(SCREENSHOT_COLUMNS)
    assert len(list(csv.DictReader(io.StringIO(content.decode("utf-8-sig"))))) == 2


def test_order_renames_constants_date_decimal_and_encodings(client):
    receipt, _ = ready_receipt(client)
    template = validate_template({"id": "accounting", "name": "Accounting", "columns": [
        {"header": "Cost", "field": "Amount"}, {"header": "When", "field": "Purchase date"},
        {"header": "Supplier name", "field": "Vendor"}, {"header": "Department", "constant": "Café, pantry"}],
        "delimiter": ";", "encoding": "utf-8", "date_format": "DDMMYYYY", "decimal_separator": ","})
    rows = render_rows([receipt], client.store, template, csv_rows)
    assert list(rows[0]) == ["Cost", "When", "Supplier name", "Department"]
    assert rows[0] == {"Cost": "100,00", "When": "19/10/2026", "Supplier name": "NORTHSIDE MARKET",
                       "Department": "Café, pantry"}
    content = serialize_rows(rows, template)
    assert not content.startswith(b"\xef\xbb\xbf")
    assert list(csv.DictReader(io.StringIO(content.decode()), delimiter=";")) == rows
    template.update(delimiter="\t", date_format="MMDDYYYY")
    assert render_rows([receipt], client.store, template, csv_rows)[0]["When"] == "10/19/2026"
    assert b"Cost\tWhen" in serialize_rows(rows, template)


def test_receipt_mode_aggregates_descriptions_without_first_item_prices(client):
    receipt, _ = ready_receipt(client)
    template = {**DEFAULT_TEMPLATE, "row_mode": "receipt"}
    rows = render_rows([receipt], client.store, template, csv_rows)
    assert len(rows) == 1
    assert rows[0]["Description"] == "Coffee; Bread"
    assert rows[0]["Total"] == rows[0]["Amount"] == "125.00"
    for field in ("Item number", "Quantity", "Unit", "Unit price", "Type", "Photo of item purchased"):
        assert rows[0][field] == ""


def test_every_text_cell_and_constant_is_formula_safe_negative_decimals_preserved(client):
    receipt, _ = ready_receipt(client)
    template = validate_template({"columns": [
        {"header": "Vendor", "field": "Vendor"}, {"header": "Amount", "field": "Amount"},
        {"header": "Photo", "field": "Receipt photo"}, {"header": "Literal", "constant": " \t=SUM(A1)"}]})
    bases = [{"Receipt ID": receipt["id"], "Vendor": "\t+COMMAND", "Amount": "-12.50", "Receipt photo": "@bad"},
             {"Receipt ID": receipt["id"], "Vendor": "-12.50", "Amount": "=1+1", "Receipt photo": "-photo"}]
    rows = render_rows([receipt], client.store, template, bases)
    assert rows[0] == {"Vendor": "'\t+COMMAND", "Amount": "-12.50", "Photo": "'@bad", "Literal": "' \t=SUM(A1)"}
    assert rows[1]["Vendor"] == "'-12.50" and rows[1]["Amount"] == "'=1+1"
    assert rows[1]["Photo"] == "'-photo"
    # Serialization is also a trust boundary if a caller supplies rows directly.
    raw = serialize_rows([{"Vendor": "=evil", "Amount": "-1.25", "Photo": "@bad", "Literal": "-3"}], template)
    row = next(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))))
    assert row == {"Vendor": "'=evil", "Amount": "-1.25", "Photo": "'@bad", "Literal": "'-3"}


@pytest.mark.parametrize("payload", [
    {"columns": []}, {"columns": [{"header": "Same", "field": "Vendor"}, {"header": " same ", "field": "Total"}]},
    {"columns": [{"header": "Run", "expression": "__import__('os')"}]},
    {"columns": [{"header": "X", "field": "Vendor", "constant": "X"}]},
    {"columns": [{"header": "=CMD", "constant": "safe"}]}, {"columns": ["Unknown"]},
    {"columns": [{"header": "X", "constant": 1}]}, {"delimiter": "|"}, {"encoding": "latin-1"},
    {"row_mode": "expression"}, {"required_fields": ["unknown"]}, {"required_fields": ["Vendor", "Vendor"]},
    {"date_format": "%x"}, {"decimal_separator": "::"}, {"version": True}, {"executable": "python"},
])
def test_templates_reject_unsafe_or_ambiguous_definitions(payload):
    with pytest.raises(ValueError):
        validate_template(payload)


def test_required_fields_and_versioned_persistence(client):
    receipt, _ = ready_receipt(client)
    template = save_template(client.store, {"id": "finance", "name": "Finance", "columns": ["Vendor", "Total"],
                                             "required_fields": ["Receipt number", "Photo of item purchased"]})
    assert template["version"] == 1
    assert get_template(client.store, "finance") == template
    assert len(list_templates(client.store)) == 2
    assert get_template(client.store, "missing") is None
    issues = required_issues([receipt], template)
    assert len(issues) == 2 and all(issue["blocking"] and issue["field"] == "Photo of item purchased" for issue in issues)
    receipt["data"]["receipt_number"] = ""
    assert any(issue["field"] == "Receipt number" for issue in required_issues([receipt], template))
    updated = save_template(client.store, {**template, "name": "Finance updated"})
    assert updated["version"] == 2
    with pytest.raises(TemplateConflict):
        save_template(client.store, template)
    with pytest.raises(TemplateConflict):
        save_template(client.store, {"id": "finance", "name": "Version omitted"})
    with pytest.raises(TemplateConflict):
        save_template(client.store, {"id": "deleted", "name": "Deleted"}, expected_version=3)
    with pytest.raises(ValueError):
        save_template(client.store, DEFAULT_TEMPLATE)
    with pytest.raises(TemplateConflict):
        delete_template(client.store, "finance", 1)
    assert delete_template(client.store, "finance", 2)
    assert not delete_template(client.store, "finance", 2)
    assert template["name"] == "Finance"  # Saved export snapshots remain independent of later template edits.
