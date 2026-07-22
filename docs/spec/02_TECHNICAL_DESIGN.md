# Technical Design — Implementation Reference

This is a **distilled, implementation-facing summary**. For full reasoning, alternatives considered, and trade-off defense, see `turbotax_design_learning.md` (referenced by section below).

---

## 1. Stack and Why

- **Backend:** FastAPI, deployed on Vercel's Python serverless runtime.
- **State requiring cross-request persistence — Vercel KV, not in-memory.** Vercel's serverless model does not guarantee shared memory between invocations. Three things absolutely require KV: the cache (TTL-keyed refund status), the circuit breaker's failure count and open/half-open/closed state, and the mock IRS mode toggle (set by the demo control panel, read by the next status request — which may be a different serverless instance entirely). This is the actual moment the original "Redis-shaped interface, swap in real Redis later" decision gets cashed in — see `DECISIONS.md`.
- **Core seed data (returns, predictions):** in-memory per-request is fine — this data is read-only from the app's perspective (seeded once, never mutated by user action in this PoC), so it doesn't have the same cross-invocation consistency requirement as cache/breaker state.
- **Frontend:** Single HTML page, Tailwind CDN, Alpine.js CDN. No build step, ever.
- **Tax Help Assistant:** LangChain + LangGraph, a hosted embedding API, a small cross-encoder, Gemini Flash for synthesis. Full reasoning: §6.

---

## 2. Component Responsibilities

### Refund Status Service (cache-aside, backed by Vercel KV)
1. Check KV for `(return_id, tax_year)`
2. Hit → return immediately
3. Miss → call IRS Integration Service, write result to KV with TTL by filing method, return

| Filing method | TTL |
|---|---|
| E-file, current year | ~20–24h |
| E-file, prior year | ~3 days |
| Paper | No meaningful status for weeks — absence is expected, not an error |
| Transitional state | 2–4h, shorter |

### IRS Integration Service (mock) — expanded state set

The mock IRS service must support the following modes, each mapped to a real production failure class this design already addresses in the full system design (`turbotax_design_learning.md`). This is deliberately richer than a simple up/down toggle, because each mode demonstrates a *different* piece of the hardened-connector design:

| Mode | Simulates | What it demonstrates |
|---|---|---|
| `NORMAL` | Healthy IRS | Happy path |
| `FAILING` | Connection error / 503 | Feeds the circuit breaker's consecutive-failure count |
| `FLAPPING` | Intermittent failures (alternating success/fail) | The breaker must NOT open on isolated failures — only on the configured number of **consecutive** ones. This is an easy thing for a naive circuit breaker to get wrong, and worth demonstrating deliberately |
| `SLOW` | High latency, eventually succeeds | Tests the latency budget / SLA-monitoring concern, distinct from a hard failure — a slow-but-successful response should NOT count toward the circuit breaker's failure count |
| `TIMEOUT` | Request hangs past a defined ceiling | A genuinely different code path from `FAILING` — a timeout exception vs. a clean error response. Should count as a failure for circuit-breaker purposes, but is worth demonstrating as its own distinct mode since real systems handle these differently (e.g., different retry/backoff logic is often warranted) |
| `MALFORMED_RESPONSE` | IRS returns schema-invalid data (missing required field, wrong type) | Triggers the schema-validation quarantine path (`turbotax_design_learning.md` §20) — this failure mode existed in the design but had no way to be demonstrated live before this expansion |
| `UNKNOWN_STATUS_CODE` | IRS returns a status code not in the normalization map | Triggers the "unknown code → explicit fallback + alert, human reviews before adding to the map" path (`turbotax_design_learning.md` §23, status taxonomy drift) — same situation: designed, never demoable, until now |
| `RATE_LIMITED` | IRS returns a 429-equivalent | Connects directly to the "what if IRS moves to rate-limited live queries" scenario deep-dive (`turbotax_design_learning.md` §14) — demonstrates backoff behavior distinct from a hard failure |

**Circuit breaker: a real CLOSED / OPEN / HALF_OPEN state machine, stored in Vercel KV.**

```
CLOSED (normal operation)
  → 3 CONSECUTIVE failures (FAILING or TIMEOUT modes specifically;
    FLAPPING's alternating pattern should NOT reach 3 consecutive) → OPEN

OPEN (short-circuit; serve stale cache + last_validated_at timestamp)
  → cooldown period elapses → HALF_OPEN

HALF_OPEN (exactly one trial request allowed through)
  → trial succeeds → CLOSED (failure count reset)
  → trial fails → OPEN (fresh cooldown timer)
```

This is the standard, textbook circuit breaker pattern (as used in Hystrix, Polly, etc.) — a genuine upgrade over a simplified two-state version, and the FLAPPING mode exists specifically to prove the "consecutive, not just cumulative" distinction is real in the implementation, not just claimed.

### Explanation & Prediction Logic — unchanged, still no LLM
See `01_SPEC.md` §3.3 and `turbotax_design_learning.md` §21 for the full, unchanged defense. Nothing about the Vercel deployment or the new mock IRS states changes this reasoning.

### Notification Logic — unchanged
Preview-only, no live send. See `06_SCOPE.md`.

### Audit Logging — unchanged
Every read logged, PII redacted without exception.

---

## 3. Entity Model
Unchanged from the finalized version — see `turbotax_entity_model.mermaid`.

---

## 4. Why No LLM in the Refund-Status Core — Summary
Unchanged. Full defense: `turbotax_design_learning.md` §21. The Tax Help Assistant (§6 below) does not revise this — it's a separate feature answering a genuinely different kind of question.

---

## 5. Demo Interface
Six scenarios (five core + balance-due), each reachable from a browser demo control panel. See `05_ACCEPTANCE_CRITERIA.md` and `DEMO_WALKTHROUGH_SCRIPT.md` for the live-narration version of this list, now expanded to include the new mock IRS states.

---

## 6. Tax Help Assistant — RAG Pipeline (new, additional feature)

**Why this exists and why it's separate:** FR-7 in `01_SPEC.md`. This feature exists specifically because open-ended, natural-language tax questions are a genuinely good fit for retrieval-augmented generation — unlike the refund-status core's small, enumerable rule set. Building both, with a clear architectural line between them, demonstrates judgment about *when* AI/RAG is the right tool, which is a stronger signal than either "no LLM anywhere" or "LLM everywhere."

### Pipeline

```
User question (free text)
  → Query embedding (hosted embedding API — see cold-start note below)
  → Hybrid retrieval: dense similarity + BM25 sparse, over a small seeded
    FAQ corpus (10-20 short documents covering common tax questions —
    what is form 1040-X, why identity verification is requested, what
    "still processing" means, etc.)
  → RRF fusion of dense + sparse rankings
  → Cross-encoder rerank of the top candidates (see cold-start note below)
  → Confidence floor: if the top reranked result scores below threshold,
    do not proceed to synthesis — return "I don't have a confident answer
    to that, here's where to find official guidance" rather than a
    weak-context guess
  → Synthesis via Gemini Flash, grounded strictly in the retrieved
    context, with an explicit instruction not to answer beyond it
  → Guardrail check: an NLI-style contradiction check (premise = retrieved
    context, hypothesis = generated answer) before the response is shown
  → If the guardrail fails: retry synthesis once with the guardrail's
    rejection reason fed back in; if it fails again, degrade gracefully
    to "I'm not able to answer that confidently right now" — never show
    a flagged response
```

### Orchestration: LangGraph

The retrieve → rerank → synthesize → guardrail-check → (retry once on failure) flow is a real, if small, conditional loop — exactly the shape LangGraph is suited for, and directly mirrors the Retriever/Analyst/Reviewer pattern already designed for the broader system (`06_multiagent_langgraph.mermaid`). Using LangGraph here is proportionate to the task, not framework-flexing for its own sake — state this distinction if asked, since "why LangGraph for something this small" is a fair question with a good answer: the conditional retry-on-guardrail-failure logic is genuinely a small state machine, not a linear pipeline.

### Cold-start trade-off on Vercel — read this before choosing models

Vercel serverless functions have real memory and cold-start constraints (10s execution / 1024MB on Hobby tier). Loading a full local embedding model or cross-encoder from `sentence-transformers` on every cold start is a genuine risk — first-request latency after idle could be slow enough to be visible and awkward live.

**Recommendation:** use a **hosted embedding API** (not a locally-loaded model) for dense retrieval, to sidestep cold-start risk entirely. For the cross-encoder, two honest options, pick based on how it behaves once actually deployed:
- A small, fast cross-encoder (`cross-encoder/ms-marco-MiniLM-L-6-v2`, ~80MB) loaded locally — likely acceptable, but test actual cold-start behavior on Vercel specifically before relying on it live, and have the fallback below ready if it isn't
- **Fallback:** skip a dedicated cross-encoder model and have Gemini Flash itself score/reorder the top candidates as part of the synthesis call — avoids an additional heavy dependency entirely, at the cost of it being a less "textbook" reranking implementation. If you end up using this fallback, document it plainly in `DECISIONS.md` as a Vercel-driven trade-off, not a universal best practice — in a non-serverless deployment, a dedicated cross-encoder is the better default.

This is exactly the kind of infrastructure-constraint-driven decision that's genuinely defensible in an interview: "which one depends on where it's running" is a stronger answer than a fixed opinion independent of context.

### Guardrail reuse, not reinvention

The NLI-style contradiction check and confidence gating here are **the same conceptual pattern** already designed in the broader system (`04_synthesis_guardrails.mermaid`), applied to this feature's specific context. Don't design a new guardrail approach from scratch — reference and adapt the existing one, and say so explicitly if asked, since consistent application of a pattern across a system is itself a signal of mature design thinking.
