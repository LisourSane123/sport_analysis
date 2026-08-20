#!/usr/bin/env bash
# Instalator Waga_RP dla Raspberry Pi 5 (Raspberry Pi OS / Debian).
#   bash install.sh            - instalacja + uslugi systemd
#   bash install.sh --no-services  - sama konfiguracja srodowiska
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_USER="${SUDO_USER:-$USER}"
WITH_SERVICES=1
[[ "${1:-}" == "--no-services" ]] && WITH_SERVICES=0

echo "==> Katalog projektu: $ROOT (uzytkownik: $RUN_USER)"

if command -v apt-get >/dev/null; then
  echo "==> Pakiety systemowe"
  sudo apt-get update -qq
  sudo apt-get install -y python3 python3-venv python3-pip bluez libglib2.0-dev
fi

echo "==> Wirtualne srodowisko"
python3 -m venv "$ROOT/.venv"
"$ROOT/.venv/bin/pip" install --quiet --upgrade pip
"$ROOT/.venv/bin/pip" install --quiet -r "$ROOT/requirements.txt"

if [[ ! -f "$ROOT/.env" ]]; then
  cp "$ROOT/.env.example" "$ROOT/.env"
  echo "==> Utworzono .env - uzupelnij SCALE_MAC oraz dane aplikacji Strava"
fi

echo "==> Inicjalizacja bazy SQLite"
"$ROOT/.venv/bin/python" -m app.db

echo "==> Uprawnienia BLE (skanowanie bez roota)"
sudo setcap 'cap_net_raw,cap_net_admin+eip' "$(readlink -f "$ROOT/.venv/bin/python")" || \
  echo "    (setcap nieudany - skanowanie moze wymagac sudo)"
sudo systemctl enable --now bluetooth || true

if [[ "$WITH_SERVICES" == "1" ]]; then
  echo "==> Uslugi systemd"
  for unit in waga-scale waga-strava waga-garmin waga-dashboard; do
    sed -e "s|__ROOT__|$ROOT|g" -e "s|__USER__|$RUN_USER|g" \
        "$ROOT/systemd/$unit.service" | sudo tee "/etc/systemd/system/$unit.service" >/dev/null
  done
  sudo systemctl daemon-reload
  sudo systemctl enable waga-scale waga-dashboard
  echo "    Wlacz po skonfigurowaniu .env:"
  echo "      sudo systemctl start waga-scale waga-dashboard"
  echo "      sudo systemctl enable --now waga-strava   # po autoryzacji Stravy"
  echo "      sudo systemctl enable --now waga-garmin   # po logowaniu do Garmina"
fi

cat <<INFO

Gotowe. Nastepne kroki:
  1. source .venv/bin/activate
  2. python3 -m app.scale.discover            # znajdz MAC wagi -> wpisz do .env
  3. python3 manage_users.py add             # kreator: imie, plec, wzrost, data ur., zakres wagi
  4. python3 -m app.strava.auth               # autoryzacja Stravy (CLIENT_ID/SECRET w .env)
  5. python3 -m app.strava.sync --all         # pierwszy import aktywnosci
  6. python3 -m app.garmin.auth               # logowanie do Garmin Connect (opcjonalne)
  7. python3 -m app.garmin.sync --days 30     # pierwszy import danych z Garmina
  8. python3 -m app.web.server                # dashboard: http://<ip>:11230
INFO
