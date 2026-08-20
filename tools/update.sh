#!/usr/bin/env bash
# Aktualizacja Waga_RP na Raspberry Pi: pobranie zmian, zaleznosci, restart uslug.
#   bash tools/update.sh
#
# Restart jest tu kluczowy: `git pull` podmienia pliki na dysku, ale kod Pythona
# siedzi w pamieci dzialajacych procesow az do restartu.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "==> Kopia zapasowa bazy"
DB="${DB_PATH:-data/waga.db}"
if [[ -f "$DB" ]]; then
  sqlite3 "$DB" ".backup '${DB%.db}-kopia-$(date +%F-%H%M).db'" && echo "    zrobiona"
else
  echo "    (brak bazy, pomijam)"
fi

echo "==> Pobieranie zmian"
git pull --ff-only

echo "==> Zaleznosci"
"$ROOT/.venv/bin/pip" install --quiet --upgrade -r requirements.txt

echo "==> Migracja bazy (schemat + naprawa dat z nieustawionego zegara wagi)"
"$ROOT/.venv/bin/python" -m app.db

echo "==> Restart uslug"
RESTARTED=()
for unit in waga-scale waga-garmin waga-dashboard; do
  if systemctl list-unit-files "$unit.service" >/dev/null 2>&1 \
     && systemctl is-enabled --quiet "$unit" 2>/dev/null; then
    sudo systemctl restart "$unit" && RESTARTED+=("$unit")
  fi
done
echo "    zrestartowane: ${RESTARTED[*]:-brak}"

echo
echo "Gotowe. Sprawdz stan:"
echo "  systemctl status waga-scale --no-pager | head -5"
echo "  journalctl -u waga-scale -n 20 --no-pager"
