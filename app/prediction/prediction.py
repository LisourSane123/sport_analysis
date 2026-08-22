import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path
import polars as pl
import sqlalchemy as sa
from pathlib import Path

DB = Path(__file__).resolve().parents[2] / "data" / "waga.db"
engine = sa.create_engine(f"sqlite:///file:{DB}?mode=ro&uri=true")
with engine.connect() as conn:
    query_garmin_daily = sa.text("""SELECT start_time_local, distance_m, moving_time_s, total_elevation_gain, average_speed, max_speed, average_heartrate, max_heartrate, 
                                    calories, aerobic_te, anaerobic_te 
                                    FROM garmin_activities
                                    WHERE sport_type = 'running'""")
    query_garmin_activities = sa.text("""SELECT day, steps, floors_climbed, calories_total, calories_active, resting_hr, avg_stress, body_battery_high, sleep_seconds, 
                                         deep_sleep_seconds, light_sleep_seconds, rem_sleep_seconds, awake_seconds, sleep_score, hrv_status, training_readiness 
                                         FROM garmin_daily""")
    query_scale_mesurements = sa.text("""SELECT recorded_at, weight_kg, fat_percentage, muscle_mass, bone_mass, water_percentage
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
print("Tutaj po czym grupuje się scale_measurements_df")
print(scale_measurements_df["recorded_at"])
print("Tutaj po czym grupuje się garmin_activities_df")
print(garmin_activities_df["day"])
print("Tutaj po czym grupuje się garmin_daily_df")
print(garmin_daily_df["start_time_local"])
#print(scale_measurements_df)
class Weight_predictor:
    def __init__(self):
        DB = Path(__file__).resolve().parents[2] / "data" / "waga.db"
        self.engine = sa.create_engine(f"sqlite:///file:{DB}?mode=ro&uri=true")

    def _get_garmin_daily(self):
        with self.engine.connect() as conn:
            query_garmin_daily = sa.text("""SELECT start_time_local, distance_m, moving_time_s, total_elevation_gain, average_speed, max_speed, average_heartrate, max_heartrate, 
                                            calories, aerobic_te, anaerobic_te 
                                            FROM garmin_activities
                                            WHERE sport_type = 'running'""")
            garmin_daily_df = pl.read_database(query_garmin_daily, conn)
        return garmin_daily_df

    def _get_garmin_activities(self):
        with self.engine.connect() as conn:
            query_garmin_activities = sa.text("""SELECT day, steps, floors_climbed, calories_total, calories_active, resting_hr, avg_stress, body_battery_high, sleep_seconds, 
                                                 deep_sleep_seconds, light_sleep_seconds, rem_sleep_seconds, awake_seconds, sleep_score, hrv_status, training_readiness 
                                                 FROM garmin_daily""")
            garmin_activities_df = pl.read_database(query_garmin_activities, conn)
        return garmin_activities_df

    def _get_scale_measurements(self):
        with self.engine.connect() as conn:
            query_scale_mesurements = sa.text("""SELECT recorded_at, weight_kg, fat_percentage, muscle_mass, bone_mass, water_percentage
                                                 from measurements""")
            scale_measurements_df = pl.read_database(query_scale_mesurements, conn)
        return scale_measurements_df

    def get_model_data(self):
        garmin_daily_df = self._get_garmin_daily()
        garmin_activities_df = self._get_garmin_activities()
        scale_measurements_df = self._get_scale_measurements()
        return garmin_daily_df, garmin_activities_df, scale_measurements_df

    def transform_model_data(self):
        garmin_daily_df, garmin_activities_df, scale_measurements_df = self.get_model_data()
        garmin_daily_df = garmin_daily_df.with_columns(pl.col("start_time_local").str.strptime(pl.Datetime, fmt="%Y-%m-%d %H:%M:%S"))
        garmin_activities_df = garmin_activities_df.with_columns(pl.col("day").str.strptime(pl.Date, fmt="%Y-%m-%d"))
        scale_measurements_df = scale_measurements_df.with_columns(pl.col("recorded_at").str.strptime(pl.Datetime, fmt="%Y-%m-%d %H:%M:%S"))
        return garmin_daily_df, garmin_activities_df, scale_measurements_df

    def prepare_weight_prediction_data(self):
        garmin_daily_df, garmin_activities_df, scale_measurements_df = self.transform_model_data()
        targeted_df = scale_measurements_df.select(["recorded_at", "weight_kg"]).rename({"recorded_at": "date"})
        garmin_activities_df = garmin_activities_df.rename({"day": "date"})
        garmin_daily_df = garmin_daily_df.rename({"start_time_local": "date"})
        merged_weight_df = targeted_df.join(garmin_daily_df, on="date", how="left")
        merged_weight_df = merged_weight_df.join(garmin_activities_df, on="date", how="left")
        return merged_weight_df

    
