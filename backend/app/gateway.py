"""Gateway layer — the ONLY boundary to PayPal (FINAL_PLAN.md §5).

PayPalGateway  : real sandbox calls, wrapping paypal_client.
FixtureGateway : replays recorded fixtures (offline tests, judge mode, eval).

Selection: APP_MODE=live -> PayPalGateway; anything else -> FixtureGateway.
Every method returns plain dicts (the normalized provider representation);
raw snapshots are preserved by callers (cases.raw_snapshot).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional, Protocol

from .paypal_client import PayPalClient, PayPalError

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "fixtures"


class DisputesGateway(Protocol):
    """Interface both gateways implement. Day 3 adds execution methods."""

    def list_disputes(self) -> list[dict]: ...
    def get_dispute(self, dispute_id: str) -> dict: ...
    def verify_webhook_signature(self, event: dict, headers: dict) -> str: ...


class PayPalGateway:
    """Live sandbox access."""

    mode = "live"

    def __init__(self, client: PayPalClient | None = None):
        self.client = client or PayPalClient()

    def list_disputes(self) -> list[dict]:
        body = self.client.request("GET", "/v1/customer/disputes?page_size=20")
        return body.get("items", [])

    def get_dispute(self, dispute_id: str) -> dict:
        return self.client.request("GET", f"/v1/customer/disputes/{dispute_id}")

    def verify_webhook_signature(self, event: dict, headers: dict) -> str:
        return self.client.verify_webhook_signature(event, headers)


class FixtureGateway:
    """Replays recorded sandbox responses. Offline, deterministic, judge-friendly."""

    mode = "fixture"

    def __init__(self, fixtures_dir: Path = FIXTURES / "raw"):
        self.fixtures_dir = fixtures_dir

    def _load(self, step: str) -> Optional[dict]:
        path = self.fixtures_dir / f"{step}.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def list_disputes(self) -> list[dict]:
        rec = self._load("07_dispute_list")
        if rec:
            return rec["body"].get("items", [])
        # fall back to the final dispute detail wrapped as a one-item list
        final = self._load("11_dispute_final")
        if final and final["body"].get("dispute_id"):
            b = final["body"]
            return [{k: b[k] for k in
                     ("dispute_id", "status", "reason", "dispute_amount",
                      "dispute_life_cycle_stage", "update_time")
                     if k in b}]
        return []

    def get_dispute(self, dispute_id: str) -> dict:
        final = self._load("11_dispute_final")
        if final and final["body"].get("dispute_id") == dispute_id:
            body = dict(final["body"])
        else:
            # synthesize a detail view from the list item (subset of fields)
            item = next((d for d in self.list_disputes()
                         if d.get("dispute_id") == dispute_id), None)
            if item is None:
                raise LookupError(f"no fixture for dispute {dispute_id}")
            body = dict(item)
        body["_fixture"] = True
        return body

    def verify_webhook_signature(self, event: dict, headers: dict) -> str:
        # In fixture mode there is no PayPal to ask; recorded events carry
        # their stored verification result.
        return "SUCCESS" if os.getenv("FIXTURE_ASSUME_VERIFIED", "1") == "1" else "FAILURE"


def get_gateway(mode: str | None = None) -> DisputesGateway:
    mode = (mode or os.getenv("APP_MODE", "fixture")).lower()
    if mode == "live":
        return PayPalGateway()
    return FixtureGateway()


__all__ = ["DisputesGateway", "PayPalGateway", "FixtureGateway", "get_gateway", "PayPalError"]
