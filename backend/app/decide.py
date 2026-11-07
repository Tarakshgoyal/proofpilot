"""Decision engine — FINAL_PLAN.md §11.

Transparent heuristic probability `p` (hand-set weights, printed in the
rationale — NEVER presented as a calibrated prediction), then expected value:

    fightEV = p·A − (1−p)·f − w        (minor units)
    foldEV  = −A
    fight   if fightEV > foldEV  and no BLOCKING contradiction

A = disputed amount, f = dispute fee assumption, w = merchant time cost.
Both assumptions are config-visible and labeled as scenario assumptions.
"""
from __future__ import annotations

import os

import sqlalchemy as sa
from sqlalchemy.orm import Session

from .models import Case, Claim, Contradiction, Decision

# hand-set weights — visible on purpose
W_BASE = 0.35
W_DELIVERED = 0.30
W_TRACKING = 0.15
W_ADDRESS = 0.10
PENALTY_BLOCKING = 0.40
PENALTY_REJECTED = 0.10   # per rejected claim beyond 2, capped

DEFAULT_FEE_MINOR = 2000   # $20.00 assumed dispute fee
DEFAULT_TIME_MINOR = 1500  # $15.00 assumed merchant time cost


def compute_p_win(verified: list[str], rejected: int, blocking: int) -> tuple[float, str]:
    p = W_BASE
    why = [f"base {W_BASE:.2f}"]
    if "DELIVERED_ON" in verified:
        p += W_DELIVERED
        why.append(f"+{W_DELIVERED:.2f} carrier delivery verified")
    if "TRACKING_ASSIGNED" in verified:
        p += W_TRACKING
        why.append(f"+{W_TRACKING:.2f} tracking on file")
    if "ADDRESS_MATCH" in verified:
        p += W_ADDRESS
        why.append(f"+{W_ADDRESS:.2f} addresses match")
    if blocking:
        p -= PENALTY_BLOCKING
        why.append(f"−{PENALTY_BLOCKING:.2f} {blocking} BLOCKING contradiction(s)")
    excess = max(0, rejected - 2)
    if excess:
        pen = min(PENALTY_REJECTED * excess, 0.20)
        p -= pen
        why.append(f"−{pen:.2f} {rejected} rejected claims")
    p = max(0.02, min(0.98, p))
    return p, "; ".join(why)


def decide(session: Session, case: Case) -> Decision:
    """Compute + persist the decision for a case (one per analysis run)."""
    claims = session.scalars(sa.select(Claim).where(Claim.case_id == case.id)).all()
    verified_types = [c.claim_type for c in claims if c.status == "VERIFIED"]
    rejected = sum(1 for c in claims if c.status in ("UNSUPPORTED", "CONTRADICTED"))
    blocking = session.scalar(sa.select(sa.func.count()).select_from(Contradiction)
                              .where(Contradiction.case_id == case.id,
                                     Contradiction.severity == "BLOCKING")) or 0
    p, rationale = compute_p_win(verified_types, rejected, blocking)

    fee = int(os.getenv("DISPUTE_FEE_MINOR", DEFAULT_FEE_MINOR))
    time_cost = int(os.getenv("MERCHANT_TIME_MINOR", DEFAULT_TIME_MINOR))
    A = case.amount_minor
    fight_ev = round(p * A - (1 - p) * fee - time_cost)
    fold_ev = -A
    if blocking:
        action = "MANUAL_REVIEW"
        rationale += "; BLOCKED: resolve contradictions before any submission"
    else:
        action = "FIGHT" if fight_ev > fold_ev else "ACCEPT"

    decision = Decision(
        case_id=case.id, recommended_action=action, p_win=round(p, 4),
        ev_fight=fight_ev, ev_fold=fold_ev,
        rationale=(f"{rationale}. Scenario assumptions, not calibrated predictions: "
                   f"fee={fee/100:.2f} {case.currency}, time={time_cost/100:.2f} "
                   f"{case.currency}, p={p:.2f} (transparent heuristic)."),
        model="heuristic_v1", prompt_version="d3v1",
    )
    session.add(decision)
    session.flush()
    return decision
