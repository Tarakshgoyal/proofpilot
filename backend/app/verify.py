"""Verifier + contradiction engine — FINAL_PLAN.md §10.

Every claim the agent proposed is checked by PLAIN CODE against the evidence
graph. A claim ends VERIFIED / UNSUPPORTED / CONTRADICTED. Only VERIFIED
claims can ever reach a submission packet (Day 3 enforces this at build time).

Contradiction rules run over facts (not model output) — deterministic:
  R1 ADDR_MISMATCH            carrier ship-to != order ship-to   (BLOCKING)
  R2 DELIVERY_AFTER_DISPUTE   delivered after dispute opened     (BLOCKING)
  R3 AMOUNT_EXCEEDS_ORDER     disputed amount > order amount     (BLOCKING)
  R4 REFUND_CONFLICT          refund on file + buyer says none   (BLOCKING)
  R5 PROMPT_INJECTION         injection text in buyer thread     (REVIEW)
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy.orm import Session

from .docs_pipeline import find_quote
from .models import Case, Claim, Contradiction, Fact

INJECTION_RE = re.compile(
    r"ignore (?:your |all |previous )?(?:previous |prior )?instructions|"
    r"system override|administrator mode|you are now (?:authorized|in admin)|"
    r"disregard (?:all|your|previous)", re.IGNORECASE)
NON_RECEIPT_RE = re.compile(r"never received|did not receive|not received|"
                            r"no refund|wasn'?t refunded", re.IGNORECASE)

ADDR_FIELDS = ("line1", "city", "state", "postal_code", "country")


def _facts(session: Session, case_id: str) -> dict[str, Fact]:
    return {f.kind: f for f in session.scalars(
        sa.select(Fact).where(Fact.case_id == case_id)).all()}


def _norm_addr(a: dict | None) -> tuple | None:
    if not a:
        return None
    return tuple(str(a.get(k, "")).strip().lower() for k in ADDR_FIELDS)


def _iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:  # engines like SQLite drop tz — treat as UTC
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


# ------------------------------------------------------------ claim checkers
def check_claim(session: Session, case: Case, claim: Claim,
                facts: dict[str, Fact]) -> tuple[str, str]:
    """Return (status, reason). status in VERIFIED|UNSUPPORTED|CONTRADICTED."""
    ct = claim.claim_type
    cited_exist = all(fid in {f.id for f in facts.values()} for fid in claim.fact_ids)
    if claim.fact_ids and not cited_exist:
        return "UNSUPPORTED", "cites fact ids that do not exist in this case"

    if ct == "DELIVERED_ON":
        f = facts.get("DELIVERED_AT")
        if not f:
            return "UNSUPPORTED", "no carrier delivery fact on file"
        delivered = _iso(f.value.get("v"))
        opened = _iso((case.raw_snapshot or {}).get("create_time"))
        if delivered and opened and delivered >= opened:
            return "CONTRADICTED", "delivery date is after the dispute was opened"
        return "VERIFIED", "carrier delivery fact predates the dispute"

    if ct == "ADDRESS_MATCH":
        order_addr = _norm_addr(facts.get("ORDER_SHIP_TO").value.get("v")
                                if facts.get("ORDER_SHIP_TO") else None)
        ship_addr = _norm_addr(facts.get("SHIPMENT_SHIP_TO").value.get("v")
                               if facts.get("SHIPMENT_SHIP_TO") else None)
        if not order_addr or not ship_addr:
            return "UNSUPPORTED", "missing order or shipment address fact"
        if order_addr == ship_addr:
            return "VERIFIED", "carrier ship-to matches order ship-to"
        return "CONTRADICTED", "carrier ship-to differs from order ship-to"

    if ct == "TRACKING_ASSIGNED":
        f = facts.get("SHIPMENT_TRACKING_NUMBER")
        if f and str(f.value.get("v") or "").strip():
            return "VERIFIED", "tracking number on file for the order"
        return "UNSUPPORTED", "no tracking number fact on file"

    if ct == "REFUND_ISSUED":
        refund_kinds = [k for k in facts if k.startswith("REFUND")]
        if refund_kinds:
            return "VERIFIED", "refund record on file: " + ", ".join(refund_kinds)
        return "UNSUPPORTED", "no refund record on file"

    if ct in ("POLICY_QUOTE", "DOC_QUOTE", "ITEM_AS_LISTED"):
        refs = claim.span_ids or []
        if not refs:
            return "UNSUPPORTED", "no document quote attached to the claim"
        ref = refs[0]
        hit = find_quote(session, ref.get("document_id", ""), ref.get("quote", ""))
        if hit is None:
            return "UNSUPPORTED", "quote not found in cited document (locator miss)"
        return "VERIFIED", f"quote located ({hit.match_type}, p{hit.page})"

    # OTHER / unknown: verified only if it cites at least one existing fact
    if claim.fact_ids and cited_exist:
        return "VERIFIED", "cited facts exist"
    return "UNSUPPORTED", "no verifiable citation"


# -------------------------------------------------------- contradiction rules
def detect_contradictions(session: Session, case: Case,
                          facts: dict[str, Fact]) -> list[dict]:
    found: list[dict] = []

    def add(rule: str, severity: str, explanation: str, a: str = "", b: str = ""):
        found.append({"rule": rule, "severity": severity,
                      "explanation": explanation, "fact_a": a, "fact_b": b})

    order_addr = _norm_addr(facts["ORDER_SHIP_TO"].value.get("v")
                            if "ORDER_SHIP_TO" in facts else None)
    ship_addr = _norm_addr(facts["SHIPMENT_SHIP_TO"].value.get("v")
                           if "SHIPMENT_SHIP_TO" in facts else None)
    if order_addr and ship_addr and order_addr != ship_addr:
        diff = [ADDR_FIELDS[i] for i in range(len(ADDR_FIELDS))
                if order_addr[i] != ship_addr[i]]
        add("ADDR_MISMATCH", "BLOCKING",
            f"carrier ship-to differs from order ship-to (fields: {', '.join(diff)})",
            facts.get("ORDER_SHIP_TO").id if "ORDER_SHIP_TO" in facts else "",
            facts.get("SHIPMENT_SHIP_TO").id if "SHIPMENT_SHIP_TO" in facts else "")

    delivered = _iso(facts["DELIVERED_AT"].value.get("v")
                     if "DELIVERED_AT" in facts else None)
    opened = _iso((case.raw_snapshot or {}).get("create_time"))
    if delivered and opened and delivered >= opened:
        add("DELIVERY_AFTER_DISPUTE", "BLOCKING",
            f"delivery ({delivered.date()}) is on/after the dispute was opened "
            f"({opened.date()})",
            facts["DELIVERED_AT"].id if "DELIVERED_AT" in facts else "")

    order_amt = (facts.get("ORDER_AMOUNT_MINOR").value.get("v", {}).get("minor")
                 if facts.get("ORDER_AMOUNT_MINOR") else None)
    if order_amt is not None and case.amount_minor > order_amt:
        add("AMOUNT_EXCEEDS_ORDER", "BLOCKING",
            f"disputed amount ({case.amount_minor}) exceeds the order amount ({order_amt})",
            facts["ORDER_AMOUNT_MINOR"].id if "ORDER_AMOUNT_MINOR" in facts else "")

    has_refund = any(k.startswith("REFUND") for k in facts)
    thread = facts.get("SUPPORT_THREAD")
    buyer_msgs = [m.get("text", "") for m in
                  ((thread.value.get("v") or []) if thread else [])
                  if m.get("from") == "BUYER"]
    if has_refund and any(NON_RECEIPT_RE.search(t) for t in buyer_msgs):
        add("REFUND_CONFLICT", "BLOCKING",
            "a refund record exists while the buyer claims none was issued",
            thread.id if thread else "")

    for t in buyer_msgs:
        if INJECTION_RE.search(t):
            add("PROMPT_INJECTION", "REVIEW",
                "buyer message contains instruction-like text; treated as data, "
                "not instructions (no capability was exposed)",
                thread.id if thread else "")
            break
    return found


# ------------------------------------------------------------------- runner
def run_verifier(session: Session, case: Case) -> dict:
    facts = _facts(session, case.id)
    claims = session.scalars(sa.select(Claim).where(Claim.case_id == case.id)).all()
    counts = {"VERIFIED": 0, "UNSUPPORTED": 0, "CONTRADICTED": 0}
    for claim in claims:
        status, reason = check_claim(session, case, claim, facts)
        claim.status = status
        claim.reason = reason
        counts[status] += 1

    session.query(Contradiction).filter(Contradiction.case_id == case.id).delete()
    found = detect_contradictions(session, case, facts)
    for c in found:
        session.add(Contradiction(case_id=case.id, **c))
    blocking = sum(1 for c in found if c["severity"] == "BLOCKING")
    session.flush()
    return {**counts, "contradictions": len(found), "blocking": blocking}
