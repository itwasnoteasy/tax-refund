# Demo Walkthrough Script

**Purpose:** a live-narration script, not a test spec (that's `05_ACCEPTANCE_CRITERIA.md`). This is what you actually *say* while clicking through the demo — happy path first, then each edge case, each one tied explicitly to the design decision or trade-off it demonstrates. Use this during the coding round, the AI-proficiency deep dive, and your own rehearsal.

**Suggested order:** go roughly in the sequence below — it builds from "the system works" to "the system fails gracefully" to "the system does something genuinely AI-interesting," which is a natural narrative arc rather than a random tour of features.

**Two screens, two tabs:** the demo now runs as `static/index.html` (the customer-facing screen — Check Refund Status, notifications, Tax Help Assistant, nothing else) and `static/admin.html` (the demo control panel), opened side by side in two browser tabs. This mirrors the real architectural separation between the product and its demo scaffolding, rather than putting both on one screen. Practically: open the admin tab, set the active demo user and mock IRS mode there, then switch to the customer tab and click Check Refund Status to see the effect — the two tabs share state through the backend (Vercel KV), not through anything browser-local, so this also works presenting from two separate windows or even two machines on the same deployment.

**The admin/demo control screen** (`admin.html`) is demo scaffolding, not part of the product surface (03_API_CONTRACT.yaml has no equivalent) — it's how you'll actually drive every failure mode below live, without a terminal or Postman in view. Three things worth calling out explicitly while presenting:
- **"Active demo user" is new:** picking a name here (John, Maria, Robert, Susan, David — one per seed return) determines who the customer screen "logs in" as the next time someone clicks Check Refund Status there. This is a single, global selection shared by every open customer tab — an explicit, disclosed trade-off for a live demo, not how a real multi-user product would work (see `06_SCOPE.md`'s "Users and roles" note). The customer screen greets them by name ("Welcome, Susan") and shows an honest acceptance line ("Your paper-filed return was accepted on June 30, 2026") built from the same seed data every other scenario already uses.
- **The circuit breaker indicator is a real, live read of `irs_integration.py`'s state** (CLOSED/OPEN/HALF_OPEN), not an inferred guess from response shape — it polls on the admin screen every few seconds, and also refreshes immediately right after you click "Apply mode" there. Worth pointing at directly during steps 9-11 rather than only describing the JSON.
- **Selecting a mode in the dropdown shows a live rubric before you even click "Apply mode"** — IRS/server impact, whether it counts toward the circuit breaker, and how it lands on the customer screen, for whichever mode is currently selected. Worth reading aloud the first time you switch modes during a live walkthrough, rather than narrating from memory — it's the same reasoning as `02_TECHNICAL_DESIGN.md` §2's table, just surfaced where you're actually clicking.
- **The "force cache miss" button clears only the freshness marker, not the durable last-known-good value** — deliberately, so it can be used right before demoing a failure mode without destroying the very fallback data that failure mode needs to show. (This was a real bug caught while rehearsing: the first version deleted both, which meant every rehearsed "miss then fail" ran into the no-fallback-exists edge case instead of the intended stale-cache moment.) It always targets whichever user is currently active, so there's no separate return-ID field to manage on the admin screen either.

---

## Part 1 — Happy Path

**1. Normal in-progress return, cache cold (first request)**
> "This is a fresh request — no cache entry yet. Watch the response time: this one goes all the way to the mock IRS service." *(click through)* "Notice the response includes `stale: false` and a `last_checked` timestamp — even on the happy path, the system always tells you how fresh the data is. That's deliberate: I wanted honesty about freshness to be structural, not something bolted on only for failure cases."

**2. Same return, requested again (cache warm)**
> "Same return, second request. This one never touches the mock IRS at all — it's served straight from cache." *(point at response time)* "Sub-200ms, which was the actual latency target in the design. The TTL here is set based on filing method — this return is e-filed and current-year, so it gets roughly a 20-24 hour cache window, matching how often IRS itself actually updates."

**3. A different filing method (paper-filed, `RET-2025-00004`)**
> "This return was paper-filed. Notice it's marked received, but there's deliberately no predicted delivery window — IRS doesn't give meaningful timeline detail for paper returns for weeks, so rather than show a confident-looking date range next to that honest gap, the window is suppressed entirely for paper filers. Showing both together looked contradictory the first time I actually put this on screen, which is exactly the kind of thing you only catch by looking at the real UI, not by reading the code."

**3b. A completed, terminal return (`RET-2025-00005`, status: sent)**
> "And this one's done — refund sent. Notice there's no predicted window and no explanation here either, but for the opposite reason: there's nothing left to predict or explain once a refund has actually shipped. Terminal states get their own clean, distinct display, not a leftover 'still processing' card that no longer makes sense."

---

## Part 2 — The Explanation Logic (your strongest single moment)

**4. EITC/CTC-flagged return**
> "This return is flagged for the Earned Income and Child Tax Credit. Watch what happens — it shows a plain-language explanation of the PATH Act hold, proactively, before anyone has to ask." *(pause)* "This is the one delay reason the system can honestly explain, because it's inferable directly from data we already hold about the return."

**5. A return with no inferable delay reason**
> "This return has no special flags — it's just still processing, for reasons the system genuinely doesn't know. Watch the explanation: it says exactly 'still processing.' It does not generate a plausible-sounding guess." *(this is worth stating explicitly)* "That's the single most important behavioral rule in this whole system — I'd rather show honest uncertainty than a confident, fabricated reason. This is templated logic, deliberately not an LLM call, for exactly that reason: a small, enumerable rule set is safer and more auditable than a model that could hallucinate a reason that sounds right but isn't."

**6. Balance-due (no refund pending)**
> "This return actually owes money, not a refund. Notice it doesn't show a blank or broken-looking refund tracker — it shows a distinct, explicit message. Handling the absence of the expected case explicitly, rather than letting it fall through as an empty state, is a small thing that matters a lot in a live product."

---

## Part 3 — Failure Modes (this is where the design gets interesting)

**7. Force `SLOW` mode, then query**
> "I'm switching the mock IRS to simulate high latency — it'll still succeed, just slowly." *(trigger it)* "Notice this does NOT trip the circuit breaker — a slow-but-successful response is a latency concern, not a reliability one, and the design treats those as genuinely different problems requiring different handling."

**8. Force `FLAPPING` mode, then query 2-3 times**
> "Now I'm simulating intermittent failures — sometimes it works, sometimes it doesn't, no consistent pattern." *(trigger several requests)* "Watch — the circuit breaker doesn't open. It's specifically designed to open only on *consecutive* failures, not cumulative ones. A system that trips on any failure at all would be far too trigger-happy against a dependency that's just having a rough patch, not actually down."

**9. Force `FAILING` mode, query 3+ times consecutively**
> "Now genuinely sustained failures — three in a row." *(trigger it)* "There — the circuit just opened. Watch what the response looks like now: it's serving the last-known-good cached value, with an honest 'last checked' timestamp, instead of an error page. The system has real data it can still show, even though the live source is down."

**10. Query again while circuit is OPEN**
> "Notice this request didn't even attempt to reach the mock IRS — it went straight to the cached fallback. That's the whole point of the circuit being open: stop hammering a dependency that's already told us it's struggling."

**11. Wait for cooldown / force `NORMAL`, then query (HALF_OPEN → CLOSED)**
> "Now I'm letting the circuit's cooldown elapse and setting IRS back to healthy." *(trigger the request)* "This request is the trial — the system allows exactly one request through in this 'half-open' state to test recovery. It succeeded, so the circuit fully closes and normal operation resumes." **(This transition is your single most technically impressive live moment — rehearse it until it's smooth.)**

**12. Force `TIMEOUT` mode**
> "This simulates a genuine hang, not a clean error response — a different failure signature than a 503. It still counts toward the circuit breaker's failure count, but through a different code path, since in a real system you might want different retry/backoff behavior for a timeout versus an explicit failure response."

**13. Force `MALFORMED_RESPONSE` mode**
> "This simulates IRS silently changing their response schema — a missing field, or a field with an unexpected type." *(trigger it)* "This goes to a quarantine path rather than being indexed or served as if it were valid — I never want a schema violation silently treated as real data. This is exactly the kind of thing that, in a real system, has caused actual production incidents when a third-party API changes a contract without notice."

**14. Force `UNKNOWN_STATUS_CODE` mode**
> "IRS returns a status code the system has never seen before — maybe they introduced a new category. Rather than guess at what it means, the system falls back explicitly and flags it for human review before that code gets added to the normalization map. Guessing at an unfamiliar status code's meaning is exactly the kind of silent assumption that causes long-tail bugs."

**15. Force `RATE_LIMITED` mode**
> "This simulates IRS pushing back with a rate-limit response — relevant if IRS ever moved from batch-only updates to some form of live query capability. The system needs to treat this differently from a hard failure, since retrying immediately would make the problem worse, not better."

---

## Part 4 — The Tax Help Assistant (a genuinely different kind of AI decision)

**16. Ask a well-covered question**
> "Switching gears — this is a separate feature from everything we just walked through. The refund status system has no LLM anywhere in it, on purpose. But general tax questions are a genuinely different shape of problem — open-ended, natural language, benefiting from retrieval. So I built this as its own small RAG pipeline." *(ask a question the FAQ corpus covers well)* "It retrieves from a small seeded FAQ set, hybrid dense-and-sparse, reranked, and only synthesizes an answer if the retrieved context is actually confident enough."

**17. Ask a question outside the FAQ corpus's coverage**
> "Now I'll ask something the corpus genuinely doesn't cover well." *(ask it)* "Notice it doesn't force an answer — the confidence gate catches the weak retrieval and it says so honestly, the same philosophy as the 'still processing' behavior in the core system, just applied to a retrieval-confidence signal instead of a rule-based flag."

**18. (If rehearsed and reliable) Simulate the LLM call failing**
> "If the model call itself fails or times out, the UI shows a calm 'temporarily unavailable' state — never a broken page. Same resilience philosophy as the circuit breaker, applied to a completely different kind of dependency."

---

## Closing line, after the full walkthrough

> "The thing I'd want you to take away from all of that: almost none of it is really about tax refunds specifically. It's about treating an external dependency as unreliable by default, being honest about uncertainty instead of guessing, and knowing precisely when AI is the right tool for a problem and when a small rule-based system is the better engineering choice."
