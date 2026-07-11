<#
.SYNOPSIS
    Build the SELF-CONTAINED Iron Jarvis Windows installer (NSIS .exe).

.DESCRIPTION
    End-to-end pipeline (the installed app needs NO Python / uv / Node / pnpm):
      1. Freeze the daemon (PyInstaller)        -> packaging/dist/ironjarvis/
      2. Build the dashboard (Next standalone)  -> dashboard/.next/standalone/
      3. Stage .next/static (+ public) INTO the standalone bundle (Next won't)
      4. electron-builder                       -> desktop/release/*.exe

    Run from anywhere:
        pnpm run dist:full       (from desktop/)
        powershell -ExecutionPolicy Bypass -File desktop\build-installer.ps1

.PARAMETER SkipDaemon
    Reuse an existing packaging/dist/ironjarvis build (skip PyInstaller).
.PARAMETER SkipDashboard
    Reuse an existing dashboard/.next/standalone build (skip pnpm build).
.PARAMETER Publish
    Publish the installer to GitHub Releases (CI only; needs GH_TOKEN). Off by
    default -- a local build never publishes anything.
#>
[CmdletBinding()]
param([switch]$SkipDaemon, [switch]$SkipDashboard, [switch]$Publish)

$ErrorActionPreference = "Stop"

# Run a native command (pnpm/electron-builder) WITHOUT letting its stderr abort
# the script -- Windows PowerShell wraps native stderr in a terminating
# NativeCommandError under EAP=Stop. We relax EAP for the call and check the
# real exit code.
function Invoke-Native {
    param([Parameter(Mandatory)][string]$What, [Parameter(Mandatory)][scriptblock]$Cmd)
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { & $Cmd 2>&1 | ForEach-Object { Write-Host $_ } }
    finally { $ErrorActionPreference = $prev }
    if ($LASTEXITCODE -ne 0) { throw "$What failed (exit $LASTEXITCODE)" }
}

$Desktop   = $PSScriptRoot
$Root      = Split-Path -Parent $Desktop
$Dashboard = Join-Path $Root "dashboard"
$Packaging = Join-Path $Root "packaging"

# 0) Single-source version -------------------------------------------------
# electron-updater compares the app version from desktop/package.json against
# the GitHub release; if it drifts from pyproject/the daemon, the update channel
# silently no-ops. Stamp package.json from the pushed tag (CI) or pyproject, and
# on a publish REFUSE a tag that doesn't match pyproject.
Write-Host "==> [0/4] Syncing version..." -ForegroundColor Cyan
$PyProject = Join-Path $Root "pyproject.toml"
$pyVer = (Select-String -Path $PyProject -Pattern '^version\s*=\s*"([^"]+)"' |
    Select-Object -First 1).Matches.Groups[1].Value
# Only a TAG ref names a version -- on a branch push GITHUB_REF_NAME is the
# branch itself ("master"), which must never be mistaken for a version.
$tag = if ($env:GITHUB_REF_TYPE -eq "tag") { $env:GITHUB_REF_NAME } else { $null }
if ($tag -and $tag -match '^v?(.+)$') { $ver = $Matches[1] } else { $ver = $pyVer }
if ($Publish -and $ver -ne $pyVer) {
    throw "version mismatch: release tag '$ver' != pyproject '$pyVer' -- tag must match pyproject.toml."
}
$PkgJson = Join-Path $Desktop "package.json"
$pkgText = Get-Content $PkgJson -Raw
$pkgText = $pkgText -replace '("version":\s*")[^"]+(")', "`${1}$ver`${2}"
# Write WITHOUT a BOM: Windows PowerShell 5.1 Set-Content -Encoding utf8
# prepends EF BB BF, which corrupts package.json for strict JSON parsers (and the
# version-drift test). UTF8Encoding($false) = no BOM.
[IO.File]::WriteAllText($PkgJson, $pkgText, (New-Object Text.UTF8Encoding($false)))
Write-Host "    desktop/package.json version = $ver (pyproject $pyVer)" -ForegroundColor Green

# 1) Freeze the daemon -----------------------------------------------------
if (-not $SkipDaemon) {
    Write-Host "==> [1/4] Freezing the daemon (PyInstaller)..." -ForegroundColor Cyan
    & (Join-Path $Packaging "build_daemon.ps1")
}
$DaemonExe = Join-Path $Packaging "dist\ironjarvis\ironjarvis.exe"
if (-not (Test-Path $DaemonExe)) { throw "daemon exe missing: $DaemonExe (run without -SkipDaemon)" }

# 2) Build the dashboard (standalone) --------------------------------------
if (-not $SkipDashboard) {
    Write-Host "==> [2/4] Building the dashboard (Next standalone)..." -ForegroundColor Cyan
    Push-Location $Dashboard
    try {
        Invoke-Native "pnpm install (dashboard)" { pnpm install }
        Invoke-Native "pnpm build (dashboard)" { pnpm build }
    } finally { Pop-Location }
}
$Standalone = Join-Path $Dashboard ".next\standalone"
if (-not (Test-Path (Join-Path $Standalone "server.js"))) {
    throw "standalone server.js missing (run without -SkipDashboard)"
}

# 3) Stage static + public into the standalone bundle ----------------------
Write-Host "==> [3/4] Staging static assets into the standalone bundle..." -ForegroundColor Cyan
$StaticSrc = Join-Path $Dashboard ".next\static"
$StaticDst = Join-Path $Standalone ".next\static"
if (Test-Path $StaticDst) { Remove-Item -Recurse -Force $StaticDst }
New-Item -ItemType Directory -Force -Path (Split-Path $StaticDst) | Out-Null
Copy-Item -Recurse -Force $StaticSrc $StaticDst
$PublicSrc = Join-Path $Dashboard "public"
if (Test-Path $PublicSrc) { Copy-Item -Recurse -Force $PublicSrc (Join-Path $Standalone "public") }

# 4) Package the installer -------------------------------------------------
Write-Host "==> [4/4] Packaging the installer (electron-builder)..." -ForegroundColor Cyan
Push-Location $Desktop
try {
    Invoke-Native "pnpm install (desktop)" { pnpm install }
    try {
        if ($Publish) {
            Invoke-Native "electron-builder (publish)" { pnpm exec electron-builder --win --publish always }
        } else {
            Invoke-Native "electron-builder" { pnpm dist }
        }
    } catch {
        if ("$_" -match "symbolic link" -or "$_" -match "winCodeSign") {
            Write-Host ""
            Write-Host "electron-builder could not unpack its winCodeSign cache because this" -ForegroundColor Yellow
            Write-Host "Windows session lacks the symlink-creation privilege (the cache contains" -ForegroundColor Yellow
            Write-Host "macOS symlinks). Fix it ONE of these ways, then re-run:" -ForegroundColor Yellow
            Write-Host "  1. Settings > Privacy & security > For developers > Developer Mode = On" -ForegroundColor Yellow
            Write-Host "  2. Run this script from an ELEVATED (Administrator) PowerShell" -ForegroundColor Yellow
            Write-Host "  3. Let CI build it: 'git tag vX.Y.Z; git push --tags' -> .github/workflows/release.yml" -ForegroundColor Yellow
            Write-Host "(The frozen daemon + standalone dashboard already built fine; only the" -ForegroundColor Yellow
            Write-Host " final installer-packaging step needs this privilege.)" -ForegroundColor Yellow
        }
        throw
    }
} finally { Pop-Location }

Write-Host "`n==> DONE. Installer(s) in desktop\release\:" -ForegroundColor Green
Get-ChildItem (Join-Path $Desktop "release") -Filter *.exe -ErrorAction SilentlyContinue |
    ForEach-Object { Write-Host ("    {0}  ({1:N1} MB)" -f $_.Name, ($_.Length / 1MB)) }

# 5) Verify the Authenticode signature -------------------------------------
# Mirror of the credential gate in desktop/sign.js. If signing creds were
# present, sign.js MUST have produced a valid, timestamped signature -- FAIL
# the build if it didn't (a silently-unsigned "signed" release is worse than an
# openly-unsigned one). If no creds were present, the installer is expected to
# be unsigned -- just report it and exit clean so CI stays green.
Write-Host "==> [5/5] Verifying Authenticode signature..." -ForegroundColor Cyan
$signExpected =
    ($env:AZURE_TENANT_ID -and $env:AZURE_CLIENT_ID -and $env:AZURE_CLIENT_SECRET -and
     $env:IJ_SIGN_ENDPOINT -and $env:IJ_SIGN_ACCOUNT -and $env:IJ_SIGN_PROFILE) -or
    ($env:IJ_SIGNTOOL_PATH -and $env:IJ_SIGN_SHA1)

$exes = Get-ChildItem (Join-Path $Desktop "release") -Filter *.exe -ErrorAction SilentlyContinue
if (-not $exes) { throw "no installer .exe found in desktop\release to verify" }

foreach ($exe in $exes) {
    $sig = Get-AuthenticodeSignature $exe.FullName
    $hasTimestamp = $null -ne $sig.TimeStamperCertificate
    if ($signExpected) {
        if ($sig.Status -ne 'Valid') {
            throw "signing creds were present but $($exe.Name) is not validly signed (status: $($sig.Status)). Check desktop/sign.js and the signing tool output above."
        }
        if (-not $hasTimestamp) {
            throw "signing creds were present but $($exe.Name) has no timestamp -- the signature will expire with the certificate. Ensure the timestamp URL is reachable."
        }
        Write-Host ("    SIGNED + timestamped: {0}  [{1}]" -f $exe.Name, $sig.SignerCertificate.Subject) -ForegroundColor Green
    } else {
        Write-Host ("    unsigned (no cert configured): {0}  [status: {1}]" -f $exe.Name, $sig.Status) -ForegroundColor Yellow
    }
}
if (-not $signExpected) {
    Write-Host "    NOTE: unsigned installers trip SmartScreen 'unknown publisher'. See docs/SIGNING.md to configure signing." -ForegroundColor Yellow
}
