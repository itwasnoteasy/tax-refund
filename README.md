# ClearRefund — refund status + AI-predicted ETA (PoC)

A TurboTax-style refund status tracker with an honest, no-LLM prediction/explanation core, plus a separate Tax Help Assistant (RAG + Gemini) for open-ended tax questions. Built as a live-interview PoC — see `/docs/spec/` for the full spec set, `CLAUDE.md` for project ground rules, and `DECISIONS.md` for every non-obvious choice made along the way.

## Running it locally

**Requirements:** Python 3.11+.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt   # includes requirements.txt + pytest/uvicorn/etc.
uvicorn api.index:app --reload --port 8000
```

Then open, ideally in two tabs side by side:
- `http://127.0.0.1:8000/static/index.html` — the **customer screen**: Check Refund Status, notifications, Tax Help Assistant. This is what an end user would actually see; it carries no demo controls.
- `http://127.0.0.1:8000/static/admin.html` — the **demo control panel**: pick which demo user is "logged in," set the mock IRS mode, watch the live circuit breaker state, force a cache miss. Not part of the product surface — see `DECISIONS.md`'s "Frontend: split into static/index.html (customer) and static/admin.html (demo control)" entry for why these are separate pages rather than one screen with an `<aside>`.
- `http://127.0.0.1:8000/docs` — interactive OpenAPI docs

Both pages talk to the same backend, so changes made on the admin page (active user, mock IRS mode) take effect on the customer page the next time it's used — there's no cross-tab JavaScript wiring involved, just two pages hitting the same API.

**No Vercel KV needed locally.** `app/kv_store.py` auto-detects the missing `KV_REST_API_URL`/`KV_REST_API_TOKEN` env vars and falls back to an in-process in-memory store — this is why the app "just works" with zero setup, but also means cache/circuit-breaker/demo-mode state resets every time you restart the server, and (unlike on Vercel) is shared correctly across requests within a single local process.

**Tax Help Assistant, locally, without any API key:** it still works — `retrieval.py` and `graph.py` both fall back to a local, dependency-free implementation (hashed bag-of-words embeddings + BM25/RRF fusion for retrieval, an extractive "return the top document verbatim" synthesizer, and a lexical-overlap guardrail) when no Gemini credentials are present. This means you can demo the full pipeline shape offline, but the answers won't be real generative synthesis — set `GEMINI_API_KEY` (or `GOOGLE_API_KEY`) to exercise the real Gemini-backed path (embeddings, LLM rerank, synthesis, and the NLI-style guardrail).

Optional env vars (all have working defaults):
| Var | Purpose |
|---|---|
| `GEMINI_API_KEY` / `GOOGLE_API_KEY` | Enables the real Gemini-backed Tax Help Assistant pipeline. |
| `GEMINI_MODEL` | Overrides the synthesis/rerank/guardrail model (default `gemini-flash-latest`). |
| `GEMINI_EMBEDDING_MODEL` | Overrides the embedding model (default `text-embedding-004`). |
| `KV_REST_API_URL` / `KV_REST_API_TOKEN` (or `UPSTASH_REDIS_REST_URL` / `UPSTASH_REDIS_REST_TOKEN`) | Points at real Vercel KV/Upstash instead of the local in-memory fallback. |

## Running the tests

Three layers, per `docs/spec/07_TESTING_STRATEGY.md` — see `tests/README.md` for exactly which test covers which of `05_ACCEPTANCE_CRITERIA.md`'s six demo scenarios. Report each layer separately; don't rely on one combined pass count:

```bash
pytest tests/test_unit_*.py -v          # Layer 1: isolated logic, no network, no app instance
pytest tests/test_integration_*.py -v   # Layer 2 + Layer 3: full API round trips, contract compliance,
                                         # and the six acceptance scenarios (folded into this layer — see DECISIONS.md)
pytest -q                               # everything, for a quick sanity check
```

## Confirming the Vercel deployment

The app auto-deploys from the `claude/repo-permission-test-o9tdfc` branch (confirm this is still the branch Vercel is watching under Project Settings → Git). After every push, verify **on the live URL itself**, not just locally — `CLAUDE.md`'s central warning is that Vercel's serverless execution model (no shared memory across invocations, Vercel KV instead of an in-memory dict, a hard 10s/1024MB ceiling) can make local and live behavior genuinely diverge in ways no local test can catch. Concretely, this has already happened once: a `vercel.json` build-config gap left `static/index.html` returning 404 live despite working fine locally (see `DECISIONS.md`'s "vercel.json: added a @vercel/static build" entry) — a category of bug only a real deployment check can surface.

Checklist for each deploy:
1. **`GET https://<your-project>.vercel.app/`, `/static/index.html`, and `/static/admin.html`** — all three should load (the `/` route redirects to the customer screen).
2. **`GET /docs`** — OpenAPI docs render.
3. **`GET /refund-status/RET-2025-00001?tax_year=2025`** — returns a normal, fresh (`stale: false`) response.
4. **On `admin.html`, set mock IRS mode to `FAILING`, then go to `index.html` and check status 3 times** — the admin screen's circuit breaker indicator should flip to `OPEN` and the customer screen's response should show a stale banner with a real cached fallback. This specifically exercises Vercel KV, since the breaker's failure count, the cache's durable value, and the active-user selection all need to persist *across separate serverless invocations* — the one behavior the local in-memory fallback can't actually prove.
5. **Set mode back to `NORMAL` on `admin.html`, wait ~15s (the circuit breaker's cooldown), check status again on `index.html`** — the indicator should show `HALF_OPEN` briefly, then `CLOSED` on success.
6. **On `admin.html`, switch the active demo user (e.g. to Susan, the paper-filed one), then check status on `index.html`** — the customer screen should greet the new name and reflect that user's return, confirming the two pages are actually sharing state through the backend, not just coincidentally showing the same default.
7. **Ask the Tax Help Assistant a well-covered question** (e.g. "where is my refund") — with `GEMINI_API_KEY` set in Vercel's project env vars, this should return a real synthesized answer, sources, and a confidence score, not the local extractive fallback.
8. **Check Vercel's function logs** for anything unexpected — `graph.py`'s retrieval/synthesis/guardrail failure paths all log a `logger.warning(...)` with the real underlying error (including the Gemini API's response body) even though the user-facing response degrades gracefully, so this is the fastest way to diagnose a live-only failure without reproducing it locally.

## What each `DEMO_WALKTHROUGH_SCRIPT.md` beat demonstrates

The full live-narration script is in `docs/spec/DEMO_WALKTHROUGH_SCRIPT.md`; this is a one-line index of what each beat is *for*, not a substitute for reading it before presenting.

**Part 1 — Happy path**
1. Cache-cold request — the honest `stale`/`last_checked` fields exist on every response, not bolted on only for failures.
2. Cache-warm request — sub-200ms cache-aside hit, no IRS call at all.
3. Paper-filed return (`RET-2025-00004`) — a non-terminal status with the predicted-delivery window deliberately suppressed, so nothing contradicts the "no detail yet" framing.
3b. Terminal SENT return (`RET-2025-00005`) — a clean, distinct "done" state instead of a stale "still processing" leftover.

**Part 2 — Explanation logic**
4. EITC/CTC-flagged return — the one delay reason the system can honestly explain (PATH Act hold), shown proactively.
5. Unflagged return — "still processing," not a fabricated guess. The single most important behavioral rule in the system.
6. Balance-due / no-refund-pending return — a distinct, explicit message instead of a blank or broken tracker.

**Part 3 — Failure modes**
7. `SLOW` mode — succeeds slowly; does *not* trip the circuit breaker (a latency concern, not a reliability one).
8. `FLAPPING` mode — intermittent failures never open the circuit, since it trips only on *consecutive* failures.
9. `FAILING` mode, 3x — the circuit opens; a stale cached fallback is served with an honest "last checked" timestamp, not an error page.
10. Query again while `OPEN` — short-circuits without even attempting the IRS call.
11. Cooldown elapses, mode back to `NORMAL` — the single trial `HALF_OPEN` request succeeds and the circuit fully closes. **The most technically impressive live moment — rehearse this one the most.**
12. `TIMEOUT` mode — a genuine hang, a different failure signature than a clean error response, still counts toward the breaker.
13. `MALFORMED_RESPONSE` mode — a schema violation goes to quarantine, never served as if it were valid data.
14. `UNKNOWN_STATUS_CODE` mode — an unrecognized status code is flagged for review, never silently guessed at.
15. `RATE_LIMITED` mode — treated distinctly from a hard failure, since immediate retry would make it worse.

**Part 4 — Tax Help Assistant**
16. A well-covered question — hybrid dense+sparse retrieval, reranked, then synthesized only if confident.
17. An out-of-corpus question — the confidence gate declines honestly rather than forcing an answer.
18. A simulated LLM failure — a calm "temporarily unavailable" state, never a broken page.

**Demo control panel** (`static/admin.html`, not part of the product surface — see `06_SCOPE.md`): picks which demo user is active (all customer tabs reflect this one, shared instance), drives every mode switch above, shows the circuit breaker's live CLOSED/OPEN/HALF_OPEN state, and has a "force cache miss" button that clears only the freshness marker (not the durable fallback), so it can be used right before demoing a failure mode without destroying the data that failure mode needs to show.

## Project layout

```
api/index.py            FastAPI app, routes, Pydantic models mirroring 03_API_CONTRACT.yaml
app/                     Core logic: storage, cache_layer, irs_integration, mock_irs,
                          refund_logic, refund_status, notifications, audit, kv_store
app/demo_users.py         Demo-only named-user directory (John, Maria, ...), mapped onto
                          storage.py's seed returns -- backs the two-screen demo split
app/help_assistant/       The separate RAG feature: retrieval.py (hybrid search), graph.py
                          (LangGraph orchestration), faq_corpus.py
static/index.html         The customer screen -- Check Refund Status, notifications,
                          Tax Help Assistant. No demo controls.
static/admin.html         The demo control panel -- active user, mock IRS mode, circuit
                          breaker indicator, force cache miss. Not part of the product.
static/shared.js           Formatting helpers shared by both pages (Tailwind + Alpine.js
                            are still loaded via CDN in each page -- no build step, ever)
docs/spec/                 The authoritative spec set — read this before the code
tests/                    test_unit_*.py (Layer 1), test_integration_*.py (Layer 2 + 3)
DECISIONS.md              Every non-obvious implementation choice, with its honest gap
```
