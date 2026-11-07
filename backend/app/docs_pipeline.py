"""Document pipeline — FINAL_PLAN.md §8 (provenance).

parse_pdf        : PyMuPDF word boxes -> Span rows (page, text, x0..y1) + text.
persist_document : store bytes, sha256, parse to spans.
find_quote       : the locator. A quote is located by matching its normalized
                   word sequence against the document's word stream; the LLM
                   never supplies coordinates. Exact match first; fuzzy match
                   (ratio >= 0.92) flagged as such. No match -> not found
                   -> the claim dies (Day 2 verifier).
"""
from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.orm import Session

from .models import Document, Span

STORAGE = Path(__file__).resolve().parents[2] / "data" / "documents"

_WORD_RE = re.compile(r"[^0-9a-z]+")


def norm_word(w: str) -> str:
    return _WORD_RE.sub("", w.lower())


@dataclass
class ParsedPage:
    page: int
    words: list[dict] = field(default_factory=list)  # {text,x0,y0,x1,y1,line}


@dataclass
class ParsedDoc:
    pages: list[ParsedPage]
    full_text: str


def parse_pdf(data: bytes) -> ParsedDoc:
    import pymupdf  # PyMuPDF (formerly fitz)

    doc = pymupdf.open(stream=data, filetype="pdf")
    pages: list[ParsedPage] = []
    texts: list[str] = []
    for pno, page in enumerate(doc, start=1):
        pp = ParsedPage(page=pno)
        # words: (x0, y0, x1, y1, word, block_no, line_no, word_no)
        for x0, y0, x1, y1, word, _b, line_no, _w in page.get_text("words"):
            clean = word.strip()
            if not clean:
                continue
            pp.words.append({"text": clean, "x0": x0, "y0": y0, "x1": x1, "y1": y1,
                             "line": line_no})
        # reading order: sort words by line, then x
        pp.words.sort(key=lambda w: (w["line"], w["x0"]))
        pages.append(pp)
        texts.append(page.get_text("text"))
    return ParsedDoc(pages=pages, full_text="\n".join(texts))


def parse_into_spans(session: Session, doc: Document, data: bytes) -> int:
    """Parse the PDF bytes and attach word-level spans to an existing Document."""
    n_spans = 0
    parsed = parse_pdf(data)
    doc.pages = len(parsed.pages)
    doc.extracted_text = parsed.full_text
    session.flush()
    session.query(Span).filter(Span.document_id == doc.id).delete()
    for pp in parsed.pages:
        for w in pp.words:
            session.add(Span(document_id=doc.id, page=pp.page, text=w["text"],
                             x0=w["x0"], y0=w["y0"], x1=w["x1"], y1=w["y1"]))
            n_spans += 1
    doc.parsed_at = datetime.now(timezone.utc)
    doc.parse_error = ""
    return n_spans


def persist_document(session: Session, case_id: str, data: bytes, filename: str,
                     mime: str = "application/pdf", doc_type: str = "OTHER",
                     origin: str = "MERCHANT_UPLOAD") -> tuple[Document, int]:
    """Store the file, hash it, extract word-level spans. Idempotent on sha256."""
    sha = hashlib.sha256(data).hexdigest()
    existing = session.scalar(
        sa.select(Document).where(Document.case_id == case_id, Document.sha256 == sha))
    if existing:
        return existing, 0
    STORAGE.mkdir(parents=True, exist_ok=True)
    path = STORAGE / f"{case_id}_{uuid.uuid4().hex[:8]}_{Path(filename).name}"
    path.write_bytes(data)
    doc = Document(
        case_id=case_id, filename=filename, mime=mime, sha256=sha,
        storage_path=str(path), doc_type=doc_type, origin=origin,
    )
    session.add(doc)
    session.flush()
    try:
        n_spans = parse_into_spans(session, doc, data)
    except Exception as exc:  # noqa: BLE001 — record parse failure, keep the doc
        doc.parse_error = f"{type(exc).__name__}: {exc}"[:500]
        n_spans = 0
    return doc, n_spans


# ----------------------------------------------------------------- locator
@dataclass
class LocatorHit:
    page: int
    box: tuple[float, float, float, float]
    span_ids: list[str]
    match_type: str  # EXACT | FUZZY
    score: float


def _ordered_spans(session: Session, document_id: str) -> list[Span]:
    return list(session.scalars(
        sa.select(Span).where(Span.document_id == document_id)
        .order_by(Span.page, Span.y0, Span.x0)))


def find_quote(session: Session, document_id: str, quote: str,
               fuzzy_threshold: float = 0.92) -> LocatorHit | None:
    """Locate a verbatim quote in the span stream; return union box + spans.

    Punctuation-only spans (':', '—', ...) normalize to '' and are skipped:
    they never participate in matching or highlights.
    """
    spans = _ordered_spans(session, document_id)
    if not spans:
        return None
    pairs = [(norm_word(s.text), s) for s in spans]
    pairs = [(nw, s) for nw, s in pairs if nw]
    if not pairs:
        return None
    d_words = [nw for nw, _ in pairs]
    q_words = [norm_word(w) for w in quote.split()]
    q_words = [w for w in q_words if w]
    if not q_words:
        return None
    n, m = len(d_words), len(q_words)

    # exact sliding-window match on normalized words
    for i in range(0, n - m + 1):
        if d_words[i:i + m] == q_words:
            return _hit([s for _, s in pairs[i:i + m]], "EXACT", 1.0)

    # fuzzy: best window by SequenceMatcher over the joined strings
    best = None
    q_joined = " ".join(q_words)
    for i in range(0, n - m + 1):
        window = d_words[i:i + m]
        ratio = SequenceMatcher(None, q_joined, " ".join(window)).ratio()
        if ratio >= fuzzy_threshold and (best is None or ratio > best[1]):
            best = (i, ratio)
    if best:
        i, ratio = best
        return _hit([s for _, s in pairs[i:i + m]], "FUZZY", ratio)
    return None


def _hit(spans: list[Span], match_type: str, score: float) -> LocatorHit:
    box = (min(s.x0 for s in spans), min(s.y0 for s in spans),
           max(s.x1 for s in spans), max(s.y1 for s in spans))
    return LocatorHit(page=spans[0].page, box=box, span_ids=[s.id for s in spans],
                      match_type=match_type, score=score)
