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

read -r DAY NOT_BEFORE_DAY YEAR MONTH_NUM < <("$PYTHON_BIN" - <<'PY'
# Calculate the backup window in one process to avoid midnight inconsistencies.
# Author: Pasquale Marzaioli
from datetime import datetime
import config

now = datetime.now()
month = now.month - 1 if now.month > 1 else 12
year = now.year if now.month > 1 else now.year - 1
print(now.day, getattr(config, 'BACKUP_NOT_BEFORE_DAY', 2), year, month)
PY
)

if [ "$DAY" -lt "$NOT_BEFORE_DAY" ]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') Backup check skipped before day ${NOT_BEFORE_DAY}."
    exit 0
fi

MONTH_NAME="$("$PYTHON_BIN" -c "import config; print(config.MONTHS[$MONTH_NUM])")"

export LOG MONTH_NAME YEAR
if "$PYTHON_BIN" - <<'PY'
# Recognize only a completed run with a zero total error count.
# Author: Pasquale Marzaioli
import os
import re
import sys

log_path = os.environ["LOG"]
target = f"BACKUP COMPLETED: {os.environ['MONTH_NAME']} {os.environ['YEAR']}"

try:
    with open(log_path, "r", encoding="utf-8") as fh:
        target_run = False
        for line in fh:
            if "BACKUP STARTED:" in line:
                target_run = False
            elif "BACKUP COMPLETED:" in line:
                target_run = target in line
            elif target_run and re.search(r"\bINFO\s+Errors\s*:\s*0\s*$", line):
                sys.exit(0)
except FileNotFoundError:
    sys.exit(1)

sys.exit(1)
PY
then
    echo "$(date '+%Y-%m-%d %H:%M:%S') Successful backup for ${MONTH_NAME} ${YEAR} already exists."
    exit 0
fi

echo "$(date '+%Y-%m-%d %H:%M:%S') Successful backup for ${MONTH_NAME} ${YEAR} not found; starting backup."
exec "$PYTHON_BIN" "$SCRIPT_DIR/backup.py" "$YEAR" "$MONTH_NUM"
