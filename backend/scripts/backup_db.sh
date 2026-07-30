#!/usr/bin/env bash
# Безопасная резервная копия SQLite-базы через .backup (консистентна даже
# при работающем приложении, в отличие от простого cp).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_DIR="${CLINIC_DATA_DIR:-$SCRIPT_DIR/../data}"
DB_PATH="$DATA_DIR/clinic.db"
BACKUP_DIR="$DATA_DIR/backups"

if [ ! -f "$DB_PATH" ]; then
    echo "База не найдена: $DB_PATH" >&2
    exit 1
fi

mkdir -p "$BACKUP_DIR"
STAMP="$(date +%Y%m%d_%H%M%S)"
DEST="$BACKUP_DIR/clinic_$STAMP.db"

sqlite3 "$DB_PATH" ".backup '$DEST'"
chmod 600 "$DEST"
echo "Резервная копия создана: $DEST"
echo "ВНИМАНИЕ: копия содержит медицинские данные — храните её так же защищённо, как основную базу."
