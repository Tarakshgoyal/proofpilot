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
from .models import Analysis, Case, Claim, Contradiction, Document, Fact, Job, Span
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
        return {"jobId": job.id, "status": "QUEUED"}, 202


@router.get("/cases")
def list_cases():
    """Batched counts (no N+1) — safe against remote DBs like Neon."""
    with SessionLocal() as session:
        cases = session.scalars(sa.select(Case).order_by(Case.created_at.desc())).all()

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
        return {"documentId": doc.id, "sha256": doc.sha256, "spans": n_spans,
                "parsed": bool(doc.parsed_at)}, 202


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
        return {"jobId": job.id, "status": "QUEUED"}, 202


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
