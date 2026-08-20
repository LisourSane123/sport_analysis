"""Petla glowna: skanuje wage, rozkodowuje pomiar i zapisuje do SQLite.

Uruchomienie: python3 -m app.scale.runner
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from app import config, settings
from app.db import connect, init_db, insert_measurement, last_measurement
from app.scale.ble import scan_once
from app.scale.body_metrics import composition_for
from app.scale.decoder import ScaleMeasurement
from app.scale.identify import identify

log = logging.getLogger("scale")

# Zegar wagi bywa nieustawiony (rok 1970) albo rozjechany. Ponizej tej granicy
# ufamy wadze, powyzej bierzemy czas z Raspberry Pi.
MAX_CLOCK_DRIFT_S = 24 * 3600
# Gdy czas bierzemy z Pi, kazda ramka dostaje inny znacznik i klucz
# (user_id, measured_at) przestaje wylapywac powtorki - stad osobna blokada.
REPEAT_WINDOW_S = 180

_clock_warned = False


def resolve_time(m: ScaleMeasurement, now: datetime | None = None) -> tuple[datetime, str | None]:
    """Czas pomiaru: z wagi, jesli da sie mu ufac, inaczej z Raspberry Pi.

    Zwraca (czas, powod odrzucenia zegara wagi albo None).
    """
    now = now or datetime.now()
    if m.measured_at is None:
        podano = m.scale_clock.isoformat(sep=" ") if m.scale_clock else "brak daty"
        return now, f"zegar wagi nieustawiony (waga podala {podano})"
    drift = abs((m.measured_at - now).total_seconds())
    if drift > MAX_CLOCK_DRIFT_S:
        return now, f"zegar wagi rozjechany o {drift / 3600:.1f} h"
    return m.measured_at, None


def _looks_like_repeat(conn, weight_kg: float, impedance, when: datetime,
                       window_s: int = REPEAT_WINDOW_S) -> bool:
    """Czy to ta sama ramka zlapana w kolejnym cyklu skanowania."""
    since = (when - timedelta(seconds=window_s)).isoformat(timespec="seconds")
    row = conn.execute(
        """SELECT 1 FROM measurements
           WHERE measured_at >= ? AND ABS(weight_kg - ?) < 0.05
             AND impedance IS ?
           LIMIT 1""",
        (since, weight_kg, impedance)).fetchone()
    return row is not None


def store_measurement(conn, m: ScaleMeasurement) -> int | None:
    """Rozpoznaje uzytkownika, liczy kompozycje ciala i zapisuje pomiar."""
    global _clock_warned
    when, clock_problem = resolve_time(m)
    if clock_problem:
        if not _clock_warned:
            log.warning("%s - czas pomiaru biore z zegara Raspberry Pi. "
                        "Zegar wagi ustawia aplikacja producenta przy synchronizacji; "
                        "bez niej to normalne i niegrozne.", clock_problem)
            _clock_warned = True
        if _looks_like_repeat(conn, m.weight_kg, m.impedance, when):
            log.info("Ten sam pomiar co przed chwila (%.2f kg) - pomijam", m.weight_kg)
            return None
    cfg = settings.all_values(conn)          # panel moze je zmienic w locie
    result = identify(
        conn, m.weight_kg, when,
        window_days=cfg["ident_window_days"], confidence=cfg["ident_confidence"],
        sd_prior=cfg["ident_sd_prior"], nu_prior=cfg["ident_nu_prior"],
        sd_floor=cfg["ident_sd_floor"], max_half_kg=cfg["ident_max_half_kg"],
        fallback_max_kg=cfg["ident_fallback_max_kg"],
    )
    for cand in result.candidates:
        lo, hi = cand.bounds
        log.debug("  %-12s n=%d przedzial %.2f-%.2f kg, score %.2f",
                  cand.username, cand.n, lo, hi, cand.score)

    user = None
    if result.user_id is not None:
        user = conn.execute("SELECT * FROM users WHERE id = ?", (result.user_id,)).fetchone()
        log.info("Rozpoznano: %s (%s, %s)", user["display_name"], result.method, result.detail)
    else:
        log.warning("Nie rozpoznano profilu dla %.2f kg (%s) - zapis bez uzytkownika",
                    m.weight_kg, result.detail)

    measured_at = when.isoformat(timespec="seconds")
    row = {
        "user_id": result.user_id,
        "measured_at": measured_at,
        "weight_kg": m.weight_kg,
        "unit": m.unit,
        "impedance": m.impedance,
        "identify_method": result.method,
        "identify_score": result.score,
        "raw_hex": m.raw_hex,
    }

    composition = composition_for(user, m.weight_kg, m.impedance, when)
    row.update({k: v for k, v in composition.items() if v is not None})
    if user is not None and m.impedance and composition["bmi"] is None:
        log.warning("Nie udalo sie policzyc kompozycji ciala dla %.2f kg", m.weight_kg)

    new_id = insert_measurement(conn, row)
    who = user["display_name"] if user else "nieprzypisany"
    if new_id is None:
        log.info("Pomiar %s (%s) juz w bazie - pomijam", measured_at, who)
    else:
        log.info("Zapisano #%d: %.2f kg, %s ohm, %s (%s)", new_id, m.weight_kg,
                 m.impedance, who, measured_at)
    return new_id


async def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    if not config.SCALE_MAC or config.SCALE_MAC == "00:00:00:00:00:00":
        raise SystemExit("Ustaw SCALE_MAC w .env (pomoze: python3 -m app.scale.discover)")

    init_db()
    conn = connect()
    last = last_measurement(conn)
    cfg = settings.all_values(conn)
    log.info("Start. Waga %s, cykl %ss/%ss, baza %s (ostatni pomiar: %s)",
             config.SCALE_MAC, cfg["scan_duration"], cfg["scan_interval"],
             config.DB_PATH, last["measured_at"] if last else "brak")
    log.info("Rozpoznawanie: okno %d dni, ufnosc %.0f%%, fallback do %.1f kg",
             cfg["ident_window_days"], cfg["ident_confidence"] * 100,
             cfg["ident_fallback_max_kg"])

    try:
        while True:
            cfg = settings.all_values(conn)    # odczyt na kazdym obiegu:
            try:                                   # zmiana z panelu dziala od razu
                measurement = await scan_once(config.SCALE_MAC, cfg["scan_duration"])
                if measurement is not None:
                    store_measurement(conn, measurement)
            except Exception:                      # BLE potrafi chwilowo padac
                log.exception("Blad cyklu skanowania - probuje dalej")
            await asyncio.sleep(cfg["scan_interval"])
    except (KeyboardInterrupt, asyncio.CancelledError):
        log.info("Zatrzymano.")
    finally:
        conn.close()


if __name__ == "__main__":
    asyncio.run(main())
