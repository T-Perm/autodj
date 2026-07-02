from sqlmodel import SQLModel, create_engine, Session
from dotenv import load_dotenv
import os

load_dotenv()

DB_PATH = os.getenv("DB_PATH", "./autodj.db")
engine = create_engine(f"sqlite:///{DB_PATH}", echo=False)


def create_db():
    SQLModel.metadata.create_all(engine)
    _migrate(engine)


def _migrate(eng):
    from sqlalchemy import text
    with eng.connect() as conn:
        cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(track)")}
        for col in ("first_beat_ms", "drop_ms"):
            if col not in cols:
                conn.execute(text(f"ALTER TABLE track ADD COLUMN {col} INTEGER"))
        conn.commit()


def get_session():
    with Session(engine) as session:
        yield session
