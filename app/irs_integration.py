"""Hardened IRS connector: response validation + 3-state circuit breaker.

Built against docs/spec/01_SPEC.md and docs/spec/02_TECHNICAL_DESIGN.md.
This module owns two related but separable jobs:

  1. `CircuitBreaker` -- the CLOSED/OPEN/HALF_OPEN state machine from
     02_TECHNICAL_DESIGN.md §2, state stored in Vercel KV (kv_store.py),
     not local memory, since the request that opens the circuit and a
     later request that checks it may run in different serverless
     invocations.
  2. `fetch_status()` -- validates whatever mock_irs.get_status()
     returns or raises, decides what that means for the breaker, and
     upserts a successful, validated result into storage.py.

Scope note, since this boundary matters for how the result of this
module gets used: when the breaker is OPEN, `fetch_status()` returns
`IRSOutcome.CIRCUIT_OPEN` and does NOT attempt to reach the (mock) IRS
-- but it does not itself serve stale cached data. This module has no
access to the cache (that's cache_layer.py) or to the response-assembly
orchestration (that's refund_status.py, not yet implemented) -- both
are later phases. A caller seeing CIRCUIT_OPEN is expected to fall back
to its own last-known-good cache entry, with its own
`last_validated_at` timestamp, at that layer.
"""
import time
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional

from app import mock_irs, storage
from app.kv_store import KVStore

# DECISION: 3-consecutive-failure threshold, matching
# 04_CODING_STANDARDS.md §3's own worked example and
# 02_TECHNICAL_DESIGN.md §2 exactly.
CIRCUIT_BREAKER_FAILURE_THRESHOLD = 3

# DECISION: a short cooldown (seconds, not minutes) is a deliberate,
# demo-driven choice -- a real production breaker would likely use
# minutes of cooldown, but a live audience won't wait that long to see
# the HALF_OPEN -> CLOSED recovery transition, which
# 05_ACCEPTANCE_CRITERIA.md calls "the single most technically
# impressive live moment." Exposed as a module constant so tests can
# monkeypatch it down further for fast execution. See DECISIONS.md.
CIRCUIT_BREAKER_COOLDOWN_SECONDS = 15.0

_STATE_KEY = "irs_breaker:state"
_FAILURE_COUNT_KEY = "irs_breaker:consecutive_failures"
_OPENED_AT_KEY = "irs_breaker:opened_at"

_REQUIRED_RESPONSE_FIELD_TYPES: Dict[str, type] = {
    "return_id": str,
    "tax_year": int,
    "irs_status_code": str,
    "last_updated_at": str,
}
_KNOWN_STATUS_CODES = {member.value for member in storage.StatusCode}


class BreakerState(str, Enum):
    """The circuit breaker's 3 states, per 02_TECHNICAL_DESIGN.md §2."""

    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class IRSOutcome(str, Enum):
    """What happened on one fetch_status() call.

    Mirrors the distinctions 02_TECHNICAL_DESIGN.md §2 draws between
    failure classes -- deliberately not a plain success/failure bool,
    since callers need to react differently to each (e.g. RATE_LIMITED
    must not trigger an immediate retry; UNKNOWN_STATUS_CODE must be
    flagged for review, not guessed at).
    """

    SUCCESS = "SUCCESS"
    CIRCUIT_OPEN = "CIRCUIT_OPEN"
    CONNECTION_FAILURE = "CONNECTION_FAILURE"
    QUARANTINED = "QUARANTINED"
    UNKNOWN_STATUS_CODE = "UNKNOWN_STATUS_CODE"
    RATE_LIMITED = "RATE_LIMITED"
    RETURN_NOT_FOUND = "RETURN_NOT_FOUND"


@dataclass(frozen=True)
class IRSFetchResult:
    """The outcome of one fetch_status() call.

    Attributes:
        outcome: What happened.
        status: The validated, persisted RefundStatus -- populated only
            when outcome is SUCCESS.
        raw_status_code: The unrecognized raw code from the IRS --
            populated only when outcome is UNKNOWN_STATUS_CODE.
        detail: Human-readable note, suitable for a log line.
    """

    outcome: IRSOutcome
    status: Optional[storage.RefundStatus] = None
    raw_status_code: Optional[str] = None
    detail: str = ""


class CircuitBreaker:
    """3-state (CLOSED/OPEN/HALF_OPEN) circuit breaker, state in Vercel KV.

    A fresh CircuitBreaker() may be constructed per call (mirroring how
    a serverless invocation would use it) -- all state lives in KV, not
    on the instance, so separately constructed breakers agree on the
    current state. See kv_store.py for why that's safe even against
    the local fallback backend.
    """

    def __init__(self) -> None:
        self._kv = KVStore()

    @property
    def state(self) -> BreakerState:
        """The breaker's current state, defaulting to CLOSED if unset."""
        raw = self._kv.get(_STATE_KEY)
        return BreakerState.CLOSED if raw is None else BreakerState(raw)

    def allow_request(self) -> bool:
        """Decide whether a request may attempt to reach the IRS now.

        Returns:
            True if the caller should proceed to call the (mock) IRS;
            False if the breaker is OPEN and still cooling down.
        """
        current_state = self.state

        if current_state == BreakerState.CLOSED:
            return True

        if current_state == BreakerState.HALF_OPEN:
            # FAILURE MODE: a trial is already in flight for this
            # breaker. In this single-request-at-a-time PoC (no
            # concurrent-request arbitration -- see DECISIONS.md), the
            # next call through is treated as that one trial.
            return True

        # current_state == BreakerState.OPEN
        if self._cooldown_elapsed():
            # FAILURE MODE: cooldown has passed. Rather than require a
            # manual reset signal, transition OPEN -> HALF_OPEN and let
            # exactly one trial request through -- the textbook pattern.
            # Resolved by whichever of record_success()/record_failure()
            # the caller invokes next.
            self._set_state(BreakerState.HALF_OPEN)
            return True

        # FAILURE MODE: still within cooldown -- short-circuit without
        # attempting to reach the IRS at all. Every request while OPEN
        # would otherwise add latency waiting on a dependency that has
        # already told us it's struggling; the caller is expected to
        # serve its own last-known-good cache instead (see this
        # module's docstring for that boundary).
        return False

    def record_success(self) -> None:
        """Record a successful request; always resets to CLOSED.

        # FAILURE MODE (recovery path): applies whether the success came
        # from ordinary CLOSED-state traffic or from the HALF_OPEN
        # recovery trial -- either way, a success means the consecutive-
        # failure count resets to zero and the breaker is fully healthy.
        """
        self._kv.set(_FAILURE_COUNT_KEY, "0")
        self._set_state(BreakerState.CLOSED)

    def record_failure(self) -> None:
        """Record a failed request, opening the circuit if warranted."""
        current_state = self.state

        if current_state == BreakerState.HALF_OPEN:
            # FAILURE MODE: the recovery trial itself failed. Back to
            # OPEN immediately with a fresh cooldown -- a single failed
            # trial is conclusive on its own and does not need to go
            # through the ordinary 3-consecutive-failure threshold again.
            self._set_state(BreakerState.OPEN)
            self._kv.set(_OPENED_AT_KEY, str(time.time()))
            return

        count = self._consecutive_failures() + 1
        self._kv.set(_FAILURE_COUNT_KEY, str(count))
        if count >= CIRCUIT_BREAKER_FAILURE_THRESHOLD:
            # FAILURE MODE: 3 CONSECUTIVE failures -- CLOSED -> OPEN.
            # Rather than keep retrying (adds latency to every request
            # while the IRS is down) or raise an error (a broken page
            # for data this system may already have cached), the breaker
            # opens so the caller can short-circuit to its own cache. A
            # single isolated failure (FLAPPING) never reaches this
            # branch, because a success in between calls record_success()
            # and resets the count to zero first -- see
            # test_unit_irs_integration.py's FLAPPING-sequence tests.
            self._set_state(BreakerState.OPEN)
            self._kv.set(_OPENED_AT_KEY, str(time.time()))

    def reset(self) -> None:
        """Reset the breaker to a fresh CLOSED state.

        Test-support only -- never called from application code.
        """
        self._kv.set(_STATE_KEY, BreakerState.CLOSED.value)
        self._kv.set(_FAILURE_COUNT_KEY, "0")
        self._kv.set(_OPENED_AT_KEY, "0")

    def _consecutive_failures(self) -> int:
        raw = self._kv.get(_FAILURE_COUNT_KEY)
        return int(raw) if raw is not None else 0

    def _opened_at(self) -> Optional[float]:
        raw = self._kv.get(_OPENED_AT_KEY)
        return float(raw) if raw is not None else None

    def _cooldown_elapsed(self) -> bool:
        opened_at = self._opened_at()
        if opened_at is None:
            # FAILURE MODE: state says OPEN but there's no recorded
            # open-time (shouldn't happen in normal operation) -- fail
            # toward allowing a trial rather than getting stuck OPEN
            # forever with no way to recover.
            return True
        return (time.time() - opened_at) >= CIRCUIT_BREAKER_COOLDOWN_SECONDS

    def _set_state(self, new_state: BreakerState) -> None:
        self._kv.set(_STATE_KEY, new_state.value)


def fetch_status(return_id: str, tax_year: int) -> IRSFetchResult:
    """Fetch and validate a return's status from the (mock) IRS.

    Gated by the circuit breaker: if it's OPEN and still cooling down,
    the (mock) IRS is never called. Otherwise, whatever mock_irs.py
    returns or raises is validated and mapped to an IRSOutcome, with
    the breaker updated accordingly (see IRSOutcome's docstring for
    which outcomes count as a breaker failure).

    Args:
        return_id: The return's unique identifier.
        tax_year: The tax year to look up.

    Returns:
        An IRSFetchResult describing what happened.
    """
    breaker = CircuitBreaker()

    if not breaker.allow_request():
        # FAILURE MODE: breaker is OPEN and cooldown hasn't elapsed --
        # short-circuit without attempting to reach the IRS at all.
        return IRSFetchResult(
            outcome=IRSOutcome.CIRCUIT_OPEN,
            detail="Circuit breaker is OPEN; no request attempted.",
        )

    try:
        raw = mock_irs.get_status(return_id, tax_year)
    except mock_irs.IRSReturnNotFoundError:
        # FAILURE MODE: the IRS was reached fine; it simply has no
        # record of this return. Not a connectivity failure, so it does
        # not affect the breaker either way.
        return IRSFetchResult(
            outcome=IRSOutcome.RETURN_NOT_FOUND,
            detail=f"No IRS record for {return_id}/{tax_year}.",
        )
    except mock_irs.IRSRateLimitedError:
        # FAILURE MODE: a 429-equivalent. Handled distinctly from a hard
        # failure -- does not increment the breaker's failure count
        # (the IRS is reachable, just asking us to back off), and the
        # caller must not immediately retry.
        return IRSFetchResult(
            outcome=IRSOutcome.RATE_LIMITED,
            detail=f"(mock) IRS rate-limited the request for {return_id}.",
        )
    except (mock_irs.IRSConnectionError, mock_irs.IRSTimeoutError) as exc:
        # FAILURE MODE: FAILING (or an isolated FLAPPING failure tick)
        # raises IRSConnectionError; TIMEOUT raises IRSTimeoutError --
        # two different exception types for two different real-world
        # failure shapes, but both count toward the breaker's
        # consecutive-failure threshold identically.
        breaker.record_failure()
        return IRSFetchResult(
            outcome=IRSOutcome.CONNECTION_FAILURE, detail=str(exc)
        )

    validation_error = _validate_raw_response(raw)
    if validation_error is not None:
        # FAILURE MODE: schema-invalid data (MALFORMED_RESPONSE mode) --
        # quarantined, never indexed or served as if it were real
        # (CLAUDE.md). Counts toward the breaker's failure threshold:
        # the IRS responded, but not usably, which this system treats
        # the same as an unreachable IRS for breaker purposes -- an
        # explicit call made here, not left ambiguous.
        breaker.record_failure()
        return IRSFetchResult(outcome=IRSOutcome.QUARANTINED, detail=validation_error)

    raw_status_code = raw["irs_status_code"]
    if raw_status_code not in _KNOWN_STATUS_CODES:
        # FAILURE MODE: a status code outside this system's
        # normalization map (UNKNOWN_STATUS_CODE mode) -- explicit
        # fallback, flagged for human review, never a guess at what it
        # might mean (CLAUDE.md). The IRS connection itself worked, so
        # -- unlike MALFORMED_RESPONSE above -- this does NOT count
        # toward the breaker; see DECISIONS.md for that asymmetry.
        return IRSFetchResult(
            outcome=IRSOutcome.UNKNOWN_STATUS_CODE,
            raw_status_code=raw_status_code,
            detail=(
                f"Unrecognized IRS status code {raw_status_code!r} for "
                f"{return_id}/{tax_year} -- flagged for review, not served."
            ),
        )

    # Well-formed, recognized response: a validated success.
    status = storage.RefundStatus(
        return_id=raw["return_id"],
        tax_year=raw["tax_year"],
        status_code=storage.StatusCode(raw_status_code),
        status_last_updated_at=datetime.fromisoformat(raw["last_updated_at"]),
    )
    # DECISION: upsert by (return_id, tax_year), not insert -- reprocessing
    # the same successful IRS response is idempotent (CLAUDE.md,
    # 01_SPEC.md §3.4), never creates a duplicate record.
    storage.upsert_refund_status(status)
    breaker.record_success()
    return IRSFetchResult(outcome=IRSOutcome.SUCCESS, status=status)


def _validate_raw_response(raw: Dict[str, Any]) -> Optional[str]:
    """Validate a raw (mock) IRS response's shape.

    Args:
        raw: The response dict returned by mock_irs.get_status().

    Returns:
        None if the response is well-formed; otherwise a human-readable
        description of what's wrong, suitable for a quarantine log line.
    """
    for field_name, expected_type in _REQUIRED_RESPONSE_FIELD_TYPES.items():
        if field_name not in raw:
            # FAILURE MODE: a required field missing entirely.
            return f"Missing required field {field_name!r}."
        if not isinstance(raw[field_name], expected_type):
            # FAILURE MODE: a field present but the wrong type -- the
            # other half of "missing required field, wrong type" from
            # 02_TECHNICAL_DESIGN.md §2's MALFORMED_RESPONSE description.
            return (
                f"Field {field_name!r} has type "
                f"{type(raw[field_name]).__name__}, expected "
                f"{expected_type.__name__}."
            )

    try:
        datetime.fromisoformat(raw["last_updated_at"])
    except ValueError:
        # FAILURE MODE: the right type (str) but not a parseable ISO
        # 8601 datetime -- still schema-invalid data, just a subtler
        # flavor than a missing or wrong-typed field.
        return (
            f"Field 'last_updated_at' is not a valid ISO 8601 datetime: "
            f"{raw['last_updated_at']!r}."
        )

    return None
