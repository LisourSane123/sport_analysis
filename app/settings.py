"""Ustawienia zmienialne w locie, z panelu w przegladarce.

Kolejnosc zrodel: tabela `settings` w bazie -> `.env` -> wartosc domyslna w kodzie.
Dzieki temu zmiana z panelu dziala od razu, bez restartu uslugi: petla skanowania
czyta ustawienia na poczatku kazdego cyklu.

`.env` nadal ma sens dla rzeczy, ktore musza byc znane przed startem (MAC wagi,
sciezka bazy, port panelu) - tych tutaj nie ma.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app import config


@dataclass(frozen=True)
class Setting:
    key: str
    label: str
    group: str
    kind: str                       # int | float
    default: Any
    minimum: float
    maximum: float
    unit: str = ""
    help: str = ""

    def parse(self, raw: Any) -> Any:
        value = int(raw) if self.kind == "int" else float(raw)
        if not (self.minimum <= value <= self.maximum):
            raise ValueError(
                f"{self.label}: wartosc {value}{self.unit} poza zakresem "
                f"{self.minimum}-{self.maximum}{self.unit}")
        return value


SETTINGS: tuple[Setting, ...] = (
    Setting("scan_duration", "Dlugosc skanu", "Skanowanie wagi", "int",
            config.SCAN_DURATION, 5, 120, " s",
            "Jak dlugo trwa jeden cykl nasluchiwania. Skan konczy sie wczesniej, "
            "gdy zlapie kompletny pomiar."),
    Setting("scan_interval", "Przerwa miedzy skanami", "Skanowanie wagi", "int",
            config.SCAN_INTERVAL, 0, 300, " s",
            "Krotsza przerwa = mniejsza szansa przegapienia wazenia, kosztem "
            "zuzycia radia Bluetooth."),
    Setting("scale_dedupe_minutes", "Okno odrzucania powtorek", "Skanowanie wagi", "int",
            30, 0, 720, " min",
            "Waga powtarza ostatni wynik jeszcze dlugo po zejsciu z niej. Pomiar o tej "
            "samej wadze i impedancji, zlapany w tym oknie, jest uznawany za te sama "
            "powtorke i nie trafia do bazy. 0 wylacza mechanizm."),
    Setting("ident_window_days", "Okno historii", "Rozpoznawanie osoby", "int",
            config.IDENT_WINDOW_DAYS, 1, 90, " dni",
            "Z ilu dni wstecz liczony jest przedzial predykcyjny profilu."),
    Setting("ident_confidence", "Poziom ufnosci", "Rozpoznawanie osoby", "float",
            config.IDENT_CONFIDENCE, 0.5, 0.999, "",
            "Nizsza wartosc = wezsze przedzialy, rzadsze nakladanie sie profili "
            "o podobnej wadze."),
    Setting("ident_sd_prior", "Zakladane wahania dobowe", "Rozpoznawanie osoby", "float",
            config.IDENT_SD_PRIOR, 0.1, 5.0, " kg",
            "Uzywane, dopoki profil nie ma wlasnej historii."),
    Setting("ident_nu_prior", "Sila zalozenia", "Rozpoznawanie osoby", "float",
            config.IDENT_NU_PRIOR, 0.0, 20.0, "",
            "Ile pseudo-obserwacji warte jest powyzsze zalozenie."),
    Setting("ident_sd_floor", "Minimalne odchylenie", "Rozpoznawanie osoby", "float",
            config.IDENT_SD_FLOOR, 0.05, 2.0, " kg",
            "Zabezpiecza przed absurdalnie waskim przedzialem przy serii "
            "identycznych wazen."),
    Setting("ident_max_half_kg", "Maksymalna polszerokosc", "Rozpoznawanie osoby", "float",
            config.IDENT_MAX_HALF_KG, 1.0, 20.0, " kg",
            "Gorny limit szerokosci przedzialu."),
    Setting("ident_fallback_max_kg", "Limit dopasowania awaryjnego", "Rozpoznawanie osoby",
            "float", config.IDENT_FALLBACK_MAX_KG, 1.0, 30.0, " kg",
            "Dalej niz tyle od ostatniej wagi profilu - pomiar zostaje nieprzypisany."),
    Setting("garmin_sync_interval", "Czestotliwosc syncu", "Garmin Connect", "int",
            config.GARMIN_SYNC_INTERVAL, 300, 86400, " s",
            "Co ile usluga waga-garmin pyta o nowe dane."),
    Setting("garmin_backfill_days", "Zakres pierwszego importu", "Garmin Connect", "int",
            config.GARMIN_BACKFILL_DAYS, 1, 365, " dni", ""),
    Setting("garmin_request_pause", "Przerwa miedzy zapytaniami", "Garmin Connect", "float",
            config.GARMIN_REQUEST_PAUSE, 0.0, 10.0, " s",
            "Za krotka przerwa konczy sie odpowiedzia 429 od Garmina."),
)

BY_KEY = {s.key: s for s in SETTINGS}


def all_values(conn) -> dict[str, Any]:
    """Komplet ustawien: baza tam, gdzie cos zapisano, inaczej wartosc z .env."""
    stored = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM settings")}
    values = {}
    for setting in SETTINGS:
        raw = stored.get(setting.key)
        if raw is None:
            values[setting.key] = setting.default
            continue
        try:
            values[setting.key] = setting.parse(raw)
        except ValueError:                      # zepsuty wpis nie moze polozyc uslugi
            values[setting.key] = setting.default
    return values


def get(conn, key: str) -> Any:
    setting = BY_KEY[key]
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    if row is None:
        return setting.default
    try:
        return setting.parse(row["value"])
    except ValueError:
        return setting.default


def save(conn, changes: dict[str, Any]) -> dict[str, Any]:
    """Zapisuje zmiany po walidacji. Nieznany klucz albo zla wartosc -> ValueError."""
    parsed = {}
    for key, raw in changes.items():
        setting = BY_KEY.get(key)
        if setting is None:
            raise ValueError(f"Nieznane ustawienie: {key}")
        parsed[key] = setting.parse(raw)
    for key, value in parsed.items():
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
            "updated_at=datetime('now')", (key, str(value)))
    conn.commit()
    return parsed


def reset(conn, key: str | None = None) -> None:
    """Kasuje wpis(y) z bazy - wracaja wartosci z .env."""
    if key is None:
        conn.execute("DELETE FROM settings")
    else:
        conn.execute("DELETE FROM settings WHERE key = ?", (key,))
    conn.commit()


def describe() -> list[dict[str, Any]]:
    """Opis ustawien dla panelu (etykiety, zakresy, grupy)."""
    return [{"key": s.key, "label": s.label, "group": s.group, "kind": s.kind,
             "default": s.default, "min": s.minimum, "max": s.maximum,
             "unit": s.unit, "help": s.help} for s in SETTINGS]
