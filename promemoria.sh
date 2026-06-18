#!/bin/bash
# Send a Telegram reminder before the monthly backup window.
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

BOT_TOKEN=$("$PYTHON_BIN" -c "import config; print(config.TELEGRAM_BOT_TOKEN)")
CHAT_ID=$("$PYTHON_BIN" -c "import config; print(config.TELEGRAM_CHAT_ID)")
MONTH_NAME=$("$PYTHON_BIN" -c "from datetime import datetime; import config; d=datetime.now(); m=d.month-1 if d.month>1 else 12; print(config.MONTHS[m])")
YEAR=$("$PYTHON_BIN" -c "from datetime import datetime; d=datetime.now(); print(d.year if d.month>1 else d.year-1)")

PREVIOUS_MONTH="$MONTH_NAME $YEAR"

MESSAGE="📸 Backup reminder for tomorrow

Tomorrow a backup of your photos from $PREVIOUS_MONTH will be executed to OneDrive.

If there are any photos you DO NOT want to save, delete them now from Google Photos before the backup starts tomorrow morning at 09:00."

curl -s -X POST "https://api.telegram.org/bot${BOT_TOKEN}/sendMessage" \
  --data-urlencode "chat_id=${CHAT_ID}" \
  --data-urlencode "text=${MESSAGE}" \
  > /dev/null
