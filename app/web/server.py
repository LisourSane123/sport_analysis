"""Dashboard: FastAPI + lekki frontend (Chart.js).

Uruchomienie: python3 -m app.web.server   albo   uvicorn app.web.server:app
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app import __version__, config, settings
from app.db import (add_user, connect, delete_duplicates, delete_measurement,
                    delete_user, find_duplicate_groups, get_measurement,
                    get_user, get_user_by_id, init_db, update_measurement,
                    update_user)
from app.scale.body_metrics import composition_for
from app.stats import weighted_averages

BASE = Path(__file__).resolve().parent
app = FastAPI(title="Waga_RP", docs_url="/api/docs", redoc_url=None)
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
INDEX = BASE / "templates" / "index.html"


@app.on_event("startup")
def _startup() -> None:
    init_db()


def rows_to_dicts(rows) -> list[dict[str, Any]]:
    return [dict(r) for r in rows]


def _since(days: int | None) -> str | None:
    if not days or days <= 0:
        return None
    return (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")


def _garmin_cutoff(since: str | None) -> str | None:
    """Garmin trzyma czas ze spacja (2026-08-20 17:56:42), reszta bazy w ISO z 'T'.
    Porownanie tekstowe musi byc w tym samym formacie, inaczej granica okna
    obcina caly ostatni dzien (spacja < 'T')."""
    return since.replace("T", " ") if since else None


def _user_id(conn, username: str | None) -> int | None:
    if not username or username == "all":
        return None
    row = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
    return row["id"] if row else None


# ---------------------------------------------------------------- strony
@app.get("/")
def index():
    return FileResponse(INDEX, media_type="text/html")


# ---------------------------------------------------------------- API
@app.get("/api/users")
def api_users():
    with connect() as conn:
        users = rows_to_dicts(conn.execute(
            """SELECT u.username, u.display_name, u.height_cm, u.sex,
                      u.garmin_profile_id, COUNT(m.id) AS measurements
               FROM users u LEFT JOIN measurements m ON m.user_id = u.id
               GROUP BY u.id ORDER BY u.display_name""").fetchall())
        orphans = conn.execute(
            "SELECT COUNT(*) c FROM measurements WHERE user_id IS NULL").fetchone()["c"]
    return {"users": users, "unassigned_measurements": orphans}


@app.get("/api/measurements")
def api_measurements(user: str | None = None, days: int = 180,
                     limit: int = Query(2000, le=10000)):
    since = _since(days)
    with connect() as conn:
        uid = _user_id(conn, user)
        sql = ("SELECT m.*, u.display_name AS user_display, u.username "
               "FROM measurements m LEFT JOIN users u ON u.id = m.user_id WHERE 1=1")
        args: list[Any] = []
        if uid is not None:
            sql += " AND m.user_id = ?"
            args.append(uid)
        if since:
            sql += " AND m.measured_at >= ?"
            args.append(since)
        sql += " ORDER BY m.measured_at ASC LIMIT ?"
        args.append(limit)
        return {"measurements": rows_to_dicts(conn.execute(sql, args).fetchall())}


@app.get("/api/activities")
def api_activities(days: int = 180, sport: str | None = None,
                   user: str | None = None, limit: int = Query(500, le=5000)):
    """Treningi z Garmina. Nazwy pol sa spojne z reszta API (start_date_local)."""
    since = _garmin_cutoff(_since(days))
    with connect() as conn:
        uid = _user_id(conn, user)
        sql = ("""SELECT id, user_id, name, sport_type,
                         REPLACE(start_time_local, ' ', 'T') AS start_date_local,
                         REPLACE(start_time_gmt, ' ', 'T')   AS start_date,
                         distance_m, moving_time_s, elapsed_time_s,
                         total_elevation_gain, average_speed, max_speed,
                         average_heartrate, max_heartrate, average_cadence,
                         average_power, calories, aerobic_te, anaerobic_te,
                         vo2max, device
                  FROM garmin_activities WHERE 1=1""")
        args: list[Any] = []
        if uid is not None:
            sql += " AND user_id = ?"
            args.append(uid)
        if since:
            sql += " AND start_time_local >= ?"
            args.append(since)
        if sport and sport != "all":
            sql += " AND sport_type = ?"
            args.append(sport)
        sql += " ORDER BY start_time_local DESC LIMIT ?"
        args.append(limit)
        activities = rows_to_dicts(conn.execute(sql, args).fetchall())
        sports = [r["sport_type"] for r in conn.execute(
            "SELECT DISTINCT sport_type FROM garmin_activities "
            "WHERE sport_type IS NOT NULL ORDER BY sport_type").fetchall()]
    return {"activities": activities, "sports": sports}


@app.get("/api/garmin/daily")
def api_garmin_daily(user: str | None = None, days: int = 90,
                     limit: int = Query(400, le=2000)):
    """Dzienne dane z Garmina: sen, HRV, tetno spoczynkowe, stres, gotowosc."""
    since = _since(days)
    with connect() as conn:
        uid = _user_id(conn, user)
        sql = "SELECT * FROM garmin_daily WHERE 1=1"
        args: list[Any] = []
        if uid is not None:
            sql += " AND user_id = ?"
            args.append(uid)
        if since:
            sql += " AND day >= ?"
            args.append(since[:10])
        sql += " ORDER BY day DESC LIMIT ?"
        args.append(limit)
        days_rows = rows_to_dicts(conn.execute(sql, args).fetchall())
    for d in days_rows:
        d.pop("raw_json", None)
    return {"days": days_rows}


WEEK_FIELDS = ("weight_kg", "bmi", "fat_percentage", "water_percentage",
               "muscle_mass", "bone_mass", "visceral_fat", "protein_percentage",
               "lbm", "bmr", "metabolic_age", "impedance")


def _week_summary(conn, latest, uid: int | None, days: int):
    """Srednie wazone czasem z ostatnich `days` dni.

    Przy filtrze "wszyscy" liczymy je dla osoby z ostatniego pomiaru - srednia
    wagi kilku osob nie znaczylaby nic. Zwracamy tez, czyje to sa liczby.
    """
    if latest is None:
        return {"days": days, "count": 0, "values": {}, "vs_latest": {},
                "span_hours": 0.0, "user": None}

    target = uid if uid is not None else latest["user_id"]
    where, args = ("AND user_id = ?", [target]) if target is not None else ("", [])
    since = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
    rows = [dict(r) for r in conn.execute(
        f"SELECT * FROM measurements WHERE measured_at >= ? {where} "
        f"ORDER BY measured_at", [since, *args]).fetchall()]

    values, span = weighted_averages(rows, WEEK_FIELDS)
    vs_latest = {field: round(latest[field] - values[field], 2)
                 for field in values
                 if latest[field] is not None}
    who = None
    if target is not None:
        row = conn.execute("SELECT username, display_name FROM users WHERE id = ?",
                           (target,)).fetchone()
        who = dict(row) if row else None
    return {"days": days, "count": len(rows), "values": values,
            "vs_latest": vs_latest, "span_hours": span, "user": who}


@app.get("/api/summary")
def api_summary(user: str | None = None, days: int = 30, week_days: int = 7):
    since = _since(days)
    with connect() as conn:
        uid = _user_id(conn, user)
        where, args = ("AND user_id = ?", [uid]) if uid is not None else ("", [])

        latest = conn.execute(
            f"SELECT * FROM measurements WHERE 1=1 {where} "
            f"ORDER BY measured_at DESC LIMIT 1", args).fetchone()
        first_in_range = conn.execute(
            f"SELECT * FROM measurements WHERE measured_at >= ? {where} "
            f"ORDER BY measured_at ASC LIMIT 1", [since or "0", *args]).fetchone()

        agg = conn.execute(
            f"""SELECT COUNT(*) n, MIN(weight_kg) wmin, MAX(weight_kg) wmax,
                       AVG(weight_kg) wavg
                FROM measurements WHERE measured_at >= ? {where}""",
            [since or "0", *args]).fetchone()

        act_where, act_args = ("AND user_id = ?", [uid]) if uid is not None else ("", [])
        act_since = _garmin_cutoff(since) or "0"
        act = conn.execute(
            f"""SELECT COUNT(*) n, COALESCE(SUM(distance_m),0) dist,
                       COALESCE(SUM(moving_time_s),0) time,
                       COALESCE(SUM(total_elevation_gain),0) elev,
                       AVG(average_heartrate) hr
                FROM garmin_activities
                WHERE start_time_local >= ? {act_where}""",
            [act_since, *act_args]).fetchone()
        last_activity = conn.execute(
            f"SELECT *, REPLACE(start_time_local, ' ', 'T') AS start_date_local "
            f"FROM garmin_activities WHERE 1=1 {act_where} "
            f"ORDER BY start_time_local DESC LIMIT 1", act_args).fetchone()

        week = _week_summary(conn, latest, uid, max(1, min(week_days, 90)))

    latest_d = dict(latest) if latest else None
    delta = None
    if latest_d and first_in_range:
        delta = round(latest_d["weight_kg"] - first_in_range["weight_kg"], 2)

    last_act = dict(last_activity) if last_activity else None
    if last_act:
        last_act.pop("raw_json", None)

    return {
        "period_days": days,
        "latest": latest_d,
        "weight_delta": delta,
        "weight": {"count": agg["n"], "min": agg["wmin"],
                   "max": agg["wmax"], "avg": agg["wavg"]},
        "week": week,
        "activity": {"count": act["n"], "distance_m": act["dist"],
                     "moving_time_s": act["time"], "elevation_m": act["elev"],
                     "avg_heartrate": act["hr"]},
        "last_activity": last_act,
    }


@app.get("/api/predictions")
def api_predictions():
    """Miejsce na modul predykcji (kolejny etap projektu)."""
    return JSONResponse({
        "available": False,
        "message": "Modul predykcji nie jest jeszcze wlaczony.",
        "planned": ["regresja liniowa trendu wagi", "ARIMA / Prophet",
                    "wplyw objetosci treningu na trend"],
    })


# ---------------------------------------------------------------- panel admina
def _admin_guard() -> None:
    if not config.ADMIN_ENABLED:
        raise HTTPException(status_code=403,
                            detail="Panel administracyjny wylaczony (ADMIN_ENABLED=0 w .env)")


def _require_user(conn, username: str):
    user = get_user(conn, username)
    if user is None:
        raise HTTPException(status_code=404, detail=f"Nie ma profilu '{username}'")
    return user


@app.get("/api/settings")
def api_settings():
    """Opis ustawien + aktualne wartosci (baza -> .env -> domyslne)."""
    with connect() as conn:
        values = settings.all_values(conn)
        stored = {r["key"] for r in conn.execute("SELECT key FROM settings")}
    return {"settings": settings.describe(), "values": values,
            "overridden": sorted(stored), "admin_enabled": config.ADMIN_ENABLED}


@app.put("/api/settings")
def api_settings_save(changes: dict = Body(...)):
    _admin_guard()
    with connect() as conn:
        try:
            settings.save(conn, changes)
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {"values": settings.all_values(conn), "saved": sorted(changes)}


@app.post("/api/settings/reset")
def api_settings_reset(key: str | None = Body(None, embed=True)):
    """Kasuje wartosc z bazy - wraca ta z .env."""
    _admin_guard()
    with connect() as conn:
        if key is not None and key not in settings.BY_KEY:
            raise HTTPException(status_code=400, detail=f"Nieznane ustawienie: {key}")
        settings.reset(conn, key)
        return {"values": settings.all_values(conn)}


@app.get("/api/admin/users")
def api_admin_users():
    """Profile z licznikami - do tabeli w panelu."""
    with connect() as conn:
        rows = rows_to_dicts(conn.execute(
            """SELECT u.*, COUNT(m.id) AS measurements,
                      MAX(m.measured_at) AS last_measured_at,
                      (SELECT weight_kg FROM measurements
                       WHERE user_id = u.id ORDER BY measured_at DESC LIMIT 1) AS last_weight
               FROM users u LEFT JOIN measurements m ON m.user_id = u.id
               GROUP BY u.id ORDER BY u.display_name""").fetchall())
    return {"users": rows}


@app.post("/api/users")
def api_user_create(payload: dict = Body(...)):
    _admin_guard()
    required = ("username", "display_name", "height_cm", "birthdate", "sex")
    missing = [f for f in required if not payload.get(f)]
    if missing:
        raise HTTPException(status_code=400, detail=f"Brakuje pol: {', '.join(missing)}")
    if payload["sex"] not in ("male", "female"):
        raise HTTPException(status_code=400, detail="Plec musi byc 'male' albo 'female'")
    with connect() as conn:
        if get_user(conn, payload["username"]) is not None:
            raise HTTPException(status_code=409,
                                detail=f"Login '{payload['username']}' jest juz zajety")
        try:
            uid = add_user(conn, payload["username"], payload["display_name"],
                           payload["height_cm"], payload["birthdate"], payload["sex"],
                           payload.get("ref_weight"), payload.get("garmin_profile_id"))
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {"id": uid, "username": payload["username"]}


@app.patch("/api/users/{username}")
def api_user_update(username: str, payload: dict = Body(...)):
    _admin_guard()
    with connect() as conn:
        _require_user(conn, username)
        if payload.get("sex") not in (None, "male", "female"):
            raise HTTPException(status_code=400, detail="Plec musi byc 'male' albo 'female'")
        changed = update_user(conn, username, payload)
        return {"updated": changed}


@app.delete("/api/users/{username}")
def api_user_delete(username: str):
    """Usuwa profil. Pomiary zostaja w bazie jako nieprzypisane."""
    _admin_guard()
    with connect() as conn:
        _require_user(conn, username)
        return {"deleted": delete_user(conn, username)}


@app.get("/api/measurements/all")
def api_measurements_all(user: str | None = None, unassigned: bool = False,
                         limit: int = Query(200, le=2000), offset: int = 0):
    """Wszystkie pomiary z wagi - bez okna czasowego, ze stronicowaniem."""
    with connect() as conn:
        where, args = "WHERE 1=1", []
        if unassigned:
            where += " AND m.user_id IS NULL"
        elif user and user != "all":
            uid = _user_id(conn, user)
            where += " AND m.user_id = ?"
            args.append(uid)
        total = conn.execute(
            f"SELECT COUNT(*) c FROM measurements m {where}", args).fetchone()["c"]
        rows = rows_to_dicts(conn.execute(
            f"""SELECT m.*, u.display_name AS user_display, u.username
                FROM measurements m LEFT JOIN users u ON u.id = m.user_id
                {where} ORDER BY m.measured_at DESC LIMIT ? OFFSET ?""",
            [*args, limit, offset]).fetchall())
    return {"measurements": rows, "total": total, "limit": limit, "offset": offset}


@app.get("/api/measurements/duplicates")
def api_duplicates(window: int = Query(60, ge=1, le=1440)):
    """Powtorzone wazenia: waga rozglasza ostatni wynik dlugo po zejsciu z niej."""
    with connect() as conn:
        groups = find_duplicate_groups(conn, window)
        total = conn.execute("SELECT COUNT(*) c FROM measurements").fetchone()["c"]
    return {"groups": groups, "total_measurements": total,
            "duplicates": sum(len(g["remove"]) for g in groups), "window_minutes": window}


@app.post("/api/measurements/dedupe")
def api_dedupe(window: int = Body(60, embed=True)):
    """Usuwa powtorki, zostawiajac najstarszy pomiar z kazdej serii."""
    _admin_guard()
    with connect() as conn:
        groups, removed = delete_duplicates(conn, max(1, min(window, 1440)))
    return {"groups": groups, "removed": removed}


@app.patch("/api/measurements/{measurement_id}")
def api_measurement_update(measurement_id: int, payload: dict = Body(...)):
    """Zmiana przypisania pomiaru. Sklad ciala jest przeliczany dla nowej osoby."""
    _admin_guard()
    with connect() as conn:
        row = get_measurement(conn, measurement_id)
        if row is None:
            raise HTTPException(status_code=404, detail=f"Nie ma pomiaru #{measurement_id}")

        username = payload.get("username")
        if username in (None, "", "none", "null"):
            user, user_id = None, None
        else:
            user = _require_user(conn, username)
            user_id = user["id"]

        when = datetime.fromisoformat(row["measured_at"])
        changes = {"user_id": user_id, "identify_method": "manual",
                   "identify_score": None}
        changes.update(composition_for(user, row["weight_kg"], row["impedance"], when))
        try:
            update_measurement(conn, measurement_id, changes)
        except Exception as exc:                # kolizja z UNIQUE(user_id, measured_at)
            raise HTTPException(
                status_code=409,
                detail=f"Ten profil ma juz pomiar z {row['measured_at']} ({exc})")
        return {"id": measurement_id, "user_id": user_id,
                "user": user["display_name"] if user else None}


@app.delete("/api/measurements/{measurement_id}")
def api_measurement_delete(measurement_id: int):
    _admin_guard()
    with connect() as conn:
        if get_measurement(conn, measurement_id) is None:
            raise HTTPException(status_code=404, detail=f"Nie ma pomiaru #{measurement_id}")
        return {"deleted": delete_measurement(conn, measurement_id)}


@app.get("/api/health")
def api_health():
    with connect() as conn:
        m = conn.execute("SELECT COUNT(*) c FROM measurements").fetchone()["c"]
        u = conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
        a = conn.execute("SELECT COUNT(*) c FROM garmin_activities").fetchone()["c"]
        gd = conn.execute("SELECT MAX(day) d, COUNT(*) c FROM garmin_daily").fetchone()
    return {"status": "ok", "version": __version__, "db": str(config.DB_PATH), "users": u,
            "measurements": m, "activities": a, "garmin_days": gd["c"],
            "garmin_last_day": gd["d"],
            "garmin_connected": Path(config.GARMIN_TOKENSTORE).exists()}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=config.WEB_HOST, port=config.WEB_PORT)
