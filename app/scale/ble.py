"""Odczyt reklam BLE z wagi przy pomocy bleak (BlueZ)."""
from __future__ import annotations

import asyncio
import logging

from bleak import BleakScanner
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData

from app.scale.decoder import (BODY_COMPOSITION_UUID, ScaleMeasurement,
                               WEIGHT_SCALE_UUID, decode)

log = logging.getLogger(__name__)


def _extract(adv: AdvertisementData) -> bytes | None:
    for uuid in (BODY_COMPOSITION_UUID, WEIGHT_SCALE_UUID):
        raw = adv.service_data.get(uuid)
        if raw:
            return bytes(raw)
    return None


async def scan_once(mac: str, duration: float = 20.0,
                    require_impedance: bool = True) -> ScaleMeasurement | None:
    """Skanuje przez `duration` sekund i zwraca pierwszy ustabilizowany pomiar.

    Konczy wczesniej, gdy pomiar sie pojawi. None = nic nie znaleziono.
    """
    mac = mac.upper()
    found: asyncio.Future[ScaleMeasurement] = asyncio.get_running_loop().create_future()

    def on_detection(device: BLEDevice, adv: AdvertisementData) -> None:
        if found.done() or device.address.upper() != mac:
            return
        raw = _extract(adv)
        if not raw:
            return
        measurement = decode(raw)
        if measurement is None:
            return
        log.debug("Ramka: %s -> %.2f %s (stab=%s, imp=%s)", measurement.raw_hex,
                  measurement.weight, measurement.unit,
                  measurement.stabilized, measurement.impedance)
        if not measurement.stabilized:
            return
        if require_impedance and not measurement.is_complete:
            return
        found.set_result(measurement)

    scanner = BleakScanner(detection_callback=on_detection)
    await scanner.start()
    try:
        return await asyncio.wait_for(found, timeout=duration)
    except asyncio.TimeoutError:
        return None
    finally:
        await scanner.stop()


async def discover(duration: float = 10.0) -> list[tuple[str, str, bool]]:
    """Lista widocznych urzadzen: (MAC, nazwa, czy wyglada na wage)."""
    devices: dict[str, tuple[str, bool]] = {}

    def on_detection(device: BLEDevice, adv: AdvertisementData) -> None:
        looks_like_scale = _extract(adv) is not None or (
            (device.name or "").upper().startswith(("MIBFS", "MI_SCALE", "MI SCALE"))
        )
        name = device.name or adv.local_name or "?"
        prev = devices.get(device.address)
        devices[device.address] = (name, looks_like_scale or bool(prev and prev[1]))

    scanner = BleakScanner(detection_callback=on_detection)
    await scanner.start()
    try:
        await asyncio.sleep(duration)
    finally:
        await scanner.stop()
    return [(mac, name, is_scale) for mac, (name, is_scale) in sorted(devices.items())]
