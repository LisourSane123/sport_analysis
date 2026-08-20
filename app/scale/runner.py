"""Petla glowna: skanuje wage, rozkodowuje pomiar i zapisuje do SQLite.

Uruchomienie: python3 -m app.scale.runner
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from app import config
from app.db import (age_of, connect, init_db, insert_measurement,
                    last_measurement)
from app.scale.ble import scan_once
from app.scale.body_metrics import BodyMetrics
from app.scale.decoder import ScaleMeasurement
from app.scale.identify import identify

log = logging.getLogger("scale")


def store_measurement(conn, m: ScaleMeasurement) -> int | None:
    """Rozpoznaje uzytkownika, liczy kompozycje ciala i zapisuje pomiar."""
    when = m.measured_at or datetime.now()
    result = identify(
        conn, m.weight_kg, when,
        window_days=config.IDENT_WINDOW_DAYS, confidence=config.IDENT_CONFIDENCE,
        sd_prior=config.IDENT_SD_PRIOR, nu_prior=config.IDENT_NU_PRIOR,
        sd_floor=config.IDENT_SD_FLOOR, max_half_kg=config.IDENT_MAX_HALF_KG,
        fallback_max_kg=config.IDENT_FALLBACK_MAX_KG,
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

    if user is not None and m.impedance:
        try:
            metrics = BodyMetrics(
                weight=m.weight_kg,
                height=user["height_cm"],
                age=age_of(user, when.date()),
                sex=user["sex"],
                impedance=m.impedance,
            ).compute()
            row.update(metrics.as_dict())
        except ValueError as exc:
            log.warning("Pomijam kompozycje ciala: %s", exc)

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
    log.info("Start. Waga %s, cykl %ss/%ss, baza %s (ostatni pomiar: %s)",
             config.SCALE_MAC, config.SCAN_DURATION, config.SCAN_INTERVAL,
             config.DB_PATH, last["measured_at"] if last else "brak")
    log.info("Rozpoznawanie: okno %d dni, ufnosc %.0f%%, fallback do %.1f kg",
             config.IDENT_WINDOW_DAYS, config.IDENT_CONFIDENCE * 100,
             config.IDENT_FALLBACK_MAX_KG)

    try:
        while True:
            try:
                measurement = await scan_once(config.SCALE_MAC, config.SCAN_DURATION)
                if measurement is not None:
                    store_measurement(conn, measurement)
            except Exception:                      # BLE potrafi chwilowo padac
                log.exception("Blad cyklu skanowania - probuje dalej")
            await asyncio.sleep(config.SCAN_INTERVAL)
    except (KeyboardInterrupt, asyncio.CancelledError):
        log.info("Zatrzymano.")
    finally:
        conn.close()


if __name__ == "__main__":
    asyncio.run(main())
