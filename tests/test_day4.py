"""Day 4/5 tests: eval world idempotency (regression for the FK bug)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.db import SessionLocal, init_db  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _db():
    init_db()
    yield


def test_build_world_is_idempotent():
    from seed.scenarios import archetypes, build_world

    archs = {a["name"]: a for a in archetypes()}
    with SessionLocal() as s:
        for name in ("missing_tracking", "snad_policy_quote"):
            a = archs[name]
            c1 = build_world(s, a, 0, "runTEST0001")
            c2 = build_world(s, a, 0, "runTEST0001")  # same run -> teardown + rebuild
            assert c1 and c2, "rebuild must succeed without FK/Integrity errors"
            c3 = build_world(s, a, 0, "runTEST0002")  # different run, fresh world
            assert c3 not in (c1, c2)
