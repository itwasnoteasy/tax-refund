# Scope

## In scope

The six demo scenarios in `05_ACCEPTANCE_CRITERIA.md`, backed by the components described in `02_TECHNICAL_DESIGN.md`, exposed via the API contract in `03_API_CONTRACT.yaml`, through the browser-based demo UI.

## Explicitly out of scope — and why

Stating the reason for each exclusion matters: in an interview, "I deliberately didn't build X, here's why" is a stronger answer than either silence or an apologetic admission of an unfinished feature.

| Excluded | Why |
|---|---|
| Real Redis | An in-memory TTL dict with the same interface (`get`/`set`/`ttl`) demonstrates the identical cache-aside logic without requiring Docker/Redis setup risk on interview morning. Swapping in real Redis later is mechanical, not a redesign. |
| Real Kafka / event streaming | A direct function call simulating "event emitted" is sufficient to demonstrate the notification and calibration-pipeline consumption pattern without message-broker setup overhead. |
| A real trained ML model | The prediction logic is a rule-based stub, clearly labeled as such. Training a real survival-analysis model is unnecessary to demonstrate the architectural pattern (tiering, calibration gate, season-versioning) being evaluated. |
| Real email/push sending | A live demo should never depend on a third-party network call succeeding in real time — this is the same graceful-degradation philosophy applied to the demo itself, not just the IRS integration. The rendered preview demonstrates the same design thinking with zero live-network risk. |
| MCP integration | Not relevant at this system's scope — there is no live enterprise document catalog to query. |
| OpenTelemetry / real tracing infrastructure | Structured print/log statements demonstrate the same observability *thinking* (see `turbotax_design_learning.md` §7 on observability) without the setup cost of a real tracing backend for a PoC. |
| Multi-tenant document ACL / RBAC system | Not applicable to this system at all — there is no shared, multi-user document corpus. See `turbotax_design_learning.md` §22 for the full reasoning on why this doesn't transfer here. |
| Amended return tracking | Explicitly out of scope for the entire system design, not just the PoC — amended returns run through a separate IRS system with a different status taxonomy and SLA. |
| `ModelCalibrationRecord` entity implementation | Real part of the production design (`turbotax_design_learning.md` §21), but not necessary to implement in code to demonstrate the six core scenarios. Mention this distinction if it comes up. |
| Real production-scale load testing | Not meaningful for a PoC. The architectural patterns being evaluated (cache-aside, bulkhead isolation, circuit breaker) are demonstrated at small scale; the back-of-envelope capacity math (`turbotax_design_learning.md` §24) is the artifact that addresses production scale, not a load test of this code. |
| React / any frontend build tooling | A CDN-based, build-step-free frontend (Tailwind + Alpine.js) achieves the same visual polish with zero risk of a broken `npm install` on interview morning. This is a risk-management decision, not a capability gap. |
| Split spec/code/security PR review | This is a solo build with no team — the reviewer distinction that matters in an enterprise setting (separating "is this the right requirement" from "is this correctly implemented") doesn't apply when one person owns both. The *discipline* still applies: read `01_SPEC.md` before judging code correctness, don't conflate the two questions in your own head either. |
| Canary release / feature flags / shadow mode / auto-rollback | No production deployment exists for this PoC to roll out. Note the distinction: the *full system design* (not this PoC's code) already has an equivalent concept for the one place it matters — the calibration gate in `turbotax_design_learning.md` §21 blocks a new prediction model version from serving live traffic if it fails calibration, which is a rollout-safety gate in spirit, just scoped to model promotion rather than general deployment. |
| Real database migrations | In-memory/SQLite storage for a PoC has no schema-migration story to speak of. Idempotency and upsert-by-`return_id` (already required) are the relevant concerns here, not migration tooling. |
| Mutation testing, full SAST/dependency scanning | Disproportionate for a solo PoC with a tiny, fully-reviewed dependency set and no real secrets. See `07_TESTING_STRATEGY.md` for what's in scope instead. |
| Agentic tool-calling in the Tax Help Assistant | The assistant retrieves and answers; it never takes an action on a user's account. Adding tool-calling would introduce a real security surface (see CLAUDE.md's AI security posture note) for no benefit this feature actually needs. |
| A dedicated cross-encoder model, if Vercel cold-start makes it impractical | See `02_TECHNICAL_DESIGN.md` §6 for the honest trade-off and the documented fallback (LLM-based reranking instead). This is an infrastructure-driven decision, not a universal preference — state it as such if asked. |
| Real production-scale rate-limit handling against a real IRS API | `RATE_LIMITED` mode demonstrates the *pattern*; a real backoff/retry policy tuned against actual IRS rate limits is a production concern the full system design addresses (`turbotax_design_learning.md` §14), not something this PoC needs to fully implement. |

**A note on Vercel KV specifically:** this introduces a real network round-trip where the original design assumed in-memory speed. This is disclosed explicitly in `01_SPEC.md`'s NFR table rather than quietly accepted — if the p99 < 200ms cached-read target is genuinely at risk once deployed, that's worth knowing and saying plainly, not hiding.

## Spec-Drift Proposal Template

Use this exact structure when logging an Open Question below — a fast-path, lightweight version of the "impact analysis" the source material recommends, sized for a solo build rather than a team change-approval process:

```markdown
### [Date] — [Component]: [one-line description of the conflict]
**What the spec says:** ...
**What I hit while implementing:** ...
**Why it's a conflict, not just an implementation detail:** ...
**Proposed resolution (if you have one):** ...
**Status:** OPEN / RESOLVED — [resolution, if resolved]
```

## Open Questions

*(To be filled in during the build if a spec-drift situation arises — see `CLAUDE.md`'s spec-drift rule. Do not silently resolve an ambiguity; log it here using the template above and flag it for confirmation.)*

### 2026-07-23 — Repo completeness: referenced authoritative design artifacts are missing
**What the spec says:** `00_PROJECT_BRIEF.md` says `turbotax_design_learning.md` "should be treated as authoritative if this spec set is ever unclear or silent on a design rationale." `02_TECHNICAL_DESIGN.md` §3 says the Entity Model is "Unchanged from the finalized version — see `turbotax_entity_model.mermaid`." §6 references `06_multiagent_langgraph.mermaid` and `04_synthesis_guardrails.mermaid` as existing patterns to reuse rather than redesign. `DECISIONS.md` cites `turbotax_design_learning.md` §3, §21, §22, and §24 for deeper reasoning.
**What I hit while implementing:** None of `turbotax_design_learning.md`, `turbotax_entity_model.mermaid`, `06_multiagent_langgraph.mermaid`, or `04_synthesis_guardrails.mermaid` exist anywhere in this repository — `/docs/spec/` contains only the files enumerated in `CLAUDE.md`'s read order, plus itself and `DECISIONS.md`.
**Why it's a conflict, not just an implementation detail:** The concrete entity model (fields/types/relationships for returns, predictions, notifications) is declared "unchanged from" a document that isn't present, and it's what `storage.py` will need in the next phase. Similarly, the Help Assistant's guardrail and multi-agent orchestration are declared "reuse, don't reinvent" against diagrams that aren't here. Proceeding without them means either inventing the entity model / guardrail structure from scratch — exactly the "invent behavior" `CLAUDE.md` forbids — or deriving them from the distilled spec prose alone, which risks quietly diverging from what's actually on the interview slides.
**Proposed resolution (if you have one):** Either commit the missing `turbotax_design_learning.md` and the three `.mermaid` diagrams (e.g. under `/docs/design/`, matching how `00_PROJECT_BRIEF.md` refers to them), or confirm `/docs/spec/` is meant to be fully self-contained and these references are stale — in which case I'll derive entity fields and guardrail design directly from `01_SPEC.md`/`02_TECHNICAL_DESIGN.md` and note that derivation explicitly in `DECISIONS.md` when I reach the seed-data phase and the Help Assistant phase.
**Status:** OPEN

### 2026-07-23 — Process: `CLAUDE_CODE_BUILD_SEQUENCE.md` referenced by the deployment-checkpoint rule doesn't exist
**What the spec says:** `CLAUDE.md`'s Development loop section states: "after every phase in `CLAUDE_CODE_BUILD_SEQUENCE.md`, the human will push to GitHub and verify the Vercel deployment before proceeding to the next phase."
**What I hit while implementing:** No file named `CLAUDE_CODE_BUILD_SEQUENCE.md` exists anywhere in the repo, so there are no defined phase boundaries to check the current step against, or to know when to pause for a deployment checkpoint versus keep building.
**Why it's a conflict, not just an implementation detail:** This rule is what gates when I should stop and let you verify a Vercel deployment — which matters given `CLAUDE.md`'s explicit warning that local and Vercel behavior can diverge (KV, serverless execution). Without the phase list, I'm treating each of your numbered prompts as an implicit phase boundary, but that's my inference, not something the spec actually defines.
**Proposed resolution (if you have one):** Either commit `CLAUDE_CODE_BUILD_SEQUENCE.md` with the intended phase breakdown, or confirm that each of your prompts in this conversation is the de facto phase boundary (i.e., this scaffold is "Phase 1," and you'll deploy-check after this commit before I continue).
**Status:** OPEN
