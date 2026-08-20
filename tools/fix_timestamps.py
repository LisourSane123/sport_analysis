#!/usr/bin/env python3
"""Naprawia pomiary zapisane z bledna data (rok 1970/2000 z nieustawionego zegara wagi).

    python3 tools/fix_timestamps.py            # pokazuje, co poprawi, i pyta
    python3 tools/fix_timestamps.py --yes      # bez pytania

Za czas pomiaru bierzemy `recorded_at`, czyli moment zapisu przez Raspberry Pi -
zapisujemy pomiar sekundy po jego zlapaniu, wiec roznica jest pomijalna.
`recorded_at` jest w UTC (SQLite `datetime('now')`), a `measured_at` w czasie
lokalnym, wiec po drodze przeliczamy strefe.

Kolumna `raw_hex` zostaje nietknieta - oryginalna ramka z wagi, razem z jej
bledna data, jest dalej w bazie.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import DB_PATH          # noqa: E402
from app.db import connect              # noqa: E402

BROKEN_BEFORE = "2015-01-01"


def utc_to_local(value: str) -> str:
    """'2026-08-20 12:34:56' (UTC) -> '2026-08-20T14:34:56' (czas lokalny)."""
    naive = datetime.fromisoformat(value.replace("T", " "))
    local = naive.replace(tzinfo=timezone.utc).astimezone()
    return local.replace(tzinfo=None).isoformat(timespec="seconds")


def main() -> None:
    parser = argparse.ArgumentParser(description="Naprawa dat pomiarow")
    parser.add_argument("--yes", action="store_true", help="nie pytaj o potwierdzenie")
    parser.add_argument("--db", default=str(DB_PATH), help="sciezka do bazy")
    args = parser.parse_args()

    if not Path(args.db).exists():
        raise SystemExit(f"Nie ma bazy: {args.db}")

    conn = connect(args.db)
    rows = conn.execute(
        "SELECT id, measured_at, recorded_at, weight_kg, user_id FROM measurements "
        "WHERE measured_at < ? ORDER BY id", (BROKEN_BEFORE,)).fetchall()
    if not rows:
        print("Nie ma pomiarow z bledna data - nic do roboty.")
        return

    print(f"Baza: {args.db}")
    print(f"Pomiarow do poprawy: {len(rows)}\n")
    print(f"{'id':<6}{'bylo':<22}{'bedzie':<22}{'waga':>8}")
    plan = []
    for r in rows:
        nowy = utc_to_local(r["recorded_at"])
        plan.append((r["id"], nowy))
        print(f"{r['id']:<6}{r['measured_at']:<22}{nowy:<22}{r['weight_kg']:>8.2f}")

    if not args.yes:
        print(f'\nKopia zapasowa: sqlite3 {args.db} ".backup {args.db}.bak"')
        if input("\nPoprawic? [t/N]: ").strip().lower() not in ("t", "tak", "y", "yes"):
            print("Anulowano - nic nie zmieniono.")
            return

    poprawione = pominiete = 0
    for mid, nowy in plan:
        try:
            conn.execute("UPDATE measurements SET measured_at = ? WHERE id = ?", (nowy, mid))
            poprawione += 1
        except Exception as exc:            # kolizja z UNIQUE(user_id, measured_at)
            print(f"  #{mid}: pomijam ({exc})")
            pominiete += 1
    conn.commit()
    conn.close()
    print(f"\nPoprawiono {poprawione} pomiarow" + (f", pominieto {pominiete}" if pominiete else "") + ".")
    print("Przypisanie do profili sprawdzisz w panelu (zakladka Panel -> Pomiary).")


if __name__ == "__main__":
    main()
