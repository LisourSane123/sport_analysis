"""Klient Garmin Connect.

Garmin nie ma publicznego API dla zwyklego uzytkownika (oficjalne jest tylko
dla producentow sprzetu), wiec korzystamy z biblioteki `garminconnect`, ktora
loguje sie tak jak strona connect.garmin.com i trzyma tokeny OAuth w pliku.

Logujemy sie RAZ (python3 -m app.garmin.auth), a potem wszystko dziala z
tokenow z GARMIN_TOKENSTORE - bez hasla w konfiguracji i bez kodow MFA.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable

from garminconnect import (Garmin, GarminConnectAuthenticationError,
                           GarminConnectConnectionError,
                           GarminConnectTooManyRequestsError)

from app import config

log = logging.getLogger("garmin")


class GarminError(RuntimeError):
    pass


class GarminAuthError(GarminError):
    pass


def _tokenstore(path: Path | str | None = None) -> str:
    return str(Path(path or config.GARMIN_TOKENSTORE).expanduser())


def login(email: str | None = None, password: str | None = None,
          tokenstore: Path | str | None = None,
          prompt_mfa: Callable[[], str] | None = None) -> Garmin:
    """Zwraca zalogowanego klienta.

    Kolejnosc: tokeny z dysku -> (jesli ich nie ma) email + haslo.
    Po udanym logowaniu haslem tokeny sa zapisywane, wiec nastepnym razem
    logowanie juz sie nie odbywa.
    """
    store = _tokenstore(tokenstore)
    email = email if email is not None else config.GARMIN_EMAIL
    password = password if password is not None else config.GARMIN_PASSWORD

    api = Garmin(email=email or None, password=password or None,
                 prompt_mfa=prompt_mfa)
    try:
        api.login(tokenstore=store)
    except GarminConnectAuthenticationError as exc:
        raise GarminAuthError(
            f"Logowanie do Garmin Connect odrzucone: {exc}\n"
            "Zaloguj sie ponownie: python3 -m app.garmin.auth") from exc
    except GarminConnectTooManyRequestsError as exc:
        raise GarminError(f"Garmin ogranicza zapytania - sprobuj pozniej ({exc})") from exc
    except FileNotFoundError as exc:
        raise GarminAuthError(
            f"Brak zapisanych tokenow ({store}) i brak danych logowania.\n"
            "Uruchom raz: python3 -m app.garmin.auth") from exc
    except GarminConnectConnectionError as exc:
        raise GarminError(f"Nie udalo sie polaczyc z Garmin Connect: {exc}") from exc

    log.info("Zalogowano do Garmin Connect jako %s (profil %s)",
             api.full_name, api.display_name)
    return api


def profile_id(api: Garmin) -> str:
    """Stabilny identyfikator konta (displayName) - klucz w tabelach garmin_*."""
    return api.display_name or api.get_full_name() or "garmin"


def call(fn: Callable[..., Any], *args, default=None, what: str = "") -> Any:
    """Wywoluje metode API i nie wywraca calego syncu, gdy jeden endpoint padnie.

    Garmin potrafi zwrocic 404 dla dnia bez danych albo wylaczyc funkcje,
    ktorej zegarek nie obsluguje - to nie jest powod, zeby przerwac import.
    """
    try:
        return fn(*args)
    except GarminConnectTooManyRequestsError:
        raise
    except GarminConnectAuthenticationError:
        raise
    except Exception as exc:                       # noqa: BLE001 - celowo szeroko
        log.debug("Pominieto %s: %s", what or getattr(fn, "__name__", "?"), exc)
        return default
