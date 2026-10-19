"""Real SQLite checks; local embeddings are deterministic and never need a model download."""

import json

import pytest
import test_workflow
from test_workflow import ready_receipt, verify

from app.retrieval import EMBEDDING_MODEL, MAX_TEXT, OllamaEmbedder, Retrieval, vector

client = test_workflow.client


def verified_example(client, vendor=None):
    receipt, _ = ready_receipt(client)
    if vendor:
        receipt["data"]["supplier"] = vendor
        receipt["data"]["items"][0]["description"] = "Arabica coffee"
        response = client.patch("/api/v1/receipts/" + receipt["id"], json={
            "revision": receipt["revision"], "data": receipt["data"]})
        assert response.status_code == 200, response.text
        receipt = response.json()
    response = verify(client, receipt)
    assert response.status_code == 200, response.text
    client.store.execute("UPDATE receipts SET retrieval_allowed=1 WHERE id=?", (receipt["id"],))
    return client.store.receipt(receipt["id"])


def test_only_current_verified_opted_in_examples_with_citations(client):
    source = verified_example(client, "Northside Market")
    target, _ = ready_receipt(client)
    retrieval = Retrieval(client.store, lambda text: [1, 0])
    assert not retrieval.index(target["id"])
    assert retrieval.index(source["id"])
    result = retrieval.search(target["id"])
    assert len(result) == 1
    assert result[0]["receipt_id"] == source["id"]
    assert result[0]["revision"] == source["revision"]
    assert result[0]["citation"] == f"receipt:{source['id']}@{source['revision']}"
    assert result[0]["vendor"] == "Northside Market" and result[0]["score"] == 1
    assert {"field": "supplier", "before": "NORTHSIDE MARKET", "after": "Northside Market"} in result[0]["corrections"]
    assert result[0]["items"][0]["description"] == "Arabica coffee"
    assert not ({"quantity", "unit_price", "line_total", "total"} & set(result[0]["items"][0]))
    assert retrieval.search(source["id"]) == []
    assert client.store.receipt(target["id"])["data"] == target["data"]
    client.store.execute("UPDATE receipts SET retrieval_allowed=0 WHERE id=?", (source["id"],))
    assert retrieval.search(target["id"]) == []
    assert client.store.read("SELECT * FROM retrieval_entries") == []
    assert not retrieval.index(source["id"])


def test_persistent_index_invalidates_revisions_and_deletion(client):
    source = verified_example(client)
    target, _ = ready_receipt(client)
    assert Retrieval(client.store, lambda text: [1, 0]).index(source["id"])
    reopened = Retrieval(client.store, lambda text: [1, 0])
    assert reopened.search(target["id"])[0]["receipt_id"] == source["id"]
    client.store.execute("UPDATE receipts SET revision=revision+1,verified_revision=NULL,status='needs_review' WHERE id=?",
                         (source["id"],))
    assert reopened.search(target["id"]) == []
    assert not client.store.read("SELECT * FROM retrieval_entries")
    client.store.execute("UPDATE receipts SET status='verified',verified_revision=revision WHERE id=?", (source["id"],))
    assert reopened.index(source["id"])
    client.store.execute("DELETE FROM receipts WHERE id=?", (source["id"],))
    assert not client.store.read("SELECT * FROM retrieval_entries")
    assert reopened.search(target["id"]) == []


def test_live_validation_during_embedding_and_corrupt_vectors(client):
    source = verified_example(client)
    target, _ = ready_receipt(client)

    def opt_out(text):
        client.store.execute("UPDATE receipts SET retrieval_allowed=0 WHERE id=?", (source["id"],))
        return [1, 0]

    assert not Retrieval(client.store, opt_out).index(source["id"])
    assert not client.store.read("SELECT * FROM retrieval_entries")
    client.store.execute("UPDATE receipts SET retrieval_allowed=1 WHERE id=?", (source["id"],))
    retrieval = Retrieval(client.store, lambda text: [1, 0])
    assert retrieval.index(source["id"])
    client.store.execute("UPDATE retrieval_entries SET vector_json='[NaN,0]' WHERE receipt_id=?", (source["id"],))
    assert retrieval.search(target["id"]) == []
    client.store.execute("UPDATE retrieval_entries SET vector_json='not json' WHERE receipt_id=?", (source["id"],))
    assert retrieval.search(target["id"]) == []
    assert Retrieval(client.store, opt_out).search(target["id"]) == []


def test_missing_runtime_does_not_block_verified_csv_and_bounded_sync(client):
    source = verified_example(client)
    second = verified_example(client)
    calls = []

    def offline(text):
        calls.append(text)
        raise ConnectionError("No local model running")

    retrieval = Retrieval(client.store, offline)
    result = retrieval.sync()
    assert result["indexed"] == 0 and result["pending"] == 2 and result["error"]
    assert len(calls) == 1
    assert Retrieval(client.store, lambda text: [1, 0]).index(second["id"])
    assert retrieval.search(source["id"]) == [] and retrieval.last_error
    client.store.execute("DELETE FROM retrieval_entries")
    exported = client.post("/api/v1/exports", json={"receipt_ids": [source["id"]]})
    assert exported.status_code == 201, exported.text
    assert client.store.receipt(source["id"])["status"] == "verified"
    available = Retrieval(client.store, lambda text: [1, 0])
    assert available.sync(max_receipts=1)["indexed"] == 1
    assert available.sync()["indexed"] == 1
    assert available.sync()["indexed"] == 0
    assert available.rebuild(max_receipts=1)["indexed"] == 1
    with pytest.raises(ValueError):
        available.rebuild(0)
    assert len(client.store.read("SELECT * FROM retrieval_entries")) == 1


def test_no_eligible_examples_skips_embedding_runtime(client):
    source = verified_example(client)
    calls = []
    retrieval = Retrieval(client.store, lambda text: calls.append(text) or [1, 0])
    assert retrieval.search(source["id"]) == []
    assert calls == [] and retrieval.last_error == ""
    assert retrieval.index(source["id"])
    calls.clear()
    assert retrieval.search(source["id"]) == []
    assert calls == [] and retrieval.last_error == ""


def test_cosine_order_input_bounds_and_vector_validation(client):
    first, second = verified_example(client, "Coffee store"), verified_example(client, "Bakery")
    target, _ = ready_receipt(client)
    seen = []

    def embed(text):
        seen.append(text)
        return [0, 2] if "Bakery" in text else [3, 0]

    retrieval = Retrieval(client.store, embed)
    assert retrieval.index(second["id"]) and retrieval.index(first["id"])
    assert retrieval.search(target["id"], query="coffee", limit=1)[0]["receipt_id"] == first["id"]
    retrieval.search(target["id"], query="x" * 100_000)
    assert len(seen[-1]) == MAX_TEXT
    with pytest.raises(ValueError):
        retrieval.search(target["id"], limit=21)
    with pytest.raises(ValueError):
        retrieval.search(target["id"], query={"untrusted": "object"})
    for invalid in ([], [0, 0], [float("inf")], [True], ["1"], [1] * 2049):
        with pytest.raises(ValueError):
            vector(invalid)


def test_ollama_loopback_pinned_model_http_contract(monkeypatch):
    requests = []

    class Response:
        status = 200

        def read(self, limit):
            assert limit == 256_001
            return b'{"embeddings":[[3,4]]}'

    class Connection:
        def __init__(self, host, port, timeout):
            assert (host, port, timeout) == ("127.0.0.1", 11435, 30)

        def request(self, method, path, body, headers):
            requests.append((method, path, json.loads(body), headers))

        def getresponse(self):
            return Response()

        def close(self):
            pass

    monkeypatch.setenv("RECEIPTIQ_OLLAMA_PORT", "11435")
    monkeypatch.setattr("app.retrieval.http.client.HTTPConnection", Connection)
    assert OllamaEmbedder().embed("Coffee") == [0.6, 0.8]
    method, path, payload, _ = requests[0]
    assert (method, path) == ("POST", "/api/embed")
    assert payload["model"] == EMBEDDING_MODEL and payload["dimensions"] == 256
    assert payload["input"] == "Coffee" and payload["truncate"] is True
    monkeypatch.setenv("RECEIPTIQ_OLLAMA_PORT", "https://untrusted.example")
    with pytest.raises(ValueError):
        OllamaEmbedder().embed("Coffee")
