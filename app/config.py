"""Konfiguracja czytana z .env (z sensownymi domyslnymi wartosciami)."""
import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "").strip() or default)
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "").strip() or default)
    except ValueError:
        return default


# --- waga ---
SCALE_MAC = (os.getenv("SCALE_MAC") or "").strip().upper()
SCAN_DURATION = _int("SCAN_DURATION", 20)
SCAN_INTERVAL = _int("SCAN_INTERVAL", 10)

# --- rozpoznawanie uzytkownika (przedzial predykcyjny) ---
IDENT_WINDOW_DAYS = _int("IDENT_WINDOW_DAYS", 7)
IDENT_CONFIDENCE = _float("IDENT_CONFIDENCE", 0.95)
IDENT_SD_PRIOR = _float("IDENT_SD_PRIOR", 0.8)
IDENT_NU_PRIOR = _float("IDENT_NU_PRIOR", 2.0)
IDENT_SD_FLOOR = _float("IDENT_SD_FLOOR", 0.2)
IDENT_MAX_HALF_KG = _float("IDENT_MAX_HALF_KG", 6.0)
IDENT_FALLBACK_MAX_KG = _float("IDENT_FALLBACK_MAX_KG", 8.0)

# --- baza ---
_db = os.getenv("DB_PATH", "data/waga.db")
DB_PATH = Path(_db) if Path(_db).is_absolute() else PROJECT_ROOT / _db

# --- web ---
WEB_HOST = os.getenv("WEB_HOST", "0.0.0.0")
WEB_PORT = _int("WEB_PORT", 11230)

# --- garmin (nieoficjalne API Garmin Connect przez biblioteke garminconnect) ---
GARMIN_EMAIL = (os.getenv("GARMIN_EMAIL") or "").strip()
GARMIN_PASSWORD = os.getenv("GARMIN_PASSWORD") or ""
_tok = os.getenv("GARMIN_TOKENSTORE", "data/garmin_tokens")
GARMIN_TOKENSTORE = Path(_tok) if Path(_tok).is_absolute() else PROJECT_ROOT / _tok
GARMIN_SYNC_INTERVAL = _int("GARMIN_SYNC_INTERVAL", 3600)
GARMIN_BACKFILL_DAYS = _int("GARMIN_BACKFILL_DAYS", 30)
GARMIN_REQUEST_PAUSE = _float("GARMIN_REQUEST_PAUSE", 1.0)
