"""Przepisanie JSON-a z Garmina na wiersze tabel garmin_activities / garmin_daily.

Garmin nie publikuje kontraktu tego API - nazwy pol potrafia sie roznic miedzy
zegarkami i zmieniac miedzy wersjami serwisu. Dlatego kazda kolumna ma LISTE
kandydatow (bierzemy pierwsza wartosc, ktora istnieje), a caly oryginalny JSON
laduje w kolumnie raw_json. Jesli Garmin przemianuje pole, dane nie przepadaja
- wystarczy dopisac nazwe ponizej i puscic sync ponownie.
"""
from __future__ import annotations

import json
from typing import Any, Iterable


def pick(source: Any, *keys: str, default=None):
    """Pierwsza niepusta wartosc z podanych kluczy (obsluguje sciezki 'a.b.c')."""
    if not isinstance(source, dict):
        return default
    for key in keys:
        value: Any = source
        for part in key.split("."):
            if not isinstance(value, dict):
                value = None
                break
            value = value.get(part)
        if value is not None:
            return value
    return default


def _num(value) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _first(items: Any) -> dict:
    """Czesc endpointow zwraca liste jednoelementowa zamiast slownika."""
    if isinstance(items, list):
        return items[0] if items and isinstance(items[0], dict) else {}
    return items if isinstance(items, dict) else {}


# --------------------------------------------------------------------------
# Aktywnosci
# --------------------------------------------------------------------------
def activity_row(act: dict[str, Any], profile_id: str | None = None,
                 user_id: int | None = None) -> dict[str, Any]:
    device = pick(act, "deviceId", "manufacturer")
    return {
        "id": int(pick(act, "activityId")),
        "profile_id": profile_id,
        "user_id": user_id,
        "name": pick(act, "activityName"),
        "sport_type": pick(act, "activityType.typeKey", "activityType", "sportTypeKey"),
        "start_time_local": pick(act, "startTimeLocal"),
        "start_time_gmt": pick(act, "startTimeGMT"),
        "distance_m": _num(pick(act, "distance")),
        "moving_time_s": _num(pick(act, "movingDuration", "duration")),
        "elapsed_time_s": _num(pick(act, "elapsedDuration", "duration")),
        "total_elevation_gain": _num(pick(act, "elevationGain")),
        "average_speed": _num(pick(act, "averageSpeed")),
        "max_speed": _num(pick(act, "maxSpeed")),
        "average_heartrate": _num(pick(act, "averageHR")),
        "max_heartrate": _num(pick(act, "maxHR")),
        "average_cadence": _num(pick(
            act, "averageRunningCadenceInStepsPerMinute",
            "averageBikingCadenceInRevPerMinute", "averageSwimCadenceInStrokesPerMinute")),
        "average_power": _num(pick(act, "avgPower", "averagePower")),
        "calories": _num(pick(act, "calories")),
        "aerobic_te": _num(pick(act, "aerobicTrainingEffect")),
        "anaerobic_te": _num(pick(act, "anaerobicTrainingEffect")),
        "vo2max": _num(pick(act, "vO2MaxValue", "vo2MaxValue")),
        "avg_stride_length": _num(pick(act, "avgStrideLength")),
        "avg_ground_contact_ms": _num(pick(act, "avgGroundContactTime")),
        "avg_vertical_oscillation": _num(pick(act, "avgVerticalOscillation")),
        "device": str(device) if device is not None else None,
        "raw_json": json.dumps(act, ensure_ascii=False),
    }


# --------------------------------------------------------------------------
# Dzien
# --------------------------------------------------------------------------
def daily_row(day: str, profile_id: str, user_id: int | None = None, *,
              stats: dict | None = None, sleep: dict | None = None,
              hrv: dict | None = None, readiness: Any = None,
              max_metrics: Any = None) -> dict[str, Any]:
    """Sklada jeden wiersz dnia z kilku odpowiedzi API (kazda moze byc pusta)."""
    stats = stats or {}
    sleep_dto = pick(sleep or {}, "dailySleepDTO", default={}) or {}
    hrv_sum = pick(hrv or {}, "hrvSummary", default={}) or {}
    ready = _first(readiness)
    metrics = pick(_first(max_metrics), "generic", default={}) or {}

    return {
        "profile_id": profile_id,
        "user_id": user_id,
        "day": day,
        "steps": _num(pick(stats, "totalSteps")),
        "distance_m": _num(pick(stats, "totalDistanceMeters")),
        "floors_climbed": _num(pick(stats, "floorsAscended")),
        "calories_total": _num(pick(stats, "totalKilocalories")),
        "calories_active": _num(pick(stats, "activeKilocalories")),
        "calories_bmr": _num(pick(stats, "bmrKilocalories")),
        "resting_hr": _num(pick(stats, "restingHeartRate")),
        "min_hr": _num(pick(stats, "minHeartRate")),
        "max_hr": _num(pick(stats, "maxHeartRate")),
        "avg_stress": _num(pick(stats, "averageStressLevel")),
        "max_stress": _num(pick(stats, "maxStressLevel")),
        "body_battery_high": _num(pick(stats, "bodyBatteryHighestValue")),
        "body_battery_low": _num(pick(stats, "bodyBatteryLowestValue")),
        "intensity_min_moderate": _num(pick(stats, "moderateIntensityMinutes")),
        "intensity_min_vigorous": _num(pick(stats, "vigorousIntensityMinutes")),
        "sleep_seconds": _num(pick(sleep_dto, "sleepTimeSeconds")
                              or pick(stats, "sleepingSeconds")),
        "deep_sleep_seconds": _num(pick(sleep_dto, "deepSleepSeconds")),
        "light_sleep_seconds": _num(pick(sleep_dto, "lightSleepSeconds")),
        "rem_sleep_seconds": _num(pick(sleep_dto, "remSleepSeconds")),
        "awake_seconds": _num(pick(sleep_dto, "awakeSleepSeconds")),
        "sleep_score": _num(pick(sleep_dto, "sleepScores.overall.value")),
        "hrv_last_night": _num(pick(hrv_sum, "lastNightAvg")),
        "hrv_status": pick(hrv_sum, "status"),
        "training_readiness": _num(pick(ready, "score")),
        "vo2max": _num(pick(metrics, "vo2MaxPreciseValue", "vo2MaxValue")),
        "respiration_avg": _num(pick(sleep_dto, "averageRespirationValue")
                                or pick(stats, "avgWakingRespirationValue")),
        "spo2_avg": _num(pick(sleep_dto, "averageSpO2Value")
                         or pick(stats, "averageSpo2")),
        "raw_json": json.dumps(
            {k: v for k, v in (("stats", stats), ("sleep", sleep), ("hrv", hrv),
                               ("readiness", readiness), ("max_metrics", max_metrics))
             if v},
            ensure_ascii=False),
    }


def has_data(row: dict[str, Any], ignore: Iterable[str] =
             ("profile_id", "user_id", "day", "raw_json")) -> bool:
    """Czy w dniu jest cokolwiek poza kluczem - pusty dzien nie trafia do bazy."""
    skip = set(ignore)
    return any(v is not None for k, v in row.items() if k not in skip)
