#!/usr/bin/env python3
"""Zarzadzanie profilami uzytkownikow.

    python3 manage_users.py add                 # kreator - pyta o imie, plec, wzrost...
    python3 manage_users.py add ruka "Lukasz" 182 1995-04-12 male 84      # bez pytan
    python3 manage_users.py list
    python3 manage_users.py edit ruka --height 183 --max 97
    python3 manage_users.py link-garmin ruka
    python3 manage_users.py delete ruka

Podana waga jest tylko punktem startowym: kolejne pomiary sa przypisywane
do osoby na podstawie przedzialu predykcyjnego liczonego z jej ostatnich
wazen (patrz app/scale/identify.py).
"""
from __future__ import annotations

import argparse
import re
import unicodedata
from datetime import datetime, timedelta

from app.db import (add_user, age_of, connect, delete_user, get_user, init_db,
                    list_users)
from app.scale.identify import build_candidate


def _valid_date(value: str) -> str:
    datetime.strptime(value, "%Y-%m-%d")
    return value


# --------------------------------------------------------------------------
# Kreator: pytania zadawane, gdy wartosci nie podano w argumentach
# --------------------------------------------------------------------------
def ask(question: str, parse=str, default=None, hint: str = ""):
    """Pyta, dopoki nie dostanie poprawnej odpowiedzi. Enter = wartosc domyslna."""
    suffix = f" [{default}]" if default is not None else ""
    if hint:
        suffix = f" ({hint}){suffix}"
    while True:
        try:
            answer = input(f"{question}{suffix}: ").strip()
        except EOFError:
            raise SystemExit("\nPrzerwano.")
        if not answer:
            if default is not None:
                return parse(str(default)) if not isinstance(default, str) else parse(default)
            print("  -> podaj wartosc")
            continue
        try:
            return parse(answer)
        except (ValueError, TypeError) as exc:
            print(f"  -> niepoprawna wartosc ({exc})")


def _parse_sex(value: str) -> str:
    v = value.strip().lower()
    if v in ("m", "male", "mezczyzna", "mężczyzna", "1"):
        return "male"
    if v in ("k", "f", "female", "kobieta", "2"):
        return "female"
    raise ValueError("wpisz M (mezczyzna) albo K (kobieta)")


def _parse_height(value: str) -> float:
    height = float(value.replace(",", "."))
    if not 90 <= height <= 220:
        raise ValueError("wzrost w cm, 90-220")
    return height


def _parse_weight(value: str) -> float:
    weight = float(value.replace(",", "."))
    if not 10 <= weight <= 250:
        raise ValueError("waga w kg, 10-250")
    return weight


_PL = str.maketrans({"ł": "l", "Ł": "L", "ø": "o", "đ": "d", "ß": "ss"})


def _slugify(name: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", name.translate(_PL))
    ascii_name = ascii_name.encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "", ascii_name.lower()) or "user"


def _reference_weight(conn, user_row) -> tuple[float | None, str]:
    """Aktualny punkt odniesienia profilu: ostatni pomiar albo waga z profilu."""
    row = conn.execute(
        "SELECT weight_kg, measured_at FROM measurements WHERE user_id = ? "
        "ORDER BY measured_at DESC LIMIT 1", (user_row["id"],)).fetchone()
    if row:
        return row["weight_kg"], row["measured_at"][:10]
    return user_row["ref_weight"], "profil"


def cmd_add(conn, a) -> None:
    interactive = a.username is None
    if interactive:
        print("Nowy profil - wcisnij Enter, zeby przyjac wartosc w nawiasie.\n")

    display_name = a.display_name or ask("Imie (jak ma sie wyswietlac)")
    username = a.username or ask("Login (bez spacji)", default=_slugify(display_name))
    while get_user(conn, username) is not None:
        print(f"  -> login '{username}' jest juz zajety")
        username = ask("Login (bez spacji)")

    sex = a.sex or ask("Plec", parse=_parse_sex, hint="M/K")
    height = a.height or ask("Wzrost [cm]", parse=_parse_height)
    birthdate = a.birthdate or ask("Data urodzenia", parse=_valid_date, hint="RRRR-MM-DD")

    ref_weight = a.ref_weight
    if ref_weight is None and interactive:
        print("\nWaga startowa sluzy tylko do rozpoznania pierwszych pomiarow -")
        print("pozniej profil uczy sie z wlasnej historii wazen.")
        ref_weight = ask("Obecna waga [kg]", parse=_parse_weight)

    if ref_weight is not None:
        close = []
        for other in list_users(conn):
            reference, _ = _reference_weight(conn, other)
            if reference is not None and abs(reference - ref_weight) < 3:
                close.append(f"{other['display_name']} ({reference:.1f} kg)")
        if close:
            print(f"\nUWAGA: podobna waga ma juz: {', '.join(close)}")
            print("Przy tak bliskich wagach pomiary moga trafiac do zlego profilu -")
            print("warto sprawdzac przypisanie w zakladce Historia.")
            if interactive and ask("Zapisac mimo to?", default="t",
                                   hint="t/n").lower() not in ("t", "tak", "y"):
                raise SystemExit("Anulowano.")

    uid = add_user(conn, username, display_name, height, birthdate, sex, ref_weight)
    print(f"\nDodano profil #{uid}: {display_name} ({username}), {sex}, {height:.0f} cm, "
          f"ur. {birthdate}"
          + (f", waga startowa {ref_weight:.1f} kg" if ref_weight else ""))
    if ref_weight is None:
        print("Bez wagi startowej pierwszy pomiar moze zostac nieprzypisany "
              "(uzupelnisz: manage_users.py edit "
              f"{username} --weight <kg>)")
    print("Konto Garmina powiazesz: python3 manage_users.py link-garmin "
          f"{username}")


def cmd_list(conn, _a) -> None:
    rows = list_users(conn)
    if not rows:
        print("Brak profili. Dodaj: python3 manage_users.py add")
        return
    now = datetime.now()
    print(f"{'id':<4}{'username':<12}{'imie':<16}{'wzrost':<8}{'wiek':<6}{'plec':<8}"
          f"{'ost. waga':<15}{'przedzial (7 dni)':<22}garmin")
    for r in rows:
        reference, source = _reference_weight(conn, r)
        samples = [(datetime.fromisoformat(m["measured_at"]), m["weight_kg"])
                   for m in conn.execute(
                       "SELECT measured_at, weight_kg FROM measurements "
                       "WHERE user_id = ? AND measured_at >= ? ORDER BY measured_at",
                       (r["id"], (now - timedelta(days=7)).isoformat(timespec="seconds")))]
        cand = build_candidate(r, samples, now, r["ref_weight"])
        if cand is None:
            span = "brak danych"
        else:
            lo, hi = cand.bounds
            span = f"{lo:.1f}-{hi:.1f} kg (n={cand.n})"
        last = f"{reference:.1f} ({source})" if reference is not None else "-"
        print(f"{r['id']:<4}{r['username']:<12}{r['display_name'][:15]:<16}"
              f"{r['height_cm']:<8.0f}{age_of(r):<6}{r['sex']:<8}{last:<15}{span:<22}"
              f"{(r['garmin_profile_id'] or '-')[:24]}")


def cmd_edit(conn, a) -> None:
    user = get_user(conn, a.username)
    if user is None:
        raise SystemExit(f"Nie ma profilu '{a.username}'")
    fields = {"height_cm": a.height, "birthdate": a.birthdate, "sex": a.sex,
              "ref_weight": a.ref_weight, "display_name": a.display_name}
    changes = {k: v for k, v in fields.items() if v is not None}
    if not changes:
        raise SystemExit("Nic do zmiany - podaj przynajmniej jedna opcje")
    conn.execute(f"UPDATE users SET {','.join(f'{k}=?' for k in changes)} WHERE username=?",
                 [*changes.values(), a.username])
    conn.commit()
    print(f"Zaktualizowano {a.username}: {', '.join(changes)}")


def cmd_link_garmin(conn, a) -> None:
    if get_user(conn, a.username) is None:
        raise SystemExit(f"Nie ma profilu '{a.username}'")
    profile = a.profile_id
    if not profile:                      # bez argumentu: bierzemy z zapisanych tokenow
        from app.garmin.client import GarminError, login, profile_id
        try:
            profile = profile_id(login(email="", password=""))
        except GarminError as exc:
            raise SystemExit(f"{exc}\nAlbo podaj identyfikator recznie: "
                             f"manage_users.py link-garmin {a.username} <profile_id>")
    conn.execute("UPDATE users SET garmin_profile_id=? WHERE username=?",
                 (profile, a.username))
    for table in ("garmin_activities", "garmin_daily"):
        conn.execute(f"UPDATE {table} SET user_id=(SELECT id FROM users WHERE username=?) "
                     f"WHERE profile_id=?", (a.username, profile))
    conn.commit()
    print(f"Powiazano {a.username} z profilem Garmina {profile} "
          "(istniejace dane tez przypisane)")


def cmd_delete(conn, a) -> None:
    if delete_user(conn, a.username):
        print(f"Usunieto {a.username}. Pomiary zostaly w bazie jako nieprzypisane.")
    else:
        print(f"Nie ma profilu '{a.username}'")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Profile uzytkownikow wagi")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("add", help="dodaj profil (bez argumentow: kreator z pytaniami)")
    a.add_argument("username", nargs="?")
    a.add_argument("display_name", nargs="?")
    a.add_argument("height", nargs="?", type=_parse_height, help="wzrost w cm")
    a.add_argument("birthdate", nargs="?", type=_valid_date, help="RRRR-MM-DD")
    a.add_argument("sex", nargs="?", type=_parse_sex, help="male/female (lub M/K)")
    a.add_argument("ref_weight", nargs="?", type=_parse_weight,
                   help="obecna waga [kg] - punkt startowy rozpoznawania")
    a.set_defaults(func=cmd_add)

    l = sub.add_parser("list", help="pokaz profile")
    l.set_defaults(func=cmd_list)

    e = sub.add_parser("edit", help="zmien profil")
    e.add_argument("username")
    e.add_argument("--name", dest="display_name")
    e.add_argument("--height", type=float)
    e.add_argument("--birthdate", type=_valid_date)
    e.add_argument("--sex", choices=["male", "female"])
    e.add_argument("--weight", type=_parse_weight, dest="ref_weight",
                   help="waga startowa [kg]")
    e.set_defaults(func=cmd_edit)

    g = sub.add_parser("link-garmin", help="powiaz profil z kontem Garmin Connect")
    g.add_argument("username")
    g.add_argument("profile_id", nargs="?",
                   help="displayName konta; bez tego bierzemy z zapisanych tokenow")
    g.set_defaults(func=cmd_link_garmin)

    d = sub.add_parser("delete", help="usun profil")
    d.add_argument("username")
    d.set_defaults(func=cmd_delete)
    return p


if __name__ == "__main__":
    args = build_parser().parse_args()
    init_db()
    with connect() as conn:
        args.func(conn, args)
