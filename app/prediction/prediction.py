import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path
import polars as pl
import sqlalchemy as sa
from pathlib import Path

DB = Path(__file__).resolve().parents[2] / "data" / "waga.db"
engine = sa.create_engine(f"sqlite:///file:{DB}?mode=ro&uri=true")
with engine.connect() as conn:
    query_garmin_daily = sa.text("""SELECT start_time_local, distance, moving_time_s, total_elevation_gain, average_speed, max_speed, average_heart_rate, max_heart_rate, 
                                    calories, aerobic_te, anaerobic_te 
                                    FROM garmin_daily
                                    WHERE sport_type = 'running'""")
    query_garmin_activities = sa.text("""SELECT day, steps, floors_climbed, calories_total, calories_active, resting_hr, avg_stress, body_battery_high, sleep_seconds, 
                                         deep_sleep_seconds, light_sleep_seconds, rem_sleep_seconds, awake_seconds, sleep_score, hrv_status, treaning_readiness 
                                         FROM garmin_activities""")
    query_scale_mesurements = sa.text("""SELECT recorded_at, weight_kg, fat_percentage, muscle_mass_kg, bone_mass_kg, water_percentage
                                         from measurements""")
    garmin_daily_df = pl.read_database(query_garmin_daily, conn)
    garmin_activities_df = pl.read_database(query_garmin_activities, conn)
    scale_measurements_df = pl.read_database(query_scale_mesurements, conn)
# print(f"Garmin daily: {len(garmin_daily_df)} rows")
# for col in garmin_daily_df.columns:
#     print(f"{col}: {garmin_daily_df[col].dtype}")
#     print(f"Last 5 values: {garmin_daily_df[col].tail(5)}")
# print(garmin_daily_df)
# print(f"Garmin activities: {len(garmin_activities_df)} rows")
# for col in garmin_activities_df.columns:
#     print(f"{col}: {garmin_activities_df[col].dtype}")
#     print(f"Last 5 values: {garmin_activities_df[col].tail(5)}")
# print(garmin_activities_df)
print(f"Scale measurements: {len(scale_measurements_df)} rows")
for col in scale_measurements_df.columns:
    print(f"{col}: {scale_measurements_df[col].dtype}")
    print(f"Last 5 values: {scale_measurements_df[col].tail(5)}")
print(scale_measurements_df)
class Weight_predictor:
    def __init__(self):
        DB = Path(__file__).resolve().parents[2] / "data" / "waga.db"
        self.engine = sa.create_engine(f"sqlite:///file:{DB}?mode=ro&uri=true")
