# ProofPilot — demo video script (2:45, word-for-word)

> **Recording setup (do this first)**
> - OBS Studio (or Xbox Game Bar: Win+G) → capture the browser at 1600×900 or larger
> - Browser: open **https://proofpilot-lbpd.onrender.com** (or localhost if you prefer), zoom ~110% so text is readable
> - Mic: any; speak slightly slower than feels natural. **No background music** (copyright)
> - Before recording: run one `Re-run agent` on PP-R-BFB and PP-R-YJH so results are fresh, open the queue
> - Record in ONE take if you can; a second take as backup. Judges only watch 3:00.

---

### 0:00–0:18 — The problem (screen: queue, don't click yet)

> "Small merchants lose PayPal disputes on process, not facts. A ten-day
> response clock, evidence scattered across carriers, inboxes, and order
> systems — and no legal team. AI promises help, but an LLM that hallucinates
> a delivery date can lose the case for you.
>
> ProofPilot is an AI dispute agent with a safety architecture:
> the AI investigates — deterministic code verifies — and the merchant
> approves the exact bytes that get submitted."

### 0:18–0:40 — Live queue (screen: point mouse along the grid rows)

> "This queue is live AG Grid over real PayPal sandbox disputes. Rows turn red
> when the contradiction engine fires. This one —" *(hover the PP-R-BFB row,
> point at Contradictions = 2)* "— has two findings. Let's open it."

*(Click the PP-R-BFB-10196981 row.)*

### 0:40–1:25 — The core demo moment (screen: case panel)

> "The agent proposed six claims. The **verifier** — plain code, not the
> model — checked every one against hash-stamped evidence.
>
> This claim says the addresses match." *(point at the struck-through
> CONTRADICTED claim)* "The verifier struck it: the carrier shipped to
> nine-five-one-three-one, the order says nine-five-three-one-one. That's a
> BLOCKING contradiction — no submission can be built until a human resolves
> it.
>
> And this buyer message —" *(point at PROMPT_INJECTION card)* "— tries to
> instruct the agent to auto-refund. It's treated as data, flagged, and
> ignored. The model never had write access in the first place.
>
> Now watch the provenance." *(click **view source ↗** on the delivery claim)*
> "Clicking a claim jumps to the exact passage in the carrier document —
> located by deterministic code down to the highlighted box."

*(Close the viewer.)*

### 1:25–2:05 — Approve exact bytes → real submission (screen: switch to the YJH row, click it)

> "On a clean case, the decision engine shows its math: probability of
> winning with transparent weights, expected value of fighting versus
> folding — labelled as scenario assumptions, not predictions.
>
> **Prepare response** builds the evidence packet — only VERIFIED claims,
> each with a checksum — and this payload is hash-bound." *(click Prepare
> response; when the plan appears, click **Review & approve**)*
>
> "The approval modal shows the exact JSON that will be sent, the payload
> hash, the packet hash. If any evidence changes after this moment, the
> approval is invalidated automatically.
>
> Approve —" *(click **Approve & submit through PayPal**)* "— and the
> executor re-reads the dispute fresh from PayPal, checks the deadline and
> the permitted action, records dispatch state *before* touching the
> network, and submits exactly once.
>
> The receipt: HTTP two-hundred from PayPal's sandbox. The audit chain —
> hash-linked, tamper-evident — verifies green. A timeout here would be
> flagged execution-unknown and never blindly retried."

### 2:05–2:35 — The measured eval (screen: click the **Eval** tab)

> "Everything I claimed is measured. The Eval tab runs labelled synthetic
> scenarios — including forged documents and prompt injection — through the
> real pipeline.
>
> Across the suite, the raw model asserted claims at a forty-percent
> unsupported rate —" *(point at the metric)* "— every one struck by the
> verifier. The gated rate is zero, by construction: nothing unverifiable
> can reach PayPal. Contradiction detection: one hundred percent recall,
> zero false positives. You can run it yourself from this button."

### 2:35–2:50 — Close (screen: back to the queue tab)

> "Built with the PayPal Disputes API, shipping trackers, and signature-verified
> webhooks; NVIDIA's Nemotron model behind a read-only tool boundary;
> AG Grid, Render, and Neon. Everything runs from docker compose with zero
> credentials — link in the repo.
>
> ProofPilot: no claim without a citation, and no citation without a checksum."

*(Hold the queue screen 2 seconds. Stop recording.)*

---

**Total: ~2:50. If you're over 3:00:** trim the 1:25 decision-math sentence and shorten the close to just the pitch line.

**Upload:** YouTube → Public → title "ProofPilot — AI dispute agent for PayPal with a deterministic safety verifier" → paste link into Devpost + README (replace "(link added at submission)").
