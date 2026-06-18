#!/bin/bash
# Run the Python Google Photos -> OneDrive backup.
# Author: Pasquale Marzaioli

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [ -z "${PYTHON_BIN:-}" ]; then
    if [ -x "$SCRIPT_DIR/venv/bin/python" ]; then
        PYTHON_BIN="$SCRIPT_DIR/venv/bin/python"
    elif command -v python3.11 >/dev/null 2>&1; then
        PYTHON_BIN="$(command -v python3.11)"
    else
        PYTHON_BIN="$(command -v python3)"
    fi
fi

exec "$PYTHON_BIN" "$SCRIPT_DIR/backup.py" "$@"
