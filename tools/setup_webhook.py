"""Create / list / delete the PayPal webhook subscription for dispute events.

Day 0 usage (local receiver + ngrok):
  1. Start the receiver:   uvicorn backend.app.main:app --port 8000
  2. Expose it:            ngrok http 8000
  3. Subscribe:            python tools/setup_webhook.py https://<ngrok-id>.ngrok.app/webhooks/paypal
                           (or .../webhooks/paypal/ — ngrok free gives https URLs)
  4. Copy the printed webhook id into .env as PAYPAL_WEBHOOK_ID=...

  python tools/setup_webhook.py list
  python tools/setup_webhook.py delete <webhook_id>
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.paypal_client import PayPalClient  # noqa: E402

DISPUTE_EVENTS = [
    "CUSTOMER.DISPUTE.CREATED",
    "CUSTOMER.DISPUTE.UPDATED",
    "CUSTOMER.DISPUTE.RESOLVED",
]


def create(url: str) -> None:
    body = PayPalClient().request(
        "POST", "/v1/notifications/webhooks",
        json_body={
            "url": url,
            "event_types": [{"name": name} for name in DISPUTE_EVENTS],
        },
        step="90_webhook_create", note=f"url={url}",
    )
    print(f"webhook id : {body['id']}")
    print(f"url        : {body.get('url')}")
    print(f"events     : {[e['name'] for e in body.get('event_types', [])]}")
    print(f"\nAdd to .env:\n  PAYPAL_WEBHOOK_ID={body['id']}")


def list_webhooks() -> None:
    body = PayPalClient().request("GET", "/v1/notifications/webhooks", step="91_webhook_list")
    hooks = body.get("webhooks", [])
    if not hooks:
        print("No webhooks subscribed.")
        return
    for h in hooks:
        print(f"{h['id']}  {h.get('url')}  events={[e['name'] for e in h.get('event_types', [])]}")


def delete(webhook_id: str) -> None:
    PayPalClient().request("DELETE", f"/v1/notifications/webhooks/{webhook_id}",
                           step="92_webhook_delete")
    print(f"deleted {webhook_id}")


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "list":
        list_webhooks()
    elif args and args[0] == "delete" and len(args) == 2:
        delete(args[1])
    elif len(args) == 1 and args[0].startswith("https://"):
        create(args[0])
    else:
        print(__doc__)
        sys.exit(1)
