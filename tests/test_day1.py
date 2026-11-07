"""Day 1 tests: locator, hashing, money, upsert + fact idempotency.

Run:  .venv/Scripts/python -m pytest tests/ -q
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DATABASE_URL", f"sqlite:///{ROOT / 'data' / 'test_proofpilot.db'}")

from sqlalchemy import select  # noqa: E402

from backend.app.collector import (  # noqa: E402
    canonical_hash, collect_case, money_to_minor, upsert_case_from_dispute,
)
from backend.app.db import SessionLocal, init_db  # noqa: E402
from backend.app.docs_pipeline import find_quote, persist_document  # noqa: E402
from backend.app.models import Case, Fact  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _db():
    init_db()
    yield


DISPUTE = {
    "dispute_id": "PP-D-TEST-1",
    "reason": "MERCHANDISE_OR_SERVICE_NOT_RECEIVED",
    "status": "WAITING_FOR_SELLER_RESPONSE",
    "dispute_life_cycle_stage": "INQUIRY",
    "dispute_amount": {"currency_code": "USD", "value": "25.00"},
    "seller_response_due_date": "2026-11-17T10:00:00Z",
    "disputed_transactions": [
        {"seller_transaction_id": "CAP1", "buyer_transaction_id": "BUY1"}],
}


def _pdf_bytes(lines: list[str]) -> bytes:
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas
    import io

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    y = 740
    for line in lines:
        c.drawString(72, y, line)
        y -= 18
    c.save()
    return buf.getvalue()


# ------------------------------------------------------------------- units
def test_money_to_minor():
    assert money_to_minor("25.00") == 2500
    assert money_to_minor("0.01") == 1
    assert money_to_minor(10) == 1000
    assert money_to_minor("1234.56") == 123456


def test_canonical_hash_is_order_insensitive():
    assert canonical_hash({"a": 1, "b": 2}) == canonical_hash({"b": 2, "a": 1})


# ------------------------------------------------------- case upsert/facts
def test_case_upsert_idempotent_and_versioning():
    with SessionLocal() as s:
        case, changed = upsert_case_from_dispute(s, DISPUTE)
        s.commit()
        v1, id1 = case.version, case.id
        assert case.amount_minor == 2500

        case2, changed2 = upsert_case_from_dispute(s, DISPUTE)
        s.commit()
        assert case2.id == id1 and not changed2 and case2.version == v1

        changed_dispute = {**DISPUTE, "status": "UNDER_REVIEW"}
        case3, changed3 = upsert_case_from_dispute(s, changed_dispute)
        s.commit()
        assert changed3 and case3.version == v1 + 1

        count = len(s.scalars(select(Case).where(Case.dispute_id == DISPUTE["dispute_id"])).all())
        assert count == 1


def test_collect_case_facts_dedupe_on_resync():
    with SessionLocal() as s:
        case, _ = upsert_case_from_dispute(s, DISPUTE)
        s.commit()
        stats1 = collect_case(s, case, DISPUTE)
        s.commit()
        n1 = len(s.scalars(select(Fact).where(Fact.case_id == case.id)).all())
        assert n1 > 0

        stats2 = collect_case(s, case, DISPUTE)
        s.commit()
        n2 = len(s.scalars(select(Fact).where(Fact.case_id == case.id)).all())
        assert n2 == n1, "re-collect must not duplicate facts"
        assert stats2["paypal_facts"] == 0
        assert case.workflow_status == "ANALYSED"


# ----------------------------------------------------------------- locator
def test_locator_exact_fuzzy_miss():
    pdf = _pdf_bytes([
        "Carrier Record — Proof of Delivery",
        "Tracking number: TRK-9001",
        "Delivery status: DELIVERED",
        "Delivery date: 2026-11-05",
        "Delivered to: 2211 N 1st St, San Jose, CA 95131",
    ])
    with SessionLocal() as s:
        case, _ = upsert_case_from_dispute(s, DISPUTE)
        s.commit()
        doc, spans = persist_document(s, case.id, pdf, "carrier.pdf",
                                      doc_type="CARRIER_DOC", origin="SEED")
        s.commit()
        assert spans > 0 and doc.parsed_at

        hit = find_quote(s, doc.id, "Delivery date: 2026-11-05")
        assert hit and hit.match_type == "EXACT" and hit.page == 1
        assert hit.box[0] < hit.box[2] and hit.box[1] < hit.box[3]

        fuzzy = find_quote(s, doc.id, "Delivry date 2026-11-05")
        assert fuzzy and fuzzy.match_type == "FUZZY" and fuzzy.score >= 0.92

        assert find_quote(s, doc.id, "The buyer signed in person on Friday") is None
        assert find_quote(s, doc.id, "totally absent quote zzzqqq") is None


def test_persist_document_sha_dedupe():
    pdf = _pdf_bytes(["Duplicate document test"])
    with SessionLocal() as s:
        case, _ = upsert_case_from_dispute(s, DISPUTE)
        s.commit()
        d1, n1 = persist_document(s, case.id, pdf, "a.pdf", origin="SEED")
        s.commit()
        d2, n2 = persist_document(s, case.id, pdf, "a-again.pdf", origin="SEED")
        s.commit()
        assert d1.id == d2.id and n2 == 0
