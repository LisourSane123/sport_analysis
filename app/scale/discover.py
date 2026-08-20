"""Pomocnik: `python3 -m app.scale.discover` wypisuje urzadzenia BLE w poblizu."""
import asyncio
import sys

from app.scale.ble import discover


async def main() -> None:
    duration = float(sys.argv[1]) if len(sys.argv) > 1 else 10.0
    print(f"Skanuje {duration:.0f} s... (wejdz na wage, zeby zaczela rozglaszac)")
    devices = await discover(duration)
    if not devices:
        print("Nic nie znaleziono. Sprawdz, czy Bluetooth jest wlaczony (bluetoothctl power on).")
        return
    print(f"\n{'MAC':<20}{'Nazwa':<24}Waga?")
    for mac, name, is_scale in devices:
        print(f"{mac:<20}{name[:23]:<24}{'  <-- TAK' if is_scale else ''}")
    print("\nWybrany MAC wpisz do .env jako SCALE_MAC.")


if __name__ == "__main__":
    asyncio.run(main())
