"""Dekoder ramek BLE wagi Xiaomi Mi Body Composition Scale 2 (MIBFS).

Waga rozglasza service data pod UUID 0x181B (Body Composition Service).
Ramka ma 13 bajtow:

    [0]      ctrl0  - bity jednostki (kg / lbs / jin)
    [1]      ctrl1  - bit 1: impedancja gotowa, bit 5: pomiar ustabilizowany,
                      bit 7: uzytkownik zszedl z wagi
    [2:4]    rok    (uint16 LE)
    [4]      miesiac
    [5]      dzien
    [6]      godzina
    [7]      minuta
    [8]      sekunda
    [9:11]   impedancja (uint16 LE, om)
    [11:13]  waga (uint16 LE; /200 dla kg, /100 dla lbs i jin)
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

# Wagi bez sparowania z aplikacja producenta maja nieustawiony zegar i podaja
# rok 1970 albo 2000. Takiej daty nie wolno zapisac jako czas pomiaru.
MIN_PLAUSIBLE_YEAR = 2015

BODY_COMPOSITION_UUID = "0000181b-0000-1000-8000-00805f9b34fb"
WEIGHT_SCALE_UUID = "0000181d-0000-1000-8000-00805f9b34fb"  # Mi Scale 1 (tylko waga)
FRAME_LENGTH = 13


@dataclass
class ScaleMeasurement:
    weight: float          # w jednostce `unit`
    weight_kg: float       # zawsze w kg
    unit: str              # kg | lbs | jin
    impedance: int | None
    stabilized: bool
    has_impedance: bool
    weight_removed: bool
    measured_at: datetime | None      # czas z wagi, ale tylko gdy wiarygodny
    scale_clock: datetime | None      # to, co waga naprawde podala (do diagnozy)
    raw_hex: str

    @property
    def is_complete(self) -> bool:
        """Pomiar nadaje sie do zapisu z pelna kompozycja ciala."""
        return self.stabilized and self.has_impedance and bool(self.impedance)

    @property
    def clock_ok(self) -> bool:
        """Czy waga ma ustawiony zegar."""
        return self.measured_at is not None


def _unit_of(ctrl0: int) -> tuple[str, float]:
    if ctrl0 & 0x01:
        return "jin", 100.0
    if ctrl0 & 0x10:
        return "lbs", 100.0
    return "kg", 200.0


def _to_kg(value: float, unit: str) -> float:
    if unit == "lbs":
        return value * 0.45359237
    if unit == "jin":
        return value * 0.5
    return value


def decode(data: bytes) -> ScaleMeasurement | None:
    """Dekoduje surowe service data (bez 16-bitowego UUID). None gdy ramka zla."""
    if data is None or len(data) < FRAME_LENGTH:
        return None

    ctrl0, ctrl1 = data[0], data[1]
    unit, divisor = _unit_of(ctrl0)

    stabilized = bool(ctrl1 & (1 << 5))
    has_impedance = bool(ctrl1 & (1 << 1))
    weight_removed = bool(ctrl1 & (1 << 7))

    impedance = int.from_bytes(data[9:11], "little")
    raw_weight = int.from_bytes(data[11:13], "little")
    weight = raw_weight / divisor

    try:
        scale_clock = datetime(
            int.from_bytes(data[2:4], "little"),
            data[4], data[5], data[6], data[7], data[8],
        )
    except ValueError:                     # np. miesiac 0 albo 31 lutego
        scale_clock = None

    # Rok 1970/2000 oznacza zegar, ktorego nikt nie ustawil - wtedy czas
    # pomiaru musi wziac na siebie wolajacy (patrz app/scale/runner.py).
    measured_at = (scale_clock if scale_clock is not None
                   and scale_clock.year >= MIN_PLAUSIBLE_YEAR else None)

    return ScaleMeasurement(
        weight=round(weight, 2),
        weight_kg=round(_to_kg(weight, unit), 2),
        unit=unit,
        impedance=impedance if has_impedance and impedance not in (0, 0xFFFF) else None,
        stabilized=stabilized,
        has_impedance=has_impedance,
        weight_removed=weight_removed,
        measured_at=measured_at,
        scale_clock=scale_clock,
        raw_hex=data.hex(),
    )
