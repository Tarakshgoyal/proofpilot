# ProofPilot

> **No claim without a citation. No citation without a checksum.**

An AI dispute-resolution agent for PayPal merchants — built for the
[Build What's Next with PayPal and AI](https://paypalaihackathon.devpost.com/) hackathon.

Small merchants lose PayPal disputes on **process**, not facts: 10-day response
clocks, evidence scattered across carriers, inboxes and order systems, and no
legal team. LLMs promise help — but an LLM that hallucinates a delivery date
can **lose the case for you**. ProofPilot's answer is a safety architecture:

> **The AI investigates and proposes. Deterministic code verifies and decides.
> The merchant approves the exact bytes. Nothing unverifiable is ever submitted.**

## Try it

| Option | Link |
|---|---|
| **Hosted demo (live sandbox)** | https://proofpilot-lbpd.onrender.com |
| **Run it yourself — no credentials** | `git clone https://github.com/Tarakshgoyal/proofpilot && cd proofpilot && docker compose up` → http://localhost:8000 |
| **Demo video** | (link added at submission) |

What to try in the demo: open the dispute with the red **Contradictions** cell —
the agent's "address matches" claim is struck through (`CONTRADICTED`), a
`PROMPT_INJECTION` finding shows the buyer's instruction-bait treated as data,
and **click "view source ↗"** on any verified claim to see the exact highlighted
passage in the carrier document. Then the **Eval** tab: measured numbers,
raw-LLM unsupported-claim rate vs the verifier-gated rate (0 by construction).

## What's real vs simulated

**Real (every call hits the PayPal sandbox):** OAuth, dispute list/detail,
shipping trackers, `provide-evidence` / `provide-supporting-info` multipart
submissions with PDF attachments (hash-bound approval → execution → receipt),
webhook signature verification (`CUSTOMER.DISPUTE.*` events), sandbox
adjudication. Day-0 receipts in [`DAY0_LOG.md`](DAY0_LOG.md).

**Simulated (clearly labelled in the UI):** the merchant back office
(orders/shipments/threads/policies seeded in Postgres), the buyer accounts,
and every number on the Eval tab — synthetic scenarios, never presented as
real-world dispute outcomes.

## How it works

```text
PayPal sandbox ──webhook (signature-verified)──▶ events ─▶ sync job ─▶ case
                                                                │
              evidence graph: hash-stamped facts (PayPal API + merchant DB),
              documents parsed to word-level spans (PyMuPDF)
                                                                ▼
              LLM agent (read-only context, strict JSON schema, rules fallback)
                                                                ▼
              DETERMINISTIC VERIFIER — per-claim checkers + locator (exact/fuzzy
              quote location); only VERIFIED claims survive ────────┐
                                                                ▼   │ struck
              contradiction engine (5 rules) — blocking gates      │
                                                                ▼   ▼
              decision (transparent p + expected value) ─▶ packet (VERIFIED-only
              exhibits + hash manifest) ─▶ hash-bound approval ─▶ executor:
              fresh-state revalidation, HATEOAS-gated, exactly-once dispatch,
              EXECUTION_UNKNOWN never blind-retried ─▶ receipt + audit hash chain
```

Every step is a durable job (lease + recovery), every fact carries its source
pointer and SHA-256, every submission is bound to the exact payload hash the
merchant approved, and the audit log is a tamper-evident hash chain.

## The safety properties (all covered by tests)

- Tampered approval hash → **refused**; evidence change after approval → plan **STALE**
- Double-click approve → **exactly one** PayPal dispatch
- Network timeout mid-dispatch → `EXECUTION_UNKNOWN`, **never auto-resubmitted**
- LLM failure → deterministic **rules fallback** (analysis marked FALLBACK)
- Buyer prompt-injection text → flagged as data; the model has no write tools, ever

## Eval (measured, reproducible)

`POST /api/eval/run?quick=true` runs 11 labelled scenarios (full suite: 33)
through the **real** agent → verifier → decision pipeline and scores them.
Results appear in the UI's **Eval** tab, including the headline pair:
**raw-LLM unsupported-claim rate vs verifier-gated rate (0 by construction)**.
Adversarial scenarios include a forged carrier document and prompt injection.
Numbers are whatever the harness measures — synthetic data, honestly labelled.

## Stack

Python 3.12 · FastAPI · SQLAlchemy · PostgreSQL (Neon) · PyMuPDF (word-level
provenance + page rendering) · ReportLab (evidence packets) · NVIDIA NIM
(`nvidia/nemotron-3-ultra`) behind an OpenAI-compatible adapter with a
deterministic rules fallback · React + Vite + TypeScript · **AG Grid Community**
(the dispute queue and the eval dashboard) · **Render** (hosting) · Docker.

**PayPal APIs used:** OAuth2, Disputes (list/show/provide-evidence/
provide-supporting-info), shipping trackers, webhook signature verification,
sandbox lifecycle tools. **AI used:** NVIDIA NIM nemotron-3-ultra for
structured claim extraction with strict schema validation, one bounded repair
attempt, and read-only, case-scoped context.

## Development

```bash
cp .env.example .env          # PayPal sandbox creds (see SETUP_PAYPAL.md)
docker compose -f docker-compose.dev.yml up -d db
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt
python -m backend.app.db      # schema
python -m seed.backoffice     # demo merchant records
python -m backend.app.worker --drain   # process jobs
uvicorn backend.app.main:app --port 8001
# frontend dev: cd frontend && npm i && npm run dev
python -m pytest tests/ -q    # 23 tests
```

## License

MIT — see [LICENSE](LICENSE).
