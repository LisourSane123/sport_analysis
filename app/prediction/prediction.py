import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path
import polars as pl
import sqlalchemy as sa
from pathlib import Path

DB = Path(__file__).resolve().parents[2] / "data" / "waga.db"
engine = sa.create_engine(f"sqlite:///file:{DB}?mode=ro&uri=true")
with engine.connect() as conn:
    query_garmin_daily = sa.text("SELECT * FROM garmin_daily")
    query_garmin_activities = sa.text("SELECT * FROM garmin_activities")
    garmin_daily_df = pl.read_database(query_garmin_daily, conn)
    garmin_activities_df = pl.read_database(query_garmin_activities, conn)
print(f"Garmin daily: {len(garmin_daily_df)} rows")
for col in garmin_daily_df.columns:
    print(f"{col}: {garmin_daily_df[col].dtype}")
    print(f"First 5 values: {garmin_daily_df[col].head(5)}")
print(garmin_daily_df)
print(f"Garmin activities: {len(garmin_activities_df)} rows")
for col in garmin_activities_df.columns:
    print(f"{col}: {garmin_activities_df[col].dtype}")
    print(f"First 5 values: {garmin_activities_df[col].head(5)}")
print(garmin_activities_df)
