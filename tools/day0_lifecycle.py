"""Day 0 — PayPal sandbox lifecycle feasibility walk (FINAL_PLAN.md §20, Day 0).

Proves the full loop end to end and records every response as a fixture:

  order (merchant) -> buyer approves in browser -> capture (merchant)
  -> shipping tracker (merchant) -> create dispute (buyer, auth-assertion)
  -> poll until actionable -> provide-evidence (multipart, merchant)
  -> adjudicate SELLER_FAVOR (sandbox-only) -> final state

Every step is resumable: ids live in fixtures/state.json, so you can run
steps one at a time and rerun after fixing any failure.

Usage:
  python tools/day0_lifecycle.py check
  python tools/day0_lifecycle.py order
  ... open the printed approve URL, log in as the sandbox buyer, pay ...
  python tools/day0_lifecycle.py capture
  python tools/day0_lifecycle.py tracker
  python tools/day0_lifecycle.py dispute
  python tools/day0_lifecycle.py wait-ready
  python tools/day0_lifecycle.py evidence
  python tools/day0_lifecycle.py adjudicate
  python tools/day0_lifecycle.py show
  python tools/day0_lifecycle.py all          # interactive, pauses for approval
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.paypal_client import (  # noqa: E402
    PayPalClient, PayPalError, build_auth_assertion,
)

STATE_PATH = ROOT / "fixtures" / "state.json"
ASSETS_DIR = ROOT / "fixtures" / "assets"

CARRIER_NAME = "ProofPilot Test Carrier"
REASON = "MERCHANDISE_OR_SERVICE_NOT_RECEIVED"


def banner(title: str) -> None:
    print(f"\n{'=' * 66}\n  {title}\n{'=' * 66}")


def load_state() -> dict:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    return {}


def save_state(**kwargs) -> None:
    state = load_state()
    state.update(kwargs)
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")
    print(f"[state] {', '.join(kwargs)}")


def money() -> tuple[str, str]:
    value = f"{float(os.getenv('TEST_AMOUNT', '25.00')):.2f}"
    return value, os.getenv("TEST_CURRENCY", "USD")


def client() -> PayPalClient:
    return PayPalClient()


# --------------------------------------------------------------------- steps
def step_check() -> None:
    banner("STEP 0 — OAuth token + scopes")
    body = client().fetch_token()
    print(f"app_id : {body.get('app_id')}")
    print(f"scope  : {body.get('scope')}")
    if "dispute" not in body.get("scope", ""):
        print("\n!! 'disputes' scope NOT present. In the developer dashboard, edit")
        print("!! your REST app and enable the Disputes feature, then retry.")


def step_order() -> None:
    banner("STEP 1 — Create order (merchant)")
    value, currency = money()
    body = client().request(
        "POST", "/v2/checkout/orders",
        json_body={
            "intent": "CAPTURE",
            "purchase_units": [{
                "reference_id": "pp-demo-001",
                "description": "ProofPilot demo widget",
                "amount": {"currency_code": currency, "value": value},
            }],
            "application_context": {
                "brand_name": "ProofPilot Demo Store",
                "user_action": "PAY_NOW",
            },
        },
        step="01_order_create",
    )
    approve = next((l["href"] for l in body.get("links", []) if l.get("rel") == "approve"), None)
    save_state(order_id=body["id"], approve_url=approve)
    print(f"order id   : {body['id']}  ({body['status']})")
    print(f"\nNEXT — approve as the buyer in your browser:\n\n  1. Open: {approve}")
    buyer = os.getenv("PAYPAL_BUYER_EMAIL", "<PAYPAL_BUYER_EMAIL from .env>")
    print(f"  2. Log in with the SANDBOX BUYER: {buyer}")
    print("  3. Click Pay Now.")
    print("\nThen run:  python tools/day0_lifecycle.py capture")


def step_capture() -> None:
    banner("STEP 2 — Capture payment (merchant)")
    state = load_state()
    order_id = state.get("order_id")
    if not order_id:
        raise SystemExit("No order_id in state — run `order` first.")
    order = client().request("GET", f"/v2/checkout/orders/{order_id}", step="02_order_get")
    print(f"order status: {order['status']}")
    if order["status"] != "APPROVED":
        raise SystemExit(
            "Order is not APPROVED yet — open the approve URL from `order` and pay as the buyer."
        )
    body = client().request("POST", f"/v2/checkout/orders/{order_id}/capture",
                            json_body={}, step="03_order_capture")
    capture = body["purchase_units"][0]["payments"]["captures"][0]
    save_state(capture_id=capture["id"], capture_status=capture["status"])
    print(f"capture id  : {capture['id']}  ({capture['status']})")
    print("This capture id is the seller_transaction_id used to create the dispute.")


def step_tracker() -> None:
    banner("STEP 3 — Seed shipping tracker (merchant)")
    state = load_state()
    capture_id = state.get("capture_id")
    if not capture_id:
        raise SystemExit("No capture_id in state — run `capture` first.")
    tracking_number = state.get("tracking_number") or f"PP{int(time.time())}IN"
    body = client().request(
        "POST", "/v1/shipping/trackers-batch",
        json_body={
            "trackers": [{
                "transaction_id": capture_id,
                "tracking_number": tracking_number,
                "status": "SHIPPED",
                "carrier": "OTHER",
                "carrier_name_other": CARRIER_NAME,
            }],
        },
        step="04_tracker_create",
        note=f"tracking {tracking_number} on capture {capture_id}",
    )
    save_state(tracking_number=tracking_number)
    print(f"tracker     : {tracking_number} -> {json.dumps(body.get('tracker_ids', body))[:200]}")
    readback = client().request(
        "GET", f"/v1/shipping/trackers?transaction_id={capture_id}", step="05_tracker_read"
    )
    print(f"read-back   : {json.dumps(readback)[:300]}")


def step_dispute() -> None:
    banner("STEP 4 — Create dispute (buyer via auth-assertion)")
    state = load_state()
    capture_id = state.get("capture_id")
    if not capture_id:
        raise SystemExit("No capture_id in state — run `capture` first.")
    buyer_email = os.getenv("PAYPAL_BUYER_EMAIL", "")
    if not buyer_email:
        raise SystemExit("PAYPAL_BUYER_EMAIL not set in .env.")
    value, currency = money()
    assertion = build_auth_assertion(os.environ["PAYPAL_CLIENT_ID"], buyer_email)
    # Documented shape (developer.paypal.com/disputes/test-go-live): the create
    # call is multipart/form-data with the JSON in a part named `input`.
    dispute_input = {
        "disputed_transactions": [{"seller_transaction_id": capture_id}],
        "reason": REASON,
        "dispute_amount": {"currency_code": currency, "value": value},
        "extensions": {
            "merchant_contacted": True,
            "merchant_contacted_outcome": "NO_RESPONSE",
            "merchandize_dispute_properties": {"issue_type": "PRODUCT"},
        },
    }
    body = client().request(
        "POST", "/v1/customer/disputes",
        files={"input": (None, json.dumps(dispute_input), "application/json")},
        auth_assertion=assertion,
        step="06_dispute_create",
        note=f"buyer {buyer_email} disputes capture {capture_id} ({REASON})",
    )
    save_state(dispute_id=body["dispute_id"])
    print(f"dispute id  : {body['dispute_id']}")
    print(f"status      : {body.get('status')}  stage: {body.get('dispute_life_cycle_stage')}")
    print("Next: python tools/day0_lifecycle.py wait-ready")


def wait_for(client_: PayPalClient, dispute_id: str, predicate, label: str,
             timeout_s: int = 300, interval_s: int = 20, step: str = "07_dispute_show") -> dict:
    deadline = time.time() + timeout_s
    attempt = 0
    while time.time() < deadline:
        dispute = client_.request("GET", f"/v1/customer/disputes/{dispute_id}",
                                  step=f"{step}_{attempt}")
        attempt += 1
        if predicate(dispute):
            return dispute
        print(f"  [{int(time.time() % 100000)}] status={dispute.get('status')} "
              f"links={client_.available_actions(dispute)} — not yet {label}; "
              f"retrying in {interval_s}s")
        time.sleep(interval_s)
    raise SystemExit(f"Timed out waiting for dispute to become {label}.")


def print_dispute(d: dict, c: PayPalClient) -> None:
    print(f"  dispute_id       : {d.get('dispute_id')}")
    print(f"  status           : {d.get('status')}")
    print(f"  stage            : {d.get('dispute_life_cycle_stage')}")
    print(f"  reason           : {d.get('reason')}")
    print(f"  amount           : {json.dumps(d.get('dispute_amount'))}")
    print(f"  seller_due_date  : {d.get('seller_response_due_date')}")
    print(f"  evidences        : {len(d.get('evidences', []))}")
    print(f"  messages         : {len(d.get('messages', []))}")
    print(f"  allowed_response : {json.dumps(d.get('allowed_response_options'))[:300]}")
    print(f"  links (actions)  : {c.available_actions(d)}")


def step_require_evidence() -> None:
    """Sandbox-only lever: flips UNDER_REVIEW -> WAITING_FOR_SELLER_RESPONSE."""
    banner("STEP 4b — Require evidence from seller (sandbox-only)")
    state = load_state()
    dispute_id = state.get("dispute_id")
    if not dispute_id:
        raise SystemExit("No dispute_id in state.")
    if os.getenv("PAYPAL_ENV", "sandbox") != "sandbox":
        raise SystemExit("Refusing: sandbox-only operation outside sandbox.")
    body = client().request(
        "POST", f"/v1/customer/disputes/{dispute_id}/require-evidence",
        json_body={"action": "SELLER_EVIDENCE"},
        step="06b_require_evidence",
        note="sandbox test tool; surfaces the provide-evidence action",
    )
    print(f"  require-evidence sent -> {json.dumps(body)[:300]}")


def step_wait_ready() -> None:
    banner("STEP 5 — Wait until seller can act (WAITING_FOR_SELLER_RESPONSE)")
    state = load_state()
    dispute_id = state.get("dispute_id")
    if not dispute_id:
        raise SystemExit("No dispute_id in state — run `dispute` first.")
    c = client()
    d = wait_for(c, dispute_id,
                 lambda d_: d_.get("status") == "WAITING_FOR_SELLER_RESPONSE"
                 and c.link(d_, "provide-evidence") is not None,
                 label="WAITING_FOR_SELLER_RESPONSE + provide-evidence link")
    print_dispute(d, c)


def build_evidence_pdf() -> Path:
    """One-page proof-of-fulfillment PDF (ReportLab)."""
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    state = load_state()
    path = ASSETS_DIR / "evidence_fulfillment.pdf"
    c = canvas.Canvas(str(path), pagesize=letter)
    c.setFont("Helvetica-Bold", 16)
    c.drawString(72, 740, "Proof of Fulfillment — Carrier Record")
    c.setFont("Helvetica", 11)
    lines = [
        "",
        f"Carrier           : {CARRIER_NAME}",
        f"Tracking number   : {state.get('tracking_number', '<tracking>')}",
        f"Transaction       : {state.get('capture_id', '<capture>')}",
        f"Delivered to      : 2211 N 1st St, San Jose, CA 95131, US",
        f"Delivery status   : DELIVERED",
        f"Delivery date     : 2026-11-05",
        "",
        "This record was generated for PayPal sandbox dispute testing.",
    ]
    y = 700
    for line in lines:
        c.drawString(72, y, line)
        y -= 18
    c.save()
    return path


def step_evidence() -> None:
    """Submit evidence through whichever action the dispute's HATEOAS links allow.

    - INQUIRY/WAITING_FOR_SELLER_RESPONSE -> provide-evidence (input + file)
    - CHARGEBACK/UNDER_REVIEW              -> provide-supporting-info
                                              (input + 'supporting document')
    The link present on the dispute decides; we never blind-post.
    """
    banner("STEP 6 — Provide evidence (multipart, merchant)")
    state = load_state()
    dispute_id = state.get("dispute_id")
    if not dispute_id:
        raise SystemExit("No dispute_id in state — run `dispute` first.")
    c = client()
    dispute = c.request("GET", f"/v1/customer/disputes/{dispute_id}")
    has_provide = c.link(dispute, "provide-evidence") is not None
    has_supporting = c.link(dispute, "provide-supporting-info") is not None
    if not (has_provide or has_supporting):
        raise SystemExit(
            f"No evidence action available (status={dispute.get('status')}, "
            f"links={c.available_actions(dispute)}). Run `require-evidence` or `wait-ready` first."
        )
    endpoint = "provide-evidence" if has_provide else "provide-supporting-info"
    pdf = build_evidence_pdf()
    print(f"evidence pdf : {pdf.relative_to(ROOT)}")
    print(f"action       : {endpoint}  (status={dispute.get('status')}, "
          f"stage={dispute.get('dispute_life_cycle_stage')})")

    input_json = {
        "evidence_type": "PROOF_OF_FULFILLMENT",
        "tracking_info": [{
            "carrier_name": CARRIER_NAME,
            "tracking_number": state.get("tracking_number", ""),
        }],
        "notes": "Carrier record confirms shipment and delivery to the buyer's confirmed address.",
    }
    file_part_names = (["file", "evidence-file"] if has_provide
                       else ["supporting document", "file"])
    for file_part in file_part_names:
        try:
            with open(pdf, "rb") as fh:
                body = c.request(
                    "POST", f"/v1/customer/disputes/{dispute_id}/{endpoint}",
                    files={
                        "input": (None, json.dumps(input_json), "application/json"),
                        file_part: ("evidence_fulfillment.pdf", fh, "application/pdf"),
                    },
                    step=f"08_{endpoint}_{file_part.replace(' ', '_').replace('-', '_')}",
                    note=f"PROOF_OF_FULFILLMENT + {pdf.name}",
                )
            print(f"  submitted via '{file_part}' -> success")
            print_dispute(body, c)
            return
        except PayPalError as err:
            print(f"  part '{file_part}' rejected: {str(err)[:160]}")
            continue
    raise SystemExit("All file part names rejected — paste the last error to debug.")

def step_wait_under_review() -> None:
    banner("STEP 6b — Wait for UNDER_REVIEW (needed before adjudicate)")
    state = load_state()
    dispute_id = state.get("dispute_id")
    c = client()
    d = wait_for(c, dispute_id, lambda d_: d_.get("status") == "UNDER_REVIEW",
                 label="UNDER_REVIEW", timeout_s=240, interval_s=15,
                 step="09_dispute_under_review")
    print_dispute(d, c)


def step_adjudicate() -> None:
    banner("STEP 7 — Adjudicate SELLER_FAVOR (sandbox-only test tool)")
    state = load_state()
    dispute_id = state.get("dispute_id")
    if not dispute_id:
        raise SystemExit("No dispute_id in state — run `dispute` first.")
    body = client().request(
        "POST", f"/v1/customer/disputes/{dispute_id}/adjudicate",
        json_body={"adjudication_outcome": "SELLER_FAVOR"},
        step="10_adjudicate",
        note="sandbox test adjudication; NOT a real outcome signal",
    )
    print(f"  adjudicated -> {json.dumps(body.get('adjudications', body))[:300]}")


def step_show() -> None:
    banner("STEP 8 — Final dispute state")
    state = load_state()
    dispute_id = state.get("dispute_id")
    if not dispute_id:
        raise SystemExit("No dispute_id in state.")
    c = client()
    d = c.request("GET", f"/v1/customer/disputes/{dispute_id}", step="11_dispute_final")
    print_dispute(d, c)
    print(f"\n  DISPUTE OUTCOME : {json.dumps(d.get('dispute_outcome'))}")


def step_all() -> None:
    step_check()
    step_order()
    print("\n>>> ACTION NEEDED: open the approve URL above, pay as the sandbox buyer <<<")
    input(">>> Press ENTER here once the payment is done... ")
    step_capture()
    step_tracker()
    step_dispute()
    step_wait_ready()
    step_evidence()
    step_wait_under_review()
    step_adjudicate()
    step_show()
    banner("DAY 0 EXIT CONDITION MET IF: capture COMPLETED, evidence accepted, dispute RESOLVED")
    print("Fixtures saved under fixtures/raw/ — verify with:  ls fixtures/raw")


STEPS = {
    "check": step_check, "order": step_order, "capture": step_capture,
    "tracker": step_tracker, "dispute": step_dispute, "require-evidence": step_require_evidence,
    "wait-ready": step_wait_ready, "evidence": step_evidence,
    "wait-under-review": step_wait_under_review,
    "adjudicate": step_adjudicate, "show": step_show, "all": step_all,
}


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in STEPS:
        print(__doc__)
        sys.exit(1)
    try:
        STEPS[sys.argv[1]]()
    except PayPalError as exc:
        print(f"\nPAYPAL ERROR:\n{exc}", file=sys.stderr)
        sys.exit(2)
