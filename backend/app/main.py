"""ProofPilot backend — Day 1.

- POST /webhooks/paypal : verified intake -> events table -> sync job
- /api/*                : case workspace surface (backend.app.api)
- GET /api/health       : liveness for Render (includes DB check)

Rules (FINAL_PLAN.md §4 Phase A): notifications are signals, not state.
An unverified event is stored as pending and NEVER triggers an action.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import sqlalchemy as sa
from fastapi import FastAPI, Request

from .api import router as api_router
from .config import settings

ROOT = Path(__file__).resolve().parents[2]
WEBHOOK_DIR = ROOT / "fixtures" / "webhooks"

app = FastAPI(title="ProofPilot", version="0.3.0")
app.include_router(api_router)

# serve the built frontend when present (dev uses `npm run dev` + proxy)
_FRONTEND = ROOT / "frontend" / "dist"
if _FRONTEND.exists():
    from fastapi.staticfiles import StaticFiles
    from fastapi.responses import FileResponse

    app.mount("/assets", StaticFiles(directory=_FRONTEND / "assets"), name="assets")

    @app.get("/", include_in_schema=False)
    async def index():
        return FileResponse(_FRONTEND / "index.html")


@app.on_event("startup")
async def _start_worker_if_configured() -> None:
    """Single-service mode (Render): run the job worker as an in-app task.

    Local dev keeps the dedicated `python -m backend.app.worker` process;
    set RUN_WORKER_IN_APP=0 to disable.
    """
    import os

    if os.getenv("RUN_WORKER_IN_APP", "0") != "1":
        return
    import asyncio

    from .worker import run_worker

    if os.getenv("SEED_ON_START", "0") == "1":
        try:
            from seed.backoffice import seed as seed_backoffice

            print("[startup] seeded:", seed_backoffice())
        except Exception as exc:  # noqa: BLE001
            print(f"[startup] seed failed: {exc}")
    asyncio.create_task(run_worker())


@app.get("/api/health")
async def health() -> dict:
    db_ok = True
    try:
        from .db import SessionLocal
        from .models import Case

        with SessionLocal() as session:
            session.query(Case).limit(1).all()
    except Exception:  # noqa: BLE001
        db_ok = False
    return {
        "status": "ok" if db_ok else "degraded",
        "mode": settings.app_mode,
        "paypal_env": settings.paypal_env,
        "webhook_configured": bool(settings.paypal_webhook_id),
        "db": "up" if db_ok else "down",
    }


@app.post("/webhooks/paypal")
async def paypal_webhook(request: Request) -> dict:
    """Accept a webhook, verify its signature, persist the event, queue a sync.

    The sync job re-reads dispute state from the API — the event itself never
    carries authoritative state into the case.
    """
    body = await request.json()
    headers = {k.lower(): v for k, v in request.headers.items()}
    event_id = body.get("id", f"unknown-{int(time.time())}")
    event_type = body.get("event_type", "UNKNOWN")

    verified = False
    verification_error = None
    if settings.paypal_webhook_id:
        try:
            from .gateway import get_gateway

            verified = get_gateway().verify_webhook_signature(
                body, headers) == "SUCCESS"
        except Exception as exc:  # noqa: BLE001 — keep receiver alive, mark pending
            verification_error = str(exc)
    else:
        verification_error = "PAYPAL_WEBHOOK_ID not configured"

    # durable record (file for audit + DB for processing)
    WEBHOOK_DIR.mkdir(parents=True, exist_ok=True)
    record = {
        "received_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "event_id": event_id, "event_type": event_type, "verified": verified,
        "verification_error": verification_error,
        "headers": {k: headers.get(k) for k in (
            "paypal-transmission-id", "paypal-transmission-time",
            "paypal-transmission-sig", "paypal-cert-url", "paypal-auth-algo")},
        "body": body,
    }
    (WEBHOOK_DIR / f"{event_id}.json").write_text(
        json.dumps(record, indent=2, default=str), encoding="utf-8")

    db_status = "skipped"
    if verified:
        try:
            from .db import SessionLocal, init_db
            from .models import WebhookEvent
            from .worker import enqueue

            init_db()
            with SessionLocal() as session:
                existing = session.scalar(
                    sa.select(WebhookEvent).where(WebhookEvent.event_id == event_id))
                if existing is None:
                    session.add(WebhookEvent(
                        event_id=event_id, event_type=event_type,
                        raw_body=body, verified=True))
                    enqueue(session, "sync_disputes", {})
                    session.commit()
                    db_status = "recorded+sync_queued"
                else:
                    db_status = "duplicate_ignored"
        except Exception as exc:  # noqa: BLE001
            db_status = f"db_error: {exc}"[:200]
    print(f"[webhook] {event_type} verified={verified} db={db_status}")
    return {"received": True, "verified": verified, "db": db_status}
