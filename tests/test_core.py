"""Testy bez sprzetu: dekoder ramek, kompozycja ciala, zapis do bazy.

Uruchomienie: .venv/bin/python -m unittest discover -s tests
"""
import os
import tempfile
import unittest
from datetime import datetime, timedelta

from app.db import (add_user, connect, init_db, insert_measurement,
                    latest_activity_epoch, upsert_activity)
from app.scale.body_metrics import BodyMetrics
from app.scale.decoder import decode
from app.scale.identify import build_candidate, identify, t_cdf, t_ppf
from app import config
from app.db import (latest_garmin_activity_day, upsert_garmin_activity,
                    upsert_garmin_daily)
from app.garmin import sync as garmin_sync
from app.garmin.mapping import activity_row, daily_row, has_data


def frame(ctrl0=0x02, ctrl1=0x22, impedance=512, raw_weight=17670):
    return (bytes([ctrl0, ctrl1]) + (2026).to_bytes(2, "little")
            + bytes([8, 19, 7, 31, 12])
            + impedance.to_bytes(2, "little") + raw_weight.to_bytes(2, "little"))


class TestDecoder(unittest.TestCase):
    def test_kg_stabilized(self):
        m = decode(frame())
        self.assertEqual(m.weight_kg, 88.35)
        self.assertEqual(m.unit, "kg")
        self.assertEqual(m.impedance, 512)
        self.assertTrue(m.is_complete)
        self.assertEqual(m.measured_at, datetime(2026, 8, 19, 7, 31, 12))

    def test_lbs_converted(self):
        m = decode(frame(ctrl0=0x12, ctrl1=0x20, impedance=0, raw_weight=19480))
        self.assertEqual(m.unit, "lbs")
        self.assertAlmostEqual(m.weight_kg, 88.36, places=1)
        self.assertFalse(m.is_complete)          # brak impedancji

    def test_unstable_frame(self):
        self.assertFalse(decode(frame(ctrl1=0x02)).stabilized)

    def test_short_frame(self):
        self.assertIsNone(decode(b"\x02\x22"))


class TestBodyMetrics(unittest.TestCase):
    def test_ranges(self):
        c = BodyMetrics(85, 182, 30, "male", 500).compute()
        self.assertTrue(5 < c.fat_percentage < 40)
        self.assertTrue(30 < c.muscle_mass < 90)
        self.assertAlmostEqual(c.bmi, 85 / 1.82 ** 2, places=1)
        self.assertTrue(1200 < c.bmr < 2600)

    def test_rejects_nonsense(self):
        with self.assertRaises(ValueError):
            BodyMetrics(5, 182, 30, "male", 500)


class TestStorage(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        self.path = tmp.name
        init_db(self.path)
        self.conn = connect(self.path)
        add_user(self.conn, "a", "Ala", 168, "1997-08-01", "female", 59)
        add_user(self.conn, "b", "Bob", 182, "1990-01-01", "male", 84)

    def tearDown(self):
        self.conn.close()
        os.unlink(self.path)

    def test_measurement_is_deduplicated(self):
        row = {"user_id": 1, "measured_at": "2026-08-19T07:31:12", "weight_kg": 60.0}
        self.assertIsNotNone(insert_measurement(self.conn, row))
        self.assertIsNone(insert_measurement(self.conn, row))

    def test_activity_upsert_and_epoch(self):
        act = {"id": 1, "athlete": {"id": 7}, "name": "Bieg", "sport_type": "Run",
               "start_date": "2026-08-19T05:00:00Z",
               "start_date_local": "2026-08-19T07:00:00", "distance": 10000,
               "moving_time": 3000}
        self.assertTrue(upsert_activity(self.conn, act))
        act["name"] = "Bieg poranny"
        self.assertFalse(upsert_activity(self.conn, act))
        self.assertEqual(self.conn.execute(
            "SELECT name FROM activities WHERE id=1").fetchone()["name"], "Bieg poranny")
        self.assertGreater(latest_activity_epoch(self.conn, 7), 0)


if __name__ == "__main__":
    unittest.main()


class TestStudentT(unittest.TestCase):
    """Wartosci referencyjne z tablic rozkladu t."""

    def test_critical_values(self):
        for df, expected in ((1, 12.706), (2, 4.303), (5, 2.571),
                             (10, 2.228), (30, 2.042), (100, 1.984)):
            self.assertAlmostEqual(t_ppf(0.975, df), expected, places=2, msg=f"df={df}")

    def test_cdf_symmetry(self):
        self.assertAlmostEqual(t_cdf(0, 7), 0.5, places=6)
        self.assertAlmostEqual(t_cdf(2.5, 7) + t_cdf(-2.5, 7), 1.0, places=6)

    def test_cdf_matches_normal_for_large_df(self):
        self.assertAlmostEqual(t_cdf(1.96, 100000), 0.975, places=3)


class TestIdentification(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        self.path = tmp.name
        init_db(self.path)
        self.conn = connect(self.path)
        self.now = datetime(2026, 8, 19, 7, 30)
        add_user(self.conn, "luk", "Lukasz", 182, "1995-04-12", "male", 84)
        add_user(self.conn, "ala", "Ala", 168, "1997-08-01", "female", 59)

    def tearDown(self):
        self.conn.close()
        os.unlink(self.path)

    def _add(self, user_id, days_ago, weight):
        insert_measurement(self.conn, {
            "user_id": user_id, "weight_kg": weight,
            "measured_at": (self.now - timedelta(days=days_ago)).isoformat(timespec="seconds")})

    def _history(self):
        for day, w in ((6, 84.4), (5, 84.1), (4, 84.3), (3, 83.9), (2, 84.0), (1, 83.8)):
            self._add(1, day, w)
        for day, w in ((6, 59.2), (4, 58.9), (2, 59.1), (1, 59.0)):
            self._add(2, day, w)

    def test_assigns_to_matching_interval(self):
        self._history()
        result = identify(self.conn, 83.9, self.now)
        self.assertEqual(result.username, "luk")
        self.assertEqual(result.method, "interval")
        self.assertGreater(result.score, 0)          # zapas wzgledem granicy

    def test_second_person_matches_own_interval(self):
        self._history()
        self.assertEqual(identify(self.conn, 59.3, self.now).username, "ala")

    def test_outlier_falls_back_to_nearest_last_weight(self):
        self._history()
        result = identify(self.conn, 80.5, self.now)   # poza oboma przedzialami
        self.assertEqual(result.username, "luk")
        self.assertEqual(result.method, "fallback_last")
        self.assertLess(result.score, 0)

    def test_far_outlier_is_unassigned(self):
        self._history()
        result = identify(self.conn, 130.0, self.now)  # gosc, nikt taki tu nie mieszka
        self.assertIsNone(result.user_id)
        self.assertEqual(result.method, "unassigned")

    def test_cold_start_uses_reference_weight(self):
        result = identify(self.conn, 83.5, self.now)   # zero pomiarow w bazie
        self.assertEqual(result.username, "luk")

    def test_interval_narrows_with_more_data(self):
        wide = build_candidate(self.conn.execute("SELECT * FROM users WHERE id=1").fetchone(),
                               [(self.now - timedelta(days=1), 84.0)], self.now, 84.0)
        self._history()
        rows = self.conn.execute(
            "SELECT measured_at, weight_kg FROM measurements WHERE user_id=1").fetchall()
        samples = [(datetime.fromisoformat(r["measured_at"]), r["weight_kg"]) for r in rows]
        narrow = build_candidate(self.conn.execute("SELECT * FROM users WHERE id=1").fetchone(),
                                 samples, self.now, 84.0)
        self.assertLess(narrow.half_width, wide.half_width)

    def test_trend_is_followed(self):
        for day, w in ((6, 86.0), (5, 85.7), (4, 85.4), (3, 85.1), (2, 84.8), (1, 84.5)):
            self._add(1, day, w)
        user = self.conn.execute("SELECT * FROM users WHERE id=1").fetchone()
        rows = self.conn.execute(
            "SELECT measured_at, weight_kg FROM measurements WHERE user_id=1").fetchall()
        samples = [(datetime.fromisoformat(r["measured_at"]), r["weight_kg"]) for r in rows]
        cand = build_candidate(user, samples, self.now, 86.0)
        self.assertLess(cand.center, 84.5)           # ekstrapoluje spadek wagi
        self.assertLess(cand.trend_kg_per_day, 0)


# --------------------------------------------------------------------------
# Garmin
# --------------------------------------------------------------------------
GARMIN_ACTIVITY = {
    "activityId": 987654321,
    "activityName": "Bieg poranny",
    "activityType": {"typeKey": "running"},
    "startTimeLocal": "2026-08-19 06:31:12",
    "startTimeGMT": "2026-08-19 04:31:12",
    "distance": 10250.0,
    "duration": 3120.0,
    "movingDuration": 3050.0,
    "elapsedDuration": 3200.0,
    "elevationGain": 84.0,
    "averageSpeed": 3.28,
    "maxSpeed": 4.7,
    "averageHR": 152.0,
    "maxHR": 176.0,
    "averageRunningCadenceInStepsPerMinute": 172.0,
    "calories": 720.0,
    "aerobicTrainingEffect": 3.4,
    "anaerobicTrainingEffect": 0.8,
    "vO2MaxValue": 52.0,
    "avgStrideLength": 114.5,
    "avgGroundContactTime": 248.0,
    "avgVerticalOscillation": 8.9,
    "deviceId": 3417886310,
}

GARMIN_STATS = {
    "totalSteps": 12480, "totalDistanceMeters": 9800.0, "floorsAscended": 6.0,
    "totalKilocalories": 2950.0, "activeKilocalories": 980.0,
    "bmrKilocalories": 1970.0, "restingHeartRate": 48, "minHeartRate": 42,
    "maxHeartRate": 176, "averageStressLevel": 31, "maxStressLevel": 92,
    "bodyBatteryHighestValue": 91, "bodyBatteryLowestValue": 24,
    "moderateIntensityMinutes": 20, "vigorousIntensityMinutes": 45,
}

GARMIN_SLEEP = {"dailySleepDTO": {
    "sleepTimeSeconds": 26400, "deepSleepSeconds": 5400, "lightSleepSeconds": 15000,
    "remSleepSeconds": 5400, "awakeSleepSeconds": 600,
    "averageRespirationValue": 13.8, "averageSpO2Value": 95.0,
    "sleepScores": {"overall": {"value": 82}},
}}

GARMIN_HRV = {"hrvSummary": {"lastNightAvg": 64, "status": "BALANCED"}}


class _FakeGarmin:
    """Zastepuje biblioteke garminconnect - te same nazwy metod, stale dane."""

    display_name = "abc-123-profile"
    full_name = "Test Testowy"

    def get_activities_by_date(self, start, end, *_a, **_k):
        return [GARMIN_ACTIVITY]

    def get_stats(self, day):
        return GARMIN_STATS

    def get_sleep_data(self, day):
        return GARMIN_SLEEP

    def get_hrv_data(self, day):
        return GARMIN_HRV

    def get_training_readiness(self, day):
        return [{"score": 71, "level": "HIGH"}]

    def get_max_metrics(self, day):
        return [{"generic": {"vo2MaxPreciseValue": 52.4, "vo2MaxValue": 52.0}}]


class TestGarminMapping(unittest.TestCase):
    def test_activity_fields(self):
        row = activity_row(GARMIN_ACTIVITY, "abc-123-profile", 3)
        self.assertEqual(row["id"], 987654321)
        self.assertEqual(row["sport_type"], "running")
        self.assertEqual(row["user_id"], 3)
        self.assertAlmostEqual(row["distance_m"], 10250.0)
        self.assertAlmostEqual(row["moving_time_s"], 3050.0)   # movingDuration ma pierwszenstwo
        self.assertAlmostEqual(row["average_cadence"], 172.0)
        self.assertAlmostEqual(row["vo2max"], 52.0)
        self.assertEqual(row["device"], "3417886310")
        self.assertIn("activityId", row["raw_json"])

    def test_activity_missing_fields_do_not_break(self):
        row = activity_row({"activityId": 5, "activityName": "Spacer"}, "p")
        self.assertEqual(row["id"], 5)
        self.assertIsNone(row["distance_m"])
        self.assertIsNone(row["average_heartrate"])

    def test_daily_merges_sources(self):
        row = daily_row("2026-08-19", "p", 1, stats=GARMIN_STATS, sleep=GARMIN_SLEEP,
                        hrv=GARMIN_HRV, readiness=[{"score": 71}],
                        max_metrics=[{"generic": {"vo2MaxPreciseValue": 52.4}}])
        self.assertEqual(row["steps"], 12480)
        self.assertAlmostEqual(row["resting_hr"], 48)
        self.assertAlmostEqual(row["sleep_seconds"], 26400)
        self.assertAlmostEqual(row["sleep_score"], 82)
        self.assertAlmostEqual(row["hrv_last_night"], 64)
        self.assertEqual(row["hrv_status"], "BALANCED")
        self.assertAlmostEqual(row["training_readiness"], 71)
        self.assertAlmostEqual(row["vo2max"], 52.4)
        self.assertTrue(has_data(row))

    def test_empty_day_is_recognised(self):
        row = daily_row("2026-08-19", "p", None)
        self.assertFalse(has_data(row))


class TestGarminStorage(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        self.path = tmp.name
        init_db(self.path)
        self.conn = connect(self.path)
        self.pause = config.GARMIN_REQUEST_PAUSE
        config.GARMIN_REQUEST_PAUSE = 0.0

    def tearDown(self):
        config.GARMIN_REQUEST_PAUSE = self.pause
        self.conn.close()
        os.unlink(self.path)

    def test_activity_upsert_is_idempotent(self):
        row = activity_row(GARMIN_ACTIVITY, "p", None)
        self.assertTrue(upsert_garmin_activity(self.conn, row))
        row["name"] = "Bieg wieczorny"
        self.assertFalse(upsert_garmin_activity(self.conn, row))
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) c FROM garmin_activities").fetchone()["c"], 1)
        self.assertEqual(self.conn.execute(
            "SELECT name FROM garmin_activities").fetchone()["name"], "Bieg wieczorny")
        self.assertEqual(latest_garmin_activity_day(self.conn, "p"), "2026-08-19")

    def test_daily_upsert_keeps_earlier_values(self):
        """Drugi przebieg bez snu nie kasuje snu zapisanego w pierwszym."""
        full = daily_row("2026-08-19", "p", None, stats=GARMIN_STATS, sleep=GARMIN_SLEEP)
        self.assertTrue(upsert_garmin_daily(self.conn, full))
        partial = daily_row("2026-08-19", "p", None, stats=GARMIN_STATS)
        self.assertFalse(upsert_garmin_daily(self.conn, partial))
        saved = self.conn.execute("SELECT * FROM garmin_daily").fetchone()
        self.assertAlmostEqual(saved["sleep_score"], 82)
        self.assertEqual(saved["steps"], 12480)

    def test_sync_writes_both_tables(self):
        uid = add_user(self.conn, "ruka", "Lukasz", 182, "1995-04-12", "male", 84)
        self.conn.execute("UPDATE users SET garmin_profile_id=? WHERE id=?",
                          ("abc-123-profile", uid))
        self.conn.commit()

        result = garmin_sync.sync(self.conn, _FakeGarmin(), days=1)
        self.assertEqual(result["new_activities"], 1)
        self.assertEqual(result["days"], 2)          # dzis i wczoraj

        act = self.conn.execute("SELECT * FROM garmin_activities").fetchone()
        self.assertEqual(act["user_id"], uid)
        self.assertEqual(act["profile_id"], "abc-123-profile")
        days = self.conn.execute(
            "SELECT * FROM garmin_daily ORDER BY day").fetchall()
        self.assertEqual(len(days), 2)
        self.assertTrue(all(d["user_id"] == uid for d in days))
        self.assertAlmostEqual(days[0]["training_readiness"], 71)

    def test_sync_without_linked_user_leaves_user_id_null(self):
        result = garmin_sync.sync(self.conn, _FakeGarmin(), days=0, do_daily=False)
        self.assertEqual(result["new_activities"], 1)
        self.assertIsNone(self.conn.execute(
            "SELECT user_id FROM garmin_activities").fetchone()["user_id"])
