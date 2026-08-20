"""Statystyki liczone na pomiarach - bez zewnetrznych bibliotek."""
from __future__ import annotations

from datetime import datetime
from typing import Iterable, Sequence


def time_weighted_mean(points: Sequence[tuple[datetime, float]]) -> tuple[float | None, float]:
    """Srednia wazona czasem (metoda trapezow). Zwraca (srednia, pokryte godziny).

    Zwykla srednia arytmetyczna klamie, gdy pomiary sa nierownomierne: piec wazen
    w poniedzialek i jedno w niedziele daja wynik opisujacy glownie poniedzialek.
    Tutaj kazdy pomiar wazy tyle, ile czasu "obowiazywal" - liczymy pole pod
    wykresem i dzielimy je przez dlugosc okresu.

    Dla jednego pomiaru zwracamy jego wartosc (pokrycie 0 h): nie ma z czego
    liczyc przedzialu, ale wartosc nadal jest sensowna.
    """
    ordered = sorted(points, key=lambda p: p[0])
    if not ordered:
        return None, 0.0
    if len(ordered) == 1:
        return float(ordered[0][1]), 0.0

    area = 0.0
    seconds = 0.0
    for (t0, v0), (t1, v1) in zip(ordered, ordered[1:]):
        step = (t1 - t0).total_seconds()
        if step <= 0:                      # pomiary z ta sama sekunda
            continue
        area += (v0 + v1) / 2.0 * step     # trapez miedzy sasiednimi pomiarami
        seconds += step

    if seconds <= 0:                       # wszystkie w tej samej chwili
        return sum(v for _, v in ordered) / len(ordered), 0.0
    return area / seconds, seconds / 3600.0


def weighted_averages(rows: Iterable[dict], fields: Sequence[str],
                      time_field: str = "measured_at") -> tuple[dict[str, float], float]:
    """Srednie wazone czasem dla wielu kolumn naraz.

    Kazda kolumna liczona jest na swoich niepustych pomiarach - brak impedancji
    zeruje sklad ciala, ale wagi z tego samego dnia nie wyrzuca.
    Zwraca (slownik srednich, najwieksze pokrycie w godzinach).
    """
    materialised = list(rows)
    result: dict[str, float] = {}
    coverage = 0.0
    for field in fields:
        points = [(datetime.fromisoformat(r[time_field]), float(r[field]))
                  for r in materialised if r.get(field) is not None]
        mean, hours = time_weighted_mean(points)
        if mean is not None:
            result[field] = round(mean, 2)
            coverage = max(coverage, hours)
    return result, round(coverage, 1)
