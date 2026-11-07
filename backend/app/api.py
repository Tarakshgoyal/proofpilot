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
from .models import Case, Document, Fact, Job, Span
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
    with SessionLocal() as session:
        cases = session.scalars(sa.select(Case).order_by(Case.created_at.desc())).all()
        out = []
        for c in cases:
            facts = session.scalar(
                sa.select(sa.func.count()).select_from(Fact).where(Fact.case_id == c.id))
            docs = session.scalar(
                sa.select(sa.func.count()).select_from(Document)
                .where(Document.case_id == c.id))
            out.append({
                "id": c.id, "dispute_id": c.dispute_id, "reason": c.reason,
                "stage": c.stage, "provider_status": c.provider_status,
                "workflow_status": c.workflow_status,
                "amount_minor": c.amount_minor, "currency": c.currency,
                "response_due_at": c.response_due_at.isoformat() if c.response_due_at else None,
                "version": c.version, "fact_count": facts, "document_count": docs,
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
