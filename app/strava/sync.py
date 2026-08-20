"""Pobieranie aktywnosci ze Stravy do tabeli `activities`.

    python3 -m app.strava.sync              # tylko nowsze niz ostatnia w bazie
    python3 -m app.strava.sync --all        # cala historia
    python3 -m app.strava.sync --days 30    # ostatnie 30 dni
    python3 -m app.strava.sync --loop       # demon: co STRAVA_SYNC_INTERVAL sekund
    python3 -m app.strava.sync --details    # dociaga kalorie (1 zapytanie/aktywnosc)
"""
from __future__ import annotations

import argparse
import logging
import time
from datetime import datetime, timedelta, timezone

from app import config
from app.db import (connect, init_db, latest_activity_epoch, list_users,
                    upsert_activity)
from app.strava.client import StravaClient, StravaError

log = logging.getLogger("strava.sync")


def _user_map(conn) -> dict[int, int]:
    return {u["strava_athlete_id"]: u["id"] for u in list_users(conn)
            if u["strava_athlete_id"]}


def sync(conn, after: int | None = None, with_details: bool = False) -> tuple[int, int]:
    """Zwraca (nowe, zaktualizowane)."""
    client = StravaClient(conn)
    athlete = client.athlete()
    athlete_id = athlete.get("id")
    users = _user_map(conn)
    user_id = users.get(athlete_id)
    if user_id is None:
        log.warning("Athlete %s nie jest powiazany z profilem "
                    "(python3 manage_users.py link <username> %s)", athlete_id, athlete_id)

    if after is None:
        after = latest_activity_epoch(conn, athlete_id)
        if after:
            log.info("Sync przyrostowy od %s",
                     datetime.fromtimestamp(after, timezone.utc).isoformat())
        else:
            log.info("Baza pusta - pobieram cala historie")

    new = updated = 0
    for act in client.activities(after=after or None):
        act.setdefault("athlete", {"id": athlete_id})
        if with_details:
            try:
                act = client.activity_detail(act["id"])
                act.setdefault("athlete", {"id": athlete_id})
            except StravaError as exc:
                log.warning("Brak szczegolow dla %s: %s", act["id"], exc)
        if upsert_activity(conn, act, user_id):
            new += 1
        else:
            updated += 1
    return new, updated


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync aktywnosci ze Stravy")
    parser.add_argument("--all", action="store_true", help="cala historia")
    parser.add_argument("--days", type=int, help="ostatnie N dni")
    parser.add_argument("--details", action="store_true", help="dociagnij kalorie/opis")
    parser.add_argument("--loop", action="store_true", help="pracuj w petli jako usluga")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    init_db()
    conn = connect()

    after: int | None = None
    if args.all:
        after = 0
    elif args.days:
        after = int((datetime.now(timezone.utc) - timedelta(days=args.days)).timestamp())

    try:
        while True:
            try:
                new, updated = sync(conn, after, args.details)
                log.info("Sync zakonczony: %d nowych, %d zaktualizowanych", new, updated)
            except StravaError as exc:
                log.error("%s", exc)
                if not args.loop:
                    raise SystemExit(1)
            if not args.loop:
                break
            after = None                      # kolejne przebiegi zawsze przyrostowo
            time.sleep(max(60, config.STRAVA_SYNC_INTERVAL))
    except KeyboardInterrupt:
        log.info("Zatrzymano.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
