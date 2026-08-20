#!/usr/bin/env python3
"""Naprawia pomiary zapisane z bledna data (rok 1970/2000 z nieustawionego zegara wagi).

    python3 tools/fix_timestamps.py            # pokazuje, co poprawi, i pyta
    python3 tools/fix_timestamps.py --yes      # bez pytania

Od wersji 0.5.0 to samo dzieje sie automatycznie przy starcie kazdej uslugi
(patrz app/db.py: repair_broken_timestamps), wiec ten skrypt przydaje sie
glownie wtedy, gdy chcesz zobaczyc liste zmian przed ich wprowadzeniem.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import DB_PATH                                    # noqa: E402
from app.db import BROKEN_TIME_BEFORE, connect, repair_broken_timestamps  # noqa: E402

def main() -> None:
    parser = argparse.ArgumentParser(description="Naprawa dat pomiarow")
    parser.add_argument("--yes", action="store_true", help="nie pytaj o potwierdzenie")
    parser.add_argument("--db", default=str(DB_PATH), help="sciezka do bazy")
    args = parser.parse_args()

    if not Path(args.db).exists():
        raise SystemExit(f"Nie ma bazy: {args.db}")

    conn = connect(args.db)
    rows = conn.execute(
        "SELECT id, measured_at, weight_kg FROM measurements WHERE measured_at < ? "
        "ORDER BY id", (BROKEN_TIME_BEFORE,)).fetchall()
    if not rows:
        print("Nie ma pomiarow z bledna data - nic do roboty.")
        return

    print(f"Baza: {args.db}")
    print(f"Pomiarow do poprawy: {len(rows)}")
    for r in rows[:10]:
        print(f"  #{r['id']:<5} {r['measured_at']:<22}{r['weight_kg']:>8.2f} kg")
    if len(rows) > 10:
        print(f"  ... i {len(rows) - 10} wiecej")

    if not args.yes:
        print(f'\nKopia zapasowa: sqlite3 {args.db} ".backup {args.db}.bak"')
        if input("\nPoprawic? [t/N]: ").strip().lower() not in ("t", "tak", "y", "yes"):
            print("Anulowano - nic nie zmieniono.")
            return

    fixed = repair_broken_timestamps(conn)
    print(f"\nPoprawiono {len(fixed)} pomiarow.")
    for mid, old, new in fixed[:10]:
        print(f"  #{mid}: {old} -> {new}")
    if len(rows) - len(fixed):
        print(f"Pominieto {len(rows) - len(fixed)} (kolizja daty albo zepsuty recorded_at).")
    conn.close()
    print("Przypisanie do profili sprawdzisz w panelu (zakladka Panel -> Pomiary).")


if __name__ == "__main__":
    main()
