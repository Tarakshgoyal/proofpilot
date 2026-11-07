"""Evidence packet builder — FINAL_PLAN.md §14.

ReportLab PDF: cover summary -> claim-to-exhibit table (claim, exhibit #,
source, hash) -> numbered exhibits (verified claims with their citations)
-> hash manifest footer. The packet is an immutable artifact; its SHA-256
is bound into the Plan (packet_hash) the merchant approves.
"""
from __future__ import annotations

import io
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.orm import Session

from .collector import canonical_hash
from .models import Case, Claim, Document, Fact

STORAGE = Path(__file__).resolve().parents[2] / "data" / "packets"


def build_packet(session: Session, case: Case, plan_payload: dict) -> tuple[bytes, str, list[dict]]:
    """Return (pdf_bytes, packet_sha256, manifest)."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.units import inch
    from reportlab.platypus import (Paragraph, SimpleDocTemplate, Spacer, Table,
                                    TableStyle)
    from reportlab.lib.styles import getSampleStyleSheet

    claims = session.scalars(sa.select(Claim).where(
        Claim.case_id == case.id, Claim.status == "VERIFIED")
        .order_by(Claim.created_at)).all()
    facts = {f.id: f for f in session.scalars(
        sa.select(Fact).where(Fact.case_id == case.id)).all()}
    docs = {d.id: d for d in session.scalars(
        sa.select(Document).where(Document.case_id == case.id)).all()}

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter,
                            leftMargin=0.8 * inch, rightMargin=0.8 * inch,
                            topMargin=0.7 * inch, bottomMargin=0.7 * inch,
                            title=f"ProofPilot evidence packet — {case.dispute_id}")
    styles = getSampleStyleSheet()
    story = [
        Paragraph("<b>ProofPilot — Evidence Packet</b>", styles["Title"]),
        Paragraph(f"Dispute <b>{case.dispute_id}</b> · reason {case.reason} · "
                  f"stage {case.stage} · status {case.provider_status} · "
                  f"amount {case.amount_minor / 100:.2f} {case.currency}",
                  styles["Normal"]),
        Spacer(1, 10),
    ]

    manifest: list[dict] = []
    rows = [["Exhibit", "Claim", "Source", "SHA-256 (first 16)"]]
    for i, c in enumerate(claims, start=1):
        src, sha = "—", ""
        if c.fact_ids and c.fact_ids[0] in facts:
            f = facts[c.fact_ids[0]]
            src = f"{c.claim_type} fact ({f.source_type})"
            sha = f.sha256[:16]
        elif c.span_ids:
            ref = c.span_ids[0]
            d = docs.get(ref.get("document_id", ""))
            if d:
                src = f"{c.claim_type} doc ({d.filename})"
                sha = d.sha256[:16]
        manifest.append({"exhibit": i, "claim": c.statement, "source": src, "sha256_16": sha})
        rows.append([f"Exhibit {i}", c.statement[:80], src, sha])

    story.append(Paragraph("<b>Claim-to-exhibit index</b>", styles["Heading3"]))
    story.append(Table(rows, colWidths=[0.8 * inch, 3.1 * inch, 1.7 * inch, 1.1 * inch],
                       style=TableStyle([
                           ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                           ("FONTSIZE", (0, 0), (-1, -1), 8),
                           ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e2e8f0")),
                           ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                           ("VALIGN", (0, 0), (-1, -1), "TOP"),
                       ])))
    story.append(Spacer(1, 12))

    story.append(Paragraph("<b>Exhibits — verified claims and their evidence</b>",
                           styles["Heading3"]))
    for i, c in enumerate(claims, start=1):
        story.append(Paragraph(f"<b>Exhibit {i} — {c.claim_type}</b>: {c.statement}",
                               styles["Normal"]))
        story.append(Paragraph(f"Verifier: {c.reason}", styles["Italic"]))
        if c.span_ids:
            ref = c.span_ids[0]
            d = docs.get(ref.get("document_id", ""))
            if d:
                story.append(Paragraph(
                    f"Document: {d.filename} (sha256 {d.sha256[:16]}…) — verbatim quote: "
                    f"“{ref.get('quote', '')[:220]}”", styles["Normal"]))
        story.append(Spacer(1, 8))
    if not claims:
        story.append(Paragraph("No verified claims — packet is empty by design; "
                               "nothing unverifiable is ever submitted.", styles["Italic"]))

    packet_hash = canonical_hash({
        "dispute_id": case.dispute_id, "case_version": case.version,
        "payload": plan_payload, "manifest": manifest,
    })
    story.append(Spacer(1, 14))
    story.append(Paragraph(
        "<b>Hash manifest</b> — packet SHA-256 (canonicalized): "
        f"<font face='Courier' size='8'>{packet_hash}</font><br/>"
        "Only verifier-VERIFIED claims appear in this packet. "
        "Unverified model output is structurally excluded.", styles["Normal"]))

    doc.build(story)
    pdf = buf.getvalue()

    STORAGE.mkdir(parents=True, exist_ok=True)
    (STORAGE / f"{case.id}_{case.version}.pdf").write_bytes(pdf)
    return pdf, packet_hash, manifest
