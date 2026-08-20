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
    strava_athlete_id INTEGER,
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

CREATE TABLE IF NOT EXISTS activities (
    id                  INTEGER PRIMARY KEY,   -- id aktywnosci ze Stravy
    athlete_id          INTEGER,
    user_id             INTEGER REFERENCES users(id) ON DELETE SET NULL,
    name                TEXT,
    sport_type          TEXT,
    start_date          TEXT,                  -- UTC ISO8601
    start_date_local    TEXT,
    timezone            TEXT,
    distance_m          REAL,
    moving_time_s       INTEGER,
    elapsed_time_s      INTEGER,
    total_elevation_gain REAL,
    average_speed       REAL,                  -- m/s
    max_speed           REAL,
    average_heartrate   REAL,
    max_heartrate       REAL,
    average_cadence     REAL,
    calories            REAL,
    suffer_score        REAL,
    kudos_count         INTEGER,
    map_polyline        TEXT,
    raw_json            TEXT,
    synced_at           TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_act_start ON activities (start_date_local);

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

CREATE TABLE IF NOT EXISTS strava_tokens (
    athlete_id    INTEGER PRIMARY KEY,
    access_token  TEXT NOT NULL,
    refresh_token TEXT NOT NULL,
    expires_at    INTEGER NOT NULL,           -- unix epoch
    scope         TEXT,
    updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
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
             ref_weight=None, strava_athlete_id=None) -> int:
    cur = conn.execute(
        """INSERT INTO users (username, display_name, height_cm, birthdate, sex,
                              ref_weight, strava_athlete_id)
           VALUES (?,?,?,?,?,?,?)""",
        (username, display_name, float(height_cm), birthdate, sex,
         float(ref_weight) if ref_weight is not None else None, strava_athlete_id),
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
# Aktywnosci (Strava)
# --------------------------------------------------------------------------
ACTIVITY_COLUMNS = (
    "id", "athlete_id", "user_id", "name", "sport_type", "start_date",
    "start_date_local", "timezone", "distance_m", "moving_time_s",
    "elapsed_time_s", "total_elevation_gain", "average_speed", "max_speed",
    "average_heartrate", "max_heartrate", "average_cadence", "calories",
    "suffer_score", "kudos_count", "map_polyline", "raw_json",
)


def upsert_activity(conn, act: dict[str, Any], user_id: int | None = None) -> bool:
    """Zapisuje/aktualizuje aktywnosc. True gdy byla nowa."""
    row = {
        "id": act["id"],
        "athlete_id": (act.get("athlete") or {}).get("id"),
        "user_id": user_id,
        "name": act.get("name"),
        "sport_type": act.get("sport_type") or act.get("type"),
        "start_date": act.get("start_date"),
        "start_date_local": act.get("start_date_local"),
        "timezone": act.get("timezone"),
        "distance_m": act.get("distance"),
        "moving_time_s": act.get("moving_time"),
        "elapsed_time_s": act.get("elapsed_time"),
        "total_elevation_gain": act.get("total_elevation_gain"),
        "average_speed": act.get("average_speed"),
        "max_speed": act.get("max_speed"),
        "average_heartrate": act.get("average_heartrate"),
        "max_heartrate": act.get("max_heartrate"),
        "average_cadence": act.get("average_cadence"),
        "calories": act.get("calories") or act.get("kilojoules"),
        "suffer_score": act.get("suffer_score"),
        "kudos_count": act.get("kudos_count"),
        "map_polyline": (act.get("map") or {}).get("summary_polyline"),
        "raw_json": json.dumps(act, ensure_ascii=False),
    }
    existed = conn.execute("SELECT 1 FROM activities WHERE id = ?", (row["id"],)).fetchone()
    updates = ",".join(f"{c}=excluded.{c}" for c in ACTIVITY_COLUMNS if c != "id")
    conn.execute(
        f"INSERT INTO activities ({','.join(ACTIVITY_COLUMNS)}) "
        f"VALUES ({','.join('?' * len(ACTIVITY_COLUMNS))}) "
        f"ON CONFLICT(id) DO UPDATE SET {updates}, synced_at = datetime('now')",
        [row[c] for c in ACTIVITY_COLUMNS],
    )
    conn.commit()
    return existed is None


def latest_activity_epoch(conn, athlete_id: int | None = None) -> int:
    """Unix epoch najswiezszej zapisanej aktywnosci (0 gdy brak) - dla parametru `after`."""
    sql = "SELECT MAX(strftime('%s', REPLACE(REPLACE(start_date,'T',' '),'Z',''))) FROM activities"
    args: Iterable = ()
    if athlete_id is not None:
        sql += " WHERE athlete_id = ?"
        args = (athlete_id,)
    val = conn.execute(sql, args).fetchone()[0]
    return int(val) if val else 0


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


# --------------------------------------------------------------------------
# Tokeny Strava
# --------------------------------------------------------------------------
def save_tokens(conn, athlete_id, access_token, refresh_token, expires_at, scope=None):
    conn.execute(
        """INSERT INTO strava_tokens (athlete_id, access_token, refresh_token, expires_at, scope)
           VALUES (?,?,?,?,?)
           ON CONFLICT(athlete_id) DO UPDATE SET
             access_token=excluded.access_token,
             refresh_token=excluded.refresh_token,
             expires_at=excluded.expires_at,
             scope=COALESCE(excluded.scope, strava_tokens.scope),
             updated_at=datetime('now')""",
        (athlete_id, access_token, refresh_token, int(expires_at), scope),
    )
    conn.commit()


def get_tokens(conn, athlete_id: int | None = None):
    if athlete_id is None:
        return conn.execute(
            "SELECT * FROM strava_tokens ORDER BY updated_at DESC LIMIT 1").fetchone()
    return conn.execute(
        "SELECT * FROM strava_tokens WHERE athlete_id = ?", (athlete_id,)).fetchone()


if __name__ == "__main__":
    init_db()
    print(f"Baza gotowa: {DB_PATH}")
