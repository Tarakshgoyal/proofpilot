"""SQLAlchemy models — FINAL_PLAN.md §6 evidence-graph schema.

Money is integer minor units. UUIDs are 36-char strings (portable).
Facts/documents/spans are immutable by convention: new evidence creates new
rows and new plan versions — never an in-place overwrite.
"""
from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def _uuid() -> str:
    return str(uuid.uuid4())


def pk() -> Mapped[str]:
    return mapped_column(sa.String(36), primary_key=True, default=_uuid)


class CreatedAt:
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now()
    )


# --------------------------------------------------------------- disputes
class Case(CreatedAt, Base):
    __tablename__ = "cases"

    id: Mapped[str] = pk()
    merchant_id: Mapped[str] = mapped_column(sa.String(36), default=_uuid)
    dispute_id: Mapped[str] = mapped_column(sa.String(64), unique=True)
    reason: Mapped[str] = mapped_column(sa.String(64))
    stage: Mapped[str] = mapped_column(sa.String(32))            # INQUIRY | CHARGEBACK | ...
    provider_status: Mapped[str] = mapped_column(sa.String(64))  # what PayPal says
    workflow_status: Mapped[str] = mapped_column(sa.String(32), default="NEW")
    amount_minor: Mapped[int] = mapped_column(sa.Integer)
    currency: Mapped[str] = mapped_column(sa.String(3))
    response_due_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    raw_snapshot: Mapped[dict] = mapped_column(sa.JSON, default=dict)  # last raw dispute JSON
    version: Mapped[int] = mapped_column(sa.Integer, default=1)
    synthetic: Mapped[bool] = mapped_column(sa.Boolean, default=False)  # eval-seeded case
    eval_run_id: Mapped[str | None] = mapped_column(sa.String(36))      # owning eval run


class WebhookEvent(CreatedAt, Base):
    __tablename__ = "events"

    id: Mapped[str] = pk()
    event_id: Mapped[str] = mapped_column(sa.String(64), unique=True)
    event_type: Mapped[str] = mapped_column(sa.String(128))
    raw_body: Mapped[dict] = mapped_column(sa.JSON)
    verified: Mapped[bool] = mapped_column(sa.Boolean, default=False)
    processed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))


class Job(CreatedAt, Base):
    """Durable background job. The asyncio worker claims rows via status+lease."""

    __tablename__ = "jobs"

    id: Mapped[str] = pk()
    kind: Mapped[str] = mapped_column(sa.String(64))       # sync_disputes|collect_case|parse_document
    payload: Mapped[dict] = mapped_column(sa.JSON, default=dict)
    status: Mapped[str] = mapped_column(sa.String(16), default="QUEUED")
    # QUEUED|RUNNING|DONE|FAILED
    attempts: Mapped[int] = mapped_column(sa.Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(sa.Integer, default=3)
    lease_expires_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    last_error: Mapped[str] = mapped_column(sa.Text, default="")
    finished_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))


# --------------------------------------------------------- evidence graph
class Fact(CreatedAt, Base):
    __tablename__ = "facts"

    id: Mapped[str] = pk()
    case_id: Mapped[str] = mapped_column(sa.ForeignKey("cases.id"))
    kind: Mapped[str] = mapped_column(sa.String(64))             # e.g. DELIVERY_STATUS
    value: Mapped[dict] = mapped_column(sa.JSON)                 # typed value
    source_type: Mapped[str] = mapped_column(sa.String(32))      # PAYPAL_API|MERCHANT_DB|DOCUMENT
    source_ref: Mapped[dict] = mapped_column(sa.JSON)            # endpoint+id OR doc+page+box
    retrieved_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    sha256: Mapped[str] = mapped_column(sa.String(64))           # hash of raw payload/file


class Document(CreatedAt, Base):
    __tablename__ = "documents"

    id: Mapped[str] = pk()
    case_id: Mapped[str] = mapped_column(sa.ForeignKey("cases.id"))
    filename: Mapped[str] = mapped_column(sa.String(255))
    mime: Mapped[str] = mapped_column(sa.String(128))
    sha256: Mapped[str] = mapped_column(sa.String(64))
    storage_path: Mapped[str] = mapped_column(sa.String(512))
    extracted_text: Mapped[str] = mapped_column(sa.Text, default="")
    pages: Mapped[int] = mapped_column(sa.Integer, default=0)
    doc_type: Mapped[str] = mapped_column(sa.String(64), default="OTHER")
    # OTHER|CARRIER_DOC|INVOICE|CUSTOMER_CHAT|POLICY|PACKET
    origin: Mapped[str] = mapped_column(sa.String(32), default="MERCHANT_UPLOAD")
    # MERCHANT_UPLOAD|PAYPAL_API|SEED
    parsed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    parse_error: Mapped[str] = mapped_column(sa.Text, default="")


class Span(CreatedAt, Base):
    __tablename__ = "spans"

    id: Mapped[str] = pk()
    document_id: Mapped[str] = mapped_column(sa.ForeignKey("documents.id"))
    page: Mapped[int] = mapped_column(sa.Integer)
    text: Mapped[str] = mapped_column(sa.Text)
    x0: Mapped[float] = mapped_column(sa.Float)
    y0: Mapped[float] = mapped_column(sa.Float)
    x1: Mapped[float] = mapped_column(sa.Float)
    y1: Mapped[float] = mapped_column(sa.Float)


class Claim(CreatedAt, Base):
    __tablename__ = "claims"

    id: Mapped[str] = pk()
    case_id: Mapped[str] = mapped_column(sa.ForeignKey("cases.id"))
    claim_type: Mapped[str] = mapped_column(sa.String(64))       # DELIVERED_ON, ADDRESS_MATCH, ...
    statement: Mapped[str] = mapped_column(sa.Text)
    fact_ids: Mapped[list] = mapped_column(sa.JSON, default=list)
    span_ids: Mapped[list] = mapped_column(sa.JSON, default=list)
    status: Mapped[str] = mapped_column(sa.String(16))           # VERIFIED|UNSUPPORTED|CONTRADICTED
    reason: Mapped[str] = mapped_column(sa.Text, default="")


class Contradiction(CreatedAt, Base):
    __tablename__ = "contradictions"

    id: Mapped[str] = pk()
    case_id: Mapped[str] = mapped_column(sa.ForeignKey("cases.id"))
    rule: Mapped[str] = mapped_column(sa.String(64))
    fact_a: Mapped[str] = mapped_column(sa.String(36))
    fact_b: Mapped[str] = mapped_column(sa.String(36))
    severity: Mapped[str] = mapped_column(sa.String(16))         # BLOCKING | REVIEW
    explanation: Mapped[str] = mapped_column(sa.Text)


# ------------------------------------------------------- decision + action
class Analysis(CreatedAt, Base):
    """One agent run over a case: model, prompt version, raw structured output."""

    __tablename__ = "analyses"

    id: Mapped[str] = pk()
    case_id: Mapped[str] = mapped_column(sa.ForeignKey("cases.id"))
    model: Mapped[str] = mapped_column(sa.String(128), default="")
    prompt_version: Mapped[str] = mapped_column(sa.String(16), default="v1")
    status: Mapped[str] = mapped_column(sa.String(16))  # OK | FALLBACK | FAILED
    error: Mapped[str] = mapped_column(sa.Text, default="")
    raw_output: Mapped[dict] = mapped_column(sa.JSON, default=dict)
    recommended_action: Mapped[str] = mapped_column(sa.String(16), default="")


class EvalRun(CreatedAt, Base):
    """One evaluation run over the labelled scenario suite."""

    __tablename__ = "eval_runs"

    id: Mapped[str] = pk()
    status: Mapped[str] = mapped_column(sa.String(16), default="RUNNING")
    # RUNNING|DONE|FAILED
    quick: Mapped[bool] = mapped_column(sa.Boolean, default=False)
    model: Mapped[str] = mapped_column(sa.String(128), default="")
    totals: Mapped[dict] = mapped_column(sa.JSON, default=dict)
    error: Mapped[str] = mapped_column(sa.Text, default="")
    finished_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))


class EvalResult(CreatedAt, Base):
    __tablename__ = "eval_results"
    __table_args__ = (sa.UniqueConstraint("run_id", "scenario", name="uq_eval_run_scenario"),)

    id: Mapped[str] = pk()
    run_id: Mapped[str] = mapped_column(sa.ForeignKey("eval_runs.id"))
    scenario: Mapped[str] = mapped_column(sa.String(64))
    label: Mapped[dict] = mapped_column(sa.JSON)     # ground truth
    found: Mapped[dict] = mapped_column(sa.JSON)     # what the system did
    ok: Mapped[bool] = mapped_column(sa.Boolean, default=False)
    detail: Mapped[str] = mapped_column(sa.JSON, default=dict)


class Decision(CreatedAt, Base):
    __tablename__ = "decisions"

    id: Mapped[str] = pk()
    case_id: Mapped[str] = mapped_column(sa.ForeignKey("cases.id"))
    recommended_action: Mapped[str] = mapped_column(sa.String(16))  # FIGHT|OFFER|ACCEPT|MESSAGE
    p_win: Mapped[float] = mapped_column(sa.Float)
    ev_fight: Mapped[int] = mapped_column(sa.Integer)            # minor units
    ev_fold: Mapped[int] = mapped_column(sa.Integer)
    rationale: Mapped[str] = mapped_column(sa.Text, default="")
    model: Mapped[str] = mapped_column(sa.String(64), default="")
    prompt_version: Mapped[str] = mapped_column(sa.String(32), default="")


class Plan(CreatedAt, Base):
    __tablename__ = "plans"
    __table_args__ = (sa.UniqueConstraint("case_id", "version", name="uq_plan_case_version"),)

    id: Mapped[str] = pk()
    case_id: Mapped[str] = mapped_column(sa.ForeignKey("cases.id"))
    version: Mapped[int] = mapped_column(sa.Integer)
    payload: Mapped[dict] = mapped_column(sa.JSON)               # exact provider payload
    payload_hash: Mapped[str] = mapped_column(sa.String(64))     # SHA-256 canonicalized
    packet_hash: Mapped[str] = mapped_column(sa.String(64), default="")
    status: Mapped[str] = mapped_column(sa.String(32), default="DRAFT")
    # DRAFT|AWAITING_APPROVAL|APPROVED|STALE|EXECUTED|REJECTED


class Action(CreatedAt, Base):
    __tablename__ = "actions"
    __table_args__ = (
        sa.UniqueConstraint("plan_id", "action_type", name="uq_action_plan_type"),
        sa.UniqueConstraint("idempotency_key", name="uq_action_idempotency"),
    )

    id: Mapped[str] = pk()
    plan_id: Mapped[str] = mapped_column(sa.ForeignKey("plans.id"))
    action_type: Mapped[str] = mapped_column(sa.String(32))      # provide_evidence|accept_claim|...
    payload: Mapped[dict] = mapped_column(sa.JSON)
    idempotency_key: Mapped[str] = mapped_column(sa.String(64))  # = payload hash
    status: Mapped[str] = mapped_column(sa.String(32), default="QUEUED")
    # QUEUED|VALIDATING|DISPATCHING|ACKNOWLEDGED|RECONCILING|COMPLETED|FAILED|EXECUTION_UNKNOWN
    response: Mapped[dict] = mapped_column(sa.JSON, default=dict)
    executed_by: Mapped[str] = mapped_column(sa.String(255), default="")
    executed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))


class AuditLog(Base):
    __tablename__ = "audit_log"

    seq: Mapped[int] = mapped_column(sa.Integer, primary_key=True, autoincrement=True)
    case_id: Mapped[str] = mapped_column(sa.String(36))
    actor: Mapped[str] = mapped_column(sa.String(255))
    event: Mapped[str] = mapped_column(sa.String(64))
    payload: Mapped[dict] = mapped_column(sa.JSON, default=dict)
    prev_hash: Mapped[str] = mapped_column(sa.String(64), default="")
    hash: Mapped[str] = mapped_column(sa.String(64))             # SHA-256(prev_hash + canonical row)


# ------------------------------------------------- mock merchant back office
class Order(CreatedAt, Base):
    __tablename__ = "orders"

    id: Mapped[str] = pk()
    merchant_order_id: Mapped[str] = mapped_column(sa.String(64), unique=True)
    paypal_order_id: Mapped[str] = mapped_column(sa.String(64), default="")
    paypal_capture_id: Mapped[str] = mapped_column(sa.String(64), default="")
    amount_minor: Mapped[int] = mapped_column(sa.Integer)
    currency: Mapped[str] = mapped_column(sa.String(3))
    items: Mapped[list] = mapped_column(sa.JSON, default=list)
    shipping_address: Mapped[dict] = mapped_column(sa.JSON, default=dict)


class Shipment(CreatedAt, Base):
    __tablename__ = "shipments"

    id: Mapped[str] = pk()
    order_id: Mapped[str] = mapped_column(sa.ForeignKey("orders.id"))
    carrier: Mapped[str] = mapped_column(sa.String(64))
    tracking_number: Mapped[str] = mapped_column(sa.String(64))
    status: Mapped[str] = mapped_column(sa.String(32), default="SHIPPED")
    ship_to: Mapped[dict] = mapped_column(sa.JSON, default=dict)
    delivered_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))


class SupportThread(CreatedAt, Base):
    __tablename__ = "support_threads"

    id: Mapped[str] = pk()
    order_id: Mapped[str] = mapped_column(sa.ForeignKey("orders.id"))
    messages: Mapped[list] = mapped_column(sa.JSON, default=list)


class Policy(CreatedAt, Base):
    __tablename__ = "policies"

    id: Mapped[str] = pk()
    name: Mapped[str] = mapped_column(sa.String(64))
    version: Mapped[int] = mapped_column(sa.Integer, default=1)
    text: Mapped[str] = mapped_column(sa.Text)
    sha256: Mapped[str] = mapped_column(sa.String(64), default="")
