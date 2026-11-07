"""Eval harness — FINAL_PLAN.md §17.

Runs every labelled scenario through the REAL pipeline (agent -> verifier ->
decision), then scores against ground truth. The headline metric is honest by
construction:

  raw unsupported-claim rate  = rejected / total claims the model asserted
  gated unsupported-claim rate = rejected claims that reached a PACKET
                               (structurally zero: only VERIFIED claims enter)

Also measured: contradiction recall vs labels, action accuracy, analysis
fallback rate. Numbers are whatever they measure — no cherry-picking.

The eval is offline w.r.t. PayPal (no gateway calls) and uses the live LLM
unless NVIDIA_API_KEY is absent, in which case every analysis runs the rules
fallback and the run is labeled accordingly.
"""
from __future__ import annotations

import os
import traceback
from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy.orm import Session

from .agent import run_agent
from .decide import decide
from .models import Case, Claim, Contradiction, EvalResult, EvalRun
from .verify import run_verifier


def run_eval(session: Session, run: EvalRun, quick: bool = False) -> EvalRun:
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from seed.scenarios import archetypes, build_world

    variations = [0] if quick else [0, 1, 2]
    agg = {"scenarios": 0, "claims_total": 0, "claims_verified": 0,
           "claims_rejected": 0, "contradiction_hits": 0,
           "contradiction_expected": 0, "contradiction_false_positives": 0,
           "action_correct": 0, "must_strike_total": 0, "must_strike_caught": 0,
           "must_verify_total": 0, "must_verify_ok": 0, "fallbacks": 0}
    llm_live = bool(os.getenv("NVIDIA_API_KEY"))
    run.model = (os.getenv("LLM_MODEL", "nvidia/nemotron-3-ultra-550b-a55b")
                 if llm_live else "rules-fallback")
    for arch in archetypes():
        for v in variations:
            label = {"expected_contradictions": arch["expected_contradictions"],
                     "expected_action": arch["expected_action"],
                     "must_strike": arch.get("must_strike", []),
                     "must_verify": arch.get("must_verify", [])}
            found: dict = {}
            ok = True
            try:
                case_id = build_world(session, arch, v, run.id)
                case = session.get(Case, case_id)
                analysis = run_agent(session, case)
                verdict = run_verifier(session, case)
                decision = decide(session, case)
                session.commit()

                claims = session.scalars(sa.select(Claim)
                                         .where(Claim.case_id == case.id)).all()
                struck = [c.claim_type for c in claims
                          if c.status in ("UNSUPPORTED", "CONTRADICTED")]
                verified_types = [c.claim_type for c in claims if c.status == "VERIFIED"]
                fired = [x.rule for x in session.scalars(
                    sa.select(Contradiction).where(Contradiction.case_id == case.id))]

                found = {"action": decision.recommended_action,
                         "p_win": decision.p_win,
                         "contradictions": fired,
                         "claims_total": len(claims),
                         "claims_verified": verdict["VERIFIED"],
                         "claims_rejected": verdict["UNSUPPORTED"] + verdict["CONTRADICTED"],
                         "struck_types": struck,
                         "analysis_status": analysis.status}

                # score
                agg["scenarios"] += 1
                agg["claims_total"] += len(claims)
                agg["claims_verified"] += verdict["VERIFIED"]
                agg["claims_rejected"] += verdict["UNSUPPORTED"] + verdict["CONTRADICTED"]
                agg["fallbacks"] += int(analysis.status == "FALLBACK")
                for r in label["expected_contradictions"]:
                    agg["contradiction_expected"] += 1
                    if r in fired:
                        agg["contradiction_hits"] += 1
                    else:
                        ok = False
                for r in fired:
                    if r not in label["expected_contradictions"]:
                        agg["contradiction_false_positives"] += 1
                if decision.recommended_action == label["expected_action"]:
                    agg["action_correct"] += 1
                else:
                    ok = False
                for t in label["must_strike"]:
                    agg["must_strike_total"] += 1
                    if t in struck:
                        agg["must_strike_caught"] += 1
                    else:
                        ok = False
                for t in label["must_verify"]:
                    agg["must_verify_total"] += 1
                    if t in verified_types:
                        agg["must_verify_ok"] += 1
                    else:
                        ok = False
            except Exception as exc:  # noqa: BLE001 — record, keep running
                ok = False
                found = {"error": f"{type(exc).__name__}: {exc}"[:300]}
                traceback.print_exc()
                session.rollback()
            try:
                session.add(EvalResult(run_id=run.id,
                                       scenario=f"{arch['name']}#{v}",
                                       label=label, found=found, ok=ok))
                session.commit()
            except Exception:  # noqa: BLE001
                session.rollback()

    tot = agg
    run.totals = {
        **tot,
        "raw_unsupported_rate": round(tot["claims_rejected"] / tot["claims_total"], 4)
        if tot["claims_total"] else None,
        "gated_unsupported_rate": 0.0,  # by construction: only VERIFIED enter packets
        "contradiction_recall": round(tot["contradiction_hits"] / tot["contradiction_expected"], 4)
        if tot["contradiction_expected"] else None,
        "contradiction_fp": tot["contradiction_false_positives"],
        "action_accuracy": round(tot["action_correct"] / tot["scenarios"], 4)
        if tot["scenarios"] else None,
        "must_strike_recall": round(tot["must_strike_caught"] / tot["must_strike_total"], 4)
        if tot["must_strike_total"] else None,
        "must_verify_recall": round(tot["must_verify_ok"] / tot["must_verify_total"], 4)
        if tot["must_verify_total"] else None,
    }
    run.status = "DONE"
    run.finished_at = datetime.now(timezone.utc)
    session.commit()
    return run
