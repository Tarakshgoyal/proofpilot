"""Evidence collector — FINAL_PLAN.md §4 Phases A–C.

Pulls a dispute through the gateway, upserts the case, and builds the
evidence graph: immutable, hash-stamped facts from PAYPAL_API and the
merchant back office (MERCHANT_DB). A re-fetch creates new facts only when
the content actually changed (dedupe on kind + source + value-hash).
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Session

from .models import Case, Fact, Order, Policy, Shipment, SupportThread

MERCHANT_ID = "N999U2WKXLHAA"  # sandbox business account (Day 0)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def canonical_hash(payload) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def money_to_minor(value: str | int | float, currency: str = "USD") -> int:
    """'25.00' -> 2500 (minor units, Decimal-safe)."""
    return int((Decimal(str(value)) * Decimal(100)).quantize(Decimal(1)))


def parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


# ------------------------------------------------------------------ case
def snapshot_hash(dispute: dict) -> str:
    return canonical_hash(dispute)


def upsert_case_from_dispute(session: Session, dispute: dict) -> tuple[Case, bool]:
    """Idempotent upsert keyed on dispute_id. Returns (case, changed)."""
    dispute_id = dispute["dispute_id"]
    amount = dispute.get("dispute_amount") or {}
    raw_hash = snapshot_hash(dispute)
    case = session.scalar(sa.select(Case).where(Case.dispute_id == dispute_id))
    changed = case is None or case.raw_snapshot and canonical_hash(case.raw_snapshot) != raw_hash
    if case is None:
        case = Case(
            merchant_id=MERCHANT_ID,
            dispute_id=dispute_id,
            reason=dispute.get("reason", "UNKNOWN"),
            stage=dispute.get("dispute_life_cycle_stage", "UNKNOWN"),
            provider_status=dispute.get("status", "UNKNOWN"),
            workflow_status="NEW",
            amount_minor=money_to_minor(amount.get("value", 0), amount.get("currency_code", "USD")),
            currency=amount.get("currency_code", "USD"),
            response_due_at=parse_iso(dispute.get("seller_response_due_date")),
            raw_snapshot=dispute,
            version=1,
        )
        session.add(case)
        case.workflow_status = "COLLECTING"
    else:
        case.reason = dispute.get("reason", case.reason)
        case.stage = dispute.get("dispute_life_cycle_stage", case.stage)
        case.provider_status = dispute.get("status", case.provider_status)
        amt = dispute.get("dispute_amount") or {}
        if amt.get("value") is not None:
            case.amount_minor = money_to_minor(amt["value"], amt.get("currency_code", case.currency))
            case.currency = amt.get("currency_code", case.currency)
        case.response_due_at = parse_iso(dispute.get("seller_response_due_date")) or case.response_due_at
        if changed:
            case.raw_snapshot = dispute
            case.version += 1
            if case.workflow_status in ("NEW",):
                case.workflow_status = "COLLECTING"
    session.flush()
    return case, changed


# ------------------------------------------------------------------ facts
def _add_fact(session: Session, case_id: str, kind: str, value, source_type: str,
              source_ref: dict, sha256: str) -> bool:
    """Insert a fact unless an identical one (same kind+source+value) exists.

    Facts are immutable: a changed value creates a new fact row.
    """
    new_hash = canonical_hash(value)
    existing = session.scalars(
        sa.select(Fact).where(Fact.case_id == case_id, Fact.kind == kind,
                              Fact.source_type == source_type)
    ).all()
    if any(canonical_hash(f.value.get("v")) == new_hash for f in existing):
        return False
    session.add(Fact(
        case_id=case_id,
        kind=kind,
        value={"v": value},
        source_type=source_type,
        source_ref=source_ref,
        retrieved_at=now_utc(),
        sha256=sha256,
    ))
    return True


def facts_from_dispute(session: Session, case: Case, dispute: dict) -> int:
    raw_hash = snapshot_hash(dispute)
    ref = {"endpoint": f"/v1/customer/disputes/{case.dispute_id}"}
    n = 0
    simple = {
        "DISPUTE_REASON": dispute.get("reason"),
        "DISPUTE_STATUS": dispute.get("status"),
        "DISPUTE_STAGE": dispute.get("dispute_life_cycle_stage"),
        "DISPUTE_CHANNEL": dispute.get("dispute_channel"),
        "SELLER_RESPONSE_DUE": dispute.get("seller_response_due_date"),
    }
    for kind, val in simple.items():
        if val:
            n += _add_fact(session, case.id, kind, val, "PAYPAL_API", ref, raw_hash)
    amt = dispute.get("dispute_amount") or {}
    if amt.get("value"):
        n += _add_fact(session, case.id, "DISPUTE_AMOUNT_MINOR",
                  {"minor": money_to_minor(amt["value"]), "currency": amt.get("currency_code")},
                  "PAYPAL_API", ref, raw_hash)

    txns = dispute.get("disputed_transactions") or []
    if txns:
        n += _add_fact(session, case.id, "DISPUTED_TRANSACTION_IDS",
                  [{"buyer": t.get("buyer_transaction_id"),
                     "seller": t.get("seller_transaction_id")} for t in txns],
                  "PAYPAL_API", ref, raw_hash)

    outcome = dispute.get("dispute_outcome")
    if outcome:
        n += _add_fact(session, case.id, "DISPUTE_OUTCOME", outcome,
                       "PAYPAL_API", ref, raw_hash)
    return n


def facts_from_back_office(session: Session, case: Case) -> int:
    """Join the dispute to merchant records via the seller transaction (capture) id.

    Only exact identifier matches count (FINAL_PLAN.md Phase B) — never
    amount/date/email matching.
    """
    txn = ((case.raw_snapshot or {}).get("disputed_transactions") or [{}])[0]
    capture_id = txn.get("seller_transaction_id")
    if not capture_id:
        return 0
    n = 0
    order = session.scalar(sa.select(Order).where(Order.paypal_capture_id == capture_id))
    if order:
        ref = {"table": "orders", "id": order.id}
        h = canonical_hash({"order": order.id, "snapshot": [
            order.amount_minor, order.currency, order.shipping_address, order.items]})
        n += _add_fact(session, case.id, "ORDER_MATCHED",
                       {"merchant_order_id": order.merchant_order_id,
                        "match_method": "capture_id_exact"}, "MERCHANT_DB", ref, h)
        n += _add_fact(session, case.id, "ORDER_AMOUNT_MINOR",
                       {"minor": order.amount_minor, "currency": order.currency},
                       "MERCHANT_DB", ref, h)
        n += _add_fact(session, case.id, "ORDER_SHIP_TO", order.shipping_address,
                       "MERCHANT_DB", ref, h)
        n += _add_fact(session, case.id, "ORDER_ITEMS", order.items, "MERCHANT_DB", ref, h)
        shipment = session.scalar(sa.select(Shipment).where(Shipment.order_id == order.id))
        if shipment:
            sref = {"table": "shipments", "id": shipment.id}
            sh = canonical_hash({"shipment": shipment.id, "status": shipment.status,
                                 "delivered_at": str(shipment.delivered_at)})
            n += _add_fact(session, case.id, "SHIPMENT_CARRIER", shipment.carrier,
                           "MERCHANT_DB", sref, sh)
            n += _add_fact(session, case.id, "SHIPMENT_TRACKING_NUMBER",
                           shipment.tracking_number, "MERCHANT_DB", sref, sh)
            n += _add_fact(session, case.id, "SHIPMENT_STATUS", shipment.status,
                           "MERCHANT_DB", sref, sh)
            if shipment.delivered_at:
                n += _add_fact(session, case.id, "DELIVERED_AT",
                               shipment.delivered_at.isoformat(), "MERCHANT_DB", sref, sh)
            n += _add_fact(session, case.id, "SHIPMENT_SHIP_TO", shipment.ship_to,
                           "MERCHANT_DB", sref, sh)
        thread = session.scalar(
            sa.select(SupportThread).where(SupportThread.order_id == order.id))
        if thread:
            tref = {"table": "support_threads", "id": thread.id}
            th = canonical_hash({"thread": thread.id, "messages": thread.messages})
            n += _add_fact(session, case.id, "SUPPORT_THREAD", thread.messages,
                           "MERCHANT_DB", tref, th)
    policy = session.scalar(sa.select(Policy).order_by(Policy.version.desc()))
    if policy:
        pref = {"table": "policies", "id": policy.id}
        n += _add_fact(session, case.id, "POLICY_TEXT_SHA256", policy.sha256,
                       "MERCHANT_DB", pref, policy.sha256)
    return n


def collect_case(session: Session, case: Case, dispute: dict) -> dict:
    """Run the full collection for one case and advance the workflow state."""
    n1 = facts_from_dispute(session, case, dispute)
    n2 = facts_from_back_office(session, case)
    # Evidence graph complete -> ANALYSED (Day-2 agent enriches this state).
    if case.workflow_status in ("NEW", "COLLECTING"):
        case.workflow_status = "ANALYSED"
    return {"paypal_facts": n1, "backoffice_facts": n2,
            "workflow_status": case.workflow_status}
