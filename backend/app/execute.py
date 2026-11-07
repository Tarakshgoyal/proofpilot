"""Plans, approval, execution — FINAL_PLAN.md §12.

The plan binds: case version, verified claims snapshot, the exact provider
payload, attachment hashes. plan_hash = SHA-256(canonicalized plan).
Approval is hash+version verified; replay returns the existing action.
The executor re-validates against FRESH provider state before dispatch,
records DISPATCHING before the network call, and never blindly retries an
ambiguous write (EXECUTION_UNKNOWN). Audit rows are hash-chained.

Guarantee (honest, by design): no blind duplicate dispatch, durable operation
tracking, explicit handling of uncertainty.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy.orm import Session

from .collector import canonical_hash
from .models import Action, AuditLog, Case, Claim, Contradiction, Fact, Plan

# ------------------------------------------------------------------ audit
def _row_hash(prev_hash: str, actor: str, event: str, payload: dict) -> str:
    return hashlib.sha256((prev_hash + canonical_hash(
        {"actor": actor, "event": event, "payload": payload})).encode()).hexdigest()


def audit(session: Session, case_id: str, actor: str, event: str,
          payload: dict | None = None) -> AuditLog:
    """Append a hash-chained audit row. hash covers prev link + row content."""
    payload = payload or {}
    prev = session.scalar(sa.select(AuditLog).where(AuditLog.case_id == case_id)
                          .order_by(AuditLog.seq.desc()).limit(1))
    prev_hash = prev.hash if prev else ""
    row = AuditLog(case_id=case_id, actor=actor, event=event,
                   payload=payload, prev_hash=prev_hash,
                   hash=_row_hash(prev_hash, actor, event, payload))
    session.add(row)
    session.flush()
    return row


def verify_chain(session: Session, case_id: str) -> dict:
    rows = session.scalars(sa.select(AuditLog).where(AuditLog.case_id == case_id)
                           .order_by(AuditLog.seq)).all()
    prev = ""
    for r in rows:
        if r.prev_hash != prev:
            return {"valid": False, "broken_at_seq": r.seq, "reason": "prev_hash mismatch"}
        if r.hash != _row_hash(r.prev_hash, r.actor, r.event, r.payload):
            return {"valid": False, "broken_at_seq": r.seq, "reason": "row content tampered"}
        prev = r.hash
    return {"valid": True, "entries": len(rows), "head": prev[:16] + "…"}


# ------------------------------------------------------------------ plans
def build_plan(session: Session, case: Case) -> Plan:
    """Create the next immutable plan version for a case (AWAITING_APPROVAL)."""
    from .packet import build_packet

    claims = session.scalars(sa.select(Claim).where(Claim.case_id == case.id)).all()
    verified = [c for c in claims if c.status == "VERIFIED"]
    blocking = session.scalar(sa.select(sa.func.count()).select_from(Contradiction)
                              .where(Contradiction.case_id == case.id,
                                     Contradiction.severity == "BLOCKING")) or 0
    if blocking:
        raise ValueError(f"EVIDENCE_INCOMPLETE: {blocking} BLOCKING contradiction(s) "
                         "must be resolved before a plan can be built")
    if not verified:
        raise ValueError("EVIDENCE_INCOMPLETE: no VERIFIED claims to submit")

    tracking = next((f for f in session.scalars(
        sa.select(Claim).where(Claim.case_id == case.id,
                               Claim.claim_type == "TRACKING_ASSIGNED"))), None)
    facts = {f.kind: f for f in session.scalars(
        sa.select(Fact).where(Fact.case_id == case.id)).all()}
    tracking_no = ""
    carrier = ""
    if "SHIPMENT_TRACKING_NUMBER" in facts:
        tracking_no = str(facts["SHIPMENT_TRACKING_NUMBER"].value.get("v") or "")
    if "SHIPMENT_CARRIER" in facts:
        carrier = str(facts["SHIPMENT_CARRIER"].value.get("v") or "")

    # The exact provider payload. The endpoint is decided at EXECUTION time by
    # the dispute's live HATEOAS links; the payload content is fixed here.
    payload = {
        "dispute_id": case.dispute_id,
        "input": {
            "evidence_type": "PROOF_OF_FULFILLMENT",
            "tracking_info": [{"carrier_name": carrier or "Carrier",
                               "tracking_number": tracking_no}],
            "notes": "Carrier records confirm shipment and delivery for this order. "
                     "See attached evidence packet.",
        },
        "attachments": [{"filename": "evidence_packet.pdf", "mime": "application/pdf"}],
        "verified_claim_ids": [c.id for c in verified],
        "case_version": case.version,
    }
    _, packet_hash, _ = build_packet(session, case, payload)
    version = (session.scalar(sa.select(sa.func.max(Plan.version))
                              .where(Plan.case_id == case.id)) or 0) + 1
    plan = Plan(case_id=case.id, version=version, payload=payload,
                payload_hash=canonical_hash(payload), packet_hash=packet_hash,
                status="AWAITING_APPROVAL")
    session.add(plan)
    session.flush()
    audit(session, case.id, "system", "plan_created",
          {"plan_id": plan.id, "version": version,
           "payload_hash": plan.payload_hash, "packet_hash": packet_hash})
    return plan


def approve_plan(session: Session, plan: Plan, approver: str,
                 expected_plan_hash: str, expected_case_version: int) -> tuple[Plan, Action | None]:
    """Hash+version-bound approval. Idempotent: replays return the existing action."""
    existing = session.scalar(sa.select(Action).where(
        Action.plan_id == plan.id, Action.action_type == "submit_evidence"))
    if existing is not None:
        return plan, existing  # replay (double-click or re-approve): never a second write
    if plan.status not in ("AWAITING_APPROVAL", "APPROVED"):
        raise ValueError(f"PLAN_STALE: plan is {plan.status}; build a new version")
    case = session.get(Case, plan.case_id)
    if expected_plan_hash != plan.payload_hash:
        raise ValueError("PLAN_HASH_MISMATCH: review the exact payload before approving")
    if expected_case_version != case.version:
        raise ValueError("CASE_VERSION_CONFLICT: evidence changed — new plan required")
    plan.status = "APPROVED"
    audit(session, case.id, approver, "plan_approved",
          {"plan_id": plan.id, "plan_hash": plan.payload_hash,
           "case_version": case.version})
    # outbox-in-transaction: approval and its action row commit together
    action = Action(plan_id=plan.id, action_type="submit_evidence",
                    payload=plan.payload, idempotency_key=plan.payload_hash,
                    status="QUEUED")
    session.add(action)
    session.flush()
    return plan, action


def mark_stale_plans(session: Session, case: Case, reason: str) -> int:
    """Evidence changed under an approved plan -> STALE, never execute it."""
    n = 0
    for p in session.scalars(sa.select(Plan).where(
            Plan.case_id == case.id, Plan.status.in_(("AWAITING_APPROVAL", "APPROVED")))).all():
        p.status = "STALE"
        audit(session, case.id, "system", "plan_stale",
              {"plan_id": p.id, "version": p.version, "reason": reason})
        n += 1
    return n


# ---------------------------------------------------------------- executor
def execute_action(session: Session, action: Action) -> dict:
    """Validate against fresh provider state, dispatch ONCE, record everything."""
    import requests as _requests  # local import keeps module import light

    from .gateway import PayPalGateway
    from .paypal_client import PayPalError, save_fixture

    plan = session.get(Plan, action.plan_id)
    case = session.get(Case, plan.case_id)
    approver_note = {"action_id": action.id, "plan_id": plan.id}

    # never blind-retry: an action that already reached the wire is terminal
    # until a human reconciles it (FINAL_PLAN §12).
    if action.status in ("EXECUTION_UNKNOWN", "COMPLETED") or action.executed_at:
        action.response = dict(action.response or {},
                               note="refused re-dispatch of an already-dispatched action")
        return {"result": "refused", "reason": f"action_{action.status}"}

    if plan.status != "APPROVED":
        action.status = "FAILED"
        action.response = {"error": f"plan is {plan.status}"}
        return {"result": "refused", "reason": f"plan_{plan.status}"}
    if case.version != plan.payload.get("case_version"):
        plan.status = "STALE"
        action.status = "FAILED"
        action.response = {"error": "CASE_VERSION_CONFLICT"}
        audit(session, case.id, "executor", "execution_blocked", {**approver_note,
              "reason": "case version moved after approval"})
        return {"result": "refused", "reason": "CASE_VERSION_CONFLICT"}

    # fresh provider read — notifications are signals; this is the state
    gateway = PayPalGateway()
    action.status = "VALIDATING"
    session.commit()
    try:
        dispute = gateway.get_dispute(case.dispute_id)
    except PayPalError as exc:
        action.status = "FAILED"
        action.response = {"error": f"provider read failed: {exc}"}
        return {"result": "failed", "reason": "provider_read"}

    link = gateway.client.link(dispute, "provide-evidence") or \
        gateway.client.link(dispute, "provide-supporting-info")
    if link is None:
        action.status = "FAILED"
        action.response = {"error": "ACTION_NOT_AVAILABLE",
                           "status": dispute.get("status"),
                           "links": gateway.client.available_actions(dispute)}
        audit(session, case.id, "executor", "execution_blocked",
              {**approver_note, "reason": "no evidence action link on live dispute"})
        return {"result": "refused", "reason": "ACTION_NOT_AVAILABLE"}

    endpoint = ("provide-evidence" if link["rel"].replace("_", "-") == "provide-evidence"
                else "provide-supporting-info")
    file_part = "file" if endpoint == "provide-evidence" else "supporting document"

    # dispatch state BEFORE the network call — an unknown outcome is never lost
    action.status = "DISPATCHING"
    session.commit()

    from .packet import STORAGE
    packet_path = STORAGE / f"{case.id}_{plan.payload['case_version']}.pdf"
    try:
        with open(packet_path, "rb") as fh:
            resp = _requests.post(
                f"{gateway.client.base_url}/v1/customer/disputes/{case.dispute_id}/{endpoint}",
                headers={"Authorization": f"Bearer {gateway.client.token()}"},
                files={"input": (None, json.dumps(plan.payload["input"]),
                                 "application/json"),
                       file_part: ("evidence_packet.pdf", fh, "application/pdf")},
                timeout=60,
            )
    except _requests.RequestException as exc:
        action.status = "EXECUTION_UNKNOWN"
        action.response = {"error": f"dispatch error: {exc}"}
        audit(session, case.id, "executor", "execution_unknown", approver_note)
        return {"result": "execution_unknown"}

    body = {}
    try:
        body = resp.json()
    except ValueError:
        body = {"raw": resp.text[:500]}
    save_fixture(f"20_execute_{case.dispute_id}_{plan.version}",
                 "POST", endpoint, resp.status_code, body,
                 note=f"plan {plan.id} v{plan.version} via {endpoint}")

    if resp.status_code >= 500 or resp.status_code in (408, 429):
        action.status = "EXECUTION_UNKNOWN"
        action.response = body
        audit(session, case.id, "executor", "execution_unknown",
              {**approver_note, "http": resp.status_code})
        return {"result": "execution_unknown", "http": resp.status_code}

    if resp.status_code >= 400:
        action.status = "FAILED"
        action.response = body
        audit(session, case.id, "executor", "execution_failed",
              {**approver_note, "http": resp.status_code})
        return {"result": "failed", "http": resp.status_code, "body": body}

    plan.status = "EXECUTED"
    action.status = "COMPLETED"
    action.executed_by = "executor"
    action.executed_at = datetime.now(timezone.utc)
    action.response = {"http": resp.status_code, "body": body, "endpoint": endpoint}
    audit(session, case.id, "executor", "evidence_submitted",
          {**approver_note, "endpoint": endpoint, "http": resp.status_code})
    return {"result": "ok", "http": resp.status_code, "endpoint": endpoint}
