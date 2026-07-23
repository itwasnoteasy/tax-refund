"""Mock IRS Integration Service.

Built against docs/spec/01_SPEC.md and docs/spec/02_TECHNICAL_DESIGN.md.
Stands in for the real IRS by serving the seeded ground truth in
storage.py, filtered through one of 8 simulated behavior modes (see
02_TECHNICAL_DESIGN.md §2's table) -- each mode maps to a specific
failure class the full system design already addresses, so the demo
can trigger it live rather than just describing it.

The current mode is read from/written to Vercel KV (kv_store.py), not
held in local memory -- the demo control panel's `set_mode()` call and
a subsequent `get_status()` call may run in genuinely different
serverless invocations (see CLAUDE.md's Vercel section).

Distinguishes two kinds of "not a clean success" outcome, matching how
02_TECHNICAL_DESIGN.md §2 describes each mode:
  - Raised exceptions (FAILING, TIMEOUT, RATE_LIMITED, and a FLAPPING
    "failure" tick): the IRS could not be reached at all this call.
  - A returned but deliberately invalid dict (MALFORMED_RESPONSE,
    UNKNOWN_STATUS_CODE): the IRS responded, but with data that this
    system's future schema-validation/normalization step (in
    irs_integration.py, not yet implemented) must catch -- these are
    NOT raised as exceptions here, since the whole point is that they
    look like a successful response until validated.
"""
import time
from enum import Enum
from typing import Any, Dict

from app import storage
from app.kv_store import KVStore

# DECISION: delays kept well under Vercel's 10s execution ceiling, per
# CLAUDE.md -- long enough to be a visible latency concern (SLOW) or a
# genuine hang (TIMEOUT), not long enough to risk a real platform
# timeout during the demo. Exposed as module-level constants (rather
# than inlined) so tests can monkeypatch them down for fast execution --
# see test_unit_mock_irs.py.
SLOW_MODE_DELAY_SECONDS = 2.5
TIMEOUT_MODE_DELAY_SECONDS = 3.0

_MODE_KEY = "mock_irs:mode"
_FLAPPING_COUNTER_KEY = "mock_irs:flapping_call_count"


class IRSMode(str, Enum):
    """Mirrors POST /demo/set-irs-mode's `mode` enum in
    03_API_CONTRACT.yaml exactly.
    """

    NORMAL = "NORMAL"
    FAILING = "FAILING"
    FLAPPING = "FLAPPING"
    SLOW = "SLOW"
    TIMEOUT = "TIMEOUT"
    MALFORMED_RESPONSE = "MALFORMED_RESPONSE"
    UNKNOWN_STATUS_CODE = "UNKNOWN_STATUS_CODE"
    RATE_LIMITED = "RATE_LIMITED"


class IRSConnectionError(Exception):
    """FAILING mode, or a FLAPPING "failure" tick: connection error /
    503 from the (mock) IRS. Feeds the circuit breaker's consecutive-
    failure count (irs_integration.py, not yet implemented).
    """


class IRSTimeoutError(Exception):
    """TIMEOUT mode: a genuine hang past a defined ceiling, not a clean
    error response. A distinct code path from IRSConnectionError so a
    different retry/backoff policy can be applied upstream if desired,
    even though it counts toward the same failure threshold.
    """


class IRSRateLimitedError(Exception):
    """RATE_LIMITED mode: a 429-equivalent. Must be handled distinctly
    from a hard failure (no immediate retry storm) by whatever calls
    this.
    """


class IRSReturnNotFoundError(Exception):
    """The (mock) IRS has no record of this return_id/tax_year.

    Independent of mode -- even in NORMAL mode, a return this system's
    seed data doesn't know about can't be served. Not raised for the
    hard-failure modes (FAILING, TIMEOUT, RATE_LIMITED, a FLAPPING
    failure tick), since those represent "couldn't reach the IRS at
    all," regardless of which return was being asked about.
    """


def set_mode(mode: IRSMode) -> None:
    """Set the mock IRS's current behavior mode.

    Written to Vercel KV, not local memory -- see this module's
    docstring for why that matters here specifically.

    Args:
        mode: The mode to activate.
    """
    KVStore().set(_MODE_KEY, mode.value)


def get_mode() -> IRSMode:
    """Get the mock IRS's current behavior mode.

    Returns:
        The active mode, or IRSMode.NORMAL if none has been set yet.
    """
    raw = KVStore().get(_MODE_KEY)
    return IRSMode.NORMAL if raw is None else IRSMode(raw)


def get_status(return_id: str, tax_year: int) -> Dict[str, Any]:
    """Fetch a return's status from the mock IRS, per the current mode.

    Args:
        return_id: The return's unique identifier.
        tax_year: The tax year to look up.

    Returns:
        A raw response dict shaped like what an external IRS HTTP API
        would return, pre-validation: `return_id`, `tax_year`,
        `irs_status_code` (a raw string, not yet normalized into
        storage.StatusCode), and `last_updated_at` (ISO 8601). In
        MALFORMED_RESPONSE mode, `irs_status_code` is omitted entirely.
        In UNKNOWN_STATUS_CODE mode, `irs_status_code` holds a value
        outside storage.StatusCode's known members.

    Raises:
        IRSConnectionError: FAILING mode, or a FLAPPING "failure" tick.
        IRSTimeoutError: TIMEOUT mode, after a simulated hang.
        IRSRateLimitedError: RATE_LIMITED mode.
        IRSReturnNotFoundError: No seeded ground truth exists for this
            return_id/tax_year (checked whenever a response would
            otherwise be built -- not checked for the hard-failure
            modes above).
    """
    mode = get_mode()

    if mode == IRSMode.FAILING:
        # FAILURE MODE: every call fails -- feeds the circuit breaker's
        # consecutive-failure count toward OPEN (02_TECHNICAL_DESIGN.md §2).
        raise IRSConnectionError(f"(mock) IRS connection failed for {return_id}")

    if mode == IRSMode.TIMEOUT:
        # FAILURE MODE: a genuine hang, not a clean error response --
        # counts toward the breaker's failure threshold same as FAILING,
        # but via a distinct exception type. See TIMEOUT_MODE_DELAY_SECONDS.
        time.sleep(TIMEOUT_MODE_DELAY_SECONDS)
        raise IRSTimeoutError(f"(mock) IRS request timed out for {return_id}")

    if mode == IRSMode.RATE_LIMITED:
        # FAILURE MODE: a 429-equivalent -- the caller must back off
        # rather than retry immediately, distinct from a hard failure.
        raise IRSRateLimitedError(f"(mock) IRS rate-limited request for {return_id}")

    flapping_failed_this_call = False
    if mode == IRSMode.FLAPPING and _flapping_should_fail():
        flapping_failed_this_call = True

    if flapping_failed_this_call:
        # FAILURE MODE: an isolated failure, not a sustained one -- the
        # circuit breaker must NOT open on this (02_TECHNICAL_DESIGN.md
        # §2's "consecutive, not cumulative" requirement). See
        # _flapping_should_fail() for why this is deterministic, not
        # truly random.
        raise IRSConnectionError(f"(mock) IRS connection failed for {return_id}")

    if mode == IRSMode.SLOW:
        # DECISION: high latency, eventually succeeds -- must NOT count
        # as a circuit breaker failure (it's a latency concern, not a
        # reliability one). See SLOW_MODE_DELAY_SECONDS.
        time.sleep(SLOW_MODE_DELAY_SECONDS)

    ground_truth = storage.get_refund_status(return_id, tax_year)
    if ground_truth is None:
        raise IRSReturnNotFoundError(f"No IRS record for {return_id}/{tax_year}")

    if mode == IRSMode.MALFORMED_RESPONSE:
        # FAILURE MODE: schema-invalid data (a required field missing
        # entirely) -- must be caught by downstream validation and
        # quarantined, never silently indexed or served as real (CLAUDE.md).
        return {
            "return_id": ground_truth.return_id,
            "tax_year": ground_truth.tax_year,
            # "irs_status_code" deliberately omitted.
            "last_updated_at": ground_truth.status_last_updated_at.isoformat(),
        }

    if mode == IRSMode.UNKNOWN_STATUS_CODE:
        # FAILURE MODE: a status code outside this system's
        # normalization map -- must fall back explicitly and flag for
        # human review downstream, never be guessed at (CLAUDE.md).
        return {
            "return_id": ground_truth.return_id,
            "tax_year": ground_truth.tax_year,
            "irs_status_code": "PENDING_MANUAL_REVIEW",
            "last_updated_at": ground_truth.status_last_updated_at.isoformat(),
        }

    # NORMAL, and the successful paths of SLOW/FLAPPING: well-formed response.
    return {
        "return_id": ground_truth.return_id,
        "tax_year": ground_truth.tax_year,
        "irs_status_code": ground_truth.status_code.value,
        "last_updated_at": ground_truth.status_last_updated_at.isoformat(),
    }


def _flapping_should_fail() -> bool:
    """Decide FLAPPING mode's outcome for this call.

    Returns:
        True if this call should fail.
    """
    # DECISION: deterministic strict alternation (fail, success, fail,
    # success, ...) via a KV-backed counter, not true randomness. A live
    # demo shouldn't depend on a coin flip -- genuine 50/50 chance could
    # still (rarely) produce 3 consecutive failures and falsely trip the
    # circuit breaker, undermining the exact point this mode exists to
    # prove. Strict alternation guarantees a maximum run of 1 consecutive
    # failure and is trivially assertable in a test. The counter lives in
    # KV, not local memory, for the same cross-invocation reason as the
    # mode toggle itself. See DECISIONS.md.
    kv = KVStore()
    raw_count = kv.get(_FLAPPING_COUNTER_KEY)
    count = int(raw_count) + 1 if raw_count is not None else 1
    kv.set(_FLAPPING_COUNTER_KEY, str(count))
    return count % 2 == 1  # odd calls fail, even calls succeed


def reset_flapping_counter() -> None:
    """Reset FLAPPING mode's alternation counter to its initial state.

    Test-support only -- never called from application code.
    """
    KVStore().set(_FLAPPING_COUNTER_KEY, "0")
