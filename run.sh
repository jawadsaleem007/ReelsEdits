#!/usr/bin/env bash
# ReelsEdits — start the app.
#
#   ./run.sh                macOS / Linux
#   ./run.sh --port 9000
#   ./run.sh --reinstall    force dependency reinstall
#
# Needs Python 3.10+ and ffmpeg (with libx264) on PATH.
set -euo pipefail
cd "$(dirname "$0")"

PORT=8000
REINSTALL=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --port) PORT="$2"; shift 2 ;;
    --reinstall) REINSTALL=1; shift ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

if ! command -v ffmpeg >/dev/null; then
  echo "ffmpeg is required and was not found on PATH." >&2
  echo "  macOS:  brew install ffmpeg" >&2
  echo "  Ubuntu: sudo apt install ffmpeg" >&2
  exit 1
fi

PY=./.venv/bin/python
PIP=./.venv/bin/pip

[[ -x "$PY" ]] || { echo "Creating virtual environment..."; python3 -m venv .venv; }

# Verify the PACKAGES import rather than that a .venv folder exists. A
# directory check passes for a hand-made venv, or one left by an install that
# failed partway -- and then the server starts with no dependencies and dies on
# the first import.
NEEDS_INSTALL=$REINSTALL
if [[ $NEEDS_INSTALL -eq 0 ]]; then
  "$PY" -c "import reelsedits_common, reelsedits_analyzer, reelsedits_renderer, app.main" \
    >/dev/null 2>&1 || NEEDS_INSTALL=1
fi

if [[ $NEEDS_INSTALL -eq 1 ]]; then
  echo "Installing dependencies (a couple of minutes on first run)..."
  "$PIP" install --quiet --upgrade pip
  "$PIP" install --quiet \
    -e services/common -e services/matcher -e services/analyzer \
    -e services/indexer -e services/renderer -e services/cli -e services/api
  "$PY" -c "import reelsedits_common, app.main" >/dev/null 2>&1 || {
    echo "Install finished but packages still do not import. Try: ./run.sh --reinstall" >&2
    exit 1
  }
  echo "Dependencies ready."
fi

mkdir -p data/storage
export REELSEDITS_DATABASE_URL="${REELSEDITS_DATABASE_URL:-sqlite:///$(pwd)/data/reelsedits.db}"
export REELSEDITS_STORAGE_ROOT="${REELSEDITS_STORAGE_ROOT:-$(pwd)/data/storage}"

echo
echo "  ReelsEdits  →  http://localhost:${PORT}"
echo "  API docs    →  http://localhost:${PORT}/docs"
echo
exec "$PY" -m uvicorn app.main:app --app-dir services/api --host 0.0.0.0 --port "$PORT"
