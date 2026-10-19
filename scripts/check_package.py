"""Exercise the frozen executable with Python and developer tools absent from PATH."""

import ast
import csv
import io
import json
import os
import re
import shutil
import socket
import subprocess
import time
import urllib.request
import zipfile
from pathlib import Path
from types import CodeType

from PyInstaller.archive.readers import CArchiveReader

from app.db import data_directory, uid
from app.service import COLUMNS


def code_signature(code):
    return (code.co_code, code.co_names, code.co_varnames, tuple(
        code_signature(value) if isinstance(value, CodeType) else value for value in code.co_consts))


def main():
    project = Path(__file__).resolve().parents[1]
    executable = project / "dist/receiptiq/receiptiq.exe"
    archive = CArchiveReader(str(executable))
    modules = archive.open_embedded_archive(next(name for name in archive.toc if name.endswith(".pyz")))
    for source_path in (project / "backend/app").glob("*.py"):
        name = "app" if source_path.stem == "__init__" else "app." + source_path.stem
        source = compile(source_path.read_bytes(), str(source_path), "exec", dont_inherit=True, optimize=0)
        assert code_signature(modules.extract(name)) == code_signature(source), f"Stale frozen source: {name}"
    environment = os.environ.copy()
    environment["PATH"] = str(Path(os.environ["SystemRoot"]) / "System32")
    environment.pop("PYTHONHOME", None)
    environment.pop("PYTHONPATH", None)
    environment["RECEIPTIQ_DATA_DIR"] = str(data_directory())
    assert shutil.which("python", path=environment["PATH"]) is None
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    result = subprocess.run(
        [str(executable), "--ocr-smoke", str(project / "artifacts/samples/001-printed.png")],
        env=environment, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=180, creationflags=flags)
    (project / "artifacts/packaged-ocr.log").write_text(result.stdout + result.stderr, encoding="utf-8")
    assert result.returncode == 0, result.stderr[-2000:]
    recognized = ast.literal_eval(result.stdout.strip().splitlines()[-1])
    assert any("NORTHSIDE MARKET" in line["text"] for line in recognized)
    assert any("125.00" in line["text"] for line in recognized)
    environment["RECEIPTIQ_DATA_DIR"] = str(project / "artifacts" / ("packaged-workspace-" + uid()))
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    with (project / "artifacts/packaged-server.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen([str(executable), "--port", str(port)], env=environment,
                                   stdout=log, stderr=log, creationflags=flags)
        try:
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise AssertionError("Packaged server exited before becoming ready.")
                try:
                    with urllib.request.urlopen(base + "/api/v1/health", timeout=2) as response:
                        assert json.load(response)["status"] == "ok"
                    break
                except OSError:
                    time.sleep(0.2)
            else:
                raise AssertionError("Packaged server readiness deadline exceeded.")
            with urllib.request.urlopen(base + "/", timeout=5) as response:
                index = response.read()
                assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
            assert index == (project / "frontend/dist/index.html").read_bytes()
            asset_count = 0
            for asset in re.findall(r'(?:src|href)="(/assets/[^"]+)"', index.decode("utf-8")):
                with urllib.request.urlopen(base + asset, timeout=5) as response:
                    assert response.status == 200 and len(response.read()) > 0
                asset_count += 1
            assert asset_count >= 2
            with urllib.request.urlopen(base + "/api/v1/session", timeout=5) as response:
                token = json.load(response)["token"]
            request = urllib.request.Request(base + "/api/v1/receipts",
                                             headers={"Authorization": "Bearer " + token})
            with urllib.request.urlopen(request, timeout=5) as response:
                assert json.load(response) == []
            # New transfer module must work in the frozen bundle, not just the source checkout.
            csv_file = io.StringIO(newline="")
            writer = csv.DictWriter(csv_file, fieldnames=COLUMNS)
            writer.writeheader()
            writer.writerow({"Purchase date": "2026-10-19", "Vendor": "Packaged expense QA",
                             "Receipt number": "00-0045 / 00-0029", "Receipt photo": "receipt.png",
                             "Description": "Coffee", "Quantity": "2", "Unit": "pc",
                             "Unit price": "50", "Amount": "100", "Type": "Materials", "Total": "100"})
            bundle = io.BytesIO()
            with zipfile.ZipFile(bundle, "w") as archive:
                archive.writestr("receipts.csv", csv_file.getvalue().encode("utf-8-sig"))
                archive.write(project / "artifacts/samples/001-printed.png", arcname="receipt.png")
            boundary = "ReceiptIQ" + uid()
            body = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="expenses.zip"\r\n'
                    'Content-Type: application/zip\r\n\r\n').encode() + bundle.getvalue() + (
                    f"\r\n--{boundary}--\r\n").encode()
            request = urllib.request.Request(base + "/api/v1/imports", data=body, headers={
                "Authorization": "Bearer " + token, "Content-Type": "multipart/form-data; boundary=" + boundary})
            with urllib.request.urlopen(request, timeout=10) as response:
                imported = json.load(response)

            def api(path, payload=None):
                request = urllib.request.Request(base + "/api/v1" + path,
                    data=None if payload is None else json.dumps(payload).encode(),
                    headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
                with urllib.request.urlopen(request, timeout=10) as response:
                    return json.load(response)

            receipt_id = api("/receipts?batch_id=" + imported["id"])[0]["id"]
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                receipt = api("/receipts/" + receipt_id)
                if receipt["status"] not in ("queued", "processing"):
                    break
                time.sleep(0.2)
            assert receipt["status"] == "needs_review" and receipt["pages"]
            assert receipt["data"]["receipt_number"] == "00-0045 / 00-0029"
            assert receipt["data"]["items"][0]["unit"] == "pc"
            api(f"/receipts/{receipt_id}/verify", {"revision": receipt["revision"], "reviewer": "Package QA"})
            exported = api("/exports", {"receipt_ids": [receipt_id], "include_photos": True})
            request = urllib.request.Request(base + "/api/v1/exports/" + exported["id"] + "/download",
                                             headers={"Authorization": "Bearer " + token})
            with urllib.request.urlopen(request, timeout=10) as response:
                with zipfile.ZipFile(io.BytesIO(response.read())) as archive:
                    row = next(csv.DictReader(io.StringIO(archive.read("receipts.csv").decode("utf-8-sig"))))
                    assert row["Vendor"] == "Packaged expense QA"
                    assert archive.read(row["Receipt photo"]) == (project / "artifacts/samples/001-printed.png").read_bytes()
        finally:
            process.terminate()
            process.wait(timeout=20)
    report = {"python_on_child_path": False, "ocr": "passed", "packaged_api": "passed",
              "frontend_assets": asset_count, "native_sqlite": "passed", "expense_csv_zip": "passed",
              "frozen_sources": "match",
              "scope": "Development Windows machine, not a clean VM"}
    (project / "artifacts/package-check.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
