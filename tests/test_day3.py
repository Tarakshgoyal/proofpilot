"""Day 3 tests: decision EV, plan hash-binding, approval idempotency, STALE,
executor refusals + EXECUTION_UNKNOWN, audit chain tamper detection.

Run:  .venv/Scripts/python -m pytest tests/ -q
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import sqlalchemy as sa  # noqa: E402

from backend.app.collector import collect_case, upsert_case_from_dispute  # noqa: E402
from backend.app.db import SessionLocal, init_db  # noqa: E402
from backend.app.decide import compute_p_win  # noqa: E402
from backend.app.docs_pipeline import persist_document  # noqa: E402
from backend.app.execute import (approve_plan, audit, build_plan,  # noqa: E402
                                 execute_action, mark_stale_plans, verify_chain)
from backend.app.models import Action, AuditLog, Case, Claim, Fact, Order, Plan, Shipment  # noqa: E402
from backend.app.verify import run_verifier  # noqa: E402

ADDR = {"line1": "10 Main St", "city": "Austin", "state": "TX",
        "postal_code": "78701", "country": "US"}


@pytest.fixture(scope="session", autouse=True)
def _db():
    init_db()
    yield


def _seed_verified_case(dispute_id="PP-T3-1", amount="25.00"):
    """Fresh case with verified claims + facts (no contradictions)."""
    dispute = {
        "dispute_id": dispute_id,
        "reason": "MERCHANDISE_OR_SERVICE_NOT_RECEIVED",
        "status": "WAITING_FOR_SELLER_RESPONSE",
        "dispute_life_cycle_stage": "INQUIRY",
        "create_time": "2026-11-06T10:00:00Z",
        "dispute_amount": {"currency_code": "USD", "value": amount},
        "disputed_transactions": [{"seller_transaction_id": f"CAP-{dispute_id}"}],
    }
    with SessionLocal() as s:
        case, _ = upsert_case_from_dispute(s, dispute)
        order = Order(merchant_order_id=f"ORD-{dispute_id}",
                      paypal_capture_id=f"CAP-{dispute_id}",
                      amount_minor=2500, currency="USD", items=[],
                      shipping_address=ADDR)
        s.add(order)
        s.flush()
        s.add(Shipment(order_id=order.id, carrier="T3 Carrier",
                       tracking_number=f"TRK-{dispute_id}", status="DELIVERED",
                       ship_to=ADDR,
                       delivered_at=datetime(2026, 11, 4, 12, tzinfo=timezone.utc)))
        s.commit()
        collect_case(s, case, dispute)
        s.commit()  # autoflush is off: commit so facts are visible to queries
        facts = {f.kind: f for f in s.scalars(
            sa.select(Fact).where(Fact.case_id == case.id)).all()}
        for ct, fk in (("DELIVERED_ON", "DELIVERED_AT"),
                       ("TRACKING_ASSIGNED", "SHIPMENT_TRACKING_NUMBER"),
                       ("ADDRESS_MATCH", None)):
            s.add(Claim(case_id=case.id, claim_type=ct, statement=f"t3 {ct}",
                        fact_ids=[facts[fk].id] if fk else [], status="PENDING"))
        s.commit()
        run_verifier(s, case)
        s.commit()
        return case.id


# ---------------------------------------------------------------- EV / decision
def test_p_win_weights():
    p_clean, why = compute_p_win(["DELIVERED_ON", "TRACKING_ASSIGNED", "ADDRESS_MATCH"],
                                 0, 0)
    assert abs(p_clean - 0.90) < 1e-9
    assert "carrier delivery verified" in why
    p_blocked, _ = compute_p_win(["DELIVERED_ON"], 0, 1)
    assert abs(p_blocked - 0.25) < 1e-9  # 0.35 + 0.30 - 0.40
    p_rej, _ = compute_p_win(["DELIVERED_ON"], 5, 0)
    assert p_rej < 0.60  # penalties applied, capped


def test_ev_and_gating():
    from backend.app.decide import decide

    with SessionLocal() as s:
        case = s.get(Case, _seed_verified_case("PP-T3-EV"))
        d = decide(s, case)
        s.commit()
        # A=2500, p=0.90, f=2000, w=1500 -> 2250-200-1500 = 550
        assert d.ev_fight == 550
        assert d.ev_fold == -2500
        assert d.recommended_action == "FIGHT"
        assert "not calibrated" in d.rationale


# ---------------------------------------------------------------- plans/approval
def test_plan_requires_verified_claims():
    with SessionLocal() as s:
        case, _ = upsert_case_from_dispute(s, {
            "dispute_id": "PP-T3-EMPTY", "reason": "X",
            "status": "WAITING_FOR_SELLER_RESPONSE",
            "dispute_life_cycle_stage": "INQUIRY",
            "dispute_amount": {"currency_code": "USD", "value": "10.00"},
            "disputed_transactions": [{"seller_transaction_id": "CAP-EMPTY"}],
        })
        s.commit()
        with pytest.raises(ValueError, match="EVIDENCE_INCOMPLETE"):
            build_plan(s, case)


def test_approve_hash_bound_and_idempotent():
    with SessionLocal() as s:
        cid = _seed_verified_case("PP-T3-AP")
        case = s.get(Case, cid)
        plan = build_plan(s, case)
        s.commit()
        assert plan.status == "AWAITING_APPROVAL"
        assert plan.payload_hash and plan.packet_hash
        assert plan.payload["case_version"] == case.version

        with pytest.raises(ValueError, match="PLAN_HASH_MISMATCH"):
            approve_plan(s, plan, "user", "wrong", case.version)
        with pytest.raises(ValueError, match="CASE_VERSION_CONFLICT"):
            approve_plan(s, plan, "user", plan.payload_hash, case.version + 5)

        plan1, action1 = approve_plan(s, plan, "user", plan.payload_hash, case.version)
        plan2, action2 = approve_plan(s, plan, "user", plan.payload_hash, case.version)  # double-click
        s.commit()
        assert plan1.status == "APPROVED"
        assert action1.id == action2.id, "replay must return the same action"
        n = len(s.scalars(sa.select(Action).where(Action.plan_id == plan.id)).all())
        assert n == 1


def test_stale_on_evidence_change():
    with SessionLocal() as s:
        cid = _seed_verified_case("PP-T3-ST")
        case = s.get(Case, cid)
        plan = build_plan(s, case)
        approve_plan(s, plan, "user", plan.payload_hash, case.version)
        s.commit()
        # evidence changes -> snapshot version bumps -> plans must go STALE
        changed = dict(case.raw_snapshot)
        changed["status"] = "UNDER_REVIEW"
        upsert_case_from_dispute(s, changed)
        s.commit()
        stale = mark_stale_plans(s, case, "test evidence change")
        s.commit()
        assert stale == 1
        assert s.get(Plan, plan.id).status == "STALE"


# ---------------------------------------------------------------- executor
class _FakeGW:
    """Gateway stand-in: honor a fixed set of HATEOAS rels."""

    def __init__(self, *, links=()):
        import types

        self._links = set(links)
        self.client = types.SimpleNamespace(
            base_url="https://fake.example",
            token=lambda: "fake-token",
            available_actions=lambda d: sorted(self._links),
            link=lambda d, rel: ({"rel": rel.replace("-", "_"),
                                  "href": "https://fake.example/action",
                                  "method": "POST"}
                                 if rel.replace("-", "_") in self._links else None),
        )

    def get_dispute(self, _):
        return {"status": "WAITING_FOR_SELLER_RESPONSE",
                "links": [{"rel": r, "href": "https://fake", "method": "POST"}
                          for r in self._links]}


def _approved_plan(dispute_id="PP-T3-X"):
    with SessionLocal() as s:
        cid = _seed_verified_case(dispute_id)
        case = s.get(Case, cid)
        plan = build_plan(s, case)
        plan, action = approve_plan(s, plan, "user", plan.payload_hash, case.version)
        s.commit()
        return plan.id, action.id, cid


def test_executor_refuses_when_link_missing(monkeypatch):
    monkeypatch.setattr("backend.app.gateway.PayPalGateway", _FakeGW)
    with SessionLocal() as s:
        pid, aid, _ = _approved_plan("PP-T3-NOLINK")
        res = execute_action(s, s.get(Action, aid))
        s.commit()
        assert res["result"] == "refused" and res["reason"] == "ACTION_NOT_AVAILABLE"
        assert s.get(Action, aid).status == "FAILED"


def test_executor_dispatch_and_success(monkeypatch):
    monkeypatch.setattr("backend.app.gateway.PayPalGateway",
                        lambda: _FakeGW(links=["provide_evidence"]))
    calls = []

    class FakeResp:
        status_code = 200
        text = "{}"

        def json(self): return {"ok": True}

    def fake_post(url, **kw):
        calls.append(url)
        return FakeResp()

    monkeypatch.setattr("requests.post", fake_post)
    with SessionLocal() as s:
        pid, aid, cid = _approved_plan("PP-T3-OK")
        # packet file must exist for the multipart attachment
        from backend.app.packet import build_packet
        case = s.get(Case, cid)
        plan = s.get(Plan, pid)
        build_packet(s, case, plan.payload)
        s.commit()
        res = execute_action(s, s.get(Action, aid))
        s.commit()
        assert res["result"] == "ok"
        assert len(calls) == 1, "exactly one dispatch"
        assert s.get(Action, aid).status == "COMPLETED"
        assert s.get(Plan, pid).status == "EXECUTED"


def test_executor_unknown_on_timeout(monkeypatch):
    monkeypatch.setattr("backend.app.gateway.PayPalGateway",
                        lambda: _FakeGW(links=["provide_evidence"]))
    import requests

    def boom(url, **kw):
        raise requests.Timeout("simulated timeout")

    monkeypatch.setattr(requests, "post", boom)
    with SessionLocal() as s:
        pid, aid, cid = _approved_plan("PP-T3-TO")
        from backend.app.packet import build_packet
        plan = s.get(Plan, pid)
        build_packet(s, s.get(Case, cid), plan.payload)
        s.commit()
        res = execute_action(s, s.get(Action, aid))
        s.commit()
        assert res["result"] == "execution_unknown"
        assert s.get(Action, aid).status == "EXECUTION_UNKNOWN"
        # never auto-resend: re-running the handler must not dispatch again
        res2 = execute_action(s, s.get(Action, aid))
        assert res2["result"] == "refused"


# ---------------------------------------------------------------- audit chain
def test_audit_chain_and_tamper():
    with SessionLocal() as s:
        cid = _seed_verified_case("PP-T3-AU")
        audit(s, cid, "t", "one", {"a": 1})
        audit(s, cid, "t", "two", {"b": 2})
        s.commit()
        assert verify_chain(s, cid)["valid"] is True
        row = s.scalar(sa.select(AuditLog).where(AuditLog.event == "one"))
        row.payload = {"tampered": True}
        s.commit()
        v = verify_chain(s, cid)
        assert v["valid"] is False
