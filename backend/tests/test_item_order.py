"""Item ordering must retain identity, attachments and source warnings across saves."""

import copy
import json

from test_workflow import client as client_fixture
from test_workflow import image_bytes, ready_receipt

from app.models import Item
from app.service import csv_rows

client = client_fixture


def test_reorder_insert_delete_keep_item_evidence_and_photos(client):
    receipt, _ = ready_receipt(client)
    receipt_id = receipt["id"]
    original = copy.deepcopy(receipt["data"]["items"])
    source_ids = receipt["field_meta"]["items.0.description"]["line_ids"]
    evidence = receipt["evidence"]
    for line in evidence["lines"]:
        if line["id"] in source_ids:
            line["score"] = 0.5
    client.store.execute("UPDATE extraction_runs SET result_json=? WHERE receipt_id=? AND engine NOT LIKE 'Ollama%'",
                         (json.dumps(evidence), receipt_id))
    photo = client.post(f"/api/v1/receipts/{receipt_id}/photos",
                        files={"file": ("item.png", image_bytes(), "image/png")})
    assert photo.status_code == 201, photo.text
    photo_id = photo.json()["id"]

    def save(items):
        current = client.get(f"/api/v1/receipts/{receipt_id}").json()
        current["data"]["items"] = items
        response = client.patch(f"/api/v1/receipts/{receipt_id}",
                                json={"revision": current["revision"], "data": current["data"]})
        assert response.status_code == 200, response.text
        return response.json()

    original[0]["photo_id"] = photo_id
    reordered = save(list(reversed(original)))
    assert reordered["data"]["items"] == list(reversed(original))
    assert reordered["field_meta"]["items.1.description"]["line_ids"] == source_ids
    assert any(issue["id"] == "items.1.description.ocr" for issue in reordered["issues"])
    assert not any(issue["id"] == "items.0.description.ocr" for issue in reordered["issues"])
    assert [row["Description"] for row in csv_rows([reordered], client.store)] == ["Bread", "Coffee"]

    inserted = Item(description="New line", quantity="1", unit="unit", unit_price="0", line_total="0").model_dump()
    changed = save([reordered["data"]["items"][0], inserted, reordered["data"]["items"][1]])
    assert changed["field_meta"]["items.2.description"]["line_ids"] == source_ids
    assert any(issue["id"] == "items.2.description.ocr" for issue in changed["issues"])
    assert "items.1.description" not in changed["field_meta"]
    assert changed["data"]["items"][2]["photo_id"] == photo_id
    assert changed["data"]["items"][2]["item_number"] == original[0]["item_number"]

    changed["data"]["items"][2]["description"] = "Corrected coffee"
    corrected = save(changed["data"]["items"])
    assert corrected["field_meta"]["items.2.description"] == {"source": "manual", "line_ids": source_ids}
    assert not any(issue["id"] == "items.2.description.ocr" for issue in corrected["issues"])
    assert any(issue["id"] == "items.2.quantity.ocr" for issue in corrected["issues"])

    removed = save(corrected["data"]["items"][:2])
    assert not any(field.startswith("items.2.") for field in removed["field_meta"])
    assert all(item["id"] != original[0]["id"] for item in removed["data"]["items"])
