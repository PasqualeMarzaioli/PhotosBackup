#!/bin/bash
# Verifica se il backup del mese è stato completato
# Se no, lo esegue automaticamente (fallback per quando il Mac è stato spento il 2°)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

LOG="$SCRIPT_DIR/backup.log"

# Determina il mese attuale (il backup è sempre del mese precedente)
MONTH_NAME=$(python3 -c "from datetime import datetime; import config; d=datetime.now(); m=d.month-1 if d.month>1 else 12; print(config.MONTHS[m])")
YEAR=$(python3 -c "from datetime import datetime; d=datetime.now(); y=d.year if d.month>1 else d.year-1; print(y)")
MONTH_NUM=$(python3 -c "from datetime import datetime; d=datetime.now(); m=d.month-1 if d.month>1 else 12; print(m)")

# Controlla se il backup di questo mese è stato completato
if grep -q "BACKUP COMPLETED.*${MONTH_NAME}.*${YEAR}" "$LOG" 2>/dev/null; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') Backup di ${MONTH_NAME} ${YEAR} già completato - nessuna azione necessaria"
    exit 0
else
    echo "$(date '+%Y-%m-%d %H:%M:%S') Backup di ${MONTH_NAME} ${YEAR} non trovato nel log - esecuzione in corso..."
    python3 "$SCRIPT_DIR/backup.py" "$YEAR" "$MONTH_NUM"
    exit $?
fi
