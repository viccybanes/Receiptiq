$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) { throw 'Run scripts/setup.ps1 first.' }
if (-not (Test-Path -LiteralPath 'frontend\dist\index.html')) { throw 'Run scripts/setup.ps1 to build the interface first.' }
Write-Host 'Open http://127.0.0.1:8765 in your browser. Press Ctrl+C here to stop.'
& .\.venv\Scripts\python.exe -m app
