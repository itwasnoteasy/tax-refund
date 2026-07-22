# Testing Strategy

**Why this document exists as its own artifact, not folded into `04_CODING_STANDARDS.md`:** coding standards define *how* to write a test; this document defines *which layer catches which regression class*, and gates promotion accordingly. Passing tests and lint do not prove the system is correct, safe, or operationally stable — this document is what closes that gap, per the "spec + policy + eval + telemetry" framing this whole spec set is built around.

---

## The three layers, and what each one is actually for

### Layer 1 — Unit tests
**Catches:** logic errors in isolated functions, with no network, no cache, no other component involved.

**What belongs here:** `explain_delay()` given every combination of return flags; `predict_window()`'s output shape; the TTL-selection logic given each filing method; the circuit breaker's internal state machine (failure counting, threshold, open/closed transitions) tested directly against the class, not through the API.

**What does NOT belong here:** anything that spins up the FastAPI app, anything that touches the mock IRS service over an actual call boundary — those are Layer 2.

**Gate:** must pass on every change, fast (seconds, not requiring a running server).

### Layer 2 — Integration / contract tests
**Catches:** two things that unit tests structurally cannot: (a) components wired together incorrectly even when each is individually correct, and (b) the API's actual request/response shape drifting from `03_API_CONTRACT.yaml`.

**What belongs here:**
- Full request → Refund Status Service → cache → (mock) IRS Integration Service round trips, asserting the cache-aside behavior end to end
- The circuit breaker actually opening after 3 real (mock) failures *through the API*, not just tested against the class directly
- **Contract compliance:** every response the running API actually returns validates against the schemas in `03_API_CONTRACT.yaml`. This is the single highest-value test in this layer — a schema-validation test that loads the OpenAPI spec and checks live responses against it catches contract drift automatically, without needing to hand-write an assertion for every field every time.

**Gate:** must pass before a component is considered "done" per `05_ACCEPTANCE_CRITERIA.md`. Slower than unit tests (a real app instance, even if in-process) — acceptable to run less frequently than on every keystroke, but must run before any component is marked complete.

### Layer 3 — Scenario / eval tests
**Catches:** the thing neither of the above layers can — whether the system behaves correctly across the specific real-world scenarios it exists to demonstrate, including the ones with no single "correct" unit-level answer (a UX/behavioral judgment, not just a function's return value).

**What belongs here:** the six scenarios in `05_ACCEPTANCE_CRITERIA.md`, each as an automated scenario test where feasible (e.g., "after 3 forced IRS failures, then a request, the response has `stale: true`") — this is your project's equivalent of the "gold dataset" / eval-case layer from the source material, scoped appropriately for a system with no free-text LLM output to evaluate. There is no adversarial-prompt testing here specifically because there is no free-text model input anywhere in this system's core flow (see the AI-security note in `CLAUDE.md`) — the eval layer's job here is behavioral-scenario correctness, not output-safety scoring.

**Gate:** all six must pass before the demo is considered rehearsal-ready. This is the layer you personally re-run before every rehearsal, not just once at the end.

---

## Regression policy

A change that makes a Layer 1 test fail while all Layer 2/3 tests still pass usually means a genuine logic regression — treat it as blocking. A change that makes a Layer 3 scenario fail while Layer 1/2 still pass often means a behavioral or integration-wiring issue invisible at the lower layers — also blocking, and worth investigating specifically *why* the lower layers missed it, since that's a signal the unit/integration tests have a coverage gap worth closing.

**Do not treat an aggregate "tests passed" signal as sufficient.** Per the source material's critique of aggregate scores hiding regressions: if you add a test suite runner, report pass/fail per layer, not one combined number — a Layer 3 scenario failure hidden inside an otherwise-green aggregate is exactly the failure mode this three-layer split exists to prevent.

---

## What's deliberately not in this testing strategy, and why

- **Mutation testing, static security scanning (SAST), full dependency vulnerability scanning:** genuinely valuable at enterprise scale, disproportionate for a solo PoC with no real secrets and a tiny, fully-reviewed dependency set. If time allows, a single `pip-audit` pass before the interview is a cheap, worthwhile addition — not a required gate.
- **Load/performance testing:** the back-of-envelope capacity math (`turbotax_design_learning.md` §24) is the artifact that addresses production scale; this PoC's tests validate correctness, not throughput.
- **Adversarial/safety evals against model output:** not applicable — there is no free-text LLM generating output anywhere in this system's core flow to red-team. If this comes up as a question in the AI-proficiency round, that's the answer: the system's AI-attack surface is structurally small by design, not defended after the fact.
