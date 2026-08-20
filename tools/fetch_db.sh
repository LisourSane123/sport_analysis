#!/usr/bin/env bash
# Pobiera spojna kopie bazy z Raspberry Pi na maszyne, na ktorej liczysz model.
#   bash tools/fetch_db.sh pi@raspberrypi
#   bash tools/fetch_db.sh pi@raspberrypi /home/pi/Waga_RP/data/waga.db
#
# Kopiowanie samego pliku przez scp/rsync przy dzialajacych uslugach jest
# ryzykowne: baza chodzi w trybie WAL i czesc danych siedzi wtedy w pliku -wal.
# Dlatego najpierw robimy na Pi migawke przez ".backup", a dopiero ja kopiujemy.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REMOTE="${1:-}"
REMOTE_DB="${2:-~/Waga_RP/data/waga.db}"
LOCAL_DB="$ROOT/data/waga.db"

if [[ -z "$REMOTE" ]]; then
  echo "Uzycie: bash tools/fetch_db.sh uzytkownik@host [sciezka-do-bazy-na-pi]" >&2
  exit 1
fi

TMP_REMOTE="/tmp/waga-snapshot-$$.db"
echo "==> Migawka na $REMOTE"
ssh "$REMOTE" "sqlite3 '$REMOTE_DB' \".backup '$TMP_REMOTE'\""

echo "==> Kopiowanie"
mkdir -p "$ROOT/data"
scp -q "$REMOTE:$TMP_REMOTE" "$LOCAL_DB"
ssh "$REMOTE" "rm -f '$TMP_REMOTE'"

echo "==> Gotowe: $LOCAL_DB"
if command -v sqlite3 >/dev/null; then
  sqlite3 "$LOCAL_DB" "SELECT '    pomiary: ' || COUNT(*) FROM measurements;
                       SELECT '    treningi: ' || COUNT(*) FROM garmin_activities;
                       SELECT '    dni Garmina: ' || COUNT(*) FROM garmin_daily;"
fi
echo
echo "To kopia do odczytu - uslugi na Pi pisza dalej do swojej bazy."
echo "Odswiezysz ja, uruchamiajac ten skrypt ponownie."
