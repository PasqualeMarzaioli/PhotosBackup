#!/bin/bash
# Run the monthly backup if a successful run for the previous month is missing.
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

LOG="$SCRIPT_DIR/backup.log"

DAY="$("$PYTHON_BIN" -c "from datetime import datetime; print(datetime.now().day)")"
NOT_BEFORE_DAY="$("$PYTHON_BIN" -c "import config; print(getattr(config, 'BACKUP_NOT_BEFORE_DAY', 2))")"

if [ "$DAY" -lt "$NOT_BEFORE_DAY" ]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') Backup check skipped before day ${NOT_BEFORE_DAY}."
    exit 0
fi

MONTH_NAME="$("$PYTHON_BIN" -c "from datetime import datetime; import config; d=datetime.now(); m=d.month-1 if d.month>1 else 12; print(config.MONTHS[m])")"
YEAR="$("$PYTHON_BIN" -c "from datetime import datetime; d=datetime.now(); print(d.year if d.month>1 else d.year-1)")"
MONTH_NUM="$("$PYTHON_BIN" -c "from datetime import datetime; d=datetime.now(); print(d.month-1 if d.month>1 else 12)")"

export LOG MONTH_NAME YEAR
if "$PYTHON_BIN" - <<'PY'
import os
import re
import sys

log_path = os.environ["LOG"]
target = f"BACKUP COMPLETED: {os.environ['MONTH_NAME']} {os.environ['YEAR']}"

try:
    with open(log_path, "r", encoding="utf-8") as fh:
        lines = fh.readlines()
except FileNotFoundError:
    sys.exit(1)

for index, line in enumerate(lines):
    if target in line:
        block = "".join(lines[index:index + 10])
        if re.search(r"Errors\s*:\s*0\b", block):
            sys.exit(0)

sys.exit(1)
PY
then
    echo "$(date '+%Y-%m-%d %H:%M:%S') Successful backup for ${MONTH_NAME} ${YEAR} already exists."
    exit 0
fi

echo "$(date '+%Y-%m-%d %H:%M:%S') Successful backup for ${MONTH_NAME} ${YEAR} not found; starting backup."
exec "$PYTHON_BIN" "$SCRIPT_DIR/backup.py" "$YEAR" "$MONTH_NUM"
