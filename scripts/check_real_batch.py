"""Acceptance check: 50 real OCR jobs, offline, with review/export and a worker restart."""

import json
import logging
import shutil
import socket
import time
from pathlib import Path

import psutil
from fastapi.testclient import TestClient

from app.db import data_directory, uid
from app.main import create_app
from app.ocr import PaddleEngine


def main():
    logging.getLogger("httpx").setLevel(logging.WARNING)
    samples = Path("artifacts/samples")
    manifest = json.loads((samples / "manifest.json").read_text(encoding="utf-8"))[:50]
    root = (Path("artifacts") / ("batch-workspace-" + uid())).resolve()
    shutil.copytree(data_directory() / "models", root / "models", dirs_exist_ok=True)
    engine = PaddleEngine(root)
    started = time.monotonic()
    peak = psutil.Process().memory_info().rss
    original_connect = socket.socket.connect

    def offline(connection, address, *args, **kwargs):
        # Windows asyncio uses a loopback socket pair internally.
        if isinstance(address, tuple) and address[0] in ("127.0.0.1", "::1"):
            return original_connect(connection, address, *args, **kwargs)
        raise OSError("Outbound network disabled for batch acceptance check")

    def client_for(app):
        client = TestClient(app, base_url="http://127.0.0.1:8765")
        return client

    def authenticate(client):
        client.headers["Authorization"] = "Bearer " + client.get("/api/v1/session").json()["token"]

    def wait_for(client, predicate, timeout=600):
        nonlocal peak
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            peak = max(peak, psutil.Process().memory_info().rss)
            rows = client.get("/api/v1/receipts?batch_id=" + batch_id).json()
            if predicate(rows):
                return rows
            time.sleep(0.2)
        raise AssertionError("Batch acceptance deadline exceeded")

    socket.socket.connect = offline
    try:
        app = create_app(root, engine=engine)
        with client_for(app) as client:
            authenticate(client)
            result = client.post("/api/v1/batches", json={
                "name": "50-image real OCR acceptance", "files": [sample["file"] for sample in manifest] + ["broken.png"]})
            assert result.status_code == 201
            group = result.json()
            batch_id = group["id"]
            for index, sample in enumerate(manifest):
                response = client.post("/api/v1/receipts",
                                       params={"batch_id": batch_id, "upload_id": group["files"][index]["id"]},
                                       files={"file": (sample["file"], (samples / sample["file"]).read_bytes(), "image/png")})
                assert response.status_code == 202, response.text
            bad = client.post("/api/v1/receipts",
                              params={"batch_id": batch_id, "upload_id": group["files"][-1]["id"]},
                              files={"file": ("broken.png", b"bad image", "image/png")})
            assert bad.status_code == 422
            rows = wait_for(client, lambda rows: any(r["status"] == "needs_review" for r in rows))
            ready = next(r for r in rows if r["status"] == "needs_review")
            receipt_id = ready["id"]
            receipt = client.get("/api/v1/receipts/" + receipt_id).json()
            data = receipt["data"]
            data["supplier"] = "Human-checked batch fixture"
            edited = client.patch("/api/v1/receipts/" + receipt_id,
                                  json={"revision": receipt["revision"], "data": data})
            assert edited.status_code == 200, edited.text
            receipt = edited.json()
            verified = client.post("/api/v1/receipts/" + receipt_id + "/verify", json={
                "revision": receipt["revision"], "reviewer": "Batch QA",
                "acknowledged": [issue["id"] for issue in receipt["issues"] if not issue["blocking"]],
                "reason": "Synthetic fixture checked against its original image."})
            assert verified.status_code == 200, verified.text
            exported = client.post("/api/v1/exports", json={"receipt_ids": [receipt_id]})
            assert exported.status_code == 201
            remaining_during_review = sum(r["status"] in ("queued", "processing") for r in rows)
            assert remaining_during_review > 0
            wait_for(client, lambda rows: sum(r["status"] == "needs_review" for r in rows) >= 4)
            print("Reviewed and exported an early receipt while batch continued; restarting worker...", flush=True)
        restarted = create_app(root, engine=engine)
        with client_for(restarted) as client:
            authenticate(client)
            rows = wait_for(client, lambda rows: len(rows) == 50 and all(
                r["status"] not in ("queued", "processing") for r in rows))
            assert sum(r["status"] == "verified" for r in rows) == 1
            assert sum(r["status"] == "needs_review" for r in rows) == 49, str(rows)
            preserved = client.get("/api/v1/receipts/" + receipt_id).json()
            assert preserved["data"]["supplier"] == "Human-checked batch fixture"
            assert preserved["status"] == "verified"
            assert len(restarted.state.store.read("SELECT * FROM jobs")) == 50
            detail = client.get("/api/v1/batches/" + batch_id).json()
            assert sum(f["state"] == "failed" for f in detail["files"]) == 1
            assert client.get("/api/v1/exports/" + exported.json()["id"] + "/download").status_code == 200
        report = {"accepted": 50, "failed_uploads": 1, "processed": 50, "verified_preserved_after_restart": 1,
                  "queued_during_review": remaining_during_review,
                  "network": "external socket connections disabled; loopback permitted", "engine": "real PaddleOCR CPU",
                  "seconds": round(time.monotonic() - started, 3),
                  "peak_observed_rss_mb": round(peak / 1024 ** 2, 1)}
        Path("artifacts/real-batch.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report), flush=True)
    finally:
        socket.socket.connect = original_connect


if __name__ == "__main__":
    main()
