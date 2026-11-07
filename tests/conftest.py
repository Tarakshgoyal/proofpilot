"""Shared test fixtures: one fresh SQLite DB per test session."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DB_FILE = ROOT / "data" / "test_proofpilot.db"
os.environ["DATABASE_URL"] = f"sqlite:///{DB_FILE}"

import pytest  # noqa: E402

from backend.app.db import SessionLocal, init_db  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _fresh_db():
    if DB_FILE.exists():
        DB_FILE.unlink()
    init_db()
    yield
