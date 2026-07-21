# Project Context — Read This First

This is a **spec-driven build**. The documents in `/docs/spec/` are the source of truth, not this file, not your own judgment about what seems reasonable, and not any pattern you recall from training. If something here conflicts with `/docs/spec/`, the spec wins. If something is ambiguous or missing from the spec, **stop and ask — do not invent behavior.**

## What this project is

A working proof-of-concept of a TurboTax-style refund status and AI-predicted ETA system, deployed to Vercel with the repo on GitHub, built to support a live technical interview presentation and two follow-up deep-dive rounds (a hands-on coding round and a product/AI-proficiency round). Full context: `/docs/spec/00_PROJECT_BRIEF.md`.

## Deployment target: Vercel — this changes two architectural assumptions

**Vercel's Python runtime is serverless — no in-memory state survives reliably across requests.** This means:

- **Cache, circuit breaker state, and the mock IRS mode toggle must live in Vercel KV** (Redis-compatible), not a plain Python dict. This is the swap-in the original design's "Redis-shaped interface" decision was built for — see `DECISIONS.md`.
- **Anything that must persist between the demo control panel setting a mode and a subsequent status check being invoked in a different serverless instance depends on this.** Do not fall back to in-memory storage for these specific pieces, even though it would work fine locally — it will behave inconsistently once deployed, which is worse than a clean local-only failure because it's intermittent and hard to diagnose live.
- **Function execution time and memory are bounded** (10s / 1024MB on Vercel Hobby tier). The mock IRS `SLOW` mode's artificial delay must stay well under this ceiling (2–5 seconds is safe). Any ML model loaded for the help-assistant feature (see below) must be evaluated for cold-start cost against these limits — see `02_TECHNICAL_DESIGN.md` §6 for the specific trade-off and recommendation.

## Read in this order before writing any code

1. `/docs/spec/00_PROJECT_BRIEF.md` — why this exists, who it's for
2. `/docs/spec/01_SPEC.md` — the authoritative functional and non-functional requirements
3. `/docs/spec/02_TECHNICAL_DESIGN.md` — the architecture this implements, including the Vercel-driven KV swap, the 3-state circuit breaker, and the Tax Help Assistant RAG pipeline
4. `/docs/spec/03_API_CONTRACT.yaml` — the exact API surface, don't deviate without flagging it
5. `/docs/spec/04_CODING_STANDARDS.md` — how code must be written, given it will be read live by a technical interviewer
6. `/docs/spec/05_ACCEPTANCE_CRITERIA.md` — what "done" means for each component
7. `/docs/spec/06_SCOPE.md` — what is explicitly not being built, and why
8. `/docs/spec/07_TESTING_STRATEGY.md` — the three-layer test taxonomy (unit / integration-contract / scenario-eval) and what gates what
9. `/docs/spec/DEMO_WALKTHROUGH_SCRIPT.md` — the live narration script tying every demo moment to the design decision it demonstrates

## Non-negotiable behaviors

- **Idempotency:** all state-mutating operations (upsert by `return_id`, not insert; notification delivery keyed by `event_id`) must be safe to retry without side effects.
- **Circuit breaker on IRS integration is a real CLOSED / OPEN / HALF_OPEN state machine**, not a simplified two-state version. See `02_TECHNICAL_DESIGN.md` §2 for the full transition logic. State lives in Vercel KV, not in-memory.
- **Schema validation on every IRS response:** malformed or unrecognized responses go to a quarantine path and raise an alert. Never silently index or serve unvalidated data as if it were a real status.
- **The refund status / prediction / explanation core has no LLM anywhere in it.** This remains unchanged and is not up for revision — see `02_TECHNICAL_DESIGN.md` §4 for the full defense. The explanation logic never fabricates a reason: only explain delays where the cause is inferable from known return flags (currently: EITC/CTC → PATH Act hold). Otherwise return "still processing."
- **The separate Tax Help Assistant feature (§6 of Technical Design) is a deliberately distinct, clearly-scoped use of LLM/RAG technology** — hybrid retrieval, cross-encoder rerank, LangGraph orchestration, Gemini Flash synthesis, reusing the guardrail patterns (NLI-style contradiction check, confidence gating) from the broader system design. It answers open-ended tax questions from a small FAQ corpus. It is architecturally and conceptually separate from the refund-status core — never let the two blur together in the code or in how you'd explain it live.
- **Redact SSN and bank account fields in any logged output**, without exception.
- **No bare `except Exception`.** Catch specific, named exceptions.
- **Email notifications are previewed in the UI, never live-sent.** The Tax Help Assistant's LLM call, by contrast, IS live — but must degrade gracefully (a calm "temporarily unavailable" state, never a broken page) if it fails or times out, matching the same resilience philosophy applied everywhere else in this system.

## AI security posture — stated explicitly, not left implicit

The refund-status core's AI-attack surface is small **by design**: no free-text LLM input anywhere in that logic, so classic LLM risks (prompt injection, insecure output handling) don't apply there. **The Tax Help Assistant is different and needs its own posture stated explicitly**, since it does take free-text user input into an LLM call: retrieved FAQ content and user questions are the only trusted input path, the LLM's output must pass through the same NLI-style contradiction check and confidence gate already designed for the broader system before being shown to a user, and there is no tool-calling or agentic action capability in this feature — it only retrieves and answers, it cannot take any action on a user's account. State this distinction clearly if asked "how did you think about AI security" — the honest answer is now two answers, one per surface, not one blanket statement.

## Provenance — minimal, but real

Every code file implementing the prediction, explanation, or help-assistant logic should include a header comment noting which version of `01_SPEC.md` and `02_TECHNICAL_DESIGN.md` it was built against.

## Development loop

- **Spec-drift rule:** if you hit a case the spec doesn't cover, **stop. Do not silently patch around it or invent a resolution.** Write a short note under "Open Questions" at the bottom of `06_SCOPE.md` using the template provided there, and wait for confirmation before proceeding on that piece. This one rule matters more than any other in this file.

- **Escalation rule (distinct from spec-drift):** if you attempt to fix a failing test and it's still failing after 3 attempts, **stop patching.** Repeated low-novelty fix attempts are a sign the underlying diagnosis is wrong, not that the fourth patch will work. Report what you've tried, what you believe the actual root cause might be, and wait for direction.

- **Deployment checkpoint rule:** after every phase in `CLAUDE_CODE_BUILD_SEQUENCE.md`, the human will push to GitHub and verify the Vercel deployment before proceeding to the next phase. **Do not assume local success means Vercel success** — the KV dependency and serverless execution model mean these can genuinely diverge. If you have any reason to suspect a phase's changes might behave differently in a serverless context than locally, say so explicitly before the human deploys, not after something breaks.

- **Test-first for anything involving the circuit breaker, cache TTL logic, the explanation/prediction stub, or the calibration gate.** Implementation-first is fine for simple plumbing and UI wiring. See `07_TESTING_STRATEGY.md`.
- **Every non-obvious decision gets a `# DECISION:` comment in the code and a matching entry in `DECISIONS.md`, written as you go — not batched at the end.** See `04_CODING_STANDARDS.md`.
- **Narrow, atomic tasks.** One logical piece of functionality per implementation step. Don't refactor unrelated files without being asked.

## Stack

- Backend: FastAPI, deployed on Vercel's Python runtime
- State (cache, circuit breaker, demo mode): **Vercel KV** — not in-memory, not SQLite (SQLite's file persistence doesn't survive across serverless invocations either)
- Core data (returns, predictions, audit log): in-memory per-request is acceptable for the PoC's seed data, since it's read-heavy and doesn't need to survive being modified between requests the way cache/breaker state does — see `02_TECHNICAL_DESIGN.md` §1
- Frontend: single HTML page, Tailwind CDN, Alpine.js CDN — **no build step, ever**
- Tax Help Assistant: LangChain + LangGraph for orchestration, a hosted embedding API for dense retrieval (not a locally-loaded model — see §6's cold-start reasoning), a small cross-encoder for reranking, Gemini Flash for synthesis
- Full stack rationale: `02_TECHNICAL_DESIGN.md` §1

## What NOT to build

See `06_SCOPE.md` for the complete list and reasoning. In short: no MCP integration, no real Kafka, no OpenTelemetry infrastructure, no real trained ML model for refund prediction, no live email sending, no multi-tenant document ACL system, no agentic tool-calling in the Help Assistant. Each exclusion has a one-line reason in the scope doc.
