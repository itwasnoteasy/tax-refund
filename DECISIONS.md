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

*(New entries go below this line as the build progresses.)*
