# PayPal sandbox setup — 10 minutes (do this before anything else)

Day 0 lives or dies on this checklist. Everything below happens in the browser;
the scripts in `tools/` do the rest.

## 1. Developer account + sandbox accounts

1. Go to <https://developer.paypal.com> and **Log In** with any PayPal account
   (your personal one is fine — sandbox is fully isolated).
2. **Testing Tools → Sandbox Accounts**: you should see a default
   **Business** account and a default **Personal** (buyer) account.
   - Note the buyer account's **email address** → `PAYPAL_BUYER_EMAIL`.
   - If you need its password: **⋯ → View/Edit account** to view or reset it
     (you'll use it once to approve a test payment in the browser).
   - Sandbox personal accounts ship with a test balance — no funding needed.
   - Optional: click **Upgrade to Pro**? **No.** Do nothing else here.

## 2. REST app with the right features

1. **Apps & Credentials** → make sure the **Sandbox** toggle is selected.
2. **Create App**:
   - App name: `proofpilot`, type: **Merchant**.
   - Under features, ensure at minimum: **Accept payments**, **Transaction
     Search**, and **Disputes** are enabled (tick them before creating).
3. Copy **Client ID** and **Secret** → `.env` (`PAYPAL_CLIENT_ID`,
   `PAYPAL_CLIENT_SECRET`).

> If the Disputes feature is missing/unavailable, create the app anyway and run
> `python tools/day0_lifecycle.py check` — it prints the granted scopes and
> warns when `disputes` is absent.

## 3. Local environment

```bash
cd proofpilot
python -m venv .venv
.venv\Scripts\activate            # Windows (use source .venv/bin/activate on mac/linux)
pip install -r requirements.txt
copy .env.example .env            # then fill in the values from steps 1–2
python tools/day0_lifecycle.py check
```

Expected: `app_id`, a scope string that includes `dispute`, no errors.

## 4. Webhook receiver (ngrok)

Leave running in two terminals:

```bash
# terminal 1 — the receiver
uvicorn backend.app.main:app --port 8000

# terminal 2 — public tunnel (install from https://ngrok.com)
ngrok http 8000
```

Then subscribe (copy the https forwarding URL ngrok shows you):

```bash
python tools/setup_webhook.py https://<your-ngrok-id>.ngrok.app/webhooks/paypal
# copy the printed webhook id into .env as PAYPAL_WEBHOOK_ID=
```

## 5. The Day-0 lifecycle

```bash
python tools/day0_lifecycle.py all
```

It pauses once — to approve the payment in your browser as the sandbox buyer.
Everything else is automated, and every PayPal response is saved under
`fixtures/raw/`.

**Day 0 exit condition:** capture `COMPLETED` → dispute created →
`provide-evidence` accepted → `adjudicate` returns SELLER_FAVOR → final status
`RESOLVED`, with fixtures on disk and at least one verified webhook in
`fixtures/webhooks/`.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `check` shows no `dispute` scope | Edit the REST app in the dashboard, enable the Disputes feature, save, retry |
| 401 on any call | Client id/secret wrong, or you used live credentials against sandbox — re-copy from the **Sandbox** tab |
| create dispute: `TRANSACTION_NOT_ELIGIBLE`-style error | The capture is too fresh — wait a minute and rerun `dispute` (the script is resumable) |
| provide-evidence 400 on the file | The script auto-retries with the alternate part name; if both fail, paste the error — it names the allowed types |
| webhook never arrives | Check `ngrok` still running; `python tools/setup_webhook.py list`; the receiver logs every hit |

## Day-0 learning: creating disputes as a buyer

`POST /v1/customer/disputes` (sandbox-only) with a `PayPal-Auth-Assertion`
JWT (`{"iss": CLIENT_ID, "email": BUYER_EMAIL}`, alg:none) requires **prior
buyer consent** via a *Log in with PayPal* flow enabled on the REST app
(app settings → Features → Log in with PayPal, with the
`https://uri.paypal.com/services/disputes/create` scope in the consent),
otherwise PayPal returns `invalid_request: No permissions to set
target_client_id`.

**Day-0 shortcut (documented path):** the buyer opens the dispute through
the sandbox **Resolution Center** UI (sandbox.paypal.com → the transaction →
Report a problem → Item not received). This is what real buyers do; the
dispute then flows to the merchant API exactly the same, and the
`CUSTOMER.DISPUTE.CREATED` webhook fires.

API-created disputes remain desirable later for the one-click demo button;
that needs the LiwP consent flow (TODO Day 1+).
