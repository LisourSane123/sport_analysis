# Waga_RP

Zbieranie pomiarów z inteligentnej wagi **Xiaomi Mi Body Composition Scale 2** (BLE),
dekodowanie ich, zapis do **SQLite**, import biegów ze **Stravy**, dane treningowe
i zdrowotne z **Garmin Connect** oraz nowoczesny dashboard webowy.
Docelowa platforma: **Raspberry Pi 5** (Raspberry Pi OS).

```
[ waga BLE ] --bleak--> [ dekoder ] --> [ SQLite ] <-- [ Strava API ]
                                            |     \
                                            |      \-- [ Garmin Connect ]
                                     [ FastAPI + dashboard ]
```

## Instalacja (Raspberry Pi 5)

```bash
git clone <repo> Waga_RP && cd Waga_RP
bash install.sh                 # pakiety, venv, baza, uprawnienia BLE, usługi systemd
cp .env.example .env            # (install.sh robi to sam, jeśli pliku nie ma)
```

Konfiguracja w `.env`: MAC wagi, ścieżka bazy, port dashboardu, klucze Strava.

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

## Strava

1. Załóż aplikację na <https://www.strava.com/settings/api>.
   W polu **Authorization Callback Domain** wpisz `localhost`.
2. `STRAVA_CLIENT_ID` i `STRAVA_CLIENT_SECRET` przepisz do `.env`.
3. Autoryzacja (jednorazowo — potem token odświeża się sam):

```bash
python3 -m app.strava.auth        # otworzy adres do wklejenia w przeglądarkę
python3 manage_users.py link ruka <athlete_id>   # powiąż konto Strava z profilem
python3 -m app.strava.sync --all  # pierwszy import całej historii
python3 -m app.strava.sync        # kolejne: tylko nowsze niż ostatnia w bazie
```

Autoryzację można zrobić z dowolnego komputera w sieci; jeśli wolisz skopiować kod
ręcznie z adresu przekierowania: `python3 -m app.strava.auth --code <kod>`.

Sync w tle: `python3 -m app.strava.sync --loop` (co `STRAVA_SYNC_INTERVAL` sekund,
domyślnie 30 min) — jako usługa `waga-strava`.

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

Hasło nie jest nigdzie zapisywane. Do `GARMIN_TOKENSTORE` (domyślnie
`data/garmin_tokens/`) trafiają tylko tokeny OAuth — ważne około roku i odświeżane
w tle — dzięki czemu usługa działa bez danych logowania w `.env`.
Gdy Garmin odrzuci tokeny (zmiana hasła, wylogowanie wszystkich sesji), wystarczy
powtórzyć `python3 -m app.garmin.auth`.

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

Ponieważ to nieoficjalne API, Garmin może w każdej chwili zmienić nazwy pól. Dlatego
każda kolumna ma listę kandydatów w `app/garmin/mapping.py`, a pełna odpowiedź trafia
do `raw_json` — nawet po zmianie nazwy dane nie przepadają, wystarczy dopisać nowy klucz
i puścić sync ponownie.

## Usługi systemd

| Usługa | Co robi |
|---|---|
| `waga-scale` | pętla BLE: pomiary → SQLite |
| `waga-strava` | cykliczny import aktywności ze Stravy |
| `waga-garmin` | cykliczny import aktywności i danych dziennych z Garmin Connect |
| `waga-dashboard` | serwer WWW na porcie z `WEB_PORT` (domyślnie 11230) |

```bash
sudo systemctl start  waga-scale waga-dashboard
sudo systemctl enable --now waga-strava waga-garmin
journalctl -u waga-scale -f
```

## Baza danych (SQLite)

| Tabela | Zawartość |
|---|---|
| `users` | profile: wzrost, data urodzenia, płeć, waga startowa (`ref_weight`), `strava_athlete_id`, `garmin_profile_id` |
| `measurements` | pomiary z wagi + wyliczona kompozycja ciała (klucz `user_id + measured_at` chroni przed duplikatami) |
| `activities` | aktywności ze Stravy (dystans, czas, przewyższenie, tętno, kadencja, polilinia, pełny JSON) |
| `garmin_activities` | treningi z Garmina (klucz: `activityId`) — dystans, tętno, moc, training effect, pełny JSON |
| `garmin_daily` | jeden wiersz na dobę (klucz: `profile_id + day`) — kroki, kalorie, tętno spoczynkowe, stres, body battery, sen, HRV, gotowość, VO2max |
| `strava_tokens` | access/refresh token, czas wygaśnięcia |

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
| `GET /api/health` | stan bazy, połączenia ze Stravą i Garminem (liczba dni/aktywności) |
| `GET /api/users` | profile + liczba pomiarów |
| `GET /api/measurements?user=&days=` | pomiary |
| `GET /api/activities?days=&sport=` | aktywności |
| `GET /api/summary?user=&days=` | KPI dla kafelków |
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
* **Pomiar trafił do złego profilu** — sprawdź zakładkę Historia (kolumna *Profil* pokazuje
  metodę i zapas w kg). Przy dwóch osobach o podobnej wadze zmniejsz `IDENT_CONFIDENCE`
  do 0.9 (węższe przedziały) albo skróć `IDENT_WINDOW_DAYS`.
* **Strava 429** — limit 200 zapytań / 15 min. Sync przyrostowy pobiera tylko nowości;
  `--details` (kalorie) kosztuje 1 zapytanie na aktywność.
# sport_analysis
