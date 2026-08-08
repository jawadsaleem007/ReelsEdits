# ReelsEdits - start the app (Windows).
#
#   .\run.ps1
#   .\run.ps1 -Port 9000
#   .\run.ps1 -Reinstall      force dependency reinstall
#
# Needs Python 3.10+ and ffmpeg on PATH.
param([int]$Port = 8000, [switch]$Reinstall)

# NOTE: deliberately NOT $ErrorActionPreference = "Stop".
#
# The dependency probe below is *expected* to fail on a fresh checkout, and
# under "Stop" PowerShell promotes a native command's stderr into a terminating
# NativeCommandError -- so the script aborted on the very check whose job is to
# detect "not installed" and then install. Failures are checked explicitly via
# $LASTEXITCODE instead.
$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot

function Test-Imports {
    param([string]$Python)
    # *> $null swallows every stream (stdout, stderr, warning...). Without it
    # a failed import prints a traceback that looks like the script crashed.
    & $Python -c "import reelsedits_common, reelsedits_analyzer, reelsedits_renderer, app.main" *> $null
    return ($LASTEXITCODE -eq 0)
}

if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
    Write-Host "ffmpeg is required and was not found on PATH." -ForegroundColor Red
    Write-Host "  Install with:  winget install Gyan.FFmpeg"
    Write-Host "  Then open a NEW terminal so PATH updates."
    exit 1
}

$py  = ".\.venv\Scripts\python.exe"
$pip = ".\.venv\Scripts\pip.exe"

if (-not (Test-Path $py)) {
    Write-Host "Creating virtual environment..."
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { Write-Host "Could not create .venv" -ForegroundColor Red; exit 1 }
}

# Check that the PACKAGES import, not merely that a .venv folder exists. A
# directory check passes for a venv created by hand, or one left behind by an
# install that failed partway -- and then the server starts with no
# dependencies and dies on the first import.
if ($Reinstall -or -not (Test-Imports $py)) {
    Write-Host "Installing dependencies (a few minutes on first run)..."
    & $pip install --quiet --upgrade pip
    & $pip install --quiet `
        -e services/common -e services/matcher -e services/analyzer `
        -e services/indexer -e services/renderer -e services/cli -e services/api
    if ($LASTEXITCODE -ne 0) {
        Write-Host "`nDependency install failed - see the output above." -ForegroundColor Red
        exit 1
    }

    if (-not (Test-Imports $py)) {
        Write-Host "`nInstall finished but the packages still do not import." -ForegroundColor Red
        Write-Host "Run this to see the real error:"
        Write-Host "  .\.venv\Scripts\python -c `"import app.main`""
        exit 1
    }
    Write-Host "Dependencies ready."
}

New-Item -ItemType Directory -Force -Path data/storage | Out-Null
$root = $PWD.Path -replace '\\', '/'
$env:REELSEDITS_DATABASE_URL = "sqlite:///$root/data/reelsedits.db"
$env:REELSEDITS_STORAGE_ROOT = "$root/data/storage"

Write-Host ""
Write-Host "  ReelsEdits  ->  http://localhost:$Port" -ForegroundColor Green
Write-Host "  API docs    ->  http://localhost:$Port/docs"
Write-Host ""
& $py -m uvicorn app.main:app --app-dir services/api --host 0.0.0.0 --port $Port
