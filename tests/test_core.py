"""Testy bez sprzetu: dekoder ramek, kompozycja ciala, zapis do bazy.

Uruchomienie: .venv/bin/python -m unittest discover -s tests
"""
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app import __version__ as app_version
from app import config, settings
from app import db as db_module
from app.web import server
from app.db import (add_user, connect, delete_duplicates,
                    find_duplicate_groups, init_db, insert_measurement,
                    latest_garmin_activity_day, repair_broken_timestamps,
                    upsert_garmin_activity, upsert_garmin_daily)
from app.garmin import sync as garmin_sync
from app.garmin.mapping import activity_row, daily_row, has_data
from app.scale.body_metrics import BodyMetrics
from app.scale.decoder import decode
from app.scale.runner import _is_repeat as is_repeat
from app.scale.runner import resolve_time, store_measurement
from app.scale.identify import build_candidate, identify, t_cdf, t_ppf


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
        settings.save(self.conn, {"garmin_request_pause": 0.0})

    def tearDown(self):
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


# --------------------------------------------------------------------------
# Zegar wagi
# --------------------------------------------------------------------------
def _frame(year=2026, month=8, day=20, hour=7, minute=31, second=12,
           ctrl0=0x02, ctrl1=0x22, impedance=512, raw_weight=17670):
    return (bytes([ctrl0, ctrl1]) + year.to_bytes(2, "little")
            + bytes([month, day, hour, minute, second])
            + impedance.to_bytes(2, "little") + raw_weight.to_bytes(2, "little"))


class TestScaleClock(unittest.TestCase):
    def test_unset_clock_is_rejected(self):
        """Rok 1970 to nieustawiony zegar wagi, nie data pomiaru."""
        m = decode(_frame(year=1970, month=1, day=1, hour=0, minute=0, second=0))
        self.assertIsNone(m.measured_at)
        self.assertEqual(m.scale_clock, datetime(1970, 1, 1))
        self.assertFalse(m.clock_ok)

    def test_year_2000_is_rejected_too(self):
        self.assertIsNone(decode(_frame(year=2000, month=1, day=1)).measured_at)

    def test_sane_clock_is_kept(self):
        m = decode(_frame())
        self.assertEqual(m.measured_at, datetime(2026, 8, 20, 7, 31, 12))
        self.assertTrue(m.clock_ok)

    def test_fallback_to_pi_clock(self):
        now = datetime(2026, 8, 20, 18, 0, 0)
        when, problem = resolve_time(decode(_frame(year=1970, month=1, day=1)), now)
        self.assertEqual(when, now)
        self.assertIn("nieustawiony", problem)

    def test_drifted_clock_is_rejected(self):
        now = datetime(2026, 8, 20, 18, 0, 0)
        when, problem = resolve_time(decode(_frame(year=2024)), now)
        self.assertEqual(when, now)
        self.assertIn("rozjechany", problem)

    def test_small_drift_is_accepted(self):
        now = datetime(2026, 8, 20, 18, 0, 0)          # ramka jest z 7:31 tego dnia
        when, problem = resolve_time(decode(_frame()), now)
        self.assertEqual(when, datetime(2026, 8, 20, 7, 31, 12))
        self.assertIsNone(problem)


class TestSettings(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        self.path = tmp.name
        init_db(self.path)
        self.conn = connect(self.path)

    def tearDown(self):
        self.conn.close()
        os.unlink(self.path)

    def test_defaults_come_from_env(self):
        self.assertEqual(settings.get(self.conn, "scan_duration"), config.SCAN_DURATION)

    def test_saved_value_wins(self):
        settings.save(self.conn, {"scan_duration": 45})
        self.assertEqual(settings.get(self.conn, "scan_duration"), 45)
        self.assertEqual(settings.all_values(self.conn)["scan_duration"], 45)

    def test_out_of_range_is_rejected(self):
        with self.assertRaises(ValueError):
            settings.save(self.conn, {"scan_duration": 9999})
        self.assertEqual(settings.get(self.conn, "scan_duration"), config.SCAN_DURATION)

    def test_unknown_key_is_rejected(self):
        with self.assertRaises(ValueError):
            settings.save(self.conn, {"nie_ma_takiego": 1})

    def test_reset_restores_env_value(self):
        settings.save(self.conn, {"scan_interval": 3})
        settings.reset(self.conn, "scan_interval")
        self.assertEqual(settings.get(self.conn, "scan_interval"), config.SCAN_INTERVAL)

    def test_broken_row_does_not_break_service(self):
        """Recznie zepsuty wpis w bazie nie moze polozyc petli skanowania."""
        self.conn.execute("INSERT INTO settings (key, value) VALUES ('scan_duration','abc')")
        self.conn.commit()
        self.assertEqual(settings.get(self.conn, "scan_duration"), config.SCAN_DURATION)


class TestAdminApi(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        self.path = tmp.name
        self._orig_db = db_module.DB_PATH
        db_module.DB_PATH = self.path          # connect() czyta to przy wywolaniu
        init_db(self.path)
        self.client = TestClient(server.app)

    def tearDown(self):
        db_module.DB_PATH = self._orig_db
        os.unlink(self.path)

    def _user(self, username="ruka", **kw):
        payload = {"username": username, "display_name": "Lukasz", "height_cm": 182,
                   "birthdate": "1995-04-12", "sex": "male", "ref_weight": 84}
        payload.update(kw)
        return self.client.post("/api/users", json=payload)

    def test_create_and_duplicate_user(self):
        self.assertEqual(self._user().status_code, 200)
        self.assertEqual(self._user().status_code, 409)

    def test_missing_fields_are_reported(self):
        resp = self.client.post("/api/users", json={"username": "x"})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("display_name", resp.json()["detail"])

    def test_reassign_recomputes_body_composition(self):
        self._user()
        with connect(self.path) as conn:
            insert_measurement(conn, {"user_id": None, "measured_at": "2026-08-20T07:00:00",
                                      "weight_kg": 84.0, "impedance": 500})
        resp = self.client.patch("/api/measurements/1", json={"username": "ruka"})
        self.assertEqual(resp.status_code, 200)
        row = self.client.get("/api/measurements/all").json()["measurements"][0]
        self.assertEqual(row["username"], "ruka")
        self.assertEqual(row["identify_method"], "manual")
        self.assertIsNotNone(row["bmi"])
        self.assertIsNotNone(row["fat_percentage"])

    def test_unassign_clears_body_composition(self):
        """Sklad ciala policzony dla jednej osoby nie moze zostac przy innej."""
        self._user()
        with connect(self.path) as conn:
            insert_measurement(conn, {"user_id": None, "measured_at": "2026-08-20T07:00:00",
                                      "weight_kg": 84.0, "impedance": 500})
        self.client.patch("/api/measurements/1", json={"username": "ruka"})
        self.client.patch("/api/measurements/1", json={"username": None})
        row = self.client.get("/api/measurements/all").json()["measurements"][0]
        self.assertIsNone(row["user_id"])
        self.assertIsNone(row["bmi"])
        self.assertIsNone(row["fat_percentage"])

    def test_measurements_all_paginates_and_counts(self):
        self._user()
        with connect(self.path) as conn:
            for i in range(5):
                insert_measurement(conn, {"user_id": 1, "measured_at": f"2026-08-1{i}T07:00:00",
                                          "weight_kg": 84.0 + i})
        data = self.client.get("/api/measurements/all?limit=2&offset=0").json()
        self.assertEqual(data["total"], 5)
        self.assertEqual(len(data["measurements"]), 2)

    def test_settings_roundtrip_over_http(self):
        self.assertEqual(self.client.put("/api/settings",
                                         json={"scan_interval": 7}).status_code, 200)
        self.assertEqual(self.client.get("/api/settings").json()["values"]["scan_interval"], 7)
        self.assertEqual(self.client.put("/api/settings",
                                         json={"scan_interval": 99999}).status_code, 400)

    def test_admin_can_be_switched_off(self):
        server.config.ADMIN_ENABLED = False
        try:
            self.assertEqual(self._user("nowy").status_code, 403)
            self.assertEqual(self.client.get("/api/settings").status_code, 200)  # odczyt dziala
        finally:
            server.config.ADMIN_ENABLED = True


class TestTimestampRepair(unittest.TestCase):
    """Naprawa dat zapisanych z nieustawionego zegara wagi (rok 1970)."""

    def setUp(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        self.path = tmp.name
        init_db(self.path)
        self.conn = connect(self.path)
        add_user(self.conn, "ruka", "Lukasz", 182, "1995-04-12", "male", 84)

    def tearDown(self):
        self.conn.close()
        os.unlink(self.path)

    def _broken(self, measured_at="1970-01-01T00:00:00", recorded_at="2026-08-18 05:31:10"):
        self.conn.execute(
            "INSERT INTO measurements (user_id, measured_at, recorded_at, weight_kg) "
            "VALUES (?,?,?,?)", (1, measured_at, recorded_at, 84.2))
        self.conn.commit()

    def test_uses_recorded_at_converted_to_local_time(self):
        self._broken()
        fixed = repair_broken_timestamps(self.conn)
        self.assertEqual(len(fixed), 1)
        new_value = self.conn.execute("SELECT measured_at FROM measurements").fetchone()[0]
        expected = (datetime(2026, 8, 18, 5, 31, 10, tzinfo=timezone.utc)
                    .astimezone().replace(tzinfo=None).isoformat(timespec="seconds"))
        self.assertEqual(new_value, expected)

    def test_sane_dates_are_left_alone(self):
        self.conn.execute(
            "INSERT INTO measurements (user_id, measured_at, recorded_at, weight_kg) "
            "VALUES (1,'2026-08-19T07:00:00','2026-08-19 05:00:00',83.0)")
        self.conn.commit()
        self.assertEqual(repair_broken_timestamps(self.conn), [])

    def test_runs_automatically_on_init_and_is_idempotent(self):
        self._broken()
        init_db(self.path)                     # tak jak przy starcie uslugi
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM measurements WHERE measured_at < '2015'").fetchone()[0], 0)
        init_db(self.path)                     # drugi start nic nie psuje
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM measurements").fetchone()[0], 1)


class TestVersionExposed(unittest.TestCase):
    """Dashboard porownuje wersje z /api/health z ta wpisana w index.html."""

    def test_health_and_page_agree(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        orig = db_module.DB_PATH
        db_module.DB_PATH = tmp.name
        try:
            init_db(tmp.name)
            client = TestClient(server.app)
            self.assertEqual(client.get("/api/health").json()["version"], app_version)
            page = client.get("/").text
            self.assertIn(f'name="app-version" content="{app_version}"', page)
        finally:
            db_module.DB_PATH = orig
            os.unlink(tmp.name)


class TestRepeatedBroadcasts(unittest.TestCase):
    """Waga rozglasza ostatni wynik dlugo po zejsciu - to nie sa nowe pomiary."""

    def setUp(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        self.path = tmp.name
        init_db(self.path)
        self.conn = connect(self.path)
        add_user(self.conn, "ruka", "Lukasz", 182, "1995-04-12", "male", 84)

    def tearDown(self):
        self.conn.close()
        os.unlink(self.path)

    def test_identical_frame_is_stored_once(self):
        frame = _frame(year=1970, month=1, day=1)      # zegar wagi nieustawiony
        first = store_measurement(self.conn, decode(frame))
        self.assertIsNotNone(first)
        for _ in range(5):                              # kolejne cykle skanowania
            self.assertIsNone(store_measurement(self.conn, decode(frame)))
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM measurements").fetchone()[0], 1)

    def test_new_weighing_is_stored(self):
        store_measurement(self.conn, decode(_frame(year=1970, month=1, day=1)))
        # inna waga i inna impedancja = nowe wazenie, mimo krotkiego odstepu
        other = _frame(year=1970, month=1, day=1, impedance=613, raw_weight=12000)
        self.assertIsNotNone(store_measurement(self.conn, decode(other)))
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM measurements").fetchone()[0], 2)

    def test_window_can_be_switched_off(self):
        """Przy oknie 0 zostaje tylko odsiewanie ramek identycznych co do bajtu."""
        store_measurement(self.conn, decode(_frame(year=1970, month=1, day=1)))
        now = datetime.now()
        inna_ramka = decode(_frame(year=1970, month=1, day=1, second=30))
        self.assertIsNotNone(is_repeat(self.conn, inna_ramka, now, 30))   # okno wlaczone
        self.assertIsNone(is_repeat(self.conn, inna_ramka, now, 0))       # okno wylaczone
        ta_sama = decode(_frame(year=1970, month=1, day=1))
        self.assertIsNotNone(is_repeat(self.conn, ta_sama, now, 0))       # identyczne bajty

    def test_same_values_within_window_are_skipped(self):
        """Rozne bajty ramki, ale ta sama waga i impedancja w oknie -> powtorka."""
        store_measurement(self.conn, decode(_frame(year=1970, month=1, day=1)))
        again = _frame(year=1970, month=1, day=1, second=30)
        self.assertIsNone(store_measurement(self.conn, decode(again)))


class TestDuplicateCleanup(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        self.path = tmp.name
        init_db(self.path)
        self.conn = connect(self.path)
        add_user(self.conn, "ruka", "Lukasz", 182, "1995-04-12", "male", 84)

    def tearDown(self):
        self.conn.close()
        os.unlink(self.path)

    def _add(self, minutes, weight, impedance=500):
        when = datetime(2026, 8, 20, 7, 0, 0) + timedelta(minutes=minutes)
        insert_measurement(self.conn, {
            "user_id": 1, "measured_at": when.isoformat(timespec="seconds"),
            "weight_kg": weight, "impedance": impedance})

    def test_series_collapses_to_oldest(self):
        for minute in (0, 1, 2, 5, 9):            # jedno wazenie, piec zapisow
            self._add(minute, 84.2)
        groups, removed = delete_duplicates(self.conn, window_minutes=60)
        self.assertEqual((groups, removed), (1, 4))
        left = self.conn.execute("SELECT measured_at FROM measurements").fetchall()
        self.assertEqual(len(left), 1)
        self.assertEqual(left[0]["measured_at"], "2026-08-20T07:00:00")

    def test_separate_weighings_survive(self):
        self._add(0, 84.2)
        self._add(600, 84.2)                      # 10 godzin pozniej, poza oknem
        self._add(5, 59.0, impedance=620)         # inna osoba
        self.assertEqual(delete_duplicates(self.conn, window_minutes=60), (0, 0))
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM measurements").fetchone()[0], 3)

    def test_groups_describe_what_would_be_removed(self):
        for minute in (0, 3, 6):
            self._add(minute, 84.2)
        groups = find_duplicate_groups(self.conn, window_minutes=60)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["count"], 3)
        self.assertEqual(len(groups[0]["remove"]), 2)
        self.assertEqual(groups[0]["first"], "2026-08-20T07:00:00")
        self.assertEqual(groups[0]["last"], "2026-08-20T07:06:00")
