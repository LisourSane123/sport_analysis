import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path
import polars as pl
import sqlalchemy as sa

engine = sa.create_engine("sqlite:///file:/home/pi/Waga_RP/data/waga.db?mode=ro&uri=true")
with engine.connect() as conn:
    query = sa.text("SELECT * FROM users")
    df = pl.read_database(query, conn)
print(df)