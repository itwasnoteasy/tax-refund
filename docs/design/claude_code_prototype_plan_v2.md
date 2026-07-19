# TurboTax Refund System — Prototype Build Plan v2
### Rich, modern, browser-based demo — built after system design is finalized

---

## What changed from v1, and why

| v1 | v2 | Why |
|---|---|---|
| No interview context in CLAUDE.md | Explicit project brief referencing the actual finalized design | Claude Code makes better calls throughout when it knows the real purpose and audience |
| CLI-only demo | Rich browser UI, no build step | A refund status product deserves a visual demo; Tailwind CDN + Alpine.js gets you modern polish with zero npm/build fragility |
| No notification demo | In-browser email preview, not live send | Shows the exact same design thinking with zero live network dependency risk during the actual interview |
| Assumed cramped timeline | Assumes design-first, then build | You're sequencing deliberately — design docs should be finalized and fed in as context before implementation starts |

---

## Step 0 — Finalize the design first (your plan, confirmed correct)

Before writing a single prompt to Claude Code, finish:
- The actual Entity Model slide (currently a placeholder)
- The actual High-Level Architecture slide (currently a placeholder)

Once those exist, export their content as plain text/markdown into `/docs/design/` in your project — `entity-model.md` and `architecture.md` — alongside the `turbotax_design_learning.md` and `Slide_Content_Reference.md` you already have. These become the actual source-of-truth context Claude Code reads, so implementation decisions trace back to what's on your slides instead of Claude Code inventing its own architecture that quietly diverges from what you present. If Claude Code hits a place where the finalized design is awkward to implement, that's valuable to know now — it's a preview of exactly the kind of question a sharp assessor might ask.

---

## The project brief — feed this to Claude Code before anything else

This is new in v2 — the missing context you flagged. Put this in `/docs/design/PROJECT_BRIEF.md` and reference it explicitly in CLAUDE.md.

```markdown
# Project Brief

This prototype exists to support a live technical presentation and follow-up
deep-dive interview sessions for a Distinguished Engineer / Agentic AI Architect
role at Intuit. It will be:

1. Presented after a 45-minute system design presentation covering this exact
   system (see /docs/design/ for the finalized design docs — entity model,
   architecture, assumptions, trade-offs)
2. Used as the working artifact in a 30-minute "Programming Fundamentals" round,
   where an assessor will ask why specific implementation choices were made
3. Referenced again in a 60-minute "Product Engineering + AI Proficiency" round
   with both an engineering assessor and an AI assessor

Implications for how you should build this:
- Every implementation choice should be defensible and traceable to a reason —
  write DECISIONS.md entries as you go, not after the fact
- Prioritize a working, demoable, VISUALLY POLISHED experience over exhaustive
  backend completeness — this will be shown live on a shared screen
- The audience is senior engineers evaluating architectural judgment, not
  end users evaluating a real product — correctness and honest scoping
  decisions matter more than feature breadth
- If something in /docs/design/ is awkward or inconsistent to implement,
  flag it explicitly rather than silently working around it — that's
  valuable to know before the real interview, not during it
```

---

## The stack — modern and polished, zero build-step risk

**Backend:** FastAPI — lightweight, async-friendly, auto-generates OpenAPI docs (nice bonus: you get a live `/docs` Swagger UI for free, which is itself demo-worthy if an assessor wants to see the contract).

**Frontend:** A single HTML page, Tailwind CSS via CDN (`<script src="https://cdn.tailwindcss.com">`), and Alpine.js via CDN for reactivity (toggling states, switching scenarios, showing/hiding cards) — no npm, no webpack, no Vite, nothing to fail from a broken `node_modules` the morning of the interview. This gets you a genuinely modern, clean-looking interface — Tailwind's utility classes look current and polished without requiring a design system build.

**Why not React:** Not a capability gap — you know frontend-adjacent work well enough to use it. The reason to skip it specifically here is setup fragility close to a fixed interview date. If you want to reconsider once the core demo is solid and you have real slack left, a CDN-based React (via `esm.sh`, no build step) is a reasonable stretch option — but default to Alpine.js since the actual interactivity needed (toggle a failure flag, switch between 5 seed scenarios, show/hide cards) doesn't need real component state management complexity.

**Email:** Rendered as an in-page "email preview" panel — the exact HTML email template, styled convincingly, shown inline without a live send. A separate, optional `scripts/test_real_email.py` using a free-tier provider (Resend, or `smtplib` with a Gmail app password) can exist for your own testing satisfaction, but the interview demo path never makes a live network call for this.

---

## Updated CLAUDE.md

```markdown
# Project Context: TurboTax Refund Prototype

Read /docs/design/PROJECT_BRIEF.md first — it explains why this exists and who
it's for. Read /docs/design/*.md for the finalized system design; treat these
as the source of truth for architecture decisions, not something to reinvent.

## Non-negotiable behaviors
- All state-mutating operations must be idempotent (upsert by return_id, not insert)
- IRS integration must use a circuit breaker — after 3 consecutive failures, stop
  calling and fall back to last-known-good cache with an explicit timestamp
- Never fabricate a delay explanation. Only explain what's inferable from known
  return flags (currently: EITC/CTC -> PATH Act). Otherwise return "still processing"
- Redact SSN and bank account fields in any logged output
- No bare `except Exception` -- catch specific errors
- Email notifications are PREVIEWED in the UI, never live-sent during the demo
  path. This mirrors the design's own graceful-degradation philosophy -- a live
  demo shouldn't depend on a third-party network call succeeding in real time.

## Stack
- Backend: FastAPI, in-memory data (dict or SQLite, not a real DB)
- Frontend: single HTML page, Tailwind CDN, Alpine.js CDN -- no build step, ever
- Cache: in-memory TTL dict with a Redis-shaped interface (get/set/ttl)

## Development loop
- Write tests before implementation for the circuit breaker, cache TTL logic,
  and the explanation/prediction stub
- Implementation-first is fine for simple plumbing/UI wiring
- Every non-obvious design decision gets a `# DECISION:` comment in code AND
  a matching entry in DECISIONS.md, written as you go
- If something in /docs/design/ is hard or awkward to implement as specified,
  stop and flag it explicitly rather than silently deviating

## What NOT to build
- No MCP integration, no real Redis, no real Kafka, no OpenTelemetry
  infrastructure, no real trained ML model -- see DECISIONS.md for the
  reasoning template to use when noting these as deliberate scope cuts
- No npm/build tooling on the frontend, ever -- CDN-based only
```

---

## The UI itself — what "sleek" looks like here

Four screens/states in one page, driven by Alpine.js state, no page reloads:

**1. Landing — tax year selector**
Clean card, dropdown for the 3 most recent years, a "Check Status" button. This alone demonstrates the multi-year requirement from your Assumptions slide.

**2. Result view — normal case**
Status badge (Received / Approved / Sent, color-coded but not alarmist), the honest date range displayed prominently — not a countdown, not a fake single date — with a small "why isn't this more precise?" info icon that on hover/click shows a one-line honest explanation. A quiet "last checked: [timestamp]" footer, always visible, reinforcing the trust-through-transparency theme even when everything's working normally.

**3. Result view — PATH Act case**
A visually distinct but calm (not alarming) explanation card — blue/informational styling, not red/warning styling, matching the "don't make me feel stupid" promise from your Customer Lens slide.

**4. Demo control panel — a small, clearly-labeled dev panel in a corner**
Toggles: "Simulate IRS outage," "Switch scenario" (dropdown through your 5 seed cases), "Force cache miss." This replaces a terminal menu entirely — during the interview you never leave the browser. When you toggle "Simulate IRS outage" and then hit "Check Status" again, the UI visibly transitions to the graceful-staleness state with the "last known as of [time]" banner — this is your single most impressive live moment, so make sure this transition is smooth and instant.

**5. Notification preview panel**
An "Opt in to notifications" toggle, and below it, a rendered preview of the actual email that would be sent — styled to look like a real TurboTax-style notification email, with a small caption: "Preview only — see DECISIONS.md for why this isn't a live send in the demo."

---

## Revised prompt sequence

### Prompt 1 — Project setup with full context

```
Read /docs/design/PROJECT_BRIEF.md and every file in /docs/design/ before doing
anything else. Then read CLAUDE.md and follow it throughout this project.

1. Confirm you've read the design docs by summarizing back to me in 3-4
   sentences what this system is and who's evaluating it -- I want to verify
   the context landed before we proceed.

2. Set up project structure: /app (FastAPI backend), /static (HTML/CSS/JS,
   no build step), /tests, DECISIONS.md (seeded with the cache example),
   SCOPE.md (explicit in/out of scope list from PROJECT_BRIEF.md and
   CLAUDE.md).

3. If anything in /docs/design/ seems inconsistent or awkward to implement,
   list it now before we start building -- I'd rather know today.
```

### Prompt 2 — Mock IRS, seed data, API contract

```
1. Create mock_irs.py -- a class with get_status(return_id) with a settable
   mode: NORMAL, FAILING (raises to simulate 503s), SLOW (artificial delay).

2. Seed exactly 5 tax returns: normal in-progress, EITC/CTC-flagged pending,
   paper-filed with no status yet, balance-due (no refund pending), and one
   terminal "Refund Sent" state.

3. Write refund_api_spec.yaml (OpenAPI 3.0) for GET /refund-status/{return_id}
   and POST /notifications/opt-in. FastAPI should also expose this
   automatically at /docs -- confirm that works.

Stop here, show me the contract and seed data before implementation.
```

### Prompt 3 — Resilience tests, then implementation

```
Write test_resilience.py first: cache hit/miss behavior, circuit breaker
opening after 3 consecutive FAILING responses with fallback to last-known-good
plus staleness timestamp, and idempotent processing of duplicate IRS responses.

Stop, show me the tests.

[after approval] Implement cache_layer.py and irs_integration.py to pass them.
Add # DECISION: comments and matching DECISIONS.md entries as you go.
```

### Prompt 4 — Explanation, prediction stub, email template

```
1. explain_delay(return_flags) -- PATH Act explanation if eitc_ctc_flag set,
   otherwise "still processing." Test-first: assert it never fabricates a
   reason for unrecognized flags.

2. predict_window(return_data) -- rule-based stub, clearly labeled as a
   stand-in for the survival-analysis model in the design doc. Note the
   honest gap in DECISIONS.md.

3. Render a notification email template (HTML) for a status-change event --
   this will be shown as a preview in the UI, never live-sent in the demo
   path. Add the DECISION entry explaining why (graceful-degradation
   consistency, per CLAUDE.md).
```

### Prompt 5 — The UI

```
Build the single-page frontend (static/index.html) using Tailwind CDN and
Alpine.js CDN only -- no build step.

Screens, driven by Alpine state, no page reloads:
1. Landing: tax year selector (3 most recent years) + Check Status button
2. Result view: status badge, honest date range, "why not more precise?"
   info toggle, last-checked timestamp always visible
3. PATH Act case: calm, informational-styled explanation card
4. Demo control panel (small, clearly labeled, corner of the screen):
   toggle IRS outage simulation, switch between the 5 seed scenarios,
   force cache miss
5. Notification preview: opt-in toggle + rendered email preview below it,
   with the caption noting it's preview-only

Make the IRS-outage-to-graceful-staleness transition visually smooth and
immediate -- this is the most important moment to get right.
```

### Prompt 6 (only after 1-5 are solid) — Polish pass

```
Review the full demo flow end to end. Tighten any rough visual edges,
make sure the 5 demo scenarios each take under 20 seconds to show, and
confirm DECISIONS.md has an entry for every non-obvious choice made across
the whole build.
```

---

## What to rehearse, specifically

Run the full 5-scenario demo out loud at least 3 times, narrating exactly what you'd say live. The moment worth over-rehearsing is the IRS-outage toggle → graceful-staleness transition — it's the single most technically impressive thing this demo can show in under 10 seconds, and it should feel effortless when you click it, not fumbly.

After each rehearsal, the same check as before still applies: if an assessor asked "why did you build the email preview instead of a live send" right now, could you answer in one sentence without checking DECISIONS.md? If not, that's the gap to close.
