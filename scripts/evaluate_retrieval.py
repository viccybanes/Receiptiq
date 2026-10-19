"""Run a small synthetic retrieval benchmark with installed local embeddings, never user receipts."""

import argparse
import copy
import hashlib
import http.client
import io
import ipaddress
import json
import os
import socket
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import psutil
from PIL import Image

from app.db import Store, data_directory, now, uid
from app.models import ReceiptData
from app.retrieval import EMBEDDING_MODEL, Retrieval
from app.system import ensure_runtime, stop_runtime

EXAMPLES = [
    ("market", "Northside Market", "N0RTHSIDE M4RKET", ["Arabica coffee beans", "Whole wheat bread"]),
    ("hardware", "Bayside Hardware", "BAYS1DE HARDW4RE", ["Galvanized steel nails 2 inch", "PVC water pipe"]),
    ("pharmacy", "Riverside Pharmacy", "RIVERS1DE PHARM4CY", ["Paracetamol tablets", "Sterile gauze pads"]),
    ("bookshop", "Campus Bookshop", "CAMPUS B00KSHOP", ["A5 spiral notebook", "Black ballpoint pen"]),
    ("electronics", "Circuit Hub Electronics", "C1RCUIT HUB ELECTR0NICS", ["USB-C charging cable", "Wireless computer mouse"]),
    ("bakery", "Golden Crust Bakery", "G0LDEN CRUST BAKERY", ["Sourdough bread loaf", "Butter croissant"]),
    ("garden", "Green Leaf Garden Supply", "GREEN LE4F GARDEN SUPPLY", ["Potting soil mix", "Vegetable seedlings"]),
    ("auto", "Harbor Auto Parts", "HARB0R AUT0 PARTS", ["Engine motor oil 5W-30", "Car oil filter"]),
]
QUERIES = [
    ("market", "Vendor Nortside Mrket; arabca cofee beans and wholewheat bred"),
    ("hardware", "Baysde Hardwere: galvnized nails, PVC plumbing water tube"),
    ("pharmacy", "Riversde Phamacy: acetaminophen pain medicine and sterile bandage gauze"),
    ("bookshop", "Campus Bokshop: stationary paper spiral exercise book and ink writing pen"),
    ("electronics", "Circut Hub: Type C smartphone charger cord and cordless PC mouse"),
    ("bakery", "Goldn Crst Bkery: buttery crosant pastry and sour dough loaf"),
    ("garden", "Green Leef garden supplies: soil for vegetable pots and young seedlings"),
    ("auto", "Harbr automobile parts: engine lubricant 5w30 and replacement motor oil filter"),
]
PRICE_FIELDS = {"quantity", "unit_price", "line_total", "total", "subtotal", "tax", "discount", "amount"}


def seed(store):
    """Use the normal SQLite schema and receipt writer, with explicitly synthetic image originals."""
    buffer = io.BytesIO()
    with Image.new("RGB", (16, 16), "white") as image:
        image.save(buffer, "PNG")
    content = buffer.getvalue()
    batch_id = uid()
    sources = {}
    with store.connect() as connection:
        connection.execute("INSERT INTO batches(id,name,created_at) VALUES(?,?,?)",
                           (batch_id, "Synthetic retrieval evaluation only", now()))
        corpus = EXAMPLES + [("query", "Unverified held-out query", "", [])]
        for key, vendor, ocr_vendor, descriptions in corpus:
            receipt_id, document_id, upload_id = uid(), uid(), uid()
            relative = f"originals/synthetic-{key}.png"
            store.file(relative).write_bytes(content)
            connection.execute("INSERT INTO documents VALUES(?,?,?,?,?,?)",
                               (document_id, f"synthetic-{key}.png", relative,
                                hashlib.sha256(content).hexdigest(), "image/png", now()))
            connection.execute("INSERT INTO batch_files(id,batch_id,name,state,receipt_id) VALUES(?,?,?,?,?)",
                               (upload_id, batch_id, f"synthetic-{key}.png", "accepted", receipt_id))
            verified = key != "query"
            connection.execute(
                "INSERT INTO receipts(id,document_id,batch_id,upload_id,status,revision,verified_revision,"
                "data_json,created_at,updated_at,retrieval_allowed) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (receipt_id, document_id, batch_id, upload_id, "verified" if verified else "needs_review", 1,
                 1 if verified else None, "{}", now(), now(), int(verified)))
            data = ReceiptData(supplier=vendor, receipt_number="SYNTHETIC-" + key.upper(),
                               receipt_date="2026-10-19", total="246.90" if descriptions else "0.00",
                               items=[{"description": description, "quantity": "1", "unit": "pc",
                                       "unit_price": "123.45", "line_total": "123.45"} for description in descriptions])
            current = store.write_data(connection, receipt_id, data.model_dump())
            draft = copy.deepcopy(current)
            if verified:
                draft["supplier"] = ocr_vendor
                draft["items"][0]["description"] = descriptions[0].replace("a", "4").replace("o", "0")
                connection.execute("INSERT INTO export_queue VALUES(?,?)", (receipt_id, 1))
                store.audit(connection, receipt_id, 1, "edit", draft, current, "Synthetic evaluator")
                store.audit(connection, receipt_id, 1, "verify", after=current, reviewer="Synthetic evaluator")
            connection.execute("INSERT INTO extraction_runs VALUES(?,?,?,?,?)",
                               (uid(), receipt_id, "Synthetic labeled OCR fixture",
                                json.dumps({"lines": [], "draft": draft, "photo_id": ""}), now()))
            sources[key] = receipt_id
    return sources


@contextmanager
def loopback_only():
    connect, connect_ex = socket.socket.connect, socket.socket.connect_ex
    blocked = []

    def guard(original):
        def checked(sock, address):
            try:
                local = ipaddress.ip_address(address[0]).is_loopback
            except (ValueError, TypeError, IndexError):
                local = False
            if not local:
                blocked.append(str(address))
                raise OSError("Non-loopback sockets blocked during retrieval evaluation")
            return original(sock, address)
        return checked

    with patch.object(socket.socket, "connect", guard(connect)), patch.object(socket.socket, "connect_ex", guard(connect_ex)):
        with socket.socket() as probe:
            try:
                probe.connect(("203.0.113.1", 443))
            except OSError:
                pass
            else:
                raise AssertionError("External socket guard did not reject its test connection")
        blocked.clear()
        yield blocked


def has_price_fields(value):
    if isinstance(value, dict):
        return bool(PRICE_FIELDS & set(value)) or any(has_price_fields(item) for item in value.values())
    return isinstance(value, list) and any(has_price_fields(item) for item in value)


def observed_memory():
    process = psutil.Process()
    children = 0
    for child in process.children(recursive=True):
        try:
            children += child.memory_info().rss
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return process.memory_info().rss, children


def warmup():
    """Measure first model loading separately; normal Retrieval requests retain their production timeout."""
    connection = http.client.HTTPConnection("127.0.0.1", 11435, timeout=180)
    started = time.monotonic()
    try:
        connection.request("POST", "/api/embed", json.dumps({"model": EMBEDDING_MODEL,
            "input": "Synthetic local retrieval benchmark warmup", "dimensions": 256,
            "truncate": True, "keep_alive": "2m"}), {"Content-Type": "application/json"})
        response = connection.getresponse()
        body = response.read(256_001)
        result = json.loads(body) if len(body) <= 256_000 else {}
        if response.status != 200 or not result.get("embeddings"):
            raise RuntimeError(f"Embedding warmup HTTP {response.status}: {str(result.get('error', 'invalid response'))[:500]}")
        return {"seconds": round(time.monotonic() - started, 4), "http_status": response.status,
                "load_seconds": round(result.get("load_duration", 0) / 1e9, 4),
                "model": result.get("model"), "dimensions": len(result["embeddings"][0])}
    finally:
        connection.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("artifacts/retrieval-evaluation.json"))
    args = parser.parse_args()
    artifact_root = Path(__file__).resolve().parents[1] / "artifacts"
    artifact_root.mkdir(exist_ok=True)
    workspace = Path(tempfile.mkdtemp(prefix="retrieval-evaluation-", dir=artifact_root))
    store = Store(workspace)
    sources = seed(store)
    source_keys = {receipt_id: key for key, receipt_id in sources.items()}
    query_id = sources.pop("query")
    query_before = store.receipt(query_id)["data"]
    retrieval = Retrieval(store)
    runtime_root = data_directory()
    previous_port = os.environ.get("RECEIPTIQ_OLLAMA_PORT")
    os.environ["RECEIPTIQ_OLLAMA_PORT"] = "11435"
    report = {"kind": "Synthetic held-out typo/context queries; not real-world receipt accuracy",
              "model": EMBEDDING_MODEL, "dimensions": 256, "workspace": str(workspace),
              "user_receipts_used": False, "source_count": len(sources), "query_count": len(QUERIES),
              "outbound_network": "Python non-loopback connect/connect_ex blocked during inference; managed runtime disables cloud",
              "filtering": "Global cosine ranking with vendor/item/correction text; no explicit supplier/category filter",
              "sources": [{"key": key, "receipt_id": sources[key], "vendor": vendor, "items": items}
                          for key, vendor, _, items in EXAMPLES], "index": [], "queries": [], "checks": {}}
    began = time.monotonic()
    python_peak = runtime_peak = 0

    def sample_memory():
        nonlocal python_peak, runtime_peak
        python_rss, child_rss = observed_memory()
        python_peak, runtime_peak = max(python_peak, python_rss), max(runtime_peak, child_rss)

    def search(query, receipt_id=query_id, limit=3):
        started = time.monotonic()
        results = retrieval.search(receipt_id, query=query, limit=limit)
        if retrieval.last_error:
            raise RuntimeError(retrieval.last_error)
        sample_memory()
        return results, round(time.monotonic() - started, 4)

    try:
        print("Starting app-managed local runtime on 127.0.0.1:11435", flush=True)
        ensure_runtime(runtime_root)
        sample_memory()
        with loopback_only() as blocked:
            print("Measuring cold embedding model warmup (bounded at 180 seconds)", flush=True)
            report["warmup"] = warmup()
            sample_memory()
            print(f"Embedding warmup: {report['warmup']['seconds']}s", flush=True)
            for key, receipt_id in sources.items():
                started = time.monotonic()
                if not retrieval.index(receipt_id):
                    raise RuntimeError(retrieval.last_error or "Synthetic verified source could not be indexed")
                seconds = round(time.monotonic() - started, 4)
                report["index"].append({"key": key, "seconds": seconds})
                sample_memory()
                print(f"Indexed {key}: {seconds}s", flush=True)
            dimensions = {len(json.loads(row["vector_json"])) for row in store.read("SELECT vector_json FROM retrieval_entries")}
            assert dimensions == {256}, f"Unexpected embedding dimensions: {dimensions}"
            retrieval = Retrieval(Store(workspace))
            for expected, query in QUERIES:
                results, seconds = search(query)
                ranked = [source_keys[result["receipt_id"]] for result in results]
                record = {"expected_source": expected, "query": query, "ranked_sources": ranked, "seconds": seconds,
                          "top1_match": bool(ranked and ranked[0] == expected), "top3_match": expected in ranked,
                          "evidence": results}
                report["queries"].append(record)
                print(f"Query {expected}: top1={record['top1_match']} top3={record['top3_match']} ({seconds}s)", flush=True)
            self_results, _ = search(QUERIES[0][1], receipt_id=sources["market"], limit=20)
            assert all(result["receipt_id"] != sources["market"] for result in self_results)
            assert all(result["corrections"] for record in report["queries"] for result in record["evidence"])
            assert not any(has_price_fields(record["evidence"]) for record in report["queries"])
            assert "123.45" not in json.dumps([record["evidence"] for record in report["queries"]])
            assert all(result["citation"] == f"receipt:{result['receipt_id']}@{result['revision']}"
                       for record in report["queries"] for result in record["evidence"])
            # Bypass API invalidation deliberately to prove retrieval rechecks current SQLite state.
            store.execute("UPDATE receipts SET retrieval_allowed=0 WHERE id=?", (sources["market"],))
            opted_out, _ = search(QUERIES[0][1], limit=20)
            assert all(result["receipt_id"] != sources["market"] for result in opted_out)
            store.execute("UPDATE receipts SET revision=revision+1,verified_revision=NULL,status='needs_review' WHERE id=?",
                          (sources["hardware"],))
            edited, _ = search(QUERIES[1][1], limit=20)
            assert all(result["receipt_id"] != sources["hardware"] for result in edited)
            store.execute("DELETE FROM receipts WHERE id=?", (sources["pharmacy"],))
            deleted, _ = search(QUERIES[2][1], limit=20)
            assert all(result["receipt_id"] != sources["pharmacy"] for result in deleted)
            assert store.receipt(query_id)["data"] == query_before
            report["checks"] = dict(persistent_index_reopened=True, self_source_excluded=True,
                correction_pairs_cited=True, no_price_fields_returned=True, target_values_unchanged=True,
                live_opt_out_excluded=True, live_edit_excluded=True, deleted_source_excluded=True,
                external_socket_guard_self_check=True, blocked_external_workload_attempts=len(blocked))
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        print(f"Evaluation error: {report['error']}", flush=True)
    finally:
        sample_memory()
        stop_runtime(runtime_root)
        print("Stopped owned local runtime on port 11435", flush=True)
        if previous_port is None:
            os.environ.pop("RECEIPTIQ_OLLAMA_PORT", None)
        else:
            os.environ["RECEIPTIQ_OLLAMA_PORT"] = previous_port
    report.update(seconds=round(time.monotonic() - began, 3),
                  top1_matches=sum(record["top1_match"] for record in report["queries"]),
                  top3_matches=sum(record["top3_match"] for record in report["queries"]),
                  peak_observed_python_rss_mb=round(python_peak / 1024**2, 1),
                  peak_observed_runtime_tree_rss_mb=round(runtime_peak / 1024**2, 1),
                  memory_note="RSS sampled after requests; runtime tree sum includes shared pages and is not physical memory usage")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Report: {args.out}; top1 {report['top1_matches']}/{len(QUERIES)}, top3 {report['top3_matches']}/{len(QUERIES)}", flush=True)
    return 1 if "error" in report else 0


if __name__ == "__main__":
    raise SystemExit(main())
