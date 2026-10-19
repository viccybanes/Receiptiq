param([switch]$SkipBackendBuild)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
if (-not $SkipBackendBuild) {
    Push-Location -LiteralPath frontend
    try {
        & npm.cmd run build
        if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
    } finally { Pop-Location }
    & (Join-Path $PSScriptRoot 'package.ps1')
}
if (-not (Test-Path -LiteralPath 'dist\receiptiq\_internal\python311.dll')) {
    throw 'The complete PyInstaller directory bundle is required.'
}
& (Join-Path $projectRoot '.venv\Scripts\python.exe') (Join-Path $projectRoot 'src-tauri\prepare_resources.py')
if ($LASTEXITCODE -ne 0) { throw 'Refreshing packaged frontend assets failed.' }
# npm keeps the pinned CLI in its normal cache; no global developer-tool installation is required.
$tauriArguments = @('build', '--bundles', 'nsis', '--ci')
if ($env:RECEIPTIQ_CERT_THUMBPRINT) {
    $thumbprint = $env:RECEIPTIQ_CERT_THUMBPRINT.Trim()
    if ($thumbprint -notmatch '^[a-fA-F0-9]{40}$') { throw 'RECEIPTIQ_CERT_THUMBPRINT must be a certificate SHA-1 thumbprint.' }
    $certificate = Get-Item -LiteralPath "Cert:\CurrentUser\My\$thumbprint" -ErrorAction SilentlyContinue
    if (-not $certificate -or -not $certificate.HasPrivateKey) {
        throw 'The requested signing certificate/private key is unavailable in CurrentUser\My.'
    }
    $timestamp = $null
    if (-not [Uri]::TryCreate($env:RECEIPTIQ_TIMESTAMP_URL, [UriKind]::Absolute, [ref]$timestamp) -or
        $timestamp.Scheme -notin @('http', 'https')) {
        throw 'Set RECEIPTIQ_TIMESTAMP_URL to the timestamp service supplied by your certificate issuer.'
    }
    $signing = @{ bundle = @{ windows = @{ certificateThumbprint = $thumbprint; digestAlgorithm = 'sha256';
        timestampUrl = $timestamp.AbsoluteUri; tsp = $true } } } | ConvertTo-Json -Depth 5
    $signingFolder = Join-Path $projectRoot '.local\build'
    New-Item -ItemType Directory -Path $signingFolder -Force | Out-Null
    $signingFile = Join-Path $signingFolder 'tauri-signing.json'
    [IO.File]::WriteAllText($signingFile, $signing, (New-Object Text.UTF8Encoding($false)))
    $tauriArguments += @('--config', $signingFile)
}
$tauriArguments += @('--', '--locked')
& npx.cmd --yes --package '@tauri-apps/cli@2.12.3' tauri @tauriArguments
if ($LASTEXITCODE -ne 0) { throw 'Windows desktop installer build failed.' }
$installer = Get-ChildItem -LiteralPath 'src-tauri\target\release\bundle\nsis' -Filter '*setup.exe' | Select-Object -First 1
if (-not $installer) { throw 'Tauri reported success but no NSIS installer was found.' }
$destination = Join-Path $projectRoot 'artifacts\desktop'
New-Item -ItemType Directory -Path $destination -Force | Out-Null
Copy-Item -LiteralPath $installer.FullName -Destination $destination
$signature = Get-AuthenticodeSignature -LiteralPath $installer.FullName
if ($env:RECEIPTIQ_CERT_THUMBPRINT -and $signature.Status -ne 'Valid') {
    throw 'The installer was built but its release signature did not validate.'
}
$digest = Get-FileHash -LiteralPath $installer.FullName -Algorithm SHA256
$locks = @{}
foreach ($relative in @('src-tauri\Cargo.lock', 'backend\requirements.lock', 'frontend\package-lock.json', 'packaging\runtime-manifest.json')) {
    $locks[$relative.Replace('\', '/')] = (Get-FileHash -LiteralPath $relative -Algorithm SHA256).Hash.ToLowerInvariant()
}
$manifest = @{ version = (Get-Content -Raw -LiteralPath 'src-tauri\tauri.conf.json' | ConvertFrom-Json).version;
    installer = $installer.Name; bytes = $installer.Length; sha256 = $digest.Hash.ToLowerInvariant();
    signature_status = [string]$signature.Status; tauri_cli = '2.12.3'; lock_sha256 = $locks;
    built_at = [DateTime]::UtcNow.ToString('o'); source_commit = (& git rev-parse HEAD);
    source_tree_dirty = [bool](& git status --porcelain) } | ConvertTo-Json -Depth 5
[IO.File]::WriteAllText((Join-Path $destination 'build-manifest.json'), $manifest, (New-Object Text.UTF8Encoding($false)))
$digest | Format-List
if ($signature.Status -eq 'Valid') { Write-Host 'Signed installer ready.' }
else { Write-Host 'Unsigned preview installer ready. A publisher certificate is required for a signed release.' }
Write-Host 'User data and model caches remain outside the installation directory.'
