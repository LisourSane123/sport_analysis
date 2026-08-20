"""Pobieranie danych z Garmin Connect do tabel `garmin_activities` i `garmin_daily`.

    python3 -m app.garmin.sync                # przyrostowo: aktywnosci + dni
    python3 -m app.garmin.sync --days 90      # ostatnie 90 dni
    python3 -m app.garmin.sync --all          # cala historia aktywnosci
    python3 -m app.garmin.sync --activities   # tylko aktywnosci
    python3 -m app.garmin.sync --daily        # tylko dzienne podsumowania
    python3 -m app.garmin.sync --quick        # dzien = samo podsumowanie (1 zapytanie)
    python3 -m app.garmin.sync --loop         # demon: co GARMIN_SYNC_INTERVAL sekund

Jeden dzien to domyslnie 5 zapytan (podsumowanie, sen, HRV, gotowosc, VO2max),
miedzy zapytaniami jest przerwa GARMIN_REQUEST_PAUSE - Garmin nie lubi serii
bez oddechu i potrafi na chwile zablokowac konto.
"""
from __future__ import annotations

import argparse
import logging
import time
from datetime import date, datetime, timedelta

from app import config, settings
from app.db import (connect, init_db, latest_garmin_activity_day,
                    upsert_garmin_activity, upsert_garmin_daily,
                    user_id_for_garmin)
from app.garmin.client import GarminError, call, login, profile_id
from app.garmin.mapping import activity_row, daily_row, has_data

log = logging.getLogger("garmin.sync")

EPOCH_START = "2000-01-01"


def _pause(seconds: float | None = None) -> None:
    time.sleep(max(0.0, config.GARMIN_REQUEST_PAUSE if seconds is None else seconds))


def _days_back(days: int) -> str:
    return (date.today() - timedelta(days=max(0, days))).isoformat()


# --------------------------------------------------------------------------
def sync_activities(conn, api, profile: str, user_id: int | None,
                    start: str | None = None) -> tuple[int, int]:
    """Zwraca (nowe, zaktualizowane)."""
    if start is None:
        last = latest_garmin_activity_day(conn, profile)
        # dzien wstecz, bo aktywnosc z konca dnia moze dojechac pozniej
        start = ((datetime.strptime(last, "%Y-%m-%d") - timedelta(days=1)).date()
                 .isoformat() if last else _days_back(
                     settings.get(conn, "garmin_backfill_days")))
    log.info("Aktywnosci od %s", start)

    activities = call(api.get_activities_by_date, start, date.today().isoformat(),
                      default=[], what="get_activities_by_date") or []
    new = updated = 0
    for act in activities:
        if not act.get("activityId"):
            continue
        if upsert_garmin_activity(conn, activity_row(act, profile, user_id)):
            new += 1
        else:
            updated += 1
    return new, updated


def sync_day(conn, api, profile: str, user_id: int | None, day: str,
             quick: bool = False, pause: float | None = None) -> bool:
    """Jeden dzien: podsumowanie + (opcjonalnie) sen, HRV, gotowosc, VO2max."""
    stats = call(api.get_stats, day, default={}, what=f"stats {day}")
    _pause(pause)
    sleep = hrv = readiness = metrics = None
    if not quick:
        sleep = call(api.get_sleep_data, day, what=f"sleep {day}")
        _pause(pause)
        hrv = call(api.get_hrv_data, day, what=f"hrv {day}")
        _pause(pause)
        readiness = call(api.get_training_readiness, day, what=f"readiness {day}")
        _pause(pause)
        metrics = call(api.get_max_metrics, day, what=f"max_metrics {day}")
        _pause(pause)

    row = daily_row(day, profile, user_id, stats=stats, sleep=sleep, hrv=hrv,
                    readiness=readiness, max_metrics=metrics)
    if not has_data(row):
        log.debug("%s: brak danych, pomijam", day)
        return False
    upsert_garmin_daily(conn, row)
    return True


def sync_daily(conn, api, profile: str, user_id: int | None,
               days: int, quick: bool = False, pause: float | None = None) -> int:
    """Ostatnie `days` dni wstecz od dzisiaj. Zwraca liczbe zapisanych dni."""
    if not quick and days > 60:
        every = config.GARMIN_REQUEST_PAUSE if pause is None else pause
        log.warning("%d dni x 5 zapytan - to potrwa okolo %d min. "
                    "Szybciej: --quick", days, int(days * 5 * max(every, 0.2) / 60) + 1)
    saved = 0
    for offset in range(days, -1, -1):
        day = (date.today() - timedelta(days=offset)).isoformat()
        if sync_day(conn, api, profile, user_id, day, quick, pause):
            saved += 1
        if saved and saved % 25 == 0:
            log.info("... %d dni zapisanych (ostatni: %s)", saved, day)
    return saved


def _daily_window(conn, profile: str, requested: int | None) -> int:
    """Ile dni wstecz odswiezyc: podane przez uzytkownika albo od ostatniego wpisu."""
    if requested is not None:
        return requested
    last = conn.execute("SELECT MAX(day) FROM garmin_daily WHERE profile_id = ?",
                        (profile,)).fetchone()[0]
    if not last:
        return settings.get(conn, "garmin_backfill_days")
    gap = (date.today() - datetime.strptime(last, "%Y-%m-%d").date()).days
    # zawsze odswiezamy 2 ostatnie dni - sen i HRV dopisuja sie z opoznieniem
    return min(max(gap, 2), 365)


def sync(conn, api=None, *, days: int | None = None, all_history: bool = False,
         do_activities: bool = True, do_daily: bool = True,
         quick: bool = False) -> dict[str, int]:
    api = api or login()
    cfg = settings.all_values(conn)           # panel moze je zmienic bez restartu
    profile = profile_id(api)
    user_id = user_id_for_garmin(conn, profile)
    if user_id is None:
        log.warning("Profil Garmina '%s' nie jest powiazany z uzytkownikiem "
                    "(python3 manage_users.py link-garmin <username>)", profile)

    result = {"new_activities": 0, "updated_activities": 0, "days": 0}
    if do_activities:
        start = EPOCH_START if all_history else (_days_back(days) if days else None)
        new, updated = sync_activities(conn, api, profile, user_id, start)
        result["new_activities"], result["updated_activities"] = new, updated
    if do_daily:
        window = 365 if all_history else _daily_window(conn, profile, days)
        result["days"] = sync_daily(conn, api, profile, user_id, window, quick,
                                    cfg["garmin_request_pause"])
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync danych z Garmin Connect")
    parser.add_argument("--days", type=int, help="ile dni wstecz")
    parser.add_argument("--all", action="store_true",
                        help="cala historia aktywnosci (dzienne: rok wstecz)")
    parser.add_argument("--activities", action="store_true", help="tylko aktywnosci")
    parser.add_argument("--daily", action="store_true", help="tylko dzienne podsumowania")
    parser.add_argument("--quick", action="store_true",
                        help="dzien bez snu/HRV/gotowosci (1 zapytanie zamiast 5)")
    parser.add_argument("--loop", action="store_true", help="pracuj w petli jako usluga")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    init_db()
    conn = connect()

    do_activities = args.activities or not args.daily
    do_daily = args.daily or not args.activities
    days, all_history = args.days, args.all

    try:
        while True:
            try:
                api = login()
                res = sync(conn, api, days=days, all_history=all_history,
                           do_activities=do_activities, do_daily=do_daily,
                           quick=args.quick)
                log.info("Sync zakonczony: %d nowych aktywnosci, %d zaktualizowanych, "
                         "%d dni", res["new_activities"], res["updated_activities"],
                         res["days"])
            except GarminError as exc:
                log.error("%s", exc)
                if not args.loop:
                    raise SystemExit(1)
            if not args.loop:
                break
            days, all_history = None, False      # kolejne przebiegi przyrostowo
            time.sleep(max(300, settings.get(conn, "garmin_sync_interval")))
    except KeyboardInterrupt:
        log.info("Zatrzymano.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
