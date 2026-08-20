import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path
import polars as pl
import sqlalchemy as sa
from pathlib import Path

DB = Path(__file__).resolve().parents[2] / "data" / "waga.db"
engine = sa.create_engine(f"sqlite:///file:{DB}?mode=ro&uri=true")
with engine.connect() as conn:
    query = sa.text("SELECT * FROM users")
    df = pl.read_database(query, conn)
print(df)