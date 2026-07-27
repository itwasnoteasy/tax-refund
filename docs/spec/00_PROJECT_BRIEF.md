# Project Brief

## What this is

A working proof-of-concept of a TurboTax-style refund status and AI-predicted ETA system.

## What this means for how you build it

**This code will be read by a human, live, under questioning.** Every non-obvious choice needs to be defensible on the spot — which is why comments and the `DECISIONS.md` log matter as much as the code working. Prioritize a working, demoable, cleanly-documented experience over exhaustive backend completeness. Correctness and honest scoping decisions matter more than feature breadth.

**The audience is senior engineers evaluating architectural judgment**, not end users evaluating a polished product. A visually clean demo matters (see `02_TECHNICAL_DESIGN.md` for the UI approach), but it exists to make the underlying engineering decisions visible and explainable, not to impress on looks alone.

## The five things this PoC must be able to demonstrate live

Each maps to a specific design decision made during the system's full design phase — the demo exists to make these decisions visible and defensible, not just to "work":

| Demo moment | What it proves |
|---|---|
| Normal in-progress return, cache hit | Sub-200ms cached read |
| Force cache miss → mock IRS call | Cache-aside pattern working correctly |
| Force mock IRS into failure mode → query | Circuit breaker opening, graceful staleness fallback with timestamp |
| EITC/CTC-flagged return | Honest, inferable PATH Act explanation |
| Return with no clean inference available | "Still processing" — never a fabricated reason |

A sixth, secondary moment — a balance-due (no refund pending) return — should also be handled distinctly, not as a blank state.

## Relationship to the other design documents

This spec set (`/docs/spec/`) is a **distilled, implementation-facing version** of the full design work already completed. It intentionally does not repeat all of the reasoning, alternatives-considered, and trade-off discussion found in `turbotax_design_learning.md` — that document remains the deeper reference for *why*, and should be treated as authoritative if this spec set is ever unclear or silent on a design rationale. This spec set exists to be concise and actionable for implementation; the learning doc exists to be complete for defense under questioning.
