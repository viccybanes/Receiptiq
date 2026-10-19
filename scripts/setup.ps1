$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
if (-not (Test-Path -LiteralPath '.bootstrap\Scripts\uv.exe')) {
    python -m venv .bootstrap
    if ($LASTEXITCODE -ne 0) { throw 'Python is required on the development machine for initial bootstrap.' }
    & .\.bootstrap\Scripts\python.exe -m pip install uv==0.13.1
    if ($LASTEXITCODE -ne 0) { throw 'Could not install the development bootstrap tool.' }
}
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    & .\.bootstrap\Scripts\uv.exe python install 3.11
    if ($LASTEXITCODE -ne 0) { throw 'Could not download Python 3.11.' }
    & .\.bootstrap\Scripts\uv.exe venv --python 3.11 .venv
}
& .\.bootstrap\Scripts\uv.exe pip sync --python .venv\Scripts\python.exe backend\requirements.lock
if ($LASTEXITCODE -ne 0) { throw 'Backend installation failed.' }
& .\.bootstrap\Scripts\uv.exe pip install --python .venv\Scripts\python.exe --no-deps -e backend
if ($LASTEXITCODE -ne 0) { throw 'Could not register the backend package.' }
& .\.venv\Scripts\python.exe -m app.ocr
if ($LASTEXITCODE -ne 0) { throw 'OCR model setup failed. Retry setup when online.' }
Push-Location frontend
try {
    npm ci --no-fund --no-audit
    if ($LASTEXITCODE -ne 0) { throw 'Frontend installation failed.' }
    npm run build
    if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
} finally { Pop-Location }
Write-Host 'Setup complete. Run .\scripts\start.ps1 to open ReceiptIQ.'
