# Judges' quickstart — 90 seconds

1. **Fastest:** open the hosted demo → https://proofpilot-lbpd.onrender.com
2. **On your machine (no credentials, no PayPal account):**
   ```bash
   git clone https://github.com/Tarakshgoyal/proofpilot
   cd proofpilot && docker compose up
   # open http://localhost:8000
   ```
   Runs in fixture mode: replays real recorded sandbox responses; the agent
   uses the deterministic rules engine when no AI key is set.

## What to look at (in order)

1. **Queue (AG Grid):** rows with a red `Contradictions` count are gated.
2. **Open `PP-R-BFB-10196981`:** the red BLOCKING banner; the agent's
   *"address matches"* claim struck through as `CONTRADICTED` (95131 vs 95311);
   `PROMPT_INJECTION` finding (buyer instruction-bait treated as data).
3. **Click "view source ↗"** on a verified claim → the exact passage
   highlighted in the carrier PDF (locator = deterministic code, not the LLM).
4. **Open `PP-R-YJH-10196983`:** decision card (transparent p + EV), then
   **Prepare response** → review the exact payload + hashes in the approval
   modal → approve → watch the execution receipt (this case was really
   submitted through the PayPal sandbox; see the audit chain at the bottom).
5. **Eval tab:** measured numbers on labelled scenarios — raw-LLM unsupported
   claim rate vs verifier-gated rate (0 by construction). Run it yourself:
   `Run quick eval`.

## Verify the honesty claims

- Every fact in the case panel carries a source type + SHA-256.
- The packet PDF (plan → download) contains only VERIFIED claims + a hash
  manifest.
- `DAY0_LOG.md` documents every sandbox gotcha we hit — and every fix.
- Tests: `python -m pytest tests/ -q` (23 tests incl. tamper, double-click,
  stale-on-change, timeout handling).
