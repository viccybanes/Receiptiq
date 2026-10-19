"""Launch the real Windows desktop with a restricted PATH, verify auth and owned-process cleanup."""

import argparse
import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

import psutil


def check_webview(project, port, base):
    node = shutil.which("node")
    assert node, "Node is needed by this developer check; the installed application does not need it."
    playwright = project / "frontend/node_modules/playwright/index.mjs"
    screenshot = project / "artifacts/desktop/desktop-smoke.png"
    screenshot.parent.mkdir(parents=True, exist_ok=True)
    script = f"""
        const {{ chromium }} = await import({json.dumps(playwright.as_uri())});
        const browser = await chromium.connectOverCDP('http://127.0.0.1:{port}', {{timeout: 30000}});
        const context = browser.contexts()[0];
        const page = context.pages()[0] || await context.waitForEvent('page');
        await page.waitForURL({json.dumps(base + '/')}, {{timeout: 30000}});
        await page.locator('#root > *').first().waitFor({{timeout: 30000}});
        await page.waitForFunction(() => [...document.querySelectorAll('button')].some(
            button => button.textContent.trim() === 'Settings' && !button.disabled));
        const status = await page.evaluate(async () => {{
            const token = window.__RECEIPTIQ_TOKEN__;
            if (typeof token !== 'string' || token.length < 32) return -1;
            const response = await fetch('/api/v1/batches', {{headers: {{Authorization: 'Bearer ' + token}}}});
            return response.status;
        }});
        if (status !== 200) throw new Error('WebView injected session did not authenticate: ' + status);
        await page.screenshot({{path: {json.dumps(str(screenshot))}}});
        const hardwareResponse = page.waitForResponse(response => response.url().endsWith('/api/v1/system/capabilities'));
        await page.getByRole('button', {{name: 'Settings', exact: true}}).click();
        const response = await hardwareResponse;
        const hardware = await response.json();
        if (response.status() !== 200 || !hardware.cpu || !hardware.profiles) {{
            throw new Error('Installed onboarding could not load hardware and model profiles');
        }}
        process.exit(0); // Disconnect CDP without requesting that the app's browser close.
    """
    result = subprocess.run([node, "--input-type=module", "-e", script], capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=60)
    assert result.returncode == 0, result.stderr[-2000:]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--executable", type=Path)
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[1]
    executable = (args.executable or project / "src-tauri/target/release/receiptiq-desktop.exe").resolve()
    assert executable.is_file(), "Build or install the desktop executable first."
    with tempfile.TemporaryDirectory(prefix="receiptiq-desktop-check-") as scratch:
        folder = Path(scratch)
        report = folder / "ready.json"
        environment = os.environ.copy()
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            debug_port = probe.getsockname()[1]
        environment.update(PATH=str(Path(os.environ["SystemRoot"]) / "System32"),
                           RECEIPTIQ_DATA_DIR=str(folder / "workspace"), RECEIPTIQ_DESKTOP_SMOKE_REPORT=str(report),
                           WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=f"--remote-debugging-port={debug_port} --remote-debugging-address=127.0.0.1",
                           WEBVIEW2_USER_DATA_FOLDER=str(folder / "webview2"))
        environment.pop("PYTHONHOME", None)
        environment.pop("PYTHONPATH", None)
        desktop = subprocess.Popen([str(executable)], env=environment, creationflags=subprocess.CREATE_NO_WINDOW)
        backend_pid = None
        descendants = []
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            deadline = time.monotonic() + 120
            while not report.exists():
                assert desktop.poll() is None, "Desktop exited before its backend became healthy."
                assert time.monotonic() < deadline, "Desktop startup timed out."
                time.sleep(0.25)
            state = json.loads(report.read_text())
            backend_pid = state["backend_pid"]
            descendants = psutil.Process(desktop.pid).children(recursive=True)
            base = f"http://127.0.0.1:{state['port']}"
            for path in ("/api/v1/session", "/api/v1/health", "/api/v1/batches"):
                try:
                    opener.open(base + path, timeout=5)
                    raise AssertionError("Unauthenticated desktop API exposed: " + path)
                except urllib.error.HTTPError as error:
                    assert error.code == 401, (path, error.code)
            with opener.open(base, timeout=5) as response:
                assert b"<html" in response.read(10000).lower()
            assert psutil.Process(backend_pid).parent().pid == desktop.pid
            assert (folder / "workspace" / "receiptiq.sqlite3").exists()
            check_webview(project, debug_port, base)
            descendants = psutil.Process(desktop.pid).children(recursive=True)
            # Killing the host simulates a crash; the Windows Job Object must reap its entire backend tree.
            desktop.kill()
            desktop.wait(10)
            deadline = time.monotonic() + 15
            while psutil.pid_exists(backend_pid) and time.monotonic() < deadline:
                time.sleep(0.1)
            assert not psutil.pid_exists(backend_pid), "Desktop left its owned backend running."
            print(json.dumps({"desktop": str(executable), "restricted_path": True, "session_protected": True,
                              "workspace_external": True, "webview_session": True, "onboarding": True,
                              "crash_cleanup": True}, indent=2))
        finally:
            if desktop.poll() is None:
                desktop.kill()
                desktop.wait(10)
            if backend_pid and psutil.pid_exists(backend_pid):
                backend = psutil.Process(backend_pid)
                backend.kill()
                backend.wait(5)
            _, alive = psutil.wait_procs(descendants, timeout=5)
            for process in alive:
                try:
                    process.kill()
                    process.wait(5)
                except psutil.NoSuchProcess:
                    pass


if __name__ == "__main__":
    main()
