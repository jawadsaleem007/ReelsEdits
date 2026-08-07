#!/usr/bin/env bash
# ReelsEdits — start the app.
#
#   ./run.sh            http://localhost:8000
#   ./run.sh --port 9000
#
# Needs Python 3.10+ and ffmpeg (with libx264) on PATH. Everything else is
# installed into a local venv on first run.
set -euo pipefail
cd "$(dirname "$0")"

PORT=8000
while [[ $# -gt 0 ]]; do
  case "$1" in
    --port) PORT="$2"; shift 2 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

if ! command -v ffmpeg >/dev/null; then
  echo "ffmpeg is required and was not found on PATH." >&2
  echo "  macOS:  brew install ffmpeg" >&2
  echo "  Ubuntu: sudo apt install ffmpeg" >&2
  echo "  Windows: winget install Gyan.FFmpeg" >&2
  exit 1
fi

if [[ ! -d .venv ]]; then
  echo "First run — creating a virtual environment and installing dependencies."
  echo "This takes a couple of minutes; afterwards startup is instant."
  python3 -m venv .venv
  ./.venv/bin/pip install --quiet --upgrade pip
  ./.venv/bin/pip install --quiet \
    -e services/common -e services/matcher -e services/analyzer \
    -e services/indexer -e services/renderer -e services/cli -e services/api
fi

mkdir -p data/storage
export REELSEDITS_DATABASE_URL="${REELSEDITS_DATABASE_URL:-sqlite:///$(pwd)/data/reelsedits.db}"
export REELSEDITS_STORAGE_ROOT="${REELSEDITS_STORAGE_ROOT:-$(pwd)/data/storage}"

echo
echo "  ReelsEdits  →  http://localhost:${PORT}"
echo "  API docs    →  http://localhost:${PORT}/docs"
echo
exec ./.venv/bin/python -m uvicorn app.main:app \
  --app-dir services/api --host 0.0.0.0 --port "$PORT"
