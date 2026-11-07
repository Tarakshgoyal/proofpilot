"""Seed SYNTHETIC eval cases (clearly flagged, never presented as real).

Exercises the contradiction engine deterministically:
  SYN-1 delivery after the dispute was opened  -> DELIVERY_AFTER_DISPUTE
  SYN-2 refund on file + buyer says none       -> REFUND_CONFLICT
  SYN-3 disputed amount > order amount         -> AMOUNT_EXCEEDS_ORDER
  (the REAL case PP-R-BFB-10196981 also fires ADDR_MISMATCH + PROMPT_INJECTION)

Run: python -m seed.eval_cases
"""
from __future__ import annotations

import hashlib
import sys
from datetime import datetime, timezone
from pathlib import Path

import sqlalchemy as sa

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.collector import collect_case, upsert_case_from_dispute  # noqa: E402
from backend.app.db import SessionLocal, init_db  # noqa: E402
from backend.app.models import Case, Fact, Order, Shipment, SupportThread  # noqa: E402

ADDR = {"name": "Jane Roe", "line1": "500 W Madison St", "city": "Chicago",
        "state": "IL", "postal_code": "60661", "country": "US"}
ITEM = [{"item_id": "EVAL-1", "item_name": "Synthetic widget",
         "item_quantity": "1", "item_unit_price": {"currency_code": "USD", "value": "25.00"}}]

SCENARIOS = [
    {
        "dispute_id": "PP-SYN-0001",
        "capture_id": "SYN-CAP-0001",
        "order_amount": 2500, "dispute_amount": "25.00",
        "create_time": "2026-11-01T10:00:00Z",
        "delivered_at": datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc),  # AFTER dispute
        "ship_to": ADDR, "order_addr": ADDR,
        "thread": [{"from": "BUYER", "at": "2026-11-01T09:00:00Z",
                    "text": "Two weeks and nothing has arrived."}],
        "extra_facts": [],
    },
    {
        "dispute_id": "PP-SYN-0002",
        "capture_id": "SYN-CAP-0002",
        "order_amount": 2500, "dispute_amount": "25.00",
        "create_time": "2026-11-06T10:00:00Z",
        "delivered_at": datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc),
        "ship_to": ADDR, "order_addr": ADDR,
        "thread": [{"from": "BUYER", "at": "2026-11-06T09:30:00Z",
                    "text": "I never received it and no refund was issued."}],
        "extra_facts": [("REFUND_ID",
                         {"refund_id": "1XY23456AB7890123", "amount_minor": 2500})],
    },
    {
        "dispute_id": "PP-SYN-0003",
        "capture_id": "SYN-CAP-0003",
        "order_amount": 2500, "dispute_amount": "30.00",  # > order
        "create_time": "2026-11-06T10:00:00Z",
        "delivered_at": datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc),
        "ship_to": ADDR, "order_addr": ADDR,
        "thread": [{"from": "BUYER", "at": "2026-11-06T09:30:00Z",
                    "text": "Item not received."}],
        "extra_facts": [],
    },
]


def seed() -> dict:
    init_db()
    created = 0
    with SessionLocal() as session:
        for sc in SCENARIOS:
            if session.scalar(sa.select(Case).where(Case.dispute_id == sc["dispute_id"])):
                continue
            dispute = {
                "dispute_id": sc["dispute_id"],
                "reason": "MERCHANDISE_OR_SERVICE_NOT_RECEIVED",
                "status": "WAITING_FOR_SELLER_RESPONSE",
                "dispute_life_cycle_stage": "INQUIRY",
                "create_time": sc["create_time"],
                "dispute_amount": {"currency_code": "USD", "value": sc["dispute_amount"]},
                "disputed_transactions": [{"seller_transaction_id": sc["capture_id"]}],
            }
            order = Order(merchant_order_id=f"SYN-ORD-{sc['dispute_id'][-4:]}",
                          paypal_capture_id=sc["capture_id"],
                          amount_minor=sc["order_amount"], currency="USD",
                          items=ITEM, shipping_address=sc["order_addr"])
            session.add(order)
            session.flush()
            session.add(Shipment(order_id=order.id, carrier="Synthetic Carrier",
                                 tracking_number=f"SYN-TRK-{sc['dispute_id'][-4:]}",
                                 status="DELIVERED", ship_to=sc["ship_to"],
                                 delivered_at=sc["delivered_at"]))
            session.add(SupportThread(order_id=order.id, messages=sc["thread"]))
            case, _ = upsert_case_from_dispute(session, dispute)
            case.synthetic = True
            session.commit()
            collect_case(session, case, dispute)
            for kind, value in sc["extra_facts"]:
                session.add(Fact(
                    case_id=case.id, kind=kind, value={"v": value},
                    source_type="PAYPAL_API",
                    source_ref={"endpoint": "synthetic", "scenario": sc["dispute_id"]},
                    retrieved_at=datetime.now(timezone.utc),
                    sha256=hashlib.sha256(str(value).encode()).hexdigest()))
            session.commit()
            created += 1
    return {"created": created, "total_scenarios": len(SCENARIOS)}


if __name__ == "__main__":
    print("eval cases:", seed())
