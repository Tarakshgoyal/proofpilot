"""Database engine/session. Init schema:  python -m backend.app.db
"""
from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker

from .config import settings
from .models import Base

engine = sa.create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def init_db() -> None:
    Base.metadata.create_all(engine)
    _ensure_columns()


def _ensure_columns() -> None:
    """create_all doesn't ALTER existing tables; add late columns here."""
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    if "cases" in inspector.get_table_names():
        cols = {c["name"] for c in inspector.get_columns("cases")}
        if "synthetic" not in cols:
            with engine.begin() as conn:
                conn.execute(text(
                    "ALTER TABLE cases ADD COLUMN synthetic BOOLEAN DEFAULT FALSE"))
        if "eval_run_id" not in cols:
            with engine.begin() as conn:
                conn.execute(text(
                    "ALTER TABLE cases ADD COLUMN eval_run_id VARCHAR(36)"))


if __name__ == "__main__":
    init_db()
    print("schema created at", settings.database_url)
