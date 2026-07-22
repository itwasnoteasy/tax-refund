# Coding Standards

**Why this document exists and matters more than usual:** this code will be read live, by a technical interviewer, who will ask "why did you write it this way" about specific lines. Code that merely runs is not sufficient — it must be **legible enough to defend on sight**, months after being written, without the author needing to re-derive the reasoning from scratch. Every rule below serves that goal directly.

---

## 1. Every function gets a docstring — no exceptions

Google-style docstrings. Every function, including small helpers, states purpose, arguments, return value, and any exceptions raised.

```python
def get_refund_status(return_id: str, tax_year: int) -> RefundStatusResponse:
    """Fetch refund status for a return, using cache-aside with IRS fallback.

    On a cache hit, returns immediately without contacting the IRS
    integration. On a miss, calls the (mock) IRS service, computes the
    appropriate cache TTL based on filing method, writes through to cache,
    and returns the fresh result.

    Args:
        return_id: Unique identifier for the tax return.
        tax_year: The tax year being queried; must be one of the 3 most
            recent years for this user (validated upstream by the route
            handler, not here).

    Returns:
        A RefundStatusResponse reflecting either a fresh or (if the IRS
        circuit breaker is open) a stale cached result, with `stale` and
        `last_checked` always populated so the caller can display honest
        staleness to the user.

    Raises:
        ReturnNotFoundError: if no return exists for this return_id/tax_year.
    """
```

**Rationale for this rule specifically:** in a live walkthrough, the docstring is often read *before* the function body — it should let the interviewer predict what the function does and why, so the actual code reading confirms rather than surprises.

---

## 2. Type hints are mandatory, everywhere

Every function signature, every dataclass field, every variable whose type isn't immediately obvious from the right-hand side. This is not about catching bugs (though it helps) — it's about making the code self-documenting for a live reading, where the reader can't run a debugger to check what shape a variable actually is.

---

## 3. The `# DECISION:` comment convention

Every non-obvious choice — a trade-off, a rejected alternative, a deliberate simplification for the PoC — gets an inline comment starting with `# DECISION:`, immediately followed by a matching entry in `DECISIONS.md`. This is the single most important convention in this document, because it's the direct answer to "why did you write A instead of B" under live questioning.

```python
# DECISION: Rule-based template, not an LLM call. An LLM here would trade a
# small, auditable, zero-hallucination-risk rule set for a system that could
# fabricate a plausible-but-wrong reason — in the one part of this design
# where honesty is the entire point. See DECISIONS.md for full reasoning.
def explain_delay(return_flags: dict) -> str:
    ...
```

**Write the `DECISIONS.md` entry at the same time as the code**, not after. A decision explained after the fact, from memory, is reconstructed reasoning — write it while it's still the reasoning you actually used.

---

## 4. The `# FAILURE MODE:` comment convention

Every branch that exists specifically to handle a failure — a circuit breaker transition, a validation failure, a fallback path — gets a comment naming the condition that triggers it and the reasoning for the chosen handling, not just what the code does.

```python
if consecutive_failures >= CIRCUIT_BREAKER_FAILURE_THRESHOLD:
    # FAILURE MODE: 3 consecutive failures (not cumulative — FLAPPING mode
    # exists specifically to prove isolated failures don't trip this).
    # Transition CLOSED -> OPEN. Rather than keep retrying (adds latency
    # to every request while IRS is down) or raise an error (shows a
    # broken page for data we actually already have), short-circuit to
    # the last-known-good cached value with an explicit staleness
    # timestamp. State lives in Vercel KV, not local memory, so this
    # transition is visible to every serverless instance, not just this one.
    return transition_to_open(return_id)

if breaker_state == "OPEN" and cooldown_elapsed():
    # FAILURE MODE: cooldown has passed. Rather than require a manual
    # reset signal, allow exactly one trial request through (the
    # textbook HALF_OPEN pattern). Success -> CLOSED, resetting the
    # failure count. Failure -> back to OPEN with a fresh cooldown.
    return attempt_half_open_trial(return_id)
```

**Why the example above is worth reading twice:** it's not just showing syntax — it demonstrates the actual reasoning chain (why 3 and not 1, why consecutive and not cumulative, why KV and not memory) inline, which is exactly what makes a `# FAILURE MODE:` comment earn its place rather than just restating what the `if` already says.

---

## 5. No bare `except Exception`

Catch specific, named exceptions. If a broad catch is genuinely necessary at a boundary (e.g., the outermost request handler), it must log the exception type and message before returning a generic error — never swallow silently.

---

## 6. Naming conventions

- Functions: `snake_case`, verb-first (`get_refund_status`, not `refund_status_getter`)
- Test functions: `test_<function_under_test>_<scenario>_<expected_outcome>` — e.g., `test_get_refund_status_irs_circuit_open_serves_stale_cache`
- Constants: `UPPER_SNAKE_CASE`, defined once near the top of the module they belong to, never inline magic numbers (e.g., `CIRCUIT_BREAKER_FAILURE_THRESHOLD = 3`, not a bare `3` in the conditional)

---

## 7. File and module organization

Group by responsibility, matching the component boundaries in `02_TECHNICAL_DESIGN.md` — not by technical layer. A reviewer should be able to open `irs_integration.py` and find the whole hardened-connector story (poll, validate, 3-state circuit breaker) in one place, rather than needing to trace across `services/`, `handlers/`, and `utils/` to reconstruct it.

Current layout, reflecting the Vercel deployment and the Tax Help Assistant as a genuinely separate feature area (kept in its own subdirectory so the architectural boundary from `CLAUDE.md` is visible in the file tree, not just in prose):

```
/api
  index.py               # FastAPI app entry point (Vercel's expected location)
/app
  kv_store.py              # Vercel KV wrapper (get/set/ttl), with local in-memory
                            #   fallback for dev — see its own module docstring
                            #   for the fallback's explicit limitations
  mock_irs.py               # mock IRS service — NORMAL, FAILING, FLAPPING, SLOW,
                             #   TIMEOUT, MALFORMED_RESPONSE, UNKNOWN_STATUS_CODE,
                             #   RATE_LIMITED
  irs_integration.py          # hardened connector: validation, 3-state
                              #   (CLOSED/OPEN/HALF_OPEN) circuit breaker
  cache_layer.py               # TTL-by-filing-method logic, backed by kv_store.py
  refund_status.py               # cache-aside orchestration
  refund_logic.py                 # explain_delay, predict_window — rule-based,
                                   #   no LLM, unchanged by everything else here
  notifications.py                 # email template rendering (preview-only)
  storage.py                        # in-memory entity dataclasses (read-heavy
                                     #   seed data, doesn't need KV — see
                                     #   02_TECHNICAL_DESIGN.md §1 for why this
                                     #   one component is the exception)
  audit.py                           # PII-redacted logging
/app/help_assistant
  faq_corpus.py                      # seeded FAQ documents
  retrieval.py                       # hybrid dense+sparse retrieval, RRF fusion,
                                      #   cross-encoder rerank (or LLM-rerank
                                      #   fallback — see 02_TECHNICAL_DESIGN.md §6)
  graph.py                           # LangGraph orchestration: retrieve -> rerank
                                      #   -> synthesize -> guardrail -> retry-once
                                      #   -> graceful degradation
/static
  index.html                         # single-page demo UI (Tailwind + Alpine CDN),
                                      #   refund-status and Help Assistant sections
                                      #   kept visually separate
/tests
  test_unit_*.py                     # Layer 1 — see 07_TESTING_STRATEGY.md
  test_integration_*.py               # Layer 2
  test_scenario_*.py                  # Layer 3 — the six acceptance scenarios
vercel.json                           # Python runtime config
requirements.txt
CLAUDE.md
DECISIONS.md
docs/spec/...
```

**Why the Help Assistant gets its own subdirectory, not just its own files mixed in with everything else:** the physical separation in the file tree reinforces the architectural separation already stated in `CLAUDE.md` — someone browsing the repo cold should be able to tell at a glance that this is a distinct feature area, without needing to have read the docs first.

---

## 8. Test standards

- Tests for the circuit breaker, cache TTL logic, and explanation/prediction stub are **written before implementation** (see `CLAUDE.md`).
- Every test asserts one behavior. A test named `test_circuit_breaker_opens_after_three_failures` should not also be silently checking cache TTL logic.
- Prefer explicit fixtures over magic shared state — a reader should be able to understand a test's setup by reading the test itself, not by tracing through a shared `conftest.py` fixture chain.

---

## 9. Two comment expectations specific to this project

**Any read or write to `kv_store.py`** should have a comment if it's not immediately obvious why that particular piece of state needs KV rather than plain memory — per `02_TECHNICAL_DESIGN.md` §1, most of this system's data doesn't need it, so the pieces that do (cache, breaker state, demo mode) are the exception, not the default, and deserve a one-line "why this one" note the first time each is touched in a file.

**Any call into the Help Assistant's LLM/retrieval pipeline** should make the graceful-degradation path at least as visible in the code as the happy path — per `CLAUDE.md`'s AI security and resilience notes, a failed or low-confidence LLM call degrading to a calm fallback message is a first-class behavior of this feature, not an afterthought bolted on. If the happy-path code is easy to find and the fallback is buried, that's worth restructuring, not just commenting around.

## 10. Comment density — the balance to strike

Not every line needs a comment. Obvious code (`return response.json()`) needs none. The comment budget should concentrate entirely on: **external dependency boundaries** (what happens when the IRS call fails, times out, or returns something unexpected; what happens when the Help Assistant's LLM call fails), **failure modes** (see §4), **non-obvious decisions** (see §3), and **anything a reasonable senior engineer might reasonably do differently**, with the reasoning for why this way was chosen. A file that's 40% comments explaining routine control flow is as unhelpful for a live walkthrough as a file with none — the goal is that every comment earns its place by answering a question an interviewer would actually ask.
