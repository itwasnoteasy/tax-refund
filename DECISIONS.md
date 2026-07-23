# Decisions Log

Every non-obvious implementation choice gets an entry here, written **at the same time as the code**, not batched at the end. This is the primary artifact for defending "why did you write A instead of B" under live questioning — see `04_CODING_STANDARDS.md` §3 for the required format and the matching `# DECISION:` code comment convention.

Format per entry:

```markdown
### [Component]: [what you chose], not [main alternative]
**Why:** One or two sentences — the actual reasoning, not a restatement of the choice.
**Honest gap:** What this stand-in doesn't do that the real production version would.
```

---

### Cache: in-memory TTL dict, not real Redis
**Why:** Keeps local demo setup to zero external dependencies — no Docker/Redis required to run this. The interface (`get`/`set`/`ttl`) mirrors Redis's, so swapping in real Redis later is mechanical, not a redesign.
**Honest gap:** No pub/sub invalidation, no clustering, no persistence across restarts. In production this is Redis Cluster per the full design (`turbotax_design_learning.md` §3), specifically for invalidation broadcast across multiple service instances.

### Prediction: rule-based stub, not a trained model
**Why:** Demonstrates the architectural pattern (tiering, calibration gate, season-versioning) without requiring real historical data or training infrastructure for a PoC whose purpose is showing design judgment, not model performance.
**Honest gap:** No real calibration against actual outcomes; confidence intervals are illustrative, not statistically derived.

### Explanation: template lookup, not an LLM call
**Why:** The reason-set for delay explanations is small, known, and deterministic (currently: exactly one inferable case, the PATH Act hold). A template is simpler, fully auditable, and has zero hallucination risk — properties that matter more here than in almost any other part of this system, since the entire design philosophy rests on never presenting a guess as a known fact.
**Honest gap:** None, really — this is the actual production recommendation too, not just a PoC simplification. See `turbotax_design_learning.md` §21 for the full defense if pushed on this in the AI-proficiency round.

### Notifications: rendered preview, not live send
**Why:** A live demo shouldn't depend on a third-party network call succeeding in real time — the same graceful-degradation principle applied to the IRS integration, applied here to the demo itself.
**Honest gap:** No real deliverability, formatting-in-real-inbox validation, or send-rate handling. All real concerns for a production notification service, none relevant to demonstrating this system's design.

### State persistence: Vercel KV, not in-memory dict
**Why:** Vercel's serverless Python runtime doesn't guarantee shared memory across invocations. Cache, circuit breaker state, and the demo mode toggle all need to survive between what may be genuinely separate serverless instances. This is the actual moment the original "Redis-shaped interface, swap in real Redis later" decision was built for.
**Honest gap:** Introduces a real network round-trip where the original design assumed in-memory speed. Documented explicitly in `01_SPEC.md`'s NFR table rather than silently accepted.

### Circuit breaker: 3-state (CLOSED/OPEN/HALF_OPEN), not 2-state
**Why:** The textbook pattern (Hystrix, Polly) tests recovery automatically via a single trial request after cooldown, rather than requiring an external signal to close the circuit again. More correct and more interesting to defend live than a simplified always-manually-reset version.
**Honest gap:** None specific to the PoC — this is the actual production-correct pattern, not a simplification.

### Mock IRS: 8 distinct modes, not just up/down
**Why:** Several failure classes already existed in the full system design (schema drift, status taxonomy drift, rate limiting) but had no way to be demonstrated live. Each mode maps to a specific, previously-designed-but-undemonstrated concern — see `02_TECHNICAL_DESIGN.md` §2's table.
**Honest gap:** None — this is additive richness, not a simplification.

### Tax Help Assistant: a separate, additional RAG feature, not an extension of the refund-status core
**Why:** Open-ended tax questions are a genuinely good fit for retrieval-augmented generation; the refund-status core's small enumerable rule set is not. Building both, with a clear architectural boundary, demonstrates judgment about when AI is the right tool rather than a blanket policy either way.
**Honest gap:** Introduces the system's only live LLM API dependency, with a different reliability profile than the rest of the system (graceful degradation on failure, not held to the same latency SLA).

### Tax Help Assistant: hosted embedding API, not a locally-loaded model
**Why:** Avoids Vercel cold-start risk entirely for the retrieval step.
**Honest gap:** A self-hosted embedding model might be preferred in a non-serverless production deployment for cost/latency/data-residency reasons — this is an infrastructure-driven choice, not a universal one.

### Vercel KV client: `upstash-redis`, not the plain `redis` TCP client
**Why:** Vercel KV is Upstash Redis under the hood and exposes REST-based credentials (`KV_REST_API_URL` / `KV_REST_API_TOKEN`), not a raw TCP connection string. A REST-based client fits a serverless function's short-lived, per-invocation lifecycle better than a client that expects a persistent TCP connection to manage.
**Honest gap:** None specific to the PoC — this is the client Vercel's own docs point to for Python; no request-level logic (get/set/ttl) has been written yet, only the dependency choice.

### kv_store.py: auto-detect local fallback via missing env vars, not a config flag
**Why:** `KVStore` picks the in-memory backend automatically whenever `KV_REST_API_URL`/`KV_REST_API_TOKEN` (or `UPSTASH_REDIS_REST_URL`/`UPSTASH_REDIS_REST_TOKEN`) aren't set, rather than requiring an explicit `KV_BACKEND=local` setting. This means zero-config local iteration (`import app.kv_store` just works), and there's no separate flag that could drift out of sync with whether credentials are actually present.
**Honest gap:** The local backend has no cross-invocation persistence — it's a single-process dict, gone the moment the process exits. That's fine for local dev (no serverless boundary to cross) but would be silently wrong if it were ever selected on Vercel itself; it's only reachable there if the KV env vars are missing, which would itself be a deployment misconfiguration worth surfacing loudly, not something this module tries to detect or alert on.

### storage.py: entity fields derived directly from 01_SPEC.md/02_TECHNICAL_DESIGN.md, not an entity diagram
**Why:** No `turbotax_entity_model.mermaid` exists in this repo (see the resolved Open Question in `06_SCOPE.md`), so `TaxReturn` and `RefundStatus` were split by re-reading the functional requirements rather than copying a diagram: `TaxReturn` holds static filer-level facts (return_id, tax_year, filing_status, filing_method, expected_refund_amount, has_eitc_ctc_flag, PII fields); `RefundStatus` holds the IRS-reported status fact (status_code, status_last_updated_at), separately, because it's what `upsert_refund_status()`'s idempotency requirement (01_SPEC.md §3.4) actually applies to — a filer's static facts don't need upserting, but their IRS-reported status does every time the mock IRS is queried again.
**Honest gap:** This split is my inference from the spec prose, not a verified match to whatever's on the actual interview slides' entity model — flagged as such in the resolved Open Question, and worth a quick sanity check against those slides before the real interview if there's time.

### storage.py: seed data includes realistic-looking fake SSN/bank fields
**Why:** `05_ACCEPTANCE_CRITERIA.md`'s audit-logging criterion requires grep-testing log output for SSN-like patterns and bank account numbers and asserting zero matches "even under a test that deliberately includes such data in a fixture" — that only proves anything if the fixture data looks like the real thing. Omitting these fields (or using an already-safe placeholder like `"REDACTED"`) would make the redaction test vacuous.
**Honest gap:** These are fabricated values (`"123-45-6789"` style), never real PII — but they're realistic-looking, so this file itself must never be logged or displayed unredacted. `audit.py` (not yet implemented) owns the actual redaction logic.

### storage.py: three seed returns cover all six acceptance-criteria scenarios, not six
**Why:** Rows 1, 2, 3, and 5 in `05_ACCEPTANCE_CRITERIA.md`'s demo table are all properties of cache state and the mock IRS's injected failure mode — not of the seed data — so one normal, unflagged return covers all four. Row 4 needs the EITC/CTC-flagged return; row 6 needs the balance-due return. Three returns, not six, avoids seeding redundant fixtures that would all exercise identical code paths.
**Honest gap:** A "SENT" terminal-state return and a paper-filed return (both appear in `DEMO_WALKTHROUGH_SCRIPT.md`'s narration, not in the 6-row acceptance table) aren't seeded yet — out of scope for this task specifically, but worth adding before the full walkthrough is rehearsed end to end.

### kv_store.py: local fallback store is module-level, not per-instance (bug fix)
**Why:** `_LocalBackend` originally created a fresh `{}` per instance, contradicting its own "single-process dict" docstring the moment more than one `KVStore()` got constructed in the same process -- exactly what mock_irs.py does on every `get_mode()`/`set_mode()`/flapping-counter call, mirroring how a real caller would use it. Moved `_data` to a module-level `_LOCAL_STORE` dict so all `_LocalBackend` instances within one process actually share state, matching the real Upstash backend's property of being safe to reconstruct freely. Caught by writing a test for it (`test_local_backend_is_shared_across_separately_constructed_stores`) before wiring mock_irs.py up to the pattern that would have silently broken.
**Honest gap:** None -- this brings the local fallback in line with what its own docstring already claimed; no scope change.

### mock_irs.py: FLAPPING mode alternates deterministically, not randomly
**Why:** The task's stated goal -- alternating unpredictably -- was replaced with strict deterministic alternation (fail, success, fail, success, ...) via a KV-backed call counter. A live demo shouldn't depend on a coin flip: genuine 50/50 randomness has a real (if small) chance of producing 3 consecutive failures across a run of calls, which would falsely trip the circuit breaker and undermine the exact "consecutive, not cumulative" point FLAPPING exists to prove. Strict alternation guarantees a maximum run length of 1 consecutive failure, is trivially assertable in a test, and behaves identically across separate serverless invocations (the counter lives in KV, same reasoning as the mode toggle itself).
**Honest gap:** A real intermittently-failing dependency wouldn't alternate this cleanly -- true production flakiness is closer to random. This is a deliberate demo-reliability trade-off, not a claim that real-world flapping looks like this.

### mock_irs.py: MALFORMED_RESPONSE/UNKNOWN_STATUS_CODE return invalid data, not exceptions
**Why:** These two modes simulate the IRS responding "successfully" with data this system's schema validation should catch -- not a connection failure. Raising an exception for them would conflate "couldn't reach the IRS" with "reached it, but the response is wrong," which is exactly the distinction the quarantine path (irs_integration.py, not yet built) needs to make. Returning a dict with a field omitted (MALFORMED_RESPONSE) or an out-of-enum value (UNKNOWN_STATUS_CODE) keeps that responsibility where it belongs.
**Honest gap:** The exact shape of a "raw IRS response" (the `irs_status_code`/`last_updated_at` dict keys) is my own derivation, same as storage.py's entities -- there's no external IRS wire-format spec to match against in this PoC.

### irs_integration.py: signals CIRCUIT_OPEN, doesn't itself serve stale cache
**Why:** The design describes OPEN as "serve last-known-good cached value, with an explicit staleness timestamp" -- but per `04_CODING_STANDARDS.md` §7's file layout, the cache lives in `cache_layer.py` and response assembly in `refund_status.py`, neither built yet. `irs_integration.py`'s own scope, per that same layout, is "validation, 3-state circuit breaker" -- so `fetch_status()` returns `IRSOutcome.CIRCUIT_OPEN` and stops there; the caller (a later phase) is responsible for the actual cache fallback and its own `last_validated_at`.
**Honest gap:** Until `refund_status.py` exists, there's no component that actually performs the stale-cache fallback this design describes end-to-end -- `CIRCUIT_OPEN` is a correct signal with nothing downstream consuming it yet.

### irs_integration.py: MALFORMED_RESPONSE counts toward the breaker, UNKNOWN_STATUS_CODE does not
**Why:** The task instructions explicitly called out MALFORMED_RESPONSE as breaker-counting. `02_TECHNICAL_DESIGN.md` §2's table doesn't say the same for UNKNOWN_STATUS_CODE, and the underlying reasoning differs: a malformed response means the data itself can't be trusted, closer to "the connection isn't giving us anything usable right now." An unknown status code means the connection worked fine and returned syntactically valid data -- the IRS just started using a code this system doesn't recognize yet, which is a normalization-map gap, not a reliability problem. Treating the two identically would make the breaker trip on a case (a new, unrecognized code) that says nothing about IRS connectivity health.
**Honest gap:** This asymmetry isn't spelled out explicitly for UNKNOWN_STATUS_CODE in the spec -- it's my inference from "the connection worked" being the throughline for what should and shouldn't count, consistent with SLOW and RATE_LIMITED both also not counting for the same reason.

### irs_integration.py: circuit breaker cooldown is 15 seconds, not minutes
**Why:** `05_ACCEPTANCE_CRITERIA.md` calls the OPEN→HALF_OPEN→CLOSED recovery transition "the single most technically impressive live moment" and expects each scenario demoable "in under 20 seconds." A production breaker would likely cool down for minutes; an interview audience won't wait that long. Exposed as `CIRCUIT_BREAKER_COOLDOWN_SECONDS` so it's one constant to tune during rehearsal, and tests monkeypatch it down further for fast execution.
**Honest gap:** 15 seconds is a demo-appropriate guess, not derived from any real IRS SLA -- worth revisiting once the full walkthrough is rehearsed end to end and the actual pacing is felt live.

*(New entries go below this line as the build progresses.)*
