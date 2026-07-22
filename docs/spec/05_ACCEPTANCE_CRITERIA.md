# Acceptance Criteria

Defines "done" for each component and for the demo as a whole. A component is not complete until its acceptance criteria are demonstrably true — not just implemented, but observably true when exercised.

---

## Component-level acceptance criteria

### Cache-aside Refund Status Service
- [ ] Cache hit returns without any call to the (mock) IRS service — verifiable via a call counter or log line
- [ ] Cache miss calls the IRS service exactly once, then populates the cache
- [ ] TTL written to cache visibly differs by filing method (assert this directly in a test, not just by code inspection)
- [ ] Every response includes `stale` and `last_checked` fields, even when `stale` is `false`

### IRS Integration Service (mock) + 3-State Circuit Breaker
- [ ] After 3 consecutive `FAILING`-mode responses, the circuit transitions CLOSED → OPEN
- [ ] While OPEN, subsequent calls do not attempt to reach the mock IRS service at all — they serve cached data directly
- [ ] The served response has `stale: true` and a `last_checked` timestamp reflecting when that data was actually last valid, not the current time
- [ ] After the cooldown period, the circuit transitions to HALF_OPEN and allows exactly one trial request through
- [ ] A successful trial request transitions HALF_OPEN → CLOSED (failure count resets); a failed one transitions back to OPEN with a fresh cooldown
- [ ] `FLAPPING` mode (alternating success/failure) does NOT open the circuit — only sustained CONSECUTIVE failures do. Write a test that explicitly alternates success/failure and asserts the circuit stays CLOSED
- [ ] `SLOW` mode (high latency, eventual success) does NOT count as a failure toward the breaker — it's a latency concern, not a reliability one
- [ ] `TIMEOUT` mode is a distinct code path from `FAILING` (exception-based, not a clean error response) but DOES count toward the failure threshold
- [ ] `MALFORMED_RESPONSE` mode triggers the quarantine path, not a crash and not silent acceptance as valid data
- [ ] `UNKNOWN_STATUS_CODE` mode triggers the explicit fallback + flag-for-review path, not a guess at the code's meaning
- [ ] `RATE_LIMITED` mode is handled distinctly from a hard failure (no immediate retry storm)
- [ ] State (breaker status, failure count, mock mode) persists correctly in Vercel KV across what would be separate serverless invocations — test this against the deployed instance, not just locally, since this is exactly the kind of thing that can pass locally and misbehave once deployed

### Explanation Logic
- [ ] A return with `has_eitc_ctc_flag: true` and a non-terminal status returns the PATH Act explanation text, verbatim consistent with what's stated in the presentation
- [ ] A return with `has_eitc_ctc_flag: false` and a non-terminal status returns exactly `"still processing"` — a unit test should assert this string exactly, not just "is non-null"
- [ ] No code path in this function can return any string other than the PATH Act text or `"still processing"` — this is a hard constraint, and a test should attempt to construct an input that would trigger a different branch, to confirm none exists

### Prediction Logic
- [ ] Returns a `predicted_window` with `start_date`, `end_date`, and `confidence_level`
- [ ] Is clearly labeled in a code comment and in `DECISIONS.md` as a rule-based stand-in for the real model
- [ ] Never returns a single point-estimate date — always a window

### No-Refund-Pending Case
- [ ] A return with `status_code: NO_REFUND_PENDING` produces a distinctly different, explicit response — not a null/empty version of the normal response shape

### Notification Preview
- [ ] Opt-in registers correctly (idempotent — opting in twice doesn't create duplicate state)
- [ ] Preview endpoint returns rendered HTML that a human would recognize as a real-looking notification email
- [ ] No code path in the entire codebase makes an outbound network call to an actual email-sending service

### Audit Logging
- [ ] Every call to the refund-status endpoint produces an audit log entry
- [ ] Grep-testing the entire log output for SSN-like patterns or bank account numbers returns zero matches, even under a test that deliberately includes such data in a fixture

---

## Tax Help Assistant (separate feature, FR-7)
- [ ] A well-covered question returns a synthesized answer grounded in retrieved FAQ context, with sources listed
- [ ] A question outside the corpus's coverage triggers the confidence gate — `answered: false` with an honest fallback message, never a fabricated answer from weak context
- [ ] A simulated LLM failure/timeout degrades gracefully — `answered: false`, a calm fallback message, never a raw error or broken page
- [ ] The guardrail (NLI-style contradiction check) can be demonstrated to actually block a response — construct a test case where retrieved context and a candidate answer conflict, and confirm the response is blocked or regenerated, not shown as-is
- [ ] No code path in this feature can take any action on a user's account — it is retrieval-and-answer only, never agentic

## Demo-level acceptance criteria — the five (six) live scenarios

Each of these must be triggerable from the browser demo control panel in under 20 seconds, without touching a terminal:

| # | Scenario | Pass condition |
|---|---|---|
| 1 | Normal in-progress return, cache warm | Response is visibly fast (sub-200ms observable in browser dev tools or a displayed latency figure), `stale: false` |
| 2 | Same return, forced cache miss | IRS mock is called once, cache is repopulated, response still correct |
| 3 | Mock IRS set to `FAILING`, then queried 3+ times | Circuit visibly opens; response shows a "last known as of [time]" state in the UI, not an error page |
| 4 | EITC/CTC-flagged return queried | UI shows the PATH Act explanation text, styled calmly, not as a warning |
| 5 | A return with no inferable delay reason | UI shows "still processing," visibly not a fabricated explanation |
| 6 | Balance-due (no refund pending) return | UI shows a distinct message, not a blank or broken-looking tracker |

**The single most important scenario to get smooth, per `02_TECHNICAL_DESIGN.md`:** the transition from scenario 3's failure state back to normal — toggling the mock IRS back to `NORMAL` and showing the circuit close on the next request. This is the most technically impressive live moment available in the whole demo and deserves the most rehearsal.

---

## What "done" does NOT require

Per `06_SCOPE.md`: load testing at anything resembling production scale, a real trained ML model, real Redis/Kafka, real email delivery, or exhaustive edge-case coverage beyond the six scenarios above. A working, well-commented, honestly-scoped six-scenario demo is a complete deliverable for this PoC's purpose.
