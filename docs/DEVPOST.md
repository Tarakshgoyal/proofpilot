# Devpost submission text (paste into the form)

## Inspiration

Small merchants lose PayPal disputes on process, not facts: a 10-day response
clock, evidence scattered across carrier sites, inboxes and order systems, and
no legal team. AI looks like the answer — but an LLM that hallucinates a
delivery date doesn't just fail, it can lose the case and get the merchant's
account flagged. We wanted the AI to do what it's good at (reading a messy
evidence trail) while code does what IT's good at (never lying).

## What it does

ProofPilot is an AI dispute-resolution agent with a safety architecture:
**the AI investigates and proposes; deterministic code verifies and decides;
the merchant approves the exact bytes; and only verifier-passed evidence ever
reaches PayPal.**

- Pulls live disputes from the PayPal Disputes API via signature-verified
  webhooks, with a poller fallback; notifications are treated as signals, and
  state is always re-read from the API.
- Builds an evidence graph: every fact carries its source (PayPal API or
  merchant system), retrieval time and SHA-256. Uploaded documents are parsed
  to word-level coordinates (PyMuPDF).
- An LLM agent proposes cited claims — buyer text and document contents are
  untrusted data, never instructions, and the model has no write tools.
- A **deterministic verifier** checks every claim in plain code (delivery
  dates, address matches, tracking, refunds, verbatim document quotes via a
  locator that computes the highlight box). Unsupported claims are struck
  through in the UI and structurally excluded from submissions.
- A contradiction engine (address mismatches, delivery-after-dispute, refund
  conflicts, amount discrepancies, prompt-injection attempts) BLOCKS
  submissions until a human resolves them.
- A transparent decision layer (heuristic win probability + expected value,
  labelled as scenario assumptions) and a ReportLab evidence packet with a
  hash manifest.
- Approval is hash-bound to the exact payload; any evidence change makes the
  plan STALE. The executor re-validates fresh PayPal state, honours HATEOAS
  links, records dispatch state before the network call, dispatches exactly
  once (double-click safe), and never blind-retries an ambiguous write
  (EXECUTION_UNKNOWN). A tamper-evident hash-chained audit log covers every
  step, and the receipt honestly distinguishes acknowledged ≠ submitted ≠
  resolved.

## How we built it

Python/FastAPI + SQLAlchemy on Postgres (Neon), durable job worker with lease
recovery; PyMuPDF for document provenance; ReportLab packets; NVIDIA NIM
(`nvidia/nemotron-3-ultra`) behind an OpenAI-compatible adapter with a
deterministic rules fallback; React + Vite + TypeScript with AG Grid
Community for the queue and eval dashboards; deployed on Render (single
service, in-app worker) with GitHub Actions keepalive; judge-friendly
`docker compose up` fixture mode that needs zero credentials.

## Measured results (synthetic, labelled, reproducible in-app)

On our labelled scenario suite (clean wins, missing evidence, contradictions,
forged documents, prompt injection…), the raw model's asserted claims had a
~40% unsupported rate — the verifier struck every one (gated rate: 0 by
construction). Contradiction detection: 100% recall with 0 false positives
in the headline run. Runs are reproducible from the Eval tab (one API call)
and every number shown is whatever the harness measured. Sandbox adjudication
is a testing tool — we never present synthetic outcomes as real-world
win rates.

## Challenges we ran into

Sandbox dispute quirks (documented in DAY0_LOG.md): IN-region merchants
can't receive domestic IN payments; webhook event names use dot-notation;
buyer-side dispute creation needs a Log-in-with-PayPal consent flow (we route
through the Resolution Center like a real buyer); chargeback vs inquiry
lifecycle gates which HATEOAS actions exist; evidence multipart part names
differ per endpoint; and thinking-model JSON truncation (fixed with bounded
repair + rules fallback).

## Accomplishments we're proud of

The safety properties aren't slides — they're tests: tampered approval hash
refused; evidence change → STALE; double-click → exactly one dispatch;
timeout → EXECUTION_UNKNOWN, never auto-resent; LLM outage → rules fallback;
prompt injection → flagged as data. And a measured eval where the difference
between the raw model and the gated system is visible on screen.

## What we learned

That "AI + payments" is really "verification engineering with an LLM
inside." Every hard bug we hit was at the boundary where model output meets
money — and every fix was: make code the gatekeeper.

## What's next

Multiple dispute reasons (SNAD is already scenario-tested), refund-proof
flows, and the Log-in-with-PayPal consent flow for one-click API-created
test disputes.

## Built with

PayPal Disputes API · PayPal Shipping Trackers · PayPal webhooks (signature
verification) · NVIDIA NIM (nemotron-3-ultra) · Python · FastAPI · SQLAlchemy
· PostgreSQL (Neon) · PyMuPDF · ReportLab · React · TypeScript · AG Grid
Community · Render · Docker · GitHub Actions · Postman (published collection)

---

**Links to paste:** repo `https://github.com/Tarakshgoyal/proofpilot` ·
hosted demo `https://proofpilot-lbpd.onrender.com` · YouTube video `(add after recording)`
