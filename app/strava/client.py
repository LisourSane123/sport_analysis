"""Klient Strava API v3: odswiezanie tokenu + pobieranie aktywnosci."""
from __future__ import annotations

import logging
import time
from typing import Any, Iterator

import httpx

from app import config
from app.db import get_tokens, save_tokens

API = "https://www.strava.com/api/v3"
TOKEN_URL = "https://www.strava.com/oauth/token"
AUTHORIZE_URL = "https://www.strava.com/oauth/authorize"
DEFAULT_SCOPE = "read,activity:read_all,profile:read_all"

log = logging.getLogger("strava")


class StravaError(RuntimeError):
    pass


def authorize_url(redirect_uri: str, scope: str = DEFAULT_SCOPE) -> str:
    if not config.STRAVA_CLIENT_ID:
        raise StravaError("Brak STRAVA_CLIENT_ID w .env")
    return (f"{AUTHORIZE_URL}?client_id={config.STRAVA_CLIENT_ID}"
            f"&response_type=code&redirect_uri={redirect_uri}"
            f"&approval_prompt=force&scope={scope}")


def exchange_code(conn, code: str) -> dict[str, Any]:
    """Wymienia jednorazowy kod z przegladarki na parę tokenow i zapisuje ja w bazie."""
    payload = {
        "client_id": config.STRAVA_CLIENT_ID,
        "client_secret": config.STRAVA_CLIENT_SECRET,
        "code": code,
        "grant_type": "authorization_code",
    }
    resp = httpx.post(TOKEN_URL, data=payload, timeout=30)
    if resp.status_code != 200:
        raise StravaError(f"Wymiana kodu nieudana ({resp.status_code}): {resp.text}")
    data = resp.json()
    athlete_id = (data.get("athlete") or {}).get("id")
    save_tokens(conn, athlete_id, data["access_token"], data["refresh_token"],
                data["expires_at"], data.get("scope"))
    return data


class StravaClient:
    """Trzyma tokeny w SQLite i odswieza je automatycznie przed wygasnieciem."""

    def __init__(self, conn, athlete_id: int | None = None):
        self.conn = conn
        self.athlete_id = athlete_id
        if not (config.STRAVA_CLIENT_ID and config.STRAVA_CLIENT_SECRET):
            raise StravaError("Uzupelnij STRAVA_CLIENT_ID i STRAVA_CLIENT_SECRET w .env")

    def _tokens(self):
        row = get_tokens(self.conn, self.athlete_id)
        if row is None:
            raise StravaError("Brak tokenow Stravy - uruchom: python3 -m app.strava.auth")
        return row

    def access_token(self) -> str:
        row = self._tokens()
        if row["expires_at"] - time.time() > 300:
            return row["access_token"]

        log.info("Token wygasa - odswiezam")
        resp = httpx.post(TOKEN_URL, timeout=30, data={
            "client_id": config.STRAVA_CLIENT_ID,
            "client_secret": config.STRAVA_CLIENT_SECRET,
            "grant_type": "refresh_token",
            "refresh_token": row["refresh_token"],
        })
        if resp.status_code != 200:
            raise StravaError(f"Odswiezenie tokenu nieudane ({resp.status_code}): {resp.text}")
        data = resp.json()
        save_tokens(self.conn, row["athlete_id"], data["access_token"],
                    data["refresh_token"], data["expires_at"], row["scope"])
        return data["access_token"]

    def _get(self, path: str, params: dict | None = None) -> Any:
        resp = httpx.get(f"{API}{path}", params=params, timeout=30,
                         headers={"Authorization": f"Bearer {self.access_token()}"})
        if resp.status_code == 429:
            raise StravaError("Limit zapytan Stravy wyczerpany (200/15 min) - sprobuj pozniej")
        if resp.status_code != 200:
            raise StravaError(f"GET {path} -> {resp.status_code}: {resp.text}")
        return resp.json()

    # --- API ---
    def athlete(self) -> dict[str, Any]:
        return self._get("/athlete")

    def activities(self, after: int | None = None, before: int | None = None,
                   per_page: int = 100, max_pages: int = 20) -> Iterator[dict[str, Any]]:
        """Strumien aktywnosci (najnowsze najpierw wg stron Stravy)."""
        page = 1
        while page <= max_pages:
            params = {"per_page": per_page, "page": page}
            if after:
                params["after"] = int(after)
            if before:
                params["before"] = int(before)
            batch = self._get("/athlete/activities", params)
            if not batch:
                return
            yield from batch
            if len(batch) < per_page:
                return
            page += 1

    def activity_detail(self, activity_id: int) -> dict[str, Any]:
        """Pelne dane aktywnosci (m.in. kalorie, opis) - jedno zapytanie na aktywnosc."""
        return self._get(f"/activities/{activity_id}", {"include_all_efforts": "false"})
