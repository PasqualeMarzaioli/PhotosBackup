#!/bin/bash
# Backup Google Photos -> OneDrive (via rclone)
# Executed automatically every 2nd of the month at 09:00

# Path generalization
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Get configuration and secrets from config.py
BOT_TOKEN=$(python3 -c "import config; print(config.TELEGRAM_BOT_TOKEN)")
CHAT_ID=$(python3 -c "import config; print(config.TELEGRAM_CHAT_ID)")
MONTH_NAME=$(python3 -c "from datetime import datetime; import config; d=datetime.now(); m=d.month-1 if d.month>1 else 12; print(config.MONTHS[m])")
YEAR=$(python3 -c "from datetime import datetime; d=datetime.now(); y=d.year if d.month>1 else d.year-1; print(y)")
MONTH_NUM=$(python3 -c "from datetime import datetime; d=datetime.now(); m=d.month-1 if d.month>1 else 12; print(f'{m:02d}')")

LOG="$SCRIPT_DIR/backup.log"

send_telegram() {
    curl -s -X POST "https://api.telegram.org/bot${BOT_TOKEN}/sendMessage" \
        --data-urlencode "chat_id=${CHAT_ID}" \
        --data-urlencode "text=$1" > /dev/null
}

MONTH_FOLDER="${MONTH_NUM}.${MONTH_NAME} ${YEAR}"
DEST="onedrive:Memorie/immagine/${YEAR}/${MONTH_FOLDER}"
SOURCE="googlephotos:media/by-month/${YEAR}/${YEAR}-${MONTH_NUM}"

echo "========================================" | tee -a "$LOG"
echo "$(date '+%Y-%m-%d %H:%M:%S') BACKUP STARTED: ${MONTH_NAME} ${YEAR}" | tee -a "$LOG"
echo "Source : $SOURCE" | tee -a "$LOG"
echo "Destination: $DEST" | tee -a "$LOG"
echo "========================================" | tee -a "$LOG"

# IMPORTANT: Ensure rclone is installed and configured as 'onedrive' and 'googlephotos'
rclone copy "$SOURCE" "$DEST" \
    --progress \
    --log-file="$LOG" \
    --log-level INFO \
    2>&1

EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') BACKUP COMPLETED successfully" | tee -a "$LOG"
    send_telegram "✅ Backup completed!

The photos of ${MONTH_NAME} ${YEAR} are safely stored on OneDrive.

🗑 Remember to delete the photos of ${MONTH_NAME} ${YEAR} from Google Photos!"
else
    echo "$(date '+%Y-%m-%d %H:%M:%S') BACKUP FAILED (error code: $EXIT_CODE)" | tee -a "$LOG"
    send_telegram "❌ Backup for ${MONTH_NAME} ${YEAR} failed!

Check the log for details:
$LOG"
fi
