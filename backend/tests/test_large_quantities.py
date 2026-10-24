import pytest
from fastapi.testclient import TestClient
from test_workflow import Engine, ready_receipt

from app.extraction import validate
from app.main import create_app
from app.models import blank_data


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path, start_worker=False, engine=Engine())
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        client.headers["Authorization"] = "Bearer " + client.get("/api/v1/session").json()["token"]
        client.worker = app.state.worker
        yield client


def issues_for(quantity):
    data = blank_data()
    data.update(supplier="Shop", receipt_date="2026-10-24", total="100.00")
    data["items"] = [{"description": "Item", "quantity": quantity, "unit": "unit",
                      "unit_price": "1.00", "line_total": "100.00", "item_type": "Materials", "photo_id": ""}]
    return validate(data)


def test_large_integer_quantities_get_nonblocking_field_warning():
    for quantity in ("100", "-100", "000100", "100.000"):
        matches = [issue for issue in issues_for(quantity) if issue["field"] == "items.0.quantity"]
        assert len(matches) == 1
        assert not matches[0]["blocking"]
        assert "extra zeros" in matches[0]["message"] and "misread decimal" in matches[0]["message"]

    for quantity in ("99", "-99", "000099"):
        assert not any(issue["field"] == "items.0.quantity" for issue in issues_for(quantity))


def test_fractional_quantity_keeps_only_existing_blocking_issue():
    matches = [issue for issue in issues_for("1000.5") if issue["field"] == "items.0.quantity"]
    assert len(matches) == 1 and matches[0]["blocking"]
    assert "whole number" in matches[0]["message"]


def test_large_quantity_verification_requires_warning_acknowledgement(client):
    receipt, _ = ready_receipt(client)
    receipt["data"]["items"][0]["quantity"] = "100"
    updated = client.patch(f"/api/v1/receipts/{receipt['id']}",
                           json={"revision": receipt["revision"], "data": receipt["data"]}).json()
    assert any(issue["field"] == "items.0.quantity" and not issue["blocking"] for issue in updated["issues"])
    body = {"revision": updated["revision"], "reviewer": "QA", "acknowledged": [], "reason": ""}
    response = client.post(f"/api/v1/receipts/{receipt['id']}/verify", json=body)
    assert response.status_code == 422
    body.update(acknowledged=[issue["id"] for issue in updated["issues"] if not issue["blocking"]],
                reason="Checked the original quantity and amount.")
    response = client.post(f"/api/v1/receipts/{receipt['id']}/verify", json=body)
    assert response.status_code == 200, response.text
