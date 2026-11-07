# Day 0 Log — PayPal sandbox feasibility (Nov 7, 2026)

Working record of everything proven today and every sandbox gotcha + fix.
This is the institutional knowledge for the gateway, the demo, and the README.

## Proven end-to-end (real API writes, fixtures recorded)

| # | Call | Result | Fixture |
|---|---|---|---|
| 1 | `POST /v1/oauth2/token` | ✅ all dispute scopes incl. `disputes/create`, `disputes/update-seller`, `shipping/trackers/readwrite` | `00_token.json` |
| 2 | `POST /v2/checkout/orders` + buyer approval + `capture` | ✅ capture `4D070507B5530910M` COMPLETED (order 1), `4UM93153F2793482K` COMPLETED (order 2) | `01…03` |
| 3 | `POST /v1/shipping/trackers-batch` + read-back | ✅ tracker SHIPPED, carrier `OTHER` + `carrier_name_other` | `04/05` |
| 4 | Dispute exists via real buyer Resolution Center flow | ✅ `PP-R-KNZ-10196979`, INR reason, **CHARGEBACK** stage (card-funded + escalated) | `06/07` |
| 5 | `POST /v1/customer/disputes/{id}/provide-supporting-info` (multipart `input` + `supporting document` file part) | ✅ **merchant evidence PDF accepted** — the core write of the product | `08_*` |
| 6 | Webhooks `CUSTOMER.DISPUTE.CREATED/UPDATED` received via ngrok | ✅ **signature verification SUCCESS on all events** (verify-webhook-signature) | `fixtures/webhooks/*.json` |
| 7 | `adjudicate` | ⏳ blocked on CHARGEBACK (`ACTION_NOT_ALLOWED_IN_CURRENT_DISPUTE_STATE`) — expected; chargeback resolution is simulated by the card-network side. Adjudicate applies to INQUIRY. Pending: fresh INQUIRY case → provide-evidence → adjudicate → RESOLVED | — |

## Sandbox gotchas → fixes (each cost time today; none will cost time again)

1. **IN-region merchant + IN buyer = `UNSUPPORTED_PAYER_COUNTRY`.** PayPal
   doesn't do domestic India payments. Fix: create a **US-region personal
   sandbox account** as buyer; cross-border US→IN works.
2. **Webhook event names are `CUSTOMER.DISPUTE.CREATED|UPDATED|RESOLVED`**
   (dots, singular) — not `CUSTOMER_DISPUTES.*`. Wrong names →
   `VALIDATION_ERROR` on webhook create.
3. **`POST /v1/customer/disputes` (buyer-side create) needs prior buyer
   consent** via a Log in with PayPal flow on the REST app; without it the
   auth-assertion JWT (`{"iss": CLIENT_ID, "email": BUYER}`, alg:none) gets
   `invalid_request: No permissions to set target_client_id`. Day-0 path:
   buyer opens the case in the **Resolution Center UI** (what real buyers do).
   Webhook payloads conveniently carry `buyer_transaction_id` for the future
   API path (`disputed_transactions[0].buyer_transaction_id`).
4. **Resolution Center "message the seller" ≠ formal dispute.** A
   non-escalated PP-R case may be a buyer-seller message thread; escalation
   makes it a claim. Card-funded + escalated ⇒ **CHARGEBACK** stage
   (adjudicate unavailable; merchant responds via `provide-supporting-info`).
   Non-escalated card-funded INR ⇒ stays INQUIRY ⇒ merchant gets
   `provide-evidence` link ⇒ adjudicate available after evidence.
5. **HATEOAS rel names use underscores** (`provide_supporting_info`) while
   paths use hyphens. Link matching must normalize separators.
6. **Multipart part names**: `provide-evidence` file part = `file` (docs) or
   `evidence-file` (spec); `provide-supporting-info` file part = literally
   `supporting document` (with space). JSON body goes in a part named
   `input`. `adjudicate`/`require-evidence` want plain JSON (multipart → 415).
7. **Dispute list propagation lag**: a case filed in the UI can take
   **~10–30 min** to appear in `GET /v1/customer/disputes`. The webhook
   arrives in seconds — trust the webhook, then poll with patience. (Webhook
   first, API confirmation later = exactly the "signal vs state" design.)
8. **Checkout spinner hang**: after Pay Now, sandbox may spin forever because
   our test order has no `return_url`. The payment still completes — verify
   order status via API before believing the browser. Add a return_url for
   the demo.
9. require-evidence on a CHARGEBACK: `INVALID_REQUEST` — sandbox lever is for
   INQUIRY-stage disputes (flips UNDER_REVIEW → WAITING_FOR_x_RESPONSE).

## Receiver notes

- Signature verification requires the receiver to read `PAYPAL_WEBHOOK_ID`
  at startup — restart after changing `.env`.
- Stored unverified events carry their transmission headers; offline
  re-verification works (proven today: 2 events re-verified SUCCESS).

## Still open for Day 1

- [ ] INQUIRY case → `provide-evidence` (classic path) → `adjudicate
      SELLER_FAVOR` → `CUSTOMER.DISPUTE.RESOLVED` webhook (watcher running)
- [ ] Chargeback resolution simulation via `POST /v2/customer-support/process-chargeback`
- [ ] LiwP buyer-consent flow to enable API-created disputes (demo button)
- [ ] `return_url` on demo orders (fix spinner)
- [ ] Render deploy (stable webhook URL replaces ngrok)
