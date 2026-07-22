# Specification — Functional & Non-Functional Requirements

**Status: authoritative.** If code and this document disagree, this document wins — flag the conflict rather than silently resolving it in code (see `CLAUDE.md`'s spec-drift rule).

---

## 1. Functional Requirements

**FR-1 — Status lookup.** A user can check the current refund status for a selected tax year (Received → Approved → Sent, plus exception states), across the 3 most recent tax years. There is no single implicit "latest" return — the year must be explicit whenever more than one exists.

**FR-2 — AI-predicted ETA.** When a refund has not yet been sent, the system shows a predicted delivery **window** (a range), never a single point-estimate date.

**FR-3 — Explanation for delays.** The system explains a delay in plain language **only where the cause is inferable from data already held** — currently, exactly one case: an EITC/CTC-flagged return subject to the PATH Act hold. For every other "still processing" state, the system says exactly that — it never fabricates a plausible-sounding reason.

**FR-4 — Notifications.** A user may opt in to be notified when their status changes. In this PoC, notification delivery is **previewed in the UI**, not live-sent (see `06_SCOPE.md`).

**FR-5 — No-refund-pending case.** If a user has no refund pending (e.g., balance due instead), the system shows an explicit, distinct message — never a blank or broken-looking tracker.

**FR-6 — Return details surfaced.** Filing status and expected refund amount are shown alongside tracking info. The system accounts for the possibility that the actual refund may be reduced via the Treasury Offset Program (this only needs to be **acknowledged in the data model and UI copy**, not fully implemented — see `06_SCOPE.md`).

**FR-7 — Tax Help Assistant (separate, additional feature).** A user can ask an open-ended, free-text question about taxes in general (e.g., "what is form 1040-X," "why do I need to verify my identity") and receive an answer synthesized from a small seeded FAQ corpus via retrieval-augmented generation. This is **architecturally and conceptually separate** from FR-1 through FR-6 — it exists specifically to demonstrate RAG/LangChain/LangGraph competency on a problem shape where that toolset is genuinely the right fit (open-ended natural-language Q&A), in contrast to the refund-status core, where a small rule-based system is deliberately preferred. See `02_TECHNICAL_DESIGN.md` §6 for the full design and `turbotax_design_learning.md` §21 for why the core logic doesn't use this same approach.

---

## 2. Non-Functional Requirements

| Requirement | Target | Notes |
|---|---|---|
| Availability | 99.95%+ during Jan 20–Apr 15 | Not independently testable in a PoC; the *design* (circuit breaker, graceful degradation) is what's being evaluated, not measured uptime |
| Cached read latency | p99 < 200ms | Should be observably true even in the PoC. Note: Vercel KV network round-trip adds real latency vs. a true in-memory dict; if this target is at risk after deployment, say so explicitly rather than silently letting it slip — see `06_SCOPE.md` |
| Prediction latency | p99 < 2s | Precomputed, not generated live — trivially met by the rule-based stub |
| Help Assistant response latency | No hard target — this is a live LLM call, and the priority is graceful failure over speed. A few seconds is acceptable; not held to the refund-status core's SLA discipline, since it's a fundamentally different reliability profile |
| Data freshness | Bounded staleness by filing method, disclosed to the user | TTL logic must visibly differ by filing method; staleness must be visible in the UI (`last_checked` timestamp), not hidden |
| Security | Field-level encryption pattern acknowledged for SSN/bank fields; PII redacted in all logs | Full encryption-at-rest is out of scope for a PoC (in-memory store) — see `06_SCOPE.md` — but log redaction is fully in scope and must be real |
| Scalability | Design should account for 40–50x seasonal peak | Not load-tested in the PoC — the *architecture* (cache-aside, bulkhead isolation) is what's being evaluated |

---

## 3. Behavioral Contracts — the parts most likely to be probed live

### 3.1 Cache-aside behavior
- Cache hit → return immediately, no IRS call
- Cache miss → call IRS Integration Service, populate cache with the correct TTL for that return's filing method, then return
- TTL is **not uniform** — it varies by filing method (see `02_TECHNICAL_DESIGN.md` §2)

### 3.2 Circuit breaker behavior
- 3 consecutive failures from the (mock) IRS service → circuit opens
- While open: serve last-known-good cached value, with an explicit `last_validated_at` timestamp, and a `stale: true` flag in the response
- This must be a **real, triggerable state transition** in the demo, not just described — see `05_ACCEPTANCE_CRITERIA.md`

### 3.3 Explanation logic — the most important behavioral contract in this spec
- Input: a set of known return flags (currently: `has_eitc_ctc_flag`)
- If `has_eitc_ctc_flag` is true and status is still processing → return the PATH Act explanation text
- **In every other case where status is still processing, return exactly `"still processing"` — do not attempt to infer or generate any other explanation.** This is intentional and load-bearing: the system's entire design philosophy rests on never presenting a guess as if it were a known fact. Do not "improve" this by making it smarter without an explicit specification change.

### 3.4 Idempotency
- Processing the same (mock) IRS response twice must not duplicate state or double-fire a notification event
- Enforced via upsert-by-`return_id` for status records, and dedup-by-`event_id` for notification events

---

## 4. Explicit Non-Goals for this Spec

These are requirements the *full system design* addresses but this PoC does not need to satisfy — see `06_SCOPE.md` for the complete reasoning per item:

- Real production-scale load handling
- Real seasonal model retraining or calibration against actual historical data
- Amended return tracking
- Real email/push delivery
- Multi-tenant enterprise document access control (not applicable to this system at all — see `turbotax_design_learning.md` §22)
