# ReelsEdits - start the app (Windows).
#
#   .\run.ps1
#   .\run.ps1 -Port 9000
#
# Needs Python 3.10+ and ffmpeg on PATH.
param([int]$Port = 8000)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
  Write-Error "ffmpeg is required and was not found on PATH. Install with: winget install Gyan.FFmpeg"
  exit 1
}

if (-not (Test-Path .venv)) {
  Write-Host "First run - creating a virtual environment and installing dependencies."
  Write-Host "This takes a couple of minutes; afterwards startup is instant."
  python -m venv .venv
  .\.venv\Scripts\pip install --quiet --upgrade pip
  .\.venv\Scripts\pip install --quiet `
    -e services/common -e services/matcher -e services/analyzer `
    -e services/indexer -e services/renderer -e services/cli -e services/api
}

New-Item -ItemType Directory -Force -Path data/storage | Out-Null
$env:REELSEDITS_DATABASE_URL = "sqlite:///$($PWD.Path -replace '\\','/')/data/reelsedits.db"
$env:REELSEDITS_STORAGE_ROOT = "$($PWD.Path)/data/storage"

Write-Host ""
Write-Host "  ReelsEdits  ->  http://localhost:$Port"
Write-Host "  API docs    ->  http://localhost:$Port/docs"
Write-Host ""
.\.venv\Scripts\python -m uvicorn app.main:app --app-dir services/api --host 0.0.0.0 --port $Port
