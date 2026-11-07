# ProofPilot

> **No claim without a citation. No citation without a checksum.**

An AI dispute-resolution agent for small PayPal merchants, built for the
[Build What's Next with PayPal and AI](https://paypalaihackathon.devpost.com/)
hackathon. ProofPilot pulls disputes from the PayPal Disputes API, investigates
the transaction and the merchant's own records, reads supporting documents down
to the highlighted passage, flags missing and contradictory evidence, drafts a
fully-cited response packet — and submits it through the real PayPal API only
after the merchant approves the exact payload.

**Status: Day 0 — PayPal sandbox feasibility (in progress).** The product
builds over Nov 7–12; see the day-by-day plan in the project workspace.

## What's here so far

| Path | What it is |
|---|---|
| `tools/day0_lifecycle.py` | Full sandbox lifecycle walk: order → capture → tracker → dispute → evidence → adjudicate, recording every response as a fixture |
| `tools/setup_webhook.py` | Subscribe/list/delete dispute webhooks |
| `backend/app/main.py` | FastAPI receiver for `POST /webhooks/paypal` with signature verification |
| `backend/app/models.py` | The evidence-graph schema (cases, facts, documents, spans, claims, plans, actions, hash-chained audit log) |
| `fixtures/` | Recorded sandbox responses — the seed of the fixture gateway + eval harness |
| `SETUP_PAYPAL.md` | 10-minute sandbox setup checklist — **start here** |

## Quick start (sandbox, live mode)

```bash
pip install -r requirements.txt
cp .env.example .env        # fill in PayPal sandbox credentials (see SETUP_PAYPAL.md)
uvicorn backend.app.main:app --port 8000
python tools/day0_lifecycle.py all
```

## Principles

- **AI investigates and proposes. Deterministic code verifies and decides.**
  The human approves the exact bytes. Every action is bound to the evidence
  that was approved.
- Buyer text and uploaded documents are **untrusted data**, never instructions.
- Sandbox adjudication is a testing tool, **never** presented as a real-world
  outcome signal.

## License

MIT — see [LICENSE](LICENSE).
