"""Authenticated-app API surface (Day 1 slice).

POST /api/sync                    queue a dispute sync (202 + job id)
GET  /api/cases                   queue data from the DB
GET  /api/cases/{id}              case + facts + documents
GET  /api/cases/{id}/facts        evidence graph facts
POST /api/cases/{id}/documents    upload an evidence document (202 + parse job)
GET  /api/documents/{id}/spans    word-level spans for the provenance viewer
GET  /api/documents/{id}/file     the stored file (for react-pdf)
GET  /api/jobs/{id}               job status
"""
from __future__ import annotations

import json

import sqlalchemy as sa
from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from .db import SessionLocal
from .docs_pipeline import STORAGE, find_quote, persist_document
from .models import (Action, Analysis, AuditLog, Case, Claim, Contradiction,
                     Decision, Document, Fact, Job, Plan, Span)
from .worker import enqueue

router = APIRouter(prefix="/api")


def _case_or_404(session, case_id: str) -> Case:
    case = session.scalar(sa.select(Case).where(Case.id == case_id))
    if case is None:
        raise HTTPException(404, "case not found")
    return case


@router.post("/sync")
def sync():
    with SessionLocal() as session:
        job = enqueue(session, "sync_disputes", {})
        session.commit()
        from fastapi.responses import JSONResponse

        return JSONResponse({"jobId": job.id, "status": "QUEUED"}, status_code=202)


@router.get("/cases")
def list_cases(include_synthetic: bool = False):
    """Batched counts (no N+1) — safe against remote DBs like Neon.
    Eval-seeded synthetic cases are hidden unless include_synthetic=true."""
    with SessionLocal() as session:
        q = sa.select(Case).order_by(Case.created_at.desc())
        if not include_synthetic:
            q = q.where(Case.eval_run_id.is_(None))  # hide bulk eval cases
        cases = session.scalars(q).all()

        def _counts(model, *filters):
            q = (sa.select(model.case_id, sa.func.count())
                 .where(*filters).group_by(model.case_id))
            return dict(session.execute(q).all())

        fact_counts = _counts(Fact)
        doc_counts = _counts(Document)
        contra_counts = _counts(Contradiction)
        verified_counts = _counts(Claim, Claim.status == "VERIFIED")
        rejected_counts = _counts(Claim, Claim.status.in_(("UNSUPPORTED", "CONTRADICTED")))
        actions = {}
        for an in session.scalars(sa.select(Analysis)
                                  .order_by(Analysis.created_at)).all():
            actions[an.case_id] = an.recommended_action  # last write wins

        out = []
        for c in cases:
            out.append({
                "id": c.id, "dispute_id": c.dispute_id, "reason": c.reason,
                "stage": c.stage, "provider_status": c.provider_status,
                "workflow_status": c.workflow_status, "synthetic": c.synthetic,
                "amount_minor": c.amount_minor, "currency": c.currency,
                "response_due_at": c.response_due_at.isoformat() if c.response_due_at else None,
                "version": c.version,
                "fact_count": fact_counts.get(c.id, 0),
                "document_count": doc_counts.get(c.id, 0),
                "contradiction_count": contra_counts.get(c.id, 0),
                "claims_verified": verified_counts.get(c.id, 0),
                "claims_rejected": rejected_counts.get(c.id, 0),
                "recommended_action": actions.get(c.id, ""),
            })
        return {"cases": out}


@router.get("/cases/{case_id}")
def get_case(case_id: str):
    with SessionLocal() as session:
        case = _case_or_404(session, case_id)
        facts = session.scalars(sa.select(Fact).where(Fact.case_id == case.id)).all()
        docs = session.scalars(
            sa.select(Document).where(Document.case_id == case.id)).all()
        return {
            **_case_summary(case),
            "facts": [_fact_out(f) for f in facts],
            "documents": [_doc_out(d) for d in docs],
        }


@router.get("/cases/{case_id}/facts")
def get_facts(case_id: str):
    with SessionLocal() as session:
        case = _case_or_404(session, case_id)
        facts = session.scalars(sa.select(Fact).where(Fact.case_id == case.id)).all()
        return {"facts": [_fact_out(f) for f in facts]}


@router.post("/cases/{case_id}/documents")
async def upload_document(case_id: str, file: UploadFile = File(...),
                          doc_type: str = "OTHER"):
    data = await file.read()
    if len(data) > 10 * 1024 * 1024:
        raise HTTPException(413, "file too large (10MB limit)")
    with SessionLocal() as session:
        case = _case_or_404(session, case_id)
        doc, n_spans = persist_document(session, case.id, data,
                                        file.filename or "upload.pdf",
                                        file.content_type or "application/pdf",
                                        doc_type=doc_type)
        session.commit()
        enqueue(session, "parse_document", {"document_id": doc.id})
        session.commit()
        from fastapi.responses import JSONResponse

        return JSONResponse({"documentId": doc.id, "sha256": doc.sha256,
                             "spans": n_spans, "parsed": bool(doc.parsed_at)},
                            status_code=202)


@router.get("/documents/{document_id}/spans")
def get_spans(document_id: str, page: int | None = None):
    with SessionLocal() as session:
        doc = session.get(Document, document_id)
        if doc is None:
            raise HTTPException(404, "document not found")
        q = sa.select(Span).where(Span.document_id == doc.id).order_by(Span.page, Span.y0, Span.x0)
        if page:
            q = q.where(Span.page == page)
        spans = session.scalars(q).all()
        return {"documentId": doc.id, "filename": doc.filename, "pages": doc.pages,
                "spans": [{"id": s.id, "page": s.page, "text": s.text,
                           "x0": s.x0, "y0": s.y0, "x1": s.x1, "y1": s.y1}
                          for s in spans]}


@router.get("/documents/{document_id}/locate")
def locate_quote(document_id: str, quote: str):
    """Locator API: where in the document does this quote live?"""
    with SessionLocal() as session:
        doc = session.get(Document, document_id)
        if doc is None:
            raise HTTPException(404, "document not found")
        hit = find_quote(session, doc.id, quote)
        if hit is None:
            return {"found": False}
        return {"found": True, "page": hit.page, "box": hit.box,
                "matchType": hit.match_type, "score": hit.score,
                "spanIds": hit.span_ids}


@router.get("/documents/{document_id}/page/{page_no}")
def get_page_image(document_id: str, page_no: int):
    """Render one PDF page to PNG (PyMuPDF) for the provenance viewer."""
    from fastapi.responses import Response

    with SessionLocal() as session:
        doc = session.get(Document, document_id)
        if doc is None:
            raise HTTPException(404, "document not found")
    data = open(doc.storage_path, "rb").read()
    try:
        from .docs_pipeline import parse_pdf  # noqa: F401 — ensure pymupdf import path
        import pymupdf

        pdf = pymupdf.open(stream=data, filetype="pdf")
        page = pdf[page_no - 1]
        pix = page.get_pixmap(dpi=150)
        return Response(pix.tobytes("png"), media_type="image/png")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, f"render failed: {exc}")


@router.get("/documents/{document_id}/file")
def get_file(document_id: str):
    with SessionLocal() as session:
        doc = session.get(Document, document_id)
        if doc is None:
            raise HTTPException(404, "document not found")
        return FileResponse(doc.storage_path, media_type=doc.mime,
                            filename=doc.filename)


@router.post("/cases/{case_id}/analyse")
def analyse_case(case_id: str):
    with SessionLocal() as session:
        _case_or_404(session, case_id)
        job = enqueue(session, "investigate", {"case_id": case_id})
        session.commit()
        from fastapi.responses import JSONResponse

        return JSONResponse({"jobId": job.id, "status": "QUEUED"}, status_code=202)


@router.get("/cases/{case_id}/proposal")
def get_proposal(case_id: str):
    """Agent + verifier output: claims with statuses, checklist, contradictions."""
    with SessionLocal() as session:
        case = _case_or_404(session, case_id)
        analysis = session.scalar(
            sa.select(Analysis).where(Analysis.case_id == case.id)
            .order_by(Analysis.created_at.desc()))
        claims = session.scalars(
            sa.select(Claim).where(Claim.case_id == case.id)).all()
        contradictions = session.scalars(
            sa.select(Contradiction).where(Contradiction.case_id == case.id)).all()
        return {
            "case": _case_summary(case),
            "analysis": None if analysis is None else {
                "id": analysis.id, "status": analysis.status,
                "model": analysis.model, "promptVersion": analysis.prompt_version,
                "recommendedAction": analysis.recommended_action,
                "error": analysis.error or None,
                "output": analysis.raw_output,
            },
            "claims": [{
                "id": c.id, "claimType": c.claim_type, "statement": c.statement,
                "status": c.status, "reason": c.reason,
                "factIds": c.fact_ids, "quoteRef": (c.span_ids or [None])[0],
            } for c in claims],
            "contradictions": [{
                "id": x.id, "rule": x.rule, "severity": x.severity,
                "explanation": x.explanation,
            } for x in contradictions],
            "blocking": sum(1 for x in contradictions if x.severity == "BLOCKING"),
        }


@router.get("/cases/{case_id}/plan")
def get_latest_plan(case_id: str):
    """Latest plan (with its action) for the case panel to restore state."""
    with SessionLocal() as session:
        case = _case_or_404(session, case_id)
        plan = session.scalar(sa.select(Plan).where(Plan.case_id == case.id)
                              .order_by(Plan.version.desc()).limit(1))
        if plan is None:
            return {}
        action = session.scalar(sa.select(Action).where(Action.plan_id == plan.id))
        return {**_plan_out(plan), "action": None if action is None else _action_out(action)}


@router.post("/cases/{case_id}/plan")
def create_plan(case_id: str):
    """Build the next immutable plan version (hash-bound, packet attached)."""
    with SessionLocal() as session:
        case = _case_or_404(session, case_id)
        try:
            from .execute import build_plan

            plan = build_plan(session, case)
            session.commit()
        except ValueError as exc:
            raise HTTPException(409, str(exc))
        from fastapi.responses import JSONResponse

        return JSONResponse(_plan_out(plan), status_code=201)


@router.get("/plans/{plan_id}")
def get_plan(plan_id: str):
    with SessionLocal() as session:
        plan = _plan_or_404(session, plan_id)
        action = session.scalar(sa.select(Action).where(Action.plan_id == plan.id))
        return {**_plan_out(plan), "action": None if action is None else _action_out(action)}


@router.post("/plans/{plan_id}/approve")
def approve_plan(plan_id: str, body: dict):
    """Hash+version-bound approval. Idempotent replays return the same action."""
    with SessionLocal() as session:
        plan = _plan_or_404(session, plan_id)
        try:
            from .execute import approve_plan as do_approve

            plan, action = do_approve(
                session, plan, approver=body.get("approver", "merchant"),
                expected_plan_hash=body.get("expectedPlanHash", ""),
                expected_case_version=int(body.get("expectedCaseVersion", -1)))
            session.commit()
        except ValueError as exc:
            session.commit()
            raise HTTPException(409, str(exc))
        if action is not None and action.status == "QUEUED":
            enqueue(session, "execute_plan", {"action_id": action.id}, dedupe=True)
            session.commit()
        return {"plan": _plan_out(plan),
                "action": None if action is None else _action_out(action)}


@router.post("/plans/{plan_id}/reject")
def reject_plan(plan_id: str, body: dict):
    with SessionLocal() as session:
        plan = _plan_or_404(session, plan_id)
        plan.status = "REJECTED"
        from .execute import audit

        audit(session, plan.case_id, body.get("approver", "merchant"), "plan_rejected",
              {"plan_id": plan.id, "reason": body.get("reason", "")})
        session.commit()
        return _plan_out(plan)


@router.get("/plans/{plan_id}/packet")
def get_packet(plan_id: str):
    from fastapi.responses import Response

    with SessionLocal() as session:
        plan = _plan_or_404(session, plan_id)
        from .packet import STORAGE

        path = STORAGE / f"{plan.case_id}_{plan.payload.get('case_version')}.pdf"
        if not path.exists():
            raise HTTPException(404, "packet not built")
        return Response(path.read_bytes(), media_type="application/pdf",
                        headers={"Content-Disposition":
                                 f'attachment; filename="packet_{plan.id[:8]}.pdf"'})


@router.get("/actions/{action_id}")
def get_action(action_id: str):
    with SessionLocal() as session:
        action = session.get(Action, action_id)
        if action is None:
            raise HTTPException(404, "action not found")
        return _action_out(action)


@router.get("/cases/{case_id}/audit")
def get_audit(case_id: str):
    with SessionLocal() as session:
        case = _case_or_404(session, case_id)
        from .execute import verify_chain

        rows = session.scalars(sa.select(AuditLog).where(AuditLog.case_id == case.id)
                               .order_by(AuditLog.seq)).all()
        return {"verification": verify_chain(session, case.id),
                "entries": [{"seq": r.seq, "at": None, "actor": r.actor,
                             "event": r.event, "payload": r.payload,
                             "hash": r.hash[:16] + "…"} for r in rows]}


@router.get("/cases/{case_id}/decision")
def get_decision(case_id: str):
    with SessionLocal() as session:
        case = _case_or_404(session, case_id)
        d = session.scalar(sa.select(Decision).where(Decision.case_id == case.id)
                           .order_by(Decision.created_at.desc()))
        return {} if d is None else {
            "recommendedAction": d.recommended_action, "pWin": d.p_win,
            "evFightMinor": d.ev_fight, "evFoldMinor": d.ev_fold,
            "rationale": d.rationale, "model": d.model,
        }


@router.post("/eval/run")
def start_eval(quick: bool = True):
    """Queue an eval run. quick=true: one variation per archetype (11 scenarios,
    ~2-3 min). quick=false: full 33-scenario suite."""
    with SessionLocal() as session:
        from .models import EvalRun

        run = EvalRun(quick=quick)
        session.add(run)
        session.commit()
        enqueue(session, "eval_run", {"run_id": run.id}, dedupe=False)
        session.commit()
        from fastapi.responses import JSONResponse

        return JSONResponse({"runId": run.id, "quick": quick, "status": "QUEUED"},
                            status_code=202)


@router.get("/eval/runs")
def list_eval_runs():
    with SessionLocal() as session:
        from .models import EvalRun

        runs = session.scalars(sa.select(EvalRun)
                               .order_by(EvalRun.created_at.desc()).limit(10)).all()
        return {"runs": [{"id": r.id, "status": r.status, "quick": r.quick,
                          "model": r.model, "totals": r.totals,
                          "startedAt": r.created_at.isoformat() if r.created_at else None,
                          "finishedAt": r.finished_at.isoformat() if r.finished_at else None}
                         for r in runs]}


@router.get("/eval/runs/{run_id}")
def get_eval_run(run_id: str):
    with SessionLocal() as session:
        from .models import EvalResult, EvalRun

        run = session.get(EvalRun, run_id)
        if run is None:
            raise HTTPException(404, "run not found")
        results = session.scalars(sa.select(EvalResult)
                                  .where(EvalResult.run_id == run.id)).all()
        return {"id": run.id, "status": run.status, "quick": run.quick,
                "model": run.model, "totals": run.totals,
                "results": [{"scenario": x.scenario, "ok": x.ok,
                             "label": x.label, "found": x.found} for x in results]}


@router.get("/jobs/{job_id}")
def get_job(job_id: str):
    with SessionLocal() as session:
        job = session.get(Job, job_id)
        if job is None:
            raise HTTPException(404, "job not found")
        return {"id": job.id, "kind": job.kind, "status": job.status,
                "attempts": job.attempts, "payload": job.payload,
                "lastError": job.last_error or None}


def _case_summary(c: Case) -> dict:
    return {"id": c.id, "dispute_id": c.dispute_id, "reason": c.reason,
            "stage": c.stage, "provider_status": c.provider_status,
            "workflow_status": c.workflow_status, "amount_minor": c.amount_minor,
            "currency": c.currency, "version": c.version,
            "response_due_at": c.response_due_at.isoformat() if c.response_due_at else None}


def _fact_out(f: Fact) -> dict:
    return {"id": f.id, "kind": f.kind, "value": f.value.get("v"),
            "sourceType": f.source_type, "sourceRef": f.source_ref,
            "sha256": f.sha256,
            "retrievedAt": f.retrieved_at.isoformat() if f.retrieved_at else None}


def _doc_out(d: Document) -> dict:
    return {"id": d.id, "filename": d.filename, "docType": d.doc_type,
            "origin": d.origin, "sha256": d.sha256, "pages": d.pages,
            "parsed": bool(d.parsed_at), "parseError": d.parse_error or None}


def _plan_or_404(session, plan_id: str) -> Plan:
    plan = session.get(Plan, plan_id)
    if plan is None:
        raise HTTPException(404, "plan not found")
    return plan


def _plan_out(p: Plan) -> dict:
    return {"id": p.id, "caseId": p.case_id, "version": p.version,
            "status": p.status, "payload": p.payload,
            "payloadHash": p.payload_hash, "packetHash": p.packet_hash,
            "createdAt": p.created_at.isoformat() if p.created_at else None}


def _action_out(a: Action) -> dict:
    return {"id": a.id, "planId": a.plan_id, "actionType": a.action_type,
            "status": a.status, "response": a.response,
            "idempotencyKey": a.idempotency_key,
            "executedAt": a.executed_at.isoformat() if a.executed_at else None}
