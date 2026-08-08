# ReelsEdits - start the app (Windows).
#
#   .\run.ps1
#   .\run.ps1 -Port 9000
#   .\run.ps1 -Reinstall      force dependency reinstall
#
# Needs Python 3.10+ and ffmpeg on PATH.
param([int]$Port = 8000, [switch]$Reinstall)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
  Write-Error "ffmpeg is required and was not found on PATH. Install with: winget install Gyan.FFmpeg"
  exit 1
}

$py  = ".\.venv\Scripts\python.exe"
$pip = ".\.venv\Scripts\pip.exe"

if (-not (Test-Path $py)) {
  Write-Host "Creating virtual environment..."
  python -m venv .venv
}

# Check that the PACKAGES import, not merely that a .venv folder exists.
# A directory check passes for a venv the user created by hand, or one left
# behind by an install that failed partway -- and then the server starts with
# no dependencies and dies on the first import.
$needsInstall = $Reinstall
if (-not $needsInstall) {
  & $py -c "import reelsedits_common, reelsedits_analyzer, reelsedits_renderer, app.main" 2>$null
  $needsInstall = ($LASTEXITCODE -ne 0)
}

if ($needsInstall) {
  Write-Host "Installing dependencies (a couple of minutes on first run)..."
  & $pip install --quiet --upgrade pip
  & $pip install --quiet `
    -e services/common -e services/matcher -e services/analyzer `
    -e services/indexer -e services/renderer -e services/cli -e services/api
  if ($LASTEXITCODE -ne 0) { Write-Error "Dependency install failed - see the output above."; exit 1 }

  & $py -c "import reelsedits_common, app.main" 2>$null
  if ($LASTEXITCODE -ne 0) {
    Write-Error "Install finished but packages still do not import. Try: .\run.ps1 -Reinstall"
    exit 1
  }
  Write-Host "Dependencies ready."
}

New-Item -ItemType Directory -Force -Path data/storage | Out-Null
$root = $PWD.Path -replace '\\','/'
$env:REELSEDITS_DATABASE_URL = "sqlite:///$root/data/reelsedits.db"
$env:REELSEDITS_STORAGE_ROOT = "$root/data/storage"

Write-Host ""
Write-Host "  ReelsEdits  ->  http://localhost:$Port"
Write-Host "  API docs    ->  http://localhost:$Port/docs"
Write-Host ""
& $py -m uvicorn app.main:app --app-dir services/api --host 0.0.0.0 --port $Port
