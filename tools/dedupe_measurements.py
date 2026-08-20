#!/usr/bin/env python3
"""Usuwa powtorzone pomiary - te same wazenia zapisane wielokrotnie.

    python3 tools/dedupe_measurements.py              # pokazuje grupy i pyta
    python3 tools/dedupe_measurements.py --yes        # bez pytania
    python3 tools/dedupe_measurements.py --window 120 # szersze okno [min]

Waga rozglasza ostatni wynik jeszcze dlugo po zejsciu z niej. Zanim doszla
blokada powtorek (wersja 0.5.1), kazdy cykl skanowania zapisywal taka powtorke
jako osobny pomiar. Skrypt zostawia z kazdej serii najstarszy wpis - ten
z momentu prawdziwego wazenia.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import DB_PATH                                  # noqa: E402
from app.db import connect, delete_duplicates, find_duplicate_groups  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Usuwanie powtorzonych pomiarow")
    parser.add_argument("--yes", action="store_true", help="nie pytaj o potwierdzenie")
    parser.add_argument("--window", type=int, default=60,
                        help="okno w minutach, w ktorym powtorki uznajemy za te sama seria")
    parser.add_argument("--db", default=str(DB_PATH), help="sciezka do bazy")
    args = parser.parse_args()

    if not Path(args.db).exists():
        raise SystemExit(f"Nie ma bazy: {args.db}")

    conn = connect(args.db)
    total = conn.execute("SELECT COUNT(*) c FROM measurements").fetchone()["c"]
    groups = find_duplicate_groups(conn, args.window)
    to_remove = sum(len(g["remove"]) for g in groups)

    if not groups:
        print(f"Baza: {args.db}\nPomiarow: {total}. Nie znalazlem powtorek.")
        return

    print(f"Baza: {args.db}")
    print(f"Pomiarow w bazie: {total}, do usuniecia: {to_remove} "
          f"(zostanie {total - to_remove})\n")
    print(f"{'zostaje':<9}{'waga':>8}{'imp.':>7}  {'od':<20}{'do':<20}{'sztuk':>6}")
    for g in groups[:20]:
        print(f"#{g['keep']:<8}{g['weight_kg']:>8.2f}{str(g['impedance'] or '-'):>7}  "
              f"{g['first']:<20}{g['last']:<20}{g['count']:>6}")
    if len(groups) > 20:
        print(f"... i {len(groups) - 20} kolejnych grup")

    if not args.yes:
        print(f'\nKopia zapasowa: sqlite3 {args.db} ".backup {args.db}.bak"')
        if input("\nUsunac powtorki? [t/N]: ").strip().lower() not in ("t", "tak", "y", "yes"):
            print("Anulowano - nic nie zmieniono.")
            return

    count_groups, removed = delete_duplicates(conn, args.window)
    conn.close()
    print(f"\nUsunieto {removed} powtorek z {count_groups} serii.")


if __name__ == "__main__":
    main()
