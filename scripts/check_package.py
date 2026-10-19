"""Exercise the frozen executable with Python and developer tools absent from PATH."""

import ast
import json
import os
import re
import shutil
import socket
import subprocess
import time
import urllib.request
from pathlib import Path

from app.db import data_directory, uid


def main():
    project = Path(__file__).resolve().parents[1]
    executable = project / "dist/receiptiq/receiptiq.exe"
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
        finally:
            process.terminate()
            process.wait(timeout=20)
    report = {"python_on_child_path": False, "ocr": "passed", "packaged_api": "passed",
              "frontend_assets": asset_count, "native_sqlite": "passed",
              "scope": "Development Windows machine, not a clean VM"}
    (project / "artifacts/package-check.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
