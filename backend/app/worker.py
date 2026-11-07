"""Asyncio background worker — FINAL_PLAN.md §5 worker process.

Durable jobs in Postgres; a single-loop worker claims them with a lease.
Crash-safe: on startup, RUNNING jobs with expired leases are re-queued.

Run:  python -m backend.app.worker
"""
from __future__ import annotations

import asyncio
import os
import traceback
from datetime import datetime, timedelta, timezone

import sqlalchemy as sa

from .collector import collect_case, snapshot_hash, upsert_case_from_dispute
from .db import SessionLocal, init_db
from .docs_pipeline import parse_pdf
from .gateway import get_gateway
from .models import Action, Case, Document, Job, Span

LEASE_MINUTES = 5
POLL_SECONDS = 2.0


def enqueue(session, kind: str, payload: dict | None = None, dedupe: bool = True) -> Job:
    """Queue a job; with dedupe, skip if an identical QUEUED job exists."""
    payload = payload or {}
    if dedupe:
        from .collector import canonical_hash

        wanted = canonical_hash(payload)
        queued = session.scalars(sa.select(Job).where(
            Job.kind == kind, Job.status == "QUEUED")).all()
        if any(canonical_hash(j.payload) == wanted for j in queued):
            return next(j for j in queued if canonical_hash(j.payload) == wanted)
    job = Job(kind=kind, payload=payload)
    session.add(job)
    session.flush()
    return job


def recover_stale_jobs(session) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=LEASE_MINUTES)
    stale = session.scalars(sa.select(Job).where(
        Job.status == "RUNNING", Job.lease_expires_at < cutoff)).all()
    for job in stale:
        job.status = "QUEUED"
        job.attempts += 1
    return len(stale)


def claim_job(session) -> Job | None:
    job = session.scalar(sa.select(Job).where(Job.status == "QUEUED")
                         .order_by(Job.created_at).limit(1))
    if job is None:
        return None
    job.status = "RUNNING"
    job.attempts += 1
    job.lease_expires_at = datetime.now(timezone.utc) + timedelta(minutes=LEASE_MINUTES)
    session.commit()
    return job


# ----------------------------------------------------------------- handlers
def handle_sync_disputes(session, payload: dict) -> dict:
    gateway = get_gateway()
    items = gateway.list_disputes()
    results = {"seen": len(items), "collected": 0, "changed": 0}
    for item in items:
        dispute_id = item["dispute_id"]
        # notifications are signals, not state: always fetch the fresh detail
        dispute = gateway.get_dispute(dispute_id)
        case, changed = upsert_case_from_dispute(session, dispute)
        session.commit()
        if changed:
            from .execute import mark_stale_plans

            stale = mark_stale_plans(session, case, "dispute snapshot changed on re-sync")
            session.commit()
            results.setdefault("plans_staled", 0)
            results["plans_staled"] += stale
        enqueue(session, "collect_case", {"case_id": case.id, "dispute_id": dispute_id})
        results["collected"] += 1
        results["changed"] += int(changed)
        if os.getenv("APP_MODE", "fixture") == "fixture":
            # judge/demo convenience: analyse each replayed case offline
            enqueue(session, "investigate", {"case_id": case.id})
    session.commit()
    return results


def handle_collect_case(session, payload: dict) -> dict:
    case = session.scalar(sa.select(Case).where(Case.id == payload["case_id"]))
    if case is None:
        return {"error": "case_not_found"}
    stats = collect_case(session, case, case.raw_snapshot)
    # parse any unparsed documents attached to this case
    unparsed = session.scalars(sa.select(Document).where(
        Document.case_id == case.id, Document.parsed_at.is_(None),
        Document.parse_error == "")).all()
    for doc in unparsed:
        enqueue(session, "parse_document", {"document_id": doc.id})
    session.commit()
    return stats


def handle_parse_document(session, payload: dict) -> dict:
    doc = session.scalar(sa.select(Document).where(Document.id == payload["document_id"]))
    if doc is None:
        return {"error": "document_not_found"}
    if doc.parsed_at:
        return {"skipped": "already_parsed"}
    data = open(doc.storage_path, "rb").read()
    from .docs_pipeline import parse_into_spans
    n_spans = parse_into_spans(session, doc, data)
    session.commit()
    return {"spans": n_spans}


def handle_investigate(session, payload: dict) -> dict:
    case = session.scalar(sa.select(Case).where(Case.id == payload["case_id"]))
    if case is None:
        return {"error": "case_not_found"}
    from .agent import run_agent
    from .decide import decide
    from .verify import run_verifier

    analysis = run_agent(session, case)
    verdict = run_verifier(session, case)
    decision = decide(session, case)
    session.commit()
    return {"analysis": analysis.status, "action": analysis.recommended_action,
            "p_win": decision.p_win, "decision": decision.recommended_action,
            **verdict}


def handle_execute_plan(session, payload: dict) -> dict:
    from .execute import execute_action

    action = session.get(Action, payload["action_id"]) if "action_id" in payload else \
        session.scalar(sa.select(Action).where(
            Action.plan_id == payload.get("plan_id"),
            Action.action_type == "submit_evidence"))
    if action is None:
        return {"error": "action_not_found"}
    result = execute_action(session, action)
    session.commit()
    return result


def handle_eval_run(session, payload: dict) -> dict:
    from .evals import run_eval
    from .models import EvalRun

    run = session.get(EvalRun, payload["run_id"])
    if run is None:
        return {"error": "run_not_found"}
    if run.status == "DONE":
        return {"skipped": "already done"}
    run_eval(session, run, quick=run.quick)
    session.commit()
    return {"totals": run.totals}


HANDLERS = {
    "sync_disputes": handle_sync_disputes,
    "collect_case": handle_collect_case,
    "parse_document": handle_parse_document,
    "investigate": handle_investigate,
    "execute_plan": handle_execute_plan,
    "eval_run": handle_eval_run,
}


async def run_worker(drain: bool = False) -> None:
    """drain=True: process until no QUEUED jobs remain, then exit."""
    init_db()
    with SessionLocal() as session:
        recovered = recover_stale_jobs(session)
        session.commit()
    if recovered:
        print(f"[worker] recovered {recovered} stale job(s)")
    print(f"[worker] running{' (drain)' if drain else ' — Ctrl+C to stop'}")
    idle = 0
    while True:
        with SessionLocal() as session:
            job = claim_job(session)
        if job is None:
            if drain and idle >= 1:
                break
            if not drain:
                await asyncio.sleep(POLL_SECONDS)
                continue
            idle += 1
            await asyncio.sleep(POLL_SECONDS)
            continue
        idle = 0
        handler = HANDLERS.get(job.kind)
        try:
            if handler is None:
                raise RuntimeError(f"unknown job kind: {job.kind}")
            with SessionLocal() as session:
                result = handler(session, job.payload)
                print(f"[worker] {job.kind} {job.payload} -> {result}")
            with SessionLocal() as session:
                done = session.get(Job, job.id)
                done.status = "DONE"
                done.finished_at = datetime.now(timezone.utc)
                session.commit()
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            with SessionLocal() as session:
                failed = session.get(Job, job.id)
                failed.status = "FAILED" if failed.attempts >= failed.max_attempts else "QUEUED"
                failed.last_error = f"{type(exc).__name__}: {exc}"[:500]
                session.commit()


if __name__ == "__main__":
    import sys as _sys

    try:
        asyncio.run(run_worker(drain="--drain" in _sys.argv))
    except KeyboardInterrupt:
        print("\n[worker] stopped")
