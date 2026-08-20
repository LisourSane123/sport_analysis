# Waga_RP

Zbieranie pomiarów z inteligentnej wagi **Xiaomi Mi Body Composition Scale 2** (BLE),
dekodowanie ich, zapis do **SQLite**, import treningów oraz danych zdrowotnych
z **Garmin Connect** i nowoczesny dashboard webowy.
Docelowa platforma: **Raspberry Pi 5** (Raspberry Pi OS).

```
[ waga BLE ] --bleak--> [ dekoder ] --> [ SQLite ] <-- [ Garmin Connect ]
                                            |
                                     [ FastAPI + dashboard ]
```

**Spis treści:** [Instalacja](#instalacja-raspberry-pi-5) · [Pierwsze uruchomienie](#pierwsze-uruchomienie) ·
[Garmin Connect](#garmin-connect) · [Usługi](#usługi-systemd) · [Baza](#baza-danych-sqlite) ·
[Dashboard i API](#dashboard) · [Rozpoznawanie użytkownika](#rozpoznawanie-użytkownika) ·
[Problemy](#rozwiązywanie-problemów)

## Wymagania

* Raspberry Pi 5 (albo dowolny Linux z Bluetooth LE) — Raspberry Pi OS / Debian
* Python 3.11+
* Waga Xiaomi Mi Body Composition Scale 2 (`MIBFS`)
* Konto Garmin Connect — opcjonalnie, tylko dla danych treningowych

## Instalacja (Raspberry Pi 5)

```bash
git clone https://github.com/<uzytkownik>/Waga_RP.git && cd Waga_RP
bash install.sh                 # pakiety, venv, baza, uprawnienia BLE, usługi systemd
cp .env.example .env            # (install.sh robi to sam, jeśli pliku nie ma)
```

Konfiguracja w `.env`: MAC wagi, ścieżka bazy, port dashboardu, ustawienia Garmina.

## Pierwsze uruchomienie

```bash
source .venv/bin/activate

# 1. znajdź MAC wagi (wejdź na nią, żeby zaczęła rozgłaszać)
python3 -m app.scale.discover
#    -> wpisz znaleziony adres do SCALE_MAC w .env

# 2. dodaj profile — kreator pyta o imię, płeć, wzrost, datę urodzenia i obecną wagę
python3 manage_users.py add
#    wszystko naraz, bez pytań (np. w skrypcie):
#    python3 manage_users.py add ruka "Łukasz" 182 1995-04-12 male 84
python3 manage_users.py list

# 3. zbieranie pomiarów (pętla skanowania BLE)
python3 -m app.scale.runner

# 4. dashboard
python3 -m app.web.server        # http://<ip-rpi>:11230
```

## Garmin Connect

Garmin nie udostępnia publicznego API zwykłemu użytkownikowi (oficjalne jest tylko dla
producentów sprzętu), więc korzystamy z biblioteki [`garminconnect`](https://pypi.org/project/garminconnect/),
która loguje się dokładnie tak jak strona connect.garmin.com. Nie trzeba nic zakładać
ani rejestrować — wystarczy własne konto.

```bash
python3 -m app.garmin.auth                  # e-mail + hasło (+ kod MFA, jeśli włączony)
python3 manage_users.py link-garmin ruka    # powiąż konto Garmina z profilem
python3 -m app.garmin.sync --days 30        # pierwszy import
python3 -m app.garmin.sync                  # kolejne: przyrostowo
```

Hasło nie jest nigdzie zapisywane. Do `GARMIN_TOKENSTORE` (domyślnie `data/garmin_tokens/`)
trafiają tylko tokeny OAuth — ważne około roku i odświeżane w tle — dzięki czemu usługa
działa bez danych logowania w `.env`. Gdy Garmin odrzuci tokeny (zmiana hasła, wylogowanie
wszystkich sesji), wystarczy powtórzyć `python3 -m app.garmin.auth`.

Co jest pobierane:

| Skąd | Do tabeli | Co |
|---|---|---|
| lista aktywności | `garmin_activities` | dystans, czas, przewyższenie, tętno, kadencja, moc, kalorie, training effect, VO2max, długość kroku, kontakt z podłożem |
| podsumowanie dnia | `garmin_daily` | kroki, dystans, piętra, kalorie (całkowite/aktywne/BMR), tętno spoczynkowe, min/max HR, stres, body battery, minuty intensywności |
| sen | `garmin_daily` | czas snu z podziałem na fazy, wybudzenia, sleep score, oddech, SpO₂ |
| HRV | `garmin_daily` | średnia z nocy + status |
| gotowość / VO2max | `garmin_daily` | training readiness, VO2max |

Jeden dzień to 5 zapytań; `--quick` ogranicza się do samego podsumowania (1 zapytanie).
Między zapytaniami jest przerwa `GARMIN_REQUEST_PAUSE` — Garmin bywa wrażliwy na serie
bez oddechu. Sync w tle: `python3 -m app.garmin.sync --loop` (usługa `waga-garmin`,
co `GARMIN_SYNC_INTERVAL` sekund, domyślnie godzina).

Ponieważ to nieoficjalne API, Garmin może w każdej chwili zmienić nazwy pól. Dlatego każda
kolumna ma listę kandydatów w `app/garmin/mapping.py`, a pełna odpowiedź trafia do
`raw_json` — nawet po zmianie nazwy dane nie przepadają, wystarczy dopisać nowy klucz
i puścić sync ponownie.

## Panel administracyjny

Zakładka **Panel** w dashboardzie robi z przeglądarki to, co wcześniej wymagało SSH:

* **Profile** — dodawanie, edycja i usuwanie (usunięcie profilu zostawia pomiary w bazie
  jako nieprzypisane, nic nie znika).
* **Ustawienia** — częstotliwość skanów, parametry rozpoznawania osoby i ustawienia
  Garmina. Zapis trafia do tabeli `settings`, a usługi czytają ją na początku każdego
  cyklu — **zmiana działa bez restartu**. Przycisk *Przywróć wartości z .env* kasuje
  wpisy z bazy.
* **Wszystkie pomiary z wagi** — pełna lista bez okna czasowego, z filtrem
  „tylko nieprzypisane". Każdy wiersz można przypisać do innego profilu (skład ciała
  jest wtedy przeliczany dla nowej osoby — wzrost, wiek i płeć wchodzą do wzorów)
  albo usunąć.

Kolejność źródeł ustawień: **tabela `settings` → `.env` → wartość domyślna w kodzie**.
W `.env` zostają tylko rzeczy potrzebne przed startem procesu: MAC wagi, ścieżka bazy,
port panelu.

Dashboard nie ma logowania, a panel zapisuje dane. Jeśli wolisz tryb tylko do odczytu,
ustaw `ADMIN_ENABLED=0` w `.env` — endpointy zapisu zaczną zwracać 403, a panel wyświetli
o tym informację i zablokuje formularze.

## Usługi systemd

| Usługa | Co robi |
|---|---|
| `waga-scale` | pętla BLE: pomiary → SQLite |
| `waga-garmin` | cykliczny import aktywności i danych dziennych z Garmin Connect |
| `waga-dashboard` | serwer WWW na porcie z `WEB_PORT` (domyślnie 11230) |

```bash
sudo systemctl start  waga-scale waga-dashboard
sudo systemctl enable --now waga-garmin
journalctl -u waga-scale -f
```

## Baza danych (SQLite)

| Tabela | Zawartość |
|---|---|
| `users` | profile: wzrost, data urodzenia, płeć, waga startowa (`ref_weight`), `garmin_profile_id` |
| `measurements` | pomiary z wagi + wyliczona kompozycja ciała (klucz `user_id + measured_at` chroni przed duplikatami) |
| `garmin_activities` | treningi z Garmina (klucz: `activityId`) — dystans, tętno, moc, training effect, pełny JSON |
| `garmin_daily` | jeden wiersz na dobę (klucz: `profile_id + day`) — kroki, kalorie, tętno spoczynkowe, stres, body battery, sen, HRV, gotowość, VO2max |
| `settings` | ustawienia zmienione w panelu (`klucz → wartość`); brak wpisu = wartość z `.env` |

Kolumny pomiaru: `weight_kg`, `impedance`, `bmi`, `fat_percentage`, `water_percentage`,
`muscle_mass`, `bone_mass`, `visceral_fat`, `protein_percentage`, `lbm`, `bmr`,
`metabolic_age`, `ideal_weight`, `identify_method`, `identify_score`, `raw_hex`.

Tokeny Garmina leżą poza bazą, w pliku `data/garmin_tokens/garmin_tokens.json`
(uprawnienia 0600) — pamiętaj o nim przy kopii zapasowej albo świadomie go pomiń.

Podgląd danych: `sqlite3 data/waga.db "SELECT measured_at, weight_kg, fat_percentage FROM measurements ORDER BY 1 DESC LIMIT 10"`

## Dashboard

Zakładki: **Sylwetka** (KPI, trend wagi z przełącznikiem serii, skład ciała, szczegóły
ostatniego pomiaru), **Biegi** (dystans tygodniowo, tempo w czasie, tabela aktywności),
**Historia** (wszystkie pomiary), **Predykcje** — *miejsce zarezerwowane na kolejny
etap projektu*. Filtry: profil i zakres czasu; motyw jasny/ciemny; odświeżanie co minutę.
Adres wspiera deep-linki do zakładek (`#runs`, `#history`, `#forecast`).

Chart.js jest w repo (`app/web/static/vendor/`) — dashboard działa bez internetu.

### API

| Endpoint | Opis |
|---|---|
| `GET /api/health` | stan bazy i połączenia z Garminem (liczba dni i treningów) |
| `GET /api/users` | profile + liczba pomiarów |
| `GET /api/measurements?user=&days=` | pomiary |
| `GET /api/activities?days=&sport=&user=` | treningi z Garmina |
| `GET /api/garmin/daily?user=&days=` | dzienne dane: sen, HRV, tętno spoczynkowe, stres, gotowość |
| `GET /api/summary?user=&days=` | KPI dla kafelków |
| `GET /api/measurements/all?user=&unassigned=&limit=&offset=` | wszystkie pomiary, bez okna czasowego |
| `PATCH /api/measurements/{id}` | zmiana przypisania pomiaru (przelicza skład ciała) |
| `DELETE /api/measurements/{id}` | usunięcie pomiaru |
| `GET/PUT /api/settings` | odczyt i zapis ustawień |
| `POST /api/settings/reset` | powrót do wartości z `.env` |
| `GET /api/admin/users`, `POST /api/users`, `PATCH/DELETE /api/users/{username}` | zarządzanie profilami |
| `GET /api/predictions` | **placeholder** modułu predykcji (`available: false`) |
| `GET /api/docs` | interaktywna dokumentacja OpenAPI |

## Rozpoznawanie użytkownika

Nie ma sztywnych przedziałów wagi. Dla każdego profilu liczony jest **przedział
predykcyjny dla nowego pomiaru** z jego ważeń z ostatniego tygodnia
(`app/scale/identify.py`):

```
środek  = przewidywana waga na moment pomiaru
          (średnia; przy n ≥ 3 regresja liniowa, żeby nadążać za trendem diety)
se_pred = s_eff · √(1 + 1/n)                    # błąd predykcji nowej obserwacji
granice = środek ± t(1−α/2, df) · se_pred       # rozkład t-Studenta
score   = t_kryt − |waga − środek| / se_pred    # > 0 = w przedziale
```

* `s_eff` to odchylenie próbkowe **ściągnięte w stronę odchylenia a priori**
  (`IDENT_SD_PRIOR`, domyślnie 0.8 kg wahań dobowych) z wagą `IDENT_NU_PRIOR`
  pseudo-obserwacji — dzięki temu przy 1–2 pomiarach przedział nie robi się absurdalny,
  a przy kilkunastu zbiega do prawdziwej zmienności osoby.
* Wygrywa profil o **najwyższym score** (największy zapas w jednostkach odchylenia),
  więc przy nakładających się przedziałach wybierany jest ten, do którego waga pasuje pewniej.
* `identify_score` zapisywany w bazie to ten sam zapas przeliczony **na kilogramy**.

Gdy waga nie trafia w żaden przedział → **najbliższy ostatni pomiar** (`fallback_last`),
a dla profilu bez historii — jego waga startowa (`fallback_ref`). Jeśli najbliższy profil
różni się o więcej niż `IDENT_FALLBACK_MAX_KG` (domyślnie 8 kg), pomiar zostaje
nieprzypisany — to chroni przed przypisaniem gościa do domownika.

Parametry w `.env`: `IDENT_WINDOW_DAYS`, `IDENT_CONFIDENCE`, `IDENT_SD_PRIOR`,
`IDENT_NU_PRIOR`, `IDENT_SD_FLOOR`, `IDENT_MAX_HALF_KG`, `IDENT_FALLBACK_MAX_KG`.
Aktualne przedziały każdego profilu pokazuje `python3 manage_users.py list`.

Dlaczego t-Student, a nie Beta: szacujemy średnią i wariancję z kilku obserwacji
o w przybliżeniu normalnym rozkładzie — to podręcznikowy przypadek rozkładu t.
Beta opisuje wielkości ograniczone do przedziału [0, 1] (proporcje), więc do wagi w kg
nie pasuje. Rozkład t liczony jest bez `scipy` (niepełna funkcja beta), więc nic
ciężkiego nie ląduje na Raspberry Pi.

## Miejsce na predykcje (następny etap)

Szkielet jest gotowy: endpoint `/api/predictions` zwraca `{"available": false, ...}`,
a zakładka „Predykcje" ma opisaną ramkę na wykres. Wystarczy dodać moduł
`app/predict/` liczący prognozę i podmienić odpowiedź endpointu — frontend
odczyta `message` i `planned` bez zmian w HTML.

## Migracja ze starej wersji (ze Stravą)

Strava od 1 czerwca 2026 wymaga płatnej subskrypcji do korzystania z API, więc integracja
została usunięta — wszystkie dane o bieganiu pochodzą teraz z Garmina, który i tak był ich
źródłem (zegarek → Garmin Connect → Strava). Bazy założone wcześniejszą wersją nadal mają
tabele `activities` i `strava_tokens`; nic nie psują, ale można je usunąć:

```bash
sqlite3 data/waga.db ".backup data/waga-kopia.db"   # najpierw kopia!
python3 tools/drop_strava.py                        # pokaże, co usuwa, i zapyta
python3 tools/fix_timestamps.py                     # naprawa dat z 1970 roku
```

## Prywatność i bezpieczeństwo repozytorium

Do repozytorium **nie trafiają**: `.env` (MAC wagi, ustawienia), `data/*.db` (pomiary),
`data/garmin_tokens/` (tokeny konta Garmina) — blokuje je `.gitignore`. W repo jest tylko
`.env.example` z pustymi wartościami.

Po sklonowaniu warto włączyć strażnika, który zatrzyma commit z sekretem:

```bash
git config core.hooksPath .githooks     # blokuje .env, bazy i tokeny w commicie
chmod 600 .env                          # plik czytelny tylko dla właściciela
```

Sprawdzenie przed pierwszym pushem:

```bash
git status --short                                       # nie może być tu .env ani *.db
git check-ignore -v .env data/waga.db data/garmin_tokens # każdy plik musi mieć regułę
```

Hasło do Garmina nie jest przechowywane nigdzie — `python3 -m app.garmin.auth` zapisuje
wyłącznie tokeny OAuth. Panel WWW nie ma logowania: trzymaj go w sieci domowej albo
za VPN-em (WireGuard, Tailscale), nie na przekierowanym porcie routera.

## Struktura projektu

```
app/
  config.py          konfiguracja startowa z .env
  db.py              schemat SQLite, migracje, zapisy
  scale/             BLE: skan, dekoder ramek, skład ciała, rozpoznawanie osoby
  garmin/            Garmin Connect: logowanie, mapowanie pól, sync
  web/               FastAPI + dashboard (HTML, JS, Chart.js lokalnie)
manage_users.py      kreator profili i powiązań z Garminem
  settings.py        ustawienia zmienialne z panelu (baza -> .env -> domyślne)
tools/               narzędzia jednorazowe (naprawa dat, czyszczenie po Stravie)
systemd/             pliki usług: waga-scale, waga-garmin, waga-dashboard
tests/               testy bez sprzętu
```

## Testy

```bash
.venv/bin/python -m unittest discover -s tests    # dekoder, metryki, baza (bez sprzętu)
```

## Rozwiązywanie problemów

* **Nie widać wagi** — `bluetoothctl power on`, potem `python3 -m app.scale.discover 20`.
  Waga rozgłasza dane tylko przez kilka sekund po wejściu na nią.
* **Brak uprawnień do BLE** — `sudo setcap 'cap_net_raw,cap_net_admin+eip' $(readlink -f .venv/bin/python)`.
* **Pomiar bez składu ciała** — waga nie zmierzyła impedancji (bose stopy!) albo pomiaru
  nie udało się przypisać do profilu; zapisuje się wtedy sam `weight_kg`.
* **Data pomiaru z 1970 roku** — waga ma nieustawiony zegar (ustawia go dopiero aplikacja
  producenta przy synchronizacji). Od tej wersji taki czas jest odrzucany i pomiar dostaje
  czas Raspberry Pi; wcześniejsze wpisy naprawisz przez `python3 tools/fix_timestamps.py`.
* **Pomiar trafił do złego profilu** — sprawdź zakładkę Historia (kolumna *Profil* pokazuje
  metodę i zapas w kg). Przy dwóch osobach o podobnej wadze zmniejsz `IDENT_CONFIDENCE`
  do 0.9 (węższe przedziały) albo skróć `IDENT_WINDOW_DAYS`.
* **Garmin odrzuca logowanie** — najczęściej po zmianie hasła albo wylogowaniu wszystkich
  sesji: `python3 -m app.garmin.auth --logout`, potem `python3 -m app.garmin.auth`.
* **Garmin odpowiada 429** — za dużo zapytań pod rząd. Zwiększ `GARMIN_REQUEST_PAUSE`,
  używaj `--quick` przy dużych backfillach i nie odpalaj syncu częściej niż co godzinę.
* **Pusta kolumna w `garmin_daily`** — Garmin przemianował pole albo zegarek go nie mierzy.
  Zajrzyj w `raw_json` z tego dnia i dopisz nazwę do `app/garmin/mapping.py`.

## Licencja

Projekt prywatny, do własnego użytku. Dane pomiarowe i tokeny nie trafiają do repozytorium.
