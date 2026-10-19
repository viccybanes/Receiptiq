param([switch]$Release)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
if (-not (Test-Path -LiteralPath 'dist\receiptiq\receiptiq.exe')) {
    throw 'Build the backend first with scripts\package.ps1.'
}
if ($Release) {
    $desktopExecutable = Join-Path $projectRoot 'src-tauri\target\release\receiptiq-desktop.exe'
    if (-not (Test-Path -LiteralPath $desktopExecutable)) { throw 'Run scripts\build_installer.ps1 first.' }
    Start-Process -FilePath $desktopExecutable -WindowStyle Hidden
} else {
    $env:RECEIPTIQ_BACKEND_PATH = Join-Path $projectRoot 'dist\receiptiq\receiptiq.exe'
    & cargo run --manifest-path src-tauri\Cargo.toml --locked
    if ($LASTEXITCODE -ne 0) { throw 'Desktop launch failed.' }
}
