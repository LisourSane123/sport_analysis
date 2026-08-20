"""Warstwa SQLite: schemat, polaczenia, operacje na uzytkownikach/pomiarach/aktywnosciach."""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from app.config import DB_PATH

log = logging.getLogger("db")

SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT    NOT NULL UNIQUE,
    display_name  TEXT    NOT NULL,
    height_cm     REAL    NOT NULL,
    birthdate     TEXT    NOT NULL,           -- YYYY-MM-DD
    sex           TEXT    NOT NULL CHECK (sex IN ('male','female')),
    ref_weight    REAL,                       -- waga podana przy zakladaniu profilu
                                              -- (punkt startowy, zanim beda pomiary)
    garmin_profile_id TEXT,                     -- displayName konta Garmin Connect
    created_at    TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS measurements (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id        INTEGER REFERENCES users(id) ON DELETE SET NULL,
    measured_at    TEXT    NOT NULL,          -- ISO8601, czas lokalny z wagi
    recorded_at    TEXT    NOT NULL DEFAULT (datetime('now')),
    weight_kg      REAL    NOT NULL,
    unit           TEXT    NOT NULL DEFAULT 'kg',
    impedance      INTEGER,
    bmi            REAL,
    fat_percentage REAL,
    water_percentage REAL,
    muscle_mass    REAL,
    bone_mass      REAL,
    visceral_fat   REAL,
    protein_percentage REAL,
    lbm            REAL,
    bmr            REAL,
    metabolic_age  REAL,
    ideal_weight   REAL,
    identify_method TEXT,                     -- interval | fallback_last | fallback_ref
    identify_score  REAL,                     -- zapas w kg wzgledem granicy przedzialu
    raw_hex        TEXT,
    UNIQUE (user_id, measured_at)
);

CREATE INDEX IF NOT EXISTS idx_meas_user_time ON measurements (user_id, measured_at);

CREATE TABLE IF NOT EXISTS garmin_activities (
    id                  INTEGER PRIMARY KEY,   -- activityId z Garmin Connect
    profile_id          TEXT,                  -- displayName konta Garmina
    user_id             INTEGER REFERENCES users(id) ON DELETE SET NULL,
    name                TEXT,
    sport_type          TEXT,
    start_time_local    TEXT,
    start_time_gmt      TEXT,
    distance_m          REAL,
    moving_time_s       REAL,
    elapsed_time_s      REAL,
    total_elevation_gain REAL,
    average_speed       REAL,                  -- m/s
    max_speed           REAL,
    average_heartrate   REAL,
    max_heartrate       REAL,
    average_cadence     REAL,
    average_power       REAL,
    calories            REAL,
    aerobic_te          REAL,                  -- training effect tlenowy
    anaerobic_te        REAL,
    vo2max              REAL,
    avg_stride_length   REAL,                  -- cm
    avg_ground_contact_ms REAL,
    avg_vertical_oscillation REAL,
    device              TEXT,
    raw_json            TEXT,
    synced_at           TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_gact_start ON garmin_activities (start_time_local);

CREATE TABLE IF NOT EXISTS garmin_daily (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id      TEXT NOT NULL,             -- displayName konta Garmina
    user_id         INTEGER REFERENCES users(id) ON DELETE SET NULL,
    day             TEXT NOT NULL,             -- YYYY-MM-DD
    steps           INTEGER,
    distance_m      REAL,
    floors_climbed  REAL,
    calories_total  REAL,
    calories_active REAL,
    calories_bmr    REAL,
    resting_hr      REAL,
    min_hr          REAL,
    max_hr          REAL,
    avg_stress      REAL,
    max_stress      REAL,
    body_battery_high REAL,
    body_battery_low  REAL,
    intensity_min_moderate REAL,
    intensity_min_vigorous REAL,
    sleep_seconds       REAL,
    deep_sleep_seconds  REAL,
    light_sleep_seconds REAL,
    rem_sleep_seconds   REAL,
    awake_seconds       REAL,
    sleep_score         REAL,
    hrv_last_night      REAL,
    hrv_status          TEXT,
    training_readiness  REAL,
    vo2max              REAL,
    respiration_avg     REAL,
    spo2_avg            REAL,
    raw_json        TEXT,
    synced_at       TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (profile_id, day)
);

CREATE INDEX IF NOT EXISTS idx_gdaily_day ON garmin_daily (day);

CREATE TABLE IF NOT EXISTS settings (
    key        TEXT PRIMARY KEY,           -- np. scan_interval, ident_confidence
    value      TEXT NOT NULL,              -- zawsze tekst, typ pilnuje app/settings.py
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

"""


def connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    # DB_PATH czytane przy wywolaniu, nie przy imporcie - inaczej nie da sie
    # podmienic sciezki w testach ani wskazac innej bazy w locie.
    path = Path(db_path if db_path is not None else DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def connect_readonly(db_path: Path | str | None = None) -> sqlite3.Connection:
    """Polaczenie tylko do odczytu - do analiz i modeli, obok dzialajacych uslug.

    Baza chodzi w trybie WAL, wiec czytanie nie blokuje zapisu ani odwrotnie.
    Tryb `mode=ro` jest zabezpieczeniem przed przypadkowym `UPDATE` w notatniku:
    proba zapisu konczy sie bledem, zamiast po cichu zmienic pomiary.
    """
    path = Path(db_path if db_path is not None else DB_PATH)
    if not path.exists():
        # W trybie ro SQLite nie zaklada pliku i zglasza tylko "unable to open
        # database file" - bez podania sciezki, ktorej szukal.
        raise FileNotFoundError(
            f"Nie ma bazy: {path}\n"
            f"Baza nie jest w repozytorium (data/*.db jest w .gitignore), wiec po "
            f"sklonowaniu projektu trzeba ja skopiowac z Raspberry Pi:\n"
            f"  bash tools/fetch_db.sh pi@raspberrypi\n"
            f"albo wskazac inna sciezke przez DB_PATH w .env")
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: Path | str | None = None) -> None:
    with connect(db_path) as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)


BROKEN_TIME_BEFORE = "2015-01-01"


def repair_broken_timestamps(conn) -> list[tuple[int, str, str]]:
    """Podmienia daty z nieustawionego zegara wagi (rok 1970/2000).

    Za czas pomiaru bierzemy `recorded_at` - moment zapisu przez Raspberry Pi,
    czyli sekundy po samym wazeniu. `recorded_at` jest w UTC, `measured_at`
    w czasie lokalnym, wiec po drodze przeliczamy strefe.

    Zwraca liste (id, stara data, nowa data). Kolumna `raw_hex` zostaje
    nietknieta, wiec oryginalna ramka z wagi jest dalej w bazie.
    """
    rows = conn.execute(
        "SELECT id, measured_at, recorded_at FROM measurements WHERE measured_at < ?",
        (BROKEN_TIME_BEFORE,)).fetchall()
    fixed = []
    for row in rows:
        try:
            naive = datetime.fromisoformat(row["recorded_at"].replace("T", " "))
            local = naive.replace(tzinfo=timezone.utc).astimezone()
            new_value = local.replace(tzinfo=None).isoformat(timespec="seconds")
            conn.execute("UPDATE measurements SET measured_at = ? WHERE id = ?",
                         (new_value, row["id"]))
        except (ValueError, AttributeError, sqlite3.IntegrityError):
            continue                  # zepsany recorded_at albo kolizja z UNIQUE
        fixed.append((row["id"], row["measured_at"], new_value))
    if fixed:
        conn.commit()
    return fixed


def _migrate(conn) -> None:
    """Dociaga schemat baz zalozonych wczesniejsza wersja."""
    user_cols = {r["name"] for r in conn.execute("PRAGMA table_info(users)")}
    if "ref_weight" not in user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN ref_weight REAL")
        if {"weight_min", "weight_max"} <= user_cols:      # srodek starego zakresu
            conn.execute("UPDATE users SET ref_weight = (weight_min + weight_max) / 2")

    if "garmin_profile_id" not in user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN garmin_profile_id TEXT")

    meas_cols = {r["name"] for r in conn.execute("PRAGMA table_info(measurements)")}
    for column in ("identify_method TEXT", "identify_score REAL"):
        if column.split()[0] not in meas_cols:
            conn.execute(f"ALTER TABLE measurements ADD COLUMN {column}")
    conn.commit()

    fixed = repair_broken_timestamps(conn)
    if fixed:
        log.warning("Naprawiono date %d pomiarow zapisanych z nieustawionego zegara "
                    "wagi (np. #%d: %s -> %s)", len(fixed), fixed[0][0],
                    fixed[0][1], fixed[0][2])


# --------------------------------------------------------------------------
# Uzytkownicy
# --------------------------------------------------------------------------
def add_user(conn, username, display_name, height_cm, birthdate, sex,
             ref_weight=None, garmin_profile_id=None) -> int:
    cur = conn.execute(
        """INSERT INTO users (username, display_name, height_cm, birthdate, sex,
                              ref_weight, garmin_profile_id)
           VALUES (?,?,?,?,?,?,?)""",
        (username, display_name, float(height_cm), birthdate, sex,
         float(ref_weight) if ref_weight is not None else None, garmin_profile_id),
    )
    conn.commit()
    return cur.lastrowid


def list_users(conn) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM users ORDER BY display_name").fetchall()


def get_user(conn, username: str):
    return conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()


USER_EDITABLE = ("display_name", "height_cm", "birthdate", "sex", "ref_weight",
                 "garmin_profile_id")


def update_user(conn, username: str, changes: dict[str, Any]) -> int:
    """Aktualizuje wybrane pola profilu. Zwraca liczbe zmienionych wierszy."""
    fields = {k: v for k, v in changes.items() if k in USER_EDITABLE}
    if not fields:
        return 0
    sql = ",".join(f"{k} = ?" for k in fields)
    cur = conn.execute(f"UPDATE users SET {sql} WHERE username = ?",
                       [*fields.values(), username])
    conn.commit()
    return cur.rowcount


def get_user_by_id(conn, user_id: int | None):
    if user_id is None:
        return None
    return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def delete_user(conn, username: str) -> int:
    cur = conn.execute("DELETE FROM users WHERE username = ?", (username,))
    conn.commit()
    return cur.rowcount


def age_of(user_row, on: date | None = None) -> int:
    born = datetime.strptime(user_row["birthdate"], "%Y-%m-%d").date()
    on = on or date.today()
    return on.year - born.year - ((on.month, on.day) < (born.month, born.day))


# --------------------------------------------------------------------------
# Pomiary
# --------------------------------------------------------------------------
MEASUREMENT_FIELDS = (
    "user_id", "measured_at", "weight_kg", "unit", "impedance", "bmi",
    "fat_percentage", "water_percentage", "muscle_mass", "bone_mass",
    "visceral_fat", "protein_percentage", "lbm", "bmr", "metabolic_age",
    "ideal_weight", "identify_method", "identify_score", "raw_hex",
)


def insert_measurement(conn, data: dict[str, Any]) -> int | None:
    """Zapisuje pomiar. Zwraca id lub None, gdy taki pomiar juz istnieje."""
    cols = [f for f in MEASUREMENT_FIELDS if f in data]
    sql = (f"INSERT OR IGNORE INTO measurements ({','.join(cols)}) "
           f"VALUES ({','.join('?' * len(cols))})")
    cur = conn.execute(sql, [data[c] for c in cols])
    conn.commit()
    return cur.lastrowid if cur.rowcount else None


def update_measurement(conn, measurement_id: int, changes: dict[str, Any]) -> int:
    """Zmienia wybrane kolumny pomiaru (przypisanie osoby, sklad ciala)."""
    allowed = set(MEASUREMENT_FIELDS) | {"measured_at"}
    fields = {k: v for k, v in changes.items() if k in allowed}
    if not fields:
        return 0
    sql = ",".join(f"{k} = ?" for k in fields)
    cur = conn.execute(f"UPDATE measurements SET {sql} WHERE id = ?",
                       [*fields.values(), measurement_id])
    conn.commit()
    return cur.rowcount


def delete_measurement(conn, measurement_id: int) -> int:
    cur = conn.execute("DELETE FROM measurements WHERE id = ?", (measurement_id,))
    conn.commit()
    return cur.rowcount


def get_measurement(conn, measurement_id: int):
    return conn.execute("SELECT * FROM measurements WHERE id = ?",
                        (measurement_id,)).fetchone()


def find_duplicate_groups(conn, window_minutes: int = 60) -> list[dict[str, Any]]:
    """Grupy pomiarow, ktore sa powtorzeniem tego samego wazenia.

    Waga rozglasza ostatni wynik jeszcze dlugo po zejsciu z niej. Jesli czas
    pomiaru pochodzi z zegara Raspberry Pi (bo zegar wagi jest nieustawiony),
    kazda taka powtorka trafia do bazy jako osobny wiersz.

    Za powtorzenie uznajemy pomiar o tej samej wadze (+-0.05 kg) i tej samej
    impedancji, ktory pojawil sie w ciagu `window_minutes` od poprzedniego.
    W kazdej grupie zostaje najstarszy wpis - ten z momentu prawdziwego wazenia.
    """
    rows = conn.execute(
        "SELECT id, user_id, measured_at, weight_kg, impedance, raw_hex "
        "FROM measurements ORDER BY measured_at").fetchall()

    groups: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for row in rows:
        when = datetime.fromisoformat(row["measured_at"])
        same = (current is not None
                and abs(row["weight_kg"] - current["weight_kg"]) < 0.05
                and row["impedance"] == current["impedance"]
                and (when - current["last_at"]).total_seconds() <= window_minutes * 60)
        if same:
            current["remove"].append(row["id"])
            current["last_at"] = when
            current["last"] = row["measured_at"]
            continue
        if current is not None and current["remove"]:
            groups.append(current)
        current = {"keep": row["id"], "remove": [], "weight_kg": row["weight_kg"],
                   "impedance": row["impedance"], "first": row["measured_at"],
                   "last": row["measured_at"], "last_at": when}
    if current is not None and current["remove"]:
        groups.append(current)

    for group in groups:
        group.pop("last_at", None)
        group["count"] = len(group["remove"]) + 1
    return groups


def delete_duplicates(conn, window_minutes: int = 60) -> tuple[int, int]:
    """Usuwa powtorzenia, zostawiajac najstarszy pomiar z kazdej grupy.

    Zwraca (liczba grup, liczba usunietych wierszy).
    """
    groups = find_duplicate_groups(conn, window_minutes)
    ids = [mid for group in groups for mid in group["remove"]]
    for chunk_start in range(0, len(ids), 500):
        chunk = ids[chunk_start:chunk_start + 500]
        conn.execute(f"DELETE FROM measurements WHERE id IN ({','.join('?' * len(chunk))})",
                     chunk)
    conn.commit()
    return len(groups), len(ids)


def last_measurement(conn, user_id: int | None = None):
    if user_id is None:
        return conn.execute(
            "SELECT * FROM measurements ORDER BY measured_at DESC LIMIT 1").fetchone()
    return conn.execute(
        "SELECT * FROM measurements WHERE user_id = ? ORDER BY measured_at DESC LIMIT 1",
        (user_id,)).fetchone()


# --------------------------------------------------------------------------
# Garmin: aktywnosci i dzienne podsumowania
# --------------------------------------------------------------------------
GARMIN_ACTIVITY_COLUMNS = (
    "id", "profile_id", "user_id", "name", "sport_type", "start_time_local",
    "start_time_gmt", "distance_m", "moving_time_s", "elapsed_time_s",
    "total_elevation_gain", "average_speed", "max_speed", "average_heartrate",
    "max_heartrate", "average_cadence", "average_power", "calories",
    "aerobic_te", "anaerobic_te", "vo2max", "avg_stride_length",
    "avg_ground_contact_ms", "avg_vertical_oscillation", "device", "raw_json",
)

GARMIN_DAILY_COLUMNS = (
    "profile_id", "user_id", "day", "steps", "distance_m", "floors_climbed",
    "calories_total", "calories_active", "calories_bmr", "resting_hr",
    "min_hr", "max_hr", "avg_stress", "max_stress", "body_battery_high",
    "body_battery_low", "intensity_min_moderate", "intensity_min_vigorous",
    "sleep_seconds", "deep_sleep_seconds", "light_sleep_seconds",
    "rem_sleep_seconds", "awake_seconds", "sleep_score", "hrv_last_night",
    "hrv_status", "training_readiness", "vo2max", "respiration_avg",
    "spo2_avg", "raw_json",
)


def upsert_garmin_activity(conn, row: dict[str, Any]) -> bool:
    """Zapisuje/aktualizuje aktywnosc z Garmina. True gdy byla nowa."""
    values = {c: row.get(c) for c in GARMIN_ACTIVITY_COLUMNS}
    existed = conn.execute("SELECT 1 FROM garmin_activities WHERE id = ?",
                           (values["id"],)).fetchone()
    updates = ",".join(f"{c}=excluded.{c}" for c in GARMIN_ACTIVITY_COLUMNS if c != "id")
    conn.execute(
        f"INSERT INTO garmin_activities ({','.join(GARMIN_ACTIVITY_COLUMNS)}) "
        f"VALUES ({','.join('?' * len(GARMIN_ACTIVITY_COLUMNS))}) "
        f"ON CONFLICT(id) DO UPDATE SET {updates}, synced_at = datetime('now')",
        [values[c] for c in GARMIN_ACTIVITY_COLUMNS],
    )
    conn.commit()
    return existed is None


def upsert_garmin_daily(conn, row: dict[str, Any]) -> bool:
    """Zapisuje/aktualizuje dzien z Garmina (klucz: profil + data). True gdy byl nowy."""
    values = {c: row.get(c) for c in GARMIN_DAILY_COLUMNS}
    existed = conn.execute(
        "SELECT 1 FROM garmin_daily WHERE profile_id = ? AND day = ?",
        (values["profile_id"], values["day"])).fetchone()
    # NULL nie nadpisuje wartosci juz zapisanej - kolejny przebieg moze
    # dociagnac np. sen, nie kasujac tego, co przyszlo wczesniej.
    updates = ",".join(f"{c}=COALESCE(excluded.{c}, garmin_daily.{c})"
                       for c in GARMIN_DAILY_COLUMNS if c not in ("profile_id", "day"))
    conn.execute(
        f"INSERT INTO garmin_daily ({','.join(GARMIN_DAILY_COLUMNS)}) "
        f"VALUES ({','.join('?' * len(GARMIN_DAILY_COLUMNS))}) "
        f"ON CONFLICT(profile_id, day) DO UPDATE SET {updates}, "
        f"synced_at = datetime('now')",
        [values[c] for c in GARMIN_DAILY_COLUMNS],
    )
    conn.commit()
    return existed is None


def latest_garmin_activity_day(conn, profile_id: str | None = None) -> str | None:
    """Data (YYYY-MM-DD) najswiezszej zapisanej aktywnosci - punkt startu syncu."""
    sql = "SELECT MAX(start_time_local) FROM garmin_activities"
    args: Iterable = ()
    if profile_id:
        sql += " WHERE profile_id = ?"
        args = (profile_id,)
    val = conn.execute(sql, args).fetchone()[0]
    return val[:10] if val else None


def user_id_for_garmin(conn, profile_id: str | None) -> int | None:
    if not profile_id:
        return None
    row = conn.execute("SELECT id FROM users WHERE garmin_profile_id = ?",
                       (profile_id,)).fetchone()
    return row["id"] if row else None


if __name__ == "__main__":
    init_db()
    print(f"Baza gotowa: {DB_PATH}")
