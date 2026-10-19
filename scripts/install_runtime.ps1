param([ValidateSet('ocr-only', '2b', '4b')][string]$Profile = 'ocr-only', [string]$Workspace = '')
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
if ($Workspace) { $env:RECEIPTIQ_DATA_DIR = $Workspace }
$env:RECEIPTIQ_SETUP_PROFILE = $Profile
@'
import json, os, time
from app.db import data_directory
from app.system import start_setup, setup_status, stop_runtime
root = data_directory()
start_setup(root, os.environ["RECEIPTIQ_SETUP_PROFILE"])
last = None
try:
    while True:
        state = setup_status(root)["job"]
        line = (state.get("state"), state.get("stage"), state.get("completed"), state.get("total"))
        if line != last:
            print(json.dumps(state), flush=True)
            last = line
        if state.get("state") in ("complete", "failed", "interrupted"):
            raise SystemExit(0 if state["state"] == "complete" else 1)
        time.sleep(1)
finally:
    stop_runtime(root)
'@ | & .\.venv\Scripts\python.exe -
if ($LASTEXITCODE -ne 0) { throw 'Setup did not complete. Retry resumes partial downloads.' }
