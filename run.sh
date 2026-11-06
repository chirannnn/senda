#!/usr/bin/env bash
# Run with Git Bash on Windows or Bash on Linux/macOS.
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "usage: ./run.sh <SIM_URL>" >&2
  exit 2
fi

SIM_URL="$1"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

if [[ -x "$SCRIPT_DIR/.venv/bin/python" ]]; then
  PYTHON_BIN="$SCRIPT_DIR/.venv/bin/python"
elif [[ -x "$SCRIPT_DIR/.venv/Scripts/python.exe" ]]; then
  PYTHON_BIN="$SCRIPT_DIR/.venv/Scripts/python.exe"
else
  echo "Python environment not found at $SCRIPT_DIR/.venv; create it with Python 3.12 first." >&2
  exit 2
fi

export PYTHONPATH="$SCRIPT_DIR${PYTHONPATH:+:$PYTHONPATH}"
exec "$PYTHON_BIN" -m controller "$SIM_URL"
