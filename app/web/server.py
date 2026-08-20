"""Dashboard: FastAPI + lekki frontend (Chart.js).

Uruchomienie: python3 -m app.web.server   albo   uvicorn app.web.server:app
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app import config
from app.db import connect, init_db

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
                      u.strava_athlete_id, COUNT(m.id) AS measurements
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
                   limit: int = Query(500, le=5000)):
    since = _since(days)
    with connect() as conn:
        sql = "SELECT * FROM activities WHERE 1=1"
        args: list[Any] = []
        if since:
            sql += " AND start_date_local >= ?"
            args.append(since)
        if sport and sport != "all":
            sql += " AND sport_type = ?"
            args.append(sport)
        sql += " ORDER BY start_date_local DESC LIMIT ?"
        args.append(limit)
        activities = rows_to_dicts(conn.execute(sql, args).fetchall())
        sports = [r["sport_type"] for r in conn.execute(
            "SELECT DISTINCT sport_type FROM activities WHERE sport_type IS NOT NULL "
            "ORDER BY sport_type").fetchall()]
    for a in activities:
        a.pop("raw_json", None)
    return {"activities": activities, "sports": sports}


@app.get("/api/summary")
def api_summary(user: str | None = None, days: int = 30):
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
        act = conn.execute(
            f"""SELECT COUNT(*) n, COALESCE(SUM(distance_m),0) dist,
                       COALESCE(SUM(moving_time_s),0) time,
                       COALESCE(SUM(total_elevation_gain),0) elev,
                       AVG(average_heartrate) hr
                FROM activities WHERE start_date_local >= ? {act_where}""",
            [since or "0", *act_args]).fetchone()
        last_activity = conn.execute(
            f"SELECT * FROM activities WHERE 1=1 {act_where} "
            f"ORDER BY start_date_local DESC LIMIT 1", act_args).fetchone()

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


@app.get("/api/health")
def api_health():
    with connect() as conn:
        m = conn.execute("SELECT COUNT(*) c FROM measurements").fetchone()["c"]
        a = conn.execute("SELECT COUNT(*) c FROM activities").fetchone()["c"]
        u = conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
        tok = conn.execute("SELECT COUNT(*) c FROM strava_tokens").fetchone()["c"]
        ga = conn.execute("SELECT COUNT(*) c FROM garmin_activities").fetchone()["c"]
        gd = conn.execute("SELECT MAX(day) d, COUNT(*) c FROM garmin_daily").fetchone()
    return {"status": "ok", "db": str(config.DB_PATH), "users": u,
            "measurements": m, "activities": a, "strava_connected": bool(tok),
            "garmin_activities": ga, "garmin_days": gd["c"],
            "garmin_last_day": gd["d"],
            "garmin_connected": Path(config.GARMIN_TOKENSTORE).exists()}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=config.WEB_HOST, port=config.WEB_PORT)
