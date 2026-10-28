"""Controlled evidence for detail-reading bounds, fallback, and human-value safety."""

import copy
import json
import threading

import pytest
from PIL import Image

from app.layout import detail_bands, item_table_band
from app.vision import (
    MAX_OCR_PROMPT_CHARS,
    VisionEngine,
    VisionError,
    VisionReceipt,
    _compact_ocr_context,
    _normalize_candidate,
    _ocr_context,
    _runtime_error,
)


def dense_lines(count=10, score=.7):
    lines = []
    for i in range(count):
        for left, right, text in [(50, 320, f"Part {i}"), (430, 490, "2 pcs"),
                                  (560, 620, "5.00"), (700, 760, "10.00")]:
            lines.append({"text": text, "score": score, "box": [left, 100 + i * 30, right, 120 + i * 30]})
    return lines


def short_table_lines():
    lines = dense_lines(6)
    for left, right, text in [(50, 320, "Item Description/"), (430, 490, "Quantity"), (700, 760, "Amount")]:
        lines.append({"text": text, "score": .99, "box": [left, 60, right, 80]})
    lines.append({"text": "VATable Sales", "score": .99, "box": [50, 400, 200, 420]})
    return lines


def test_uncertain_short_table_uses_headers_without_splitting_rows():
    bands = item_table_band(short_table_lines(), [(800, 1000)])
    assert bands == [[0, .04, 1, .4]]
    assert all(bands[0][1] * 1000 < line["box"][1] < line["box"][3] < bands[0][3] * 1000
               for line in dense_lines(6))


@pytest.mark.parametrize("case", ["no_footer", "two_headers", "confident", "unknown", "multiple_pages"])
def test_table_focus_skips_ambiguous_or_confident_layouts(case):
    lines = short_table_lines()
    if case == "no_footer":
        lines.pop()
    elif case == "two_headers":
        lines.append({"text": "Item Description", "score": .99, "box": [50, 500, 320, 520]})
    elif case in ("confident", "unknown"):
        for line in lines[:24]:
            line["score"] = .99 if case == "confident" else float("nan")
    dimensions = [(800, 1000)] * (2 if case == "multiple_pages" else 1)
    assert item_table_band(lines, dimensions) == []


def test_complete_table_read_survives_wrong_model_count_with_visible_warning(detail_engine):
    engine, path, state = detail_engine
    table = response(["Cable", "Bolts"])
    table.visible_item_count = 1
    table.items[0].quantity, table.items[0].unit = "2 pcs", "per"
    state["responses"] = [response(["Cable", "Total Sales"]), table]
    result = engine.extract([path], short_table_lines())
    assert [item["description"] for item in result["draft"]["items"]] == ["Cable", "Bolts"]
    assert result["draft"]["items"][0]["quantity"] == "2"
    assert result["draft"]["items"][0]["unit"] == "pcs"
    assert result["row_audit"]["mismatch"] and "Item row count needs review" in result["notice"]
    assert result["row_audit"]["scope"] == "crop"
    assert result["provenance"]["detail_read"]["kind"] == "table"
    assert result["provenance"]["detail_read"]["status"] == "read"
    assert len(state["requests"]) == 2 and result["provenance"]["ocr_context_lines"] == 0


@pytest.mark.parametrize("failure", ["partial", "blank_row", "unavailable"])
def test_unusable_table_read_keeps_full_page_fallback(detail_engine, failure):
    engine, path, state = detail_engine
    table = response(["New reading"])
    if failure == "partial":
        table = table, True
    elif failure == "blank_row":
        table.items[0].description = table.items[0].quantity = table.items[0].unit_price = table.items[0].line_total = ""
    else:
        table = VisionError("Unavailable")
    state["responses"] = [response(["Original"]), table]
    result = engine.extract([path], short_table_lines())
    assert [item["description"] for item in result["draft"]["items"]] == ["Original"]
    assert result["provenance"]["detail_read"]["status"] == "unresolved"


def response(names, amount="10"):
    return VisionReceipt(supplier="Initial vendor", total="100", visible_item_count=len(names), items=[
        {"description": name, "quantity": "2", "unit": "pcs", "unit_price": "5", "line_total": amount}
        for name in names])


@pytest.fixture
def detail_engine(tmp_path, monkeypatch):
    path = tmp_path / "synthetic.png"
    Image.new("RGB", (800, 1000), "white").save(path)
    engine = VisionEngine()
    state = {"requests": [], "responses": []}

    def read(payload, stop):
        state["requests"].append(copy.deepcopy(payload))
        result = state["responses"].pop(0)
        if isinstance(result, Exception):
            raise result
        return result if isinstance(result, tuple) else (result, False)

    monkeypatch.setattr(engine, "_read_receipt", read)
    return engine, path, state


@pytest.mark.parametrize("count", [8, 10, 15, 20])
def test_bands_cover_whole_page_once_with_bounded_requests(count):
    bands = detail_bands(dense_lines(count), [(800, 1000)])
    assert 2 <= len(bands) <= 4
    assert bands[0][1] == 0 and bands[-1][3] == 1
    assert all(first[3] == second[1] for first, second in zip(bands, bands[1:]))
    assert all(box[0] == 0 and box[2] == 1 for box in bands)


@pytest.mark.parametrize("case", ["short", "too_long", "confident", "multi_page", "unknown_scores", "two_tables"])
def test_detail_plan_skips_unsupported_layouts(case):
    lines = dense_lines(7 if case == "short" else 21 if case == "too_long" else 10,
                        .99 if case == "confident" else .7)
    dimensions = [(800, 1000)] * (2 if case == "multi_page" else 1)
    if case == "unknown_scores":
        for line in lines:
            line["score"] = float("nan")
    if case == "two_tables":
        extra = copy.deepcopy(lines)
        for line in extra:
            line["box"][1] += 500
            line["box"][3] += 500
        lines += extra
    assert detail_bands(lines, dimensions) == []


def test_detached_amount_does_not_hide_dense_table():
    lines = dense_lines()
    lines.append({"text": "10.00", "score": .6, "box": [700, 240, 760, 260]})
    bands = detail_bands(lines, [(800, 1000)])
    assert len(bands) == 2
    assert bands[0][1] == 0 and bands[0][3] == bands[1][1] and bands[1][3] == 1


def test_stray_fragments_do_not_bridge_a_real_table_gap():
    lines = dense_lines()
    for line in lines[20:]:
        line["box"][1] += 150
        line["box"][3] += 150
    lines.extend({"text": "10.00", "score": .6, "box": [700, y, 760, y + 20]}
                 for y in range(250, 400, 25))
    assert detail_bands(lines, [(800, 1000)]) == []


def test_section_cuts_use_page_coordinates_after_tilt_grouping():
    lines = dense_lines()
    for line in lines:
        left, top, right, bottom = line["box"]
        # Deliberately tilted columns: projected baselines are not image pixels.
        line["polygon"] = [[left, top + .06 * left], [right, top + .06 * right],
                           [right, bottom + .06 * right], [left, bottom + .06 * left]]
        line["box"] = [left, top + .06 * left, right, bottom + .06 * right]
    bands = detail_bands(lines, [(800, 1000)])
    previous_bottom = max(line["box"][3] for line in lines[16:20])
    next_top = min(line["box"][1] for line in lines[20:24])
    assert bands[0][3] * 1000 == pytest.approx((previous_bottom + next_top) / 2)


def test_equal_counts_cannot_hide_a_lost_full_page_item(detail_engine):
    engine, path, state = detail_engine
    state["responses"] = [response(["Cable", "Tie wire"]), response(["Cable"]), response(["Cancelled entry"])]
    result = engine.extract([path], dense_lines())
    detail = result["provenance"]["detail_read"]
    assert [item["description"] for item in detail["unmatched_full_page_items"]] == ["Tie wire"]
    assert "Tie wire" in result["notice"] and "may be missing" in result["notice"]
    assert [item["description"] for item in result["draft"]["items"]] == ["Cable", "Cancelled entry"]
    assert len(state["requests"]) == 3  # No guessed row or extra inference to force agreement.


def test_inventory_comparison_preserves_repeated_purchases_and_ignores_punctuation(detail_engine):
    engine, path, state = detail_engine
    state["responses"] = [response(["Tie-wire", "Bolts", "Bolts"]), response(["tie wire"]), response(["Bolts"])]
    result = engine.extract([path], dense_lines())
    assert [item["description"] for item in result["provenance"]["detail_read"]["unmatched_full_page_items"]] == ["Bolts"]


def test_compact_context_keeps_columns_unknown_confidence_and_budget():
    lines = dense_lines(120)
    lines[0].pop("score")
    entries = _ocr_context(lines, None, [(800, 4000)])
    compact, count = _compact_ocr_context(entries)
    assert compact[0]["cells"] == [[231, "Part 0", None], [575, "2 pcs", 70],
                                   [738, "5.00", 70], [912, "10.00", 70]]
    assert 4 < count < len(lines)
    assert count == sum(len(row["cells"]) for row in compact)
    assert len(json.dumps(compact, ensure_ascii=False, separators=(",", ":"))) <= MAX_OCR_PROMPT_CHARS
    assert len(json.dumps(compact)) < len(json.dumps(entries))


def test_detail_recovers_rows_preserves_headers_and_does_not_force_money(detail_engine):
    engine, path, state = detail_engine
    first = response(["Old reading"])
    a, b = response(["Repeated"] * 5, "9"), response(["New row"] * 5)
    b.items[-1].quantity = "200"
    a.supplier = b.supplier = "Do not replace header"
    state["responses"] = [first, a, b]
    result = engine.extract([path], dense_lines())
    assert result["draft"]["supplier"] == "Initial vendor" and result["draft"]["total"] == "100"
    assert len(result["draft"]["items"]) == 10
    assert [item["description"] for item in result["draft"]["items"][:5]] == ["Repeated"] * 5
    assert result["draft"]["items"][0]["line_total"] == "9"  # Apparent discount is not repaired.
    assert result["draft"]["items"][-1]["quantity"] == "200"
    assert result["provenance"]["detail_read"]["status"] == "read"
    assert len(state["requests"]) == 3 and result["provenance"]["row_rechecks"] == 0
    assert result["provenance"]["ocr_context_lines"] == 0
    assert json.loads(state["requests"][0]["messages"][1]["content"])["ocr_lines"] == []
    assert all("Part 0" not in json.dumps(payload) for payload in state["requests"])
    assert "supplier" not in state["requests"][1]["format"]["properties"]
    assert "section boundary" in result["notice"] and "discount" in result["notice"]


@pytest.mark.parametrize("failure", ["unavailable", "partial", "mismatch", "empty"])
def test_detail_failure_keeps_entire_initial_extraction(detail_engine, failure):
    engine, path, state = detail_engine
    final = response(["Last row"])
    if failure == "unavailable":
        final = VisionError("Unavailable")
    elif failure == "partial":
        final = final, True
    elif failure == "mismatch":
        final.visible_item_count = 3
    else:
        final = response([])
    state["responses"] = [response(["Original"]), response(["Partial new"]), final]
    result = engine.extract([path], dense_lines())
    assert [item["description"] for item in result["draft"]["items"]] == ["Original"]
    assert result["provenance"]["detail_read"]["status"] == "unresolved"
    assert "kept the earlier full-page" in result["notice"]


def test_detail_cancellation_propagates_without_publishing_partial_data(detail_engine, monkeypatch):
    engine, path, state = detail_engine
    stop = threading.Event()
    original = engine._read_receipt

    def read(payload, token):
        if len(state["requests"]) == 1:
            stop.set()
            raise VisionError("cancelled")
        return original(payload, token)

    monkeypatch.setattr(engine, "_read_receipt", read)
    state["responses"] = [response(["Original"])]
    with pytest.raises(VisionError, match="cancelled"):
        engine.extract([path], dense_lines(), stop=stop)


@pytest.mark.parametrize("mode", ["disabled", "crop"])
def test_manual_crop_and_rollback_do_not_start_detail_pass(detail_engine, monkeypatch, mode):
    engine, path, state = detail_engine
    if mode == "disabled":
        monkeypatch.setenv("RECEIPTIQ_LAYOUT_ASSISTANCE", "0")
    state["responses"] = [response(["Original"])]
    result = engine.extract([path], dense_lines(), crop=[0, 0, 1, .8] if mode == "crop" else None)
    assert result["provenance"]["detail_read"] is None and len(state["requests"]) == 1


def test_detail_preserves_unread_values_identity_and_maps_crop_locations(detail_engine):
    engine, path, state = detail_engine
    first, a, b = response(["Original"]), response(["Cable"]), response(["Bolts"])
    first.supplier = ""
    a.items[0].quantity = a.items[0].unit = a.items[0].unit_price = a.items[0].line_total = ""
    b.items[0].source_page, b.items[0].source_box = 0, [.1, .2, .9, .3]
    state["responses"] = [first, a, b]
    current = {"supplier": "Human vendor", "items": [{"id": "stable-id", "description": "Cable", "quantity": "7",
                                                     "unit": "box", "unit_price": "13", "line_total": "91"}]}
    before = copy.deepcopy(current)
    result = engine.extract([path], dense_lines(), current=current, capture_regions=True)
    item = result["draft"]["items"][0]
    assert result["draft"]["supplier"] == "Human vendor"
    assert (item["id"], item["quantity"], item["unit"], item["unit_price"], item["line_total"]) == (
        "stable-id", "7", "box", "13", "91")
    top = result["provenance"]["detail_read"]["sections"][1]["box"][1]
    region = result["source_regions"][0]
    assert region["box"][1] == pytest.approx(top + .2 * (1 - top))
    assert current == before  # Adapter creates suggestions; applying uses the worker's revision guard.


@pytest.mark.parametrize("amount", ["10", "9"])
def test_shifted_price_uses_read_cell_without_calculating_or_removing_discount(amount):
    candidate = response(["Cable"], amount)
    item = candidate.items[0]
    item.quantity, item.unit, item.unit_price = "2 pcs", "5", amount
    notes = _normalize_candidate(candidate)
    assert (item.quantity, item.unit, item.unit_price, item.line_total) == ("2", "pcs", "5", amount)
    assert any("recovered shifted Unit price" in note for note in notes)


@pytest.mark.parametrize("case", ["no_unit_pair", "conflicting_price", "disabled"])
def test_ambiguous_column_shift_is_not_repaired(case, monkeypatch):
    candidate = response(["Cable"])
    item = candidate.items[0]
    item.quantity, item.unit, item.unit_price = "2 pcs", "5", "10"
    if case == "no_unit_pair":
        item.quantity = "2"
    elif case == "conflicting_price":
        item.unit_price = "7"
    else:
        monkeypatch.setenv("RECEIPTIQ_LAYOUT_ASSISTANCE", "0")
    before = item.unit_price
    notes = _normalize_candidate(candidate)
    assert item.unit_price == before
    assert not any("recovered shifted Unit price" in note for note in notes)


def test_context_overflow_has_an_actionable_error_without_private_text():
    error = _runtime_error('request (10241 tokens) exceeds the available context size (8192 tokens) private text', 400)
    assert "context limit" in str(error) and "private text" not in str(error)
