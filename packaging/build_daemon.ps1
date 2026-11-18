<#
.SYNOPSIS
    Reproducibly freeze the Iron Jarvis daemon into a standalone Windows exe.

.DESCRIPTION
    Builds packaging/dist/ironjarvis/ironjarvis.exe (PyInstaller onedir) from
    packaging/ironjarvis.spec. The result runs with NO Python/uv installed.

    Run from the repo root with the project venv available at .venv:
        powershell -ExecutionPolicy Bypass -File packaging\build_daemon.ps1

.PARAMETER Verify
    After building, boot the frozen daemon offline and assert GET /health == 200.
#>
[CmdletBinding()]
param(
    [switch]$Verify,
    [int]$Port = 8799
)

$ErrorActionPreference = "Stop"

# Repo root = parent of this script's directory.
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    throw "venv python not found at $Python -- create the venv first (uv venv / python -m venv .venv)."
}

# Ensure PyInstaller is present in the venv.
& $Python -c "import PyInstaller" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Installing pyinstaller into the venv..." -ForegroundColor Cyan
    & $Python -m pip install pyinstaller
}

$Spec     = Join-Path $Root "packaging\ironjarvis.spec"
$DistPath = Join-Path $Root "packaging\dist"
$WorkPath = Join-Path $Root "packaging\build"

Write-Host "Building frozen daemon from $Spec ..." -ForegroundColor Cyan
# PyInstaller logs INFO to stderr; in Windows PowerShell that becomes a
# terminating NativeCommandError under EAP=Stop. Relax EAP for the native call
# and rely on the real exit code instead.
$prevEAP = $ErrorActionPreference
$ErrorActionPreference = "Continue"
& $Python -m PyInstaller $Spec --noconfirm --distpath $DistPath --workpath $WorkPath 2>&1 |
    ForEach-Object { Write-Host $_ }
$code = $LASTEXITCODE
$ErrorActionPreference = $prevEAP
if ($code -ne 0) { throw "PyInstaller build failed (exit $code)." }

$Exe = Join-Path $DistPath "ironjarvis\ironjarvis.exe"
if (-not (Test-Path $Exe)) { throw "expected exe not produced at $Exe" }
Write-Host "Built: $Exe" -ForegroundColor Green

# v1.538.0 (fourth review group 29): the same spec builds the connected apps'
# bridge, a one-file program with its content id beside it. The installer
# ships that folder (desktop/package.json -> resources/bridge).
$Bridge = Join-Path $DistPath "bridge\ironjarvis-bridge.exe"
$BridgeId = Join-Path $DistPath "bridge\bridge-id.txt"
if (-not (Test-Path $Bridge)) { throw "expected bridge not produced at $Bridge" }
if (-not (Test-Path $BridgeId)) { throw "expected bridge id not produced at $BridgeId" }
Write-Host "Built: $Bridge" -ForegroundColor Green

if ($Verify) {
    $StateDir = Join-Path $env:TEMP ("ironjarvis-freeze-verify-" + [guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Force -Path $StateDir | Out-Null
    Write-Host "Booting frozen daemon on port $Port (state: $StateDir) ..." -ForegroundColor Cyan
    $proc = Start-Process -FilePath $Exe -ArgumentList @("serve", "--port", "$Port", "--root", $StateDir) -PassThru
    try {
        $ok = $false
        for ($i = 0; $i -lt 30; $i++) {
            Start-Sleep -Seconds 1
            try {
                $resp = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/health" -UseBasicParsing -TimeoutSec 3
                if ($resp.StatusCode -eq 200) {
                    Write-Host "HEALTH 200:" -ForegroundColor Green
                    Write-Host $resp.Content
                    $ok = $true
                    break
                }
            } catch { }
        }
        if (-not $ok) { throw "frozen daemon did not serve /health 200 within 30s" }
    } finally {
        if ($proc -and -not $proc.HasExited) { Stop-Process -Id $proc.Id -Force }
        Remove-Item -Recurse -Force $StateDir -ErrorAction SilentlyContinue
    }
    Write-Host "VERIFIED: frozen daemon booted offline and served /health 200." -ForegroundColor Green
}
