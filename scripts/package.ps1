$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
if (-not (Test-Path -LiteralPath 'frontend\dist\index.html')) { throw 'Build the frontend first.' }
$env:PADDLE_PDX_CACHE_HOME = Join-Path $projectRoot '.local\models\cache'
if ($env:RECEIPTIQ_DATA_DIR) {
    $env:PADDLE_PDX_CACHE_HOME = Join-Path $env:RECEIPTIQ_DATA_DIR 'models\cache'
}
$ErrorActionPreference = 'Continue' # Windows PowerShell treats redirected native stderr as error records.
& .\.venv\Scripts\pyinstaller.exe --noconfirm packaging\receiptiq.spec
$buildExit = $LASTEXITCODE
$ErrorActionPreference = 'Stop'
if ($buildExit -ne 0) { throw 'Packaging failed.' }
Write-Host 'Portable backend created in dist\receiptiq. Run scripts/build_installer.ps1 -SkipBackendBuild for the Windows installer.'
