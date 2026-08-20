"""Warstwa SQLite: schemat, polaczenia, operacje na uzytkownikach/pomiarach/aktywnosciach."""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

from app.config import DB_PATH

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

"""


def connect(db_path: Path | str = DB_PATH) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: Path | str = DB_PATH) -> None:
    with connect(db_path) as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)


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
