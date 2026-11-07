"""ProofPilot backend — Day 0 skeleton.

- POST /webhooks/paypal : verified intake (signature check + raw persistence)
- GET  /api/health      : liveness for Render
Day 1 adds the case ingestion worker, DB persistence and the API surface.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from fastapi import FastAPI, Request

from .config import settings

ROOT = Path(__file__).resolve().parents[2]
WEBHOOK_DIR = ROOT / "fixtures" / "webhooks"

app = FastAPI(title="ProofPilot", version="0.1.0")


@app.get("/api/health")
async def health() -> dict:
    return {
        "status": "ok",
        "mode": settings.app_mode,
        "paypal_env": settings.paypal_env,
        "webhook_configured": bool(settings.paypal_webhook_id),
    }


@app.post("/webhooks/paypal")
async def paypal_webhook(request: Request) -> dict:
    """Accept a webhook, verify its signature, persist the raw event.

    Rules (FINAL_PLAN.md §4 Phase A): notifications are signals, not state.
    An unverified event is stored as pending and NEVER triggers an action.
    """
    body = await request.json()
    headers = {k.lower(): v for k, v in request.headers.items()}
    event_id = body.get("id", f"unknown-{int(time.time())}")
    event_type = body.get("event_type", "UNKNOWN")

    verified = False
    verification_error = None
    if settings.paypal_webhook_id:
        try:
            from .paypal_client import PayPalClient

            verified = PayPalClient().verify_webhook_signature(
                body, headers, settings.paypal_webhook_id
            ) == "SUCCESS"
        except Exception as exc:  # noqa: BLE001 — keep receiver alive, mark pending
            verification_error = str(exc)
    else:
        verification_error = "PAYPAL_WEBHOOK_ID not configured"

    WEBHOOK_DIR.mkdir(parents=True, exist_ok=True)
    record = {
        "received_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "event_id": event_id,
        "event_type": event_type,
        "verified": verified,
        "verification_error": verification_error,
        "headers": {
            k: headers.get(k) for k in (
                "paypal-transmission-id", "paypal-transmission-time",
                "paypal-transmission-sig", "paypal-cert-url", "paypal-auth-algo",
            )
        },
        "body": body,
    }
    (WEBHOOK_DIR / f"{event_id}.json").write_text(
        json.dumps(record, indent=2, default=str), encoding="utf-8"
    )
    print(f"[webhook] {event_type} verified={verified} -> fixtures/webhooks/{event_id}.json")
    return {"received": True, "verified": verified}
