#!/bin/bash
# Path generalization
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Get configuration and secrets from config.py
BOT_TOKEN=$(python3 -c "import config; print(config.TELEGRAM_BOT_TOKEN)")
CHAT_ID=$(python3 -c "import config; print(config.TELEGRAM_CHAT_ID)")
MONTH_NAME=$(python3 -c "from datetime import datetime; import config; d=datetime.now(); m=d.month-1 if d.month>1 else 12; print(config.MONTHS[m])")
YEAR=$(python3 -c "from datetime import datetime; d=datetime.now(); y=d.year if d.month>1 else d.year-1; print(y)")

PREVIOUS_MONTH="$MONTH_NAME $YEAR"

MESSAGE="📸 Backup reminder for tomorrow

Tomorrow a backup of your photos from $PREVIOUS_MONTH will be executed to OneDrive.

If there are any photos you DO NOT want to save, delete them now from Google Photos before the backup starts tomorrow morning at 09:00."

curl -s -X POST "https://api.telegram.org/bot${BOT_TOKEN}/sendMessage" \
  --data-urlencode "chat_id=${CHAT_ID}" \
  --data-urlencode "text=${MESSAGE}" \
  > /dev/null
