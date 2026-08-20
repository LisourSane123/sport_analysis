#!/usr/bin/env python3
"""Usuwa pozostalosci po integracji ze Strava z istniejacej bazy.

    python3 tools/drop_strava.py            # pokazuje, co zostanie usuniete i pyta
    python3 tools/drop_strava.py --yes      # bez pytania (np. w skrypcie)

Kasuje tabele `activities` i `strava_tokens` oraz kolumne `users.strava_athlete_id`.
To operacja nieodwracalna - zrob najpierw kopie:

    sqlite3 data/waga.db ".backup data/waga-przed-usunieciem-stravy.db"

Nowe bazy nie maja tych obiektow w ogole; skrypt jest tylko dla baz zalozonych
wczesniejsza wersja projektu.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import DB_PATH          # noqa: E402
from app.db import connect              # noqa: E402

TABLES = ("activities", "strava_tokens")


def main() -> None:
    parser = argparse.ArgumentParser(description="Usuwa tabele Stravy z bazy")
    parser.add_argument("--yes", action="store_true", help="nie pytaj o potwierdzenie")
    parser.add_argument("--db", default=str(DB_PATH), help="sciezka do bazy")
    args = parser.parse_args()

    if not Path(args.db).exists():
        raise SystemExit(f"Nie ma bazy: {args.db}")

    conn = connect(args.db)
    existing = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    user_cols = {r["name"] for r in conn.execute("PRAGMA table_info(users)")}

    todo = [t for t in TABLES if t in existing]
    drop_column = "strava_athlete_id" in user_cols
    if not todo and not drop_column:
        print("Baza jest juz czysta - nie ma czego usuwac.")
        return

    print(f"Baza: {args.db}")
    for table in todo:
        count = conn.execute(f"SELECT COUNT(*) c FROM {table}").fetchone()["c"]
        print(f"  tabela {table:<14} -> {count} wierszy do usuniecia")
    if drop_column:
        print("  kolumna users.strava_athlete_id -> do usuniecia")

    if not args.yes:
        print("\nTo jest nieodwracalne. Kopia zapasowa:")
        print(f'  sqlite3 {args.db} ".backup {args.db}.bak"')
        if input("\nUsunac? [t/N]: ").strip().lower() not in ("t", "tak", "y", "yes"):
            print("Anulowano - nic nie zmieniono.")
            return

    for table in todo:
        conn.execute(f"DROP TABLE {table}")
    if drop_column:
        conn.execute("ALTER TABLE users DROP COLUMN strava_athlete_id")
    conn.commit()
    conn.execute("VACUUM")
    conn.close()
    print("Gotowe. Baza nie zawiera juz danych ze Stravy.")


if __name__ == "__main__":
    main()
