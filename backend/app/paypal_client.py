"""Thin PayPal REST client for sandbox feasibility work (Day 0).

This module is the ONLY place that talks HTTP to PayPal. Day 1's
LiveGateway / FixtureGateway split wraps this module.

Conventions enforced here (per FINAL_PLAN.md §7):
- HATEOAS discipline: helpers to inspect links, never blind URL execution.
- Every lifecycle call can record a fixture under fixtures/raw/ for the
  FixtureGateway and the eval harness.
- Token caching; unsigned JWT builder for PayPal-Auth-Assertion (buyer-side
  sandbox dispute creation).
"""
from __future__ import annotations

import base64
import json
import os
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")

BASE_URL = os.getenv("PAYPAL_BASE_URL", "https://api-m.sandbox.paypal.com")
FIXTURES_DIR = ROOT / "fixtures" / "raw"


class PayPalError(RuntimeError):
    """Raised for any HTTP >= 400 from PayPal; body is the parsed JSON error."""

    def __init__(self, status: int, url: str, body):
        self.status = status
        self.url = url
        self.body = body if body is not None else {}
        detail = self.body.get("message") or json.dumps(self.body)[:500]
        name = self.body.get("name", "")
        super().__init__(f"HTTP {status} {url} :: {name} {detail}")


def _safe_json(resp: requests.Response):
    try:
        return resp.json()
    except ValueError:
        return {"raw": resp.text[:2000]}


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def build_auth_assertion(client_id: str, email: str) -> str:
    """Unsigned JWT that lets the REST app act on behalf of a sandbox customer.

    header {"alg":"none"} . payload {"iss":<client_id>, "email":<buyer>} .
    """
    header = b64url(json.dumps({"alg": "none"}).encode())
    payload = b64url(json.dumps({"iss": client_id, "email": email}).encode())
    return f"{header}.{payload}."


def save_fixture(step: str, method: str, url: str, status: int, body, note: str = "") -> Path:
    """Persist one API response verbatim (sanitized of our credentials)."""
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    path = FIXTURES_DIR / f"{step}.json"
    record = {
        "step": step,
        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "method": method,
        "url": url,
        "status": status,
        "note": note,
        "body": body,
    }
    path.write_text(json.dumps(record, indent=2, default=str), encoding="utf-8")
    return path


class PayPalClient:
    def __init__(self, client_id: str | None = None, client_secret: str | None = None,
                 base_url: str | None = None):
        self.client_id = client_id or os.getenv("PAYPAL_CLIENT_ID", "")
        self.client_secret = client_secret or os.getenv("PAYPAL_CLIENT_SECRET", "")
        self.base_url = (base_url or BASE_URL).rstrip("/")
        if not self.client_id or not self.client_secret:
            raise SystemExit(
                "PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET are not set. "
                "Copy .env.example to .env and fill them in — see SETUP_PAYPAL.md."
            )
        self._token: str | None = None
        self._token_expiry: float = 0.0

    # ------------------------------------------------------------------ auth
    def fetch_token(self) -> dict:
        resp = requests.post(
            f"{self.base_url}/v1/oauth2/token",
            auth=(self.client_id, self.client_secret),
            data={"grant_type": "client_credentials"},
            timeout=30,
        )
        body = _safe_json(resp)
        save_fixture("00_token", "POST", "/v1/oauth2/token", resp.status_code,
                     {"scope": body.get("scope"), "app_id": body.get("app_id"),
                      "expires_in": body.get("expires_in")})
        if resp.status_code >= 400:
            raise PayPalError(resp.status_code, resp.url, body)
        self._token = body["access_token"]
        self._token_expiry = time.time() + int(body.get("expires_in", 300))
        return body

    def token(self) -> str:
        if self._token and time.time() < self._token_expiry - 60:
            return self._token
        self.fetch_token()
        assert self._token
        return self._token

    # --------------------------------------------------------------- request
    def request(self, method: str, path: str, *, json_body=None, data=None,
                files=None, auth_assertion: str | None = None,
                step: str | None = None, note: str = "", timeout: int = 60) -> dict:
        url = path if path.startswith("http") else f"{self.base_url}{path}"
        headers = {"Authorization": f"Bearer {self.token()}"}
        if auth_assertion:
            headers["PayPal-Auth-Assertion"] = auth_assertion
        resp = requests.request(method, url, headers=headers, json=json_body,
                                data=data, files=files, timeout=timeout)
        body = _safe_json(resp)
        if step:
            # Never record multipart file bytes — record the JSON input only.
            save_fixture(step, method, url, resp.status_code, body, note)
        if resp.status_code >= 400:
            raise PayPalError(resp.status_code, url, body)
        return body

    # -------------------------------------------------- disputes convenience
    @staticmethod
    def link(dispute_or_body: dict, rel: str) -> dict | None:
        for lnk in dispute_or_body.get("links", []):
            if lnk.get("rel") == rel:
                return lnk
        return None

    @staticmethod
    def available_actions(dispute: dict) -> list[str]:
        return sorted({lnk.get("rel") for lnk in dispute.get("links", []) if lnk.get("rel")})

    def verify_webhook_signature(self, event_body: dict, headers: dict,
                                 webhook_id: str | None = None) -> str:
        """POST /v1/notifications/verify-webhook-signature -> SUCCESS|FAILURE."""
        webhook_id = webhook_id or os.getenv("PAYPAL_WEBHOOK_ID", "")
        if not webhook_id:
            return "SKIPPED_NO_WEBHOOK_ID"
        payload = {
            "auth_algo": headers.get("paypal-auth-algo"),
            "cert_url": headers.get("paypal-cert-url"),
            "transmission_id": headers.get("paypal-transmission-id"),
            "transmission_sig": headers.get("paypal-transmission-sig"),
            "transmission_time": headers.get("paypal-transmission-time"),
            "webhook_id": webhook_id,
            "webhook_event": event_body,
        }
        resp = requests.post(
            f"{self.base_url}/v1/notifications/verify-webhook-signature",
            headers={"Authorization": f"Bearer {self.token()}"},
            json=payload, timeout=30,
        )
        if resp.status_code >= 400:
            raise PayPalError(resp.status_code, resp.url, _safe_json(resp))
        return _safe_json(resp).get("verification_status", "UNKNOWN")
