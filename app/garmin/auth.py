"""Jednorazowe logowanie do Garmin Connect.

    python3 -m app.garmin.auth              # pyta o e-mail, haslo i ewentualny kod MFA
    python3 -m app.garmin.auth --status     # sprawdza, czy zapisane tokeny dzialaja
    python3 -m app.garmin.auth --logout     # kasuje tokeny

Haslo nie jest nigdzie zapisywane. Do pliku GARMIN_TOKENSTORE trafiaja tylko
tokeny OAuth (waznosc ok. roku, odswiezane w tle), wiec sync moze dzialac
jako usluga bez zadnych danych logowania w .env.
"""
from __future__ import annotations

import argparse
import getpass
import shutil

from app import config
from app.db import connect, init_db, list_users
from app.garmin.client import GarminError, login, profile_id


def _prompt_mfa() -> str:
    return input("Kod uwierzytelniania dwuskladnikowego (MFA): ").strip()


def main() -> None:
    parser = argparse.ArgumentParser(description="Logowanie do Garmin Connect")
    parser.add_argument("--status", action="store_true", help="sprawdz zapisane tokeny")
    parser.add_argument("--logout", action="store_true", help="usun zapisane tokeny")
    parser.add_argument("--email", help="e-mail konta Garmin (domyslnie: zapyta)")
    args = parser.parse_args()

    store = config.GARMIN_TOKENSTORE
    if args.logout:
        if store.exists():
            if store.is_dir():
                shutil.rmtree(store)
            else:
                store.unlink()
            print(f"Usunieto tokeny: {store}")
        else:
            print("Nie ma czego usuwac.")
        return

    if args.status:
        try:
            api = login(email="", password="")       # tylko tokeny z dysku
        except GarminError as exc:
            raise SystemExit(str(exc))
        print(f"Tokeny dzialaja. Konto: {api.get_full_name()} "
              f"(profil {profile_id(api)})")
        return

    email = args.email or config.GARMIN_EMAIL or input("E-mail konta Garmin: ").strip()
    password = config.GARMIN_PASSWORD or getpass.getpass("Haslo (nie zostanie zapisane): ")

    try:
        api = login(email=email, password=password, prompt_mfa=_prompt_mfa)
    except GarminError as exc:
        raise SystemExit(str(exc))

    profile = profile_id(api)
    print(f"\nOK. Zalogowano jako {api.get_full_name()}")
    print(f"Tokeny zapisane w: {store}")
    print(f"Identyfikator profilu: {profile}\n")

    init_db()
    with connect() as conn:
        names = [u["username"] for u in list_users(conn)]
    print("Powiaz profil Garmina z uzytkownikiem wagi:")
    print(f"  python3 manage_users.py link-garmin <username>"
          + (f"     # profile: {', '.join(names)}" if names else ""))
    print("Nastepnie pobierz dane:  python3 -m app.garmin.sync --days 30")


if __name__ == "__main__":
    main()
