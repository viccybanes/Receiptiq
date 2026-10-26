"""Synthetic layout and local-response tests, not receipt accuracy measurements."""

import base64
import copy
import io
import json

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.extraction import parse
from app.layout import group_rows
from app.main import create_app
from app.vision import MAX_OCR_CONTEXT_CHARS, VisionEngine, VisionError, _ocr_context


def fragment(text, box, page=0, score=0.98):
    return {"text": text, "box": box, "page": page, "score": score, "id": text}


@pytest.mark.parametrize("scale", [0.5, 1, 4])
def test_small_adjacent_rows_and_large_column_offsets(scale):
    lines = [fragment("Nuts", [10, 10, 70, 18]), fragment("1.00", [200, 11, 230, 19]),
             fragment("Bolts", [10, 20, 70, 28]), fragment("2.00", [200, 21, 230, 29])]
    for line in lines:
        line["box"] = [value * scale for value in line["box"]]
    before = copy.deepcopy(lines)
    data, meta = parse(lines)
    assert [(item["description"], item["line_total"]) for item in data["items"]] == [("Nuts", "1.00"), ("Bolts", "2.00")]
    assert meta["items.0.line_total"]["line_ids"] == ["Nuts", "1.00"]
    assert lines == before


def test_polygon_tilt_corrects_price_row_shift_and_preserves_page_separation():
    lines = []
    for page in (0, 1):
        for y, name, price in [(40, "Nuts", "1.00"), (70, "Bolts", "2.00")]:
            for left, right, text in [(10, 180, name), (600, 650, price)]:
                points = [[left, y + left * .04], [right, y + right * .04],
                          [right, y + right * .04 + 12], [left, y + left * .04 + 12]]
                line = fragment(text, [left, points[0][1], right, points[2][1]], page)
                line["polygon"] = points
                lines.append(line)
    rows = group_rows(list(reversed(lines)))
    assert [(row["page"], row["text"]) for row in rows] == [(0, "Nuts 1.00"), (0, "Bolts 2.00"),
                                                          (1, "Nuts 1.00"), (1, "Bolts 2.00")]


def test_overlapping_column_lines_and_invalid_boxes_do_not_merge():
    lines = [fragment("Cable 1.00", [10, 20, 200, 40]), fragment("Cable 1.00", [10, 30, 200, 50]),
             fragment("invalid", [0, 0, float("nan"), 4]), fragment("backwards", [4, 0, 0, 4])]
    lines[0]["polygon"] = [[0, float("nan")]] * 4
    assert [row["text"] for row in group_rows(lines)] == ["Cable 1.00", "Cable 1.00"]


def test_context_keeps_geometry_confidence_and_excludes_other_pages_and_crop_edges():
    lines = [fragment("Cable", [100, 200, 300, 220]), fragment("10.00", [500, 200, 600, 220]),
             fragment("crosses crop", [0, 200, 300, 220]), fragment("wrong page", [100, 200, 300, 220], 2)]
    context = _ocr_context(lines, [0.1, 0.1, 0.9, 0.9], [(800, 1000)])
    assert [line["text"] for line in context] == ["Cable", "10.00"]
    assert {line["row_id"] for line in context} == {0}
    assert context[0]["box"] == pytest.approx([.0312, .125, .3437, .15], abs=.0001)
    assert context[0]["confidence"] == .98
    assert all(line["page"] == 0 for line in context)


def test_context_is_bounded_and_unknown_confidence_is_not_invented():
    lines = [fragment("Cable " * 400, [10, i * 20, 700, i * 20 + 15], score=float("nan")) for i in range(100)]
    context = _ocr_context(lines, None, [(800, 2100)])
    assert len(json.dumps(context, ensure_ascii=False)) <= MAX_OCR_CONTEXT_CHARS
    assert 0 < len(context) < len(lines)
    assert all("confidence" not in line for line in context)


def test_environment_switch_restores_text_only_context_and_legacy_grouping(monkeypatch):
    monkeypatch.setenv("RECEIPTIQ_LAYOUT_ASSISTANCE", "0")
    lines = [fragment("Cable", [10, 20, 200, 30]), fragment("2.00", [300, 30, 400, 40])]
    assert _ocr_context(lines, None, [(800, 1000)]) == [{"text": "Cable"}, {"text": "2.00"}]
    assert len(group_rows(lines)) == 1


@pytest.fixture
def focused_engine(tmp_path, monkeypatch):
    path = tmp_path / "synthetic.png"
    Image.new("RGB", (800, 1000), "white").save(path)
    state = {"requests": [], "replies": []}
    engine = VisionEngine()

    def request(payload, stop):
        state["requests"].append(copy.deepcopy(payload))
        value = state["replies"].pop(0)
        if isinstance(value, Exception):
            raise value
        return value, False

    monkeypatch.setattr(engine, "_read_receipt", request)
    return engine, path, state


def receipt(amount="", price="5", name="Cable"):
    from app.vision import VisionReceipt
    return VisionReceipt.model_validate({"visible_item_count": 1, "items": [
        {"description": name, "quantity": "2", "unit": "pcs", "unit_price": price, "line_total": amount}]})


def test_focused_reread_fills_only_missing_amount_and_does_not_change_header(focused_engine):
    engine, page, state = focused_engine
    first, second = receipt(), receipt("10.00")
    first.supplier, second.supplier = "Original", "Hallucinated header"
    state["replies"] = [first, second]
    lines = [fragment("Cable 2 pcs 5.00 10.00", [80, 200, 720, 230])]
    result = engine.extract([page], lines)
    assert result["draft"]["supplier"] == "Original"
    assert result["draft"]["items"][0]["line_total"] == "10.00"
    assert result["provenance"]["focused_recheck"]["status"] == "filled"
    assert result["provenance"]["row_rechecks"] == 0 and len(state["requests"]) == 2
    with Image.open(io.BytesIO(base64.b64decode(state["requests"][1]["messages"][1]["images"][0]))) as image:
        assert image.height < 100 and image.width < 800
    assert "focused image reread" in result["notice"] and result["source_regions"] == []


@pytest.mark.parametrize(("reread", "status"), [(receipt("10", "6"), "conflict"),
    (receipt("99"), "unresolved"), (receipt("5"), "unresolved"), (receipt("10", name="Other"), "unresolved"),
    (VisionError("offline"), "unavailable")])
def test_focused_reread_preserves_values_on_conflict_or_failure(focused_engine, reread, status):
    engine, page, state = focused_engine
    state["replies"] = [receipt(), reread]
    result = engine.extract([page], [fragment("Cable 2 pcs 5.00 10.00", [80, 200, 720, 230])])
    item = result["draft"]["items"][0]
    assert item["line_total"] == "" and item["unit_price"] == "5"
    assert result["provenance"]["focused_recheck"]["status"] == status


@pytest.mark.parametrize("case", ["complete", "low_confidence", "ambiguous", "manual_amount", "disabled"])
def test_focused_reread_skips_unnecessary_or_ambiguous_work(focused_engine, monkeypatch, case):
    engine, page, state = focused_engine
    state["replies"] = [receipt("10" if case == "complete" else "")]
    lines = [fragment("Cable 2 pcs 5.00 10.00", [80, 200, 720, 230], score=.7 if case == "low_confidence" else .98)]
    if case == "ambiguous":
        lines.append(fragment("Cable 2 pcs 5.00 10.00", [80, 300, 720, 330]))
    if case == "disabled":
        monkeypatch.setenv("RECEIPTIQ_LAYOUT_ASSISTANCE", "0")
    current = {"items": [{"description": "Cable", "line_total": "9.00"}]} if case == "manual_amount" else None
    result = engine.extract([page], lines, current=current)
    assert len(state["requests"]) == 1 and result["provenance"]["focused_recheck"] is None
    if current:
        assert result["draft"]["items"][0]["line_total"] == "9.00"


def test_completeness_recheck_and_focus_share_two_call_budget(focused_engine):
    engine, page, state = focused_engine
    first = receipt()
    first.visible_item_count = 2
    state["replies"] = [first, receipt()]
    result = engine.extract([page], [fragment("Cable 2 pcs 5.00 10.00", [80, 200, 720, 230])])
    assert len(state["requests"]) == 2 and result["provenance"]["focused_recheck"] is None


def test_focused_reread_propagates_cancellation(focused_engine):
    engine, page, state = focused_engine
    state["replies"] = [receipt(), VisionError("cancelled")]
    with pytest.raises(VisionError, match="cancel"):
        engine.extract([page], [fragment("Cable 2 pcs 5.00 10.00", [80, 200, 720, 230])],
                       stop=lambda: len(state["requests"]) == 2)


def test_focus_uses_selected_page_and_respects_user_crop(focused_engine):
    engine, page, state = focused_engine
    state["replies"] = [receipt(), receipt("10.00")]
    result = engine.extract([page, page], [fragment("Other 3.00", [80, 200, 720, 230]),
                                          fragment("Cable 2 pcs 5.00 10.00", [80, 200, 720, 230], page=1)])
    assert result["provenance"]["focused_recheck"]["page_index"] == 1
    focused_context = json.loads(state["requests"][1]["messages"][1]["content"])
    assert all(line["page"] == 0 for line in focused_context["ocr_lines"])
    state["replies"] = [receipt(), receipt("10.00")]
    result = engine.extract([page], [fragment("Cable 2 pcs 5.00 10.00", [80, 200, 720, 230])],
                            crop=[.1, .2, .9, .24])
    assert result["provenance"]["focused_recheck"]["box"][1] >= .2
    assert result["provenance"]["focused_recheck"]["box"][3] <= .24


@pytest.mark.parametrize("mode", ["enabled", "disabled", "unavailable", "cancelled", "edited"])
def test_worker_layout_is_independent_of_outlines_and_preserves_lifecycle(tmp_path, monkeypatch, mode):
    app = create_app(tmp_path / "workspace", start_worker=False)
    worker = app.state.worker
    calls = {"ocr": 0, "vision": 0}
    monkeypatch.setattr(worker.retrieval, "search", lambda *_: [])
    if mode == "disabled":
        monkeypatch.setenv("RECEIPTIQ_LAYOUT_ASSISTANCE", "0")

    def recognize(path):
        calls["ocr"] += 1
        if mode == "unavailable":
            raise RuntimeError("No installed OCR models")
        if mode == "cancelled":
            worker.stop.set()
        if mode == "edited":
            worker.store.execute("UPDATE receipts SET revision=revision+1 WHERE id=?", (receipt_id,))
        return [fragment("Cable 2 pcs 5.00 10.00", [80, 200, 720, 230])]

    def read(payload, stop):
        calls["vision"] += 1
        context = json.loads(payload["messages"][1]["content"])
        assert bool(context["ocr_lines"]) == (mode in ("enabled", "edited"))
        assert "source_box" not in payload["format"]["$defs"]["VisionItem"]["properties"]
        return receipt("10.00"), False

    monkeypatch.setattr(worker.engine, "recognize", recognize)
    monkeypatch.setattr(worker.vision, "_read_receipt", read)
    stream = io.BytesIO()
    Image.new("RGB", (800, 1000), "white").save(stream, format="PNG")
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        client.headers["Authorization"] = "Bearer " + client.get("/api/v1/session").json()["token"]
        batch = client.post("/api/v1/batches", json={"files": ["synthetic.png"]}).json()
        upload = client.post("/api/v1/receipts",
                             params={"batch_id": batch["id"], "upload_id": batch["files"][0]["id"], "auto_ocr": False},
                             files={"file": ("synthetic.png", stream.getvalue(), "image/png")})
        assert upload.status_code == 202
        receipt_id = upload.json()["id"]
        assert worker.process_one() and worker.process_one()
        saved = client.get("/api/v1/receipts/" + receipt_id).json()
        assert calls["ocr"] == (0 if mode == "disabled" else 1)
        if mode == "cancelled":
            assert calls["vision"] == 0 and saved["candidate"] is None
        else:
            assert calls["vision"] == 1 and saved["candidate"]["source_regions"] == []
            assert saved["status"] != "verified"
            if mode == "edited":
                assert not saved["data"]["items"] and not saved["candidate"].get("auto_applied")
            else:
                assert saved["data"]["items"][0]["line_total"] == "10.00"
            if mode == "unavailable":
                assert saved["candidate"]["ocr_assistance"]["status"] == "unavailable"
                assert "OCR layout assistance was unavailable" in saved["candidate"]["notice"]
