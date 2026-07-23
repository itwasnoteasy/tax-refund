"""Layer 1 -- the circuit breaker's state machine, tested directly
against the CircuitBreaker class, and fetch_status()'s validation/
outcome-mapping logic tested against a monkeypatched fake mock_irs
rather than the real module. Per 07_TESTING_STRATEGY.md, anything that
touches the real mock IRS "over an actual call boundary" is Layer 2 --
faking it out keeps this suite Layer 1: no network, no other real
component.
"""
from datetime import datetime

import pytest

from app import irs_integration, kv_store, mock_irs, storage
from app.irs_integration import BreakerState, CircuitBreaker, IRSOutcome

_KV_ENV_VARS = (
    "KV_REST_API_URL",
    "KV_REST_API_TOKEN",
    "UPSTASH_REDIS_REST_URL",
    "UPSTASH_REDIS_REST_TOKEN",
)
_RETURN_ID, _TAX_YEAR = "RET-2025-00001", 2025


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch: pytest.MonkeyPatch):
    for name in _KV_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    storage.reset_seed_data()
    kv_store.reset_local_backend()
    monkeypatch.setattr(irs_integration, "CIRCUIT_BREAKER_COOLDOWN_SECONDS", 0.05)
    yield
    kv_store.reset_local_backend()


def _valid_payload() -> dict:
    return {
        "return_id": _RETURN_ID,
        "tax_year": _TAX_YEAR,
        "irs_status_code": storage.StatusCode.RECEIVED.value,
        "last_updated_at": datetime(2026, 7, 20, 9, 0, 0).isoformat(),
    }


# --- CircuitBreaker: pure state machine, no mock_irs involved -----------


def test_breaker_starts_closed_and_allows_requests() -> None:
    breaker = CircuitBreaker()
    assert breaker.state == BreakerState.CLOSED
    assert breaker.allow_request() is True


def test_breaker_stays_closed_below_failure_threshold() -> None:
    breaker = CircuitBreaker()
    breaker.record_failure()
    breaker.record_failure()
    assert breaker.state == BreakerState.CLOSED
    assert breaker.allow_request() is True


def test_breaker_opens_after_three_consecutive_failures() -> None:
    breaker = CircuitBreaker()
    breaker.record_failure()
    breaker.record_failure()
    breaker.record_failure()
    assert breaker.state == BreakerState.OPEN
    assert breaker.allow_request() is False


def test_breaker_open_short_circuits_until_cooldown_elapses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import time

    monkeypatch.setattr(irs_integration, "CIRCUIT_BREAKER_COOLDOWN_SECONDS", 0.05)
    breaker = CircuitBreaker()
    for _ in range(irs_integration.CIRCUIT_BREAKER_FAILURE_THRESHOLD):
        breaker.record_failure()
    assert breaker.state == BreakerState.OPEN
    assert breaker.allow_request() is False  # still within cooldown

    time.sleep(0.1)
    assert breaker.allow_request() is True
    assert breaker.state == BreakerState.HALF_OPEN


def test_breaker_half_open_success_resets_to_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import time

    monkeypatch.setattr(irs_integration, "CIRCUIT_BREAKER_COOLDOWN_SECONDS", 0.05)
    breaker = CircuitBreaker()
    for _ in range(irs_integration.CIRCUIT_BREAKER_FAILURE_THRESHOLD):
        breaker.record_failure()
    time.sleep(0.1)
    assert breaker.allow_request() is True  # the HALF_OPEN trial

    breaker.record_success()

    assert breaker.state == BreakerState.CLOSED
    # Failure count was reset -- two more failures alone must not reopen it.
    breaker.record_failure()
    breaker.record_failure()
    assert breaker.state == BreakerState.CLOSED


def test_breaker_half_open_failure_reopens_with_fresh_cooldown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import time

    monkeypatch.setattr(irs_integration, "CIRCUIT_BREAKER_COOLDOWN_SECONDS", 0.05)
    breaker = CircuitBreaker()
    for _ in range(irs_integration.CIRCUIT_BREAKER_FAILURE_THRESHOLD):
        breaker.record_failure()
    time.sleep(0.1)
    assert breaker.allow_request() is True  # the HALF_OPEN trial

    breaker.record_failure()  # the trial itself fails

    assert breaker.state == BreakerState.OPEN
    assert breaker.allow_request() is False  # fresh cooldown, not yet elapsed


def test_breaker_synthetic_flapping_sequence_never_opens() -> None:
    """Constructs a FLAPPING-shaped sequence directly against the
    breaker -- alternating failure/success, exactly the pattern
    02_TECHNICAL_DESIGN.md §2 requires the breaker to NOT open on,
    since no run ever reaches 3 CONSECUTIVE failures.
    """
    breaker = CircuitBreaker()
    for _ in range(20):
        breaker.record_failure()
        assert breaker.state == BreakerState.CLOSED
        breaker.record_success()
        assert breaker.state == BreakerState.CLOSED


# --- fetch_status(): validation + outcome mapping, mock_irs faked ------


def test_fetch_status_success_upserts_and_closes_breaker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        mock_irs, "get_status", lambda return_id, tax_year: _valid_payload()
    )

    result = irs_integration.fetch_status(_RETURN_ID, _TAX_YEAR)

    assert result.outcome == IRSOutcome.SUCCESS
    assert result.status is not None
    assert result.status.status_code == storage.StatusCode.RECEIVED
    assert (
        storage.get_refund_status(_RETURN_ID, _TAX_YEAR).status_code
        == storage.StatusCode.RECEIVED
    )
    assert CircuitBreaker().state == BreakerState.CLOSED


def test_fetch_status_success_is_idempotent_on_repeat_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        mock_irs, "get_status", lambda return_id, tax_year: _valid_payload()
    )

    irs_integration.fetch_status(_RETURN_ID, _TAX_YEAR)
    irs_integration.fetch_status(_RETURN_ID, _TAX_YEAR)

    # DECISION: exactly one record for this key, proven by re-reading it
    # rather than needing a separate "count records" API storage.py
    # doesn't otherwise expose.
    status = storage.get_refund_status(_RETURN_ID, _TAX_YEAR)
    assert status.status_code == storage.StatusCode.RECEIVED


def test_fetch_status_connection_failure_counts_toward_breaker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _always_fails(return_id, tax_year):
        raise mock_irs.IRSConnectionError("simulated failure")

    monkeypatch.setattr(mock_irs, "get_status", _always_fails)

    for _ in range(irs_integration.CIRCUIT_BREAKER_FAILURE_THRESHOLD):
        result = irs_integration.fetch_status(_RETURN_ID, _TAX_YEAR)
        assert result.outcome == IRSOutcome.CONNECTION_FAILURE

    assert CircuitBreaker().state == BreakerState.OPEN


def test_fetch_status_short_circuits_when_breaker_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Once OPEN, mock_irs.get_status must not be called at all."""
    call_count = 0

    def _always_fails(return_id, tax_year):
        nonlocal call_count
        call_count += 1
        raise mock_irs.IRSConnectionError("simulated failure")

    monkeypatch.setattr(mock_irs, "get_status", _always_fails)

    for _ in range(irs_integration.CIRCUIT_BREAKER_FAILURE_THRESHOLD):
        irs_integration.fetch_status(_RETURN_ID, _TAX_YEAR)
    assert call_count == irs_integration.CIRCUIT_BREAKER_FAILURE_THRESHOLD

    result = irs_integration.fetch_status(_RETURN_ID, _TAX_YEAR)

    assert result.outcome == IRSOutcome.CIRCUIT_OPEN
    assert call_count == irs_integration.CIRCUIT_BREAKER_FAILURE_THRESHOLD  # unchanged


def test_fetch_status_timeout_counts_toward_breaker_same_as_connection_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _always_times_out(return_id, tax_year):
        raise mock_irs.IRSTimeoutError("simulated timeout")

    monkeypatch.setattr(mock_irs, "get_status", _always_times_out)

    for _ in range(irs_integration.CIRCUIT_BREAKER_FAILURE_THRESHOLD):
        result = irs_integration.fetch_status(_RETURN_ID, _TAX_YEAR)
        assert result.outcome == IRSOutcome.CONNECTION_FAILURE

    assert CircuitBreaker().state == BreakerState.OPEN


def test_fetch_status_rate_limited_does_not_count_toward_breaker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _always_rate_limited(return_id, tax_year):
        raise mock_irs.IRSRateLimitedError("simulated rate limit")

    monkeypatch.setattr(mock_irs, "get_status", _always_rate_limited)

    for _ in range(5):
        result = irs_integration.fetch_status(_RETURN_ID, _TAX_YEAR)
        assert result.outcome == IRSOutcome.RATE_LIMITED

    assert CircuitBreaker().state == BreakerState.CLOSED


def test_fetch_status_return_not_found_leaves_breaker_untouched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _always_not_found(return_id, tax_year):
        raise mock_irs.IRSReturnNotFoundError("simulated not found")

    monkeypatch.setattr(mock_irs, "get_status", _always_not_found)

    for _ in range(5):
        result = irs_integration.fetch_status(_RETURN_ID, _TAX_YEAR)
        assert result.outcome == IRSOutcome.RETURN_NOT_FOUND

    assert CircuitBreaker().state == BreakerState.CLOSED


def test_fetch_status_malformed_response_quarantines_and_counts_as_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Explicit per the task: MALFORMED_RESPONSE must count toward the
    breaker, unlike UNKNOWN_STATUS_CODE below.
    """

    def _malformed(return_id, tax_year):
        payload = _valid_payload()
        del payload["irs_status_code"]
        return payload

    monkeypatch.setattr(mock_irs, "get_status", _malformed)

    for _ in range(irs_integration.CIRCUIT_BREAKER_FAILURE_THRESHOLD - 1):
        result = irs_integration.fetch_status(_RETURN_ID, _TAX_YEAR)
        assert result.outcome == IRSOutcome.QUARANTINED
        assert CircuitBreaker().state == BreakerState.CLOSED

    final_result = irs_integration.fetch_status(_RETURN_ID, _TAX_YEAR)
    assert final_result.outcome == IRSOutcome.QUARANTINED
    assert CircuitBreaker().state == BreakerState.OPEN


def test_fetch_status_malformed_response_wrong_type_also_quarantines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _wrong_type(return_id, tax_year):
        payload = _valid_payload()
        payload["tax_year"] = str(payload["tax_year"])  # wrong type, not missing
        return payload

    monkeypatch.setattr(mock_irs, "get_status", _wrong_type)

    result = irs_integration.fetch_status(_RETURN_ID, _TAX_YEAR)

    assert result.outcome == IRSOutcome.QUARANTINED


def test_fetch_status_unknown_status_code_does_not_count_toward_breaker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The IRS connection worked, so -- unlike MALFORMED_RESPONSE -- this
    must not push the breaker toward OPEN.
    """

    def _unknown_code(return_id, tax_year):
        payload = _valid_payload()
        payload["irs_status_code"] = "PENDING_MANUAL_REVIEW"
        return payload

    monkeypatch.setattr(mock_irs, "get_status", _unknown_code)

    for _ in range(5):
        result = irs_integration.fetch_status(_RETURN_ID, _TAX_YEAR)
        assert result.outcome == IRSOutcome.UNKNOWN_STATUS_CODE
        assert result.raw_status_code == "PENDING_MANUAL_REVIEW"

    assert CircuitBreaker().state == BreakerState.CLOSED


def test_fetch_status_flapping_sequence_through_full_validation_never_opens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A FLAPPING-shaped sequence run through fetch_status() itself
    (validation + breaker together, mock_irs faked to stay Layer 1) --
    the circuit must never open.
    """
    call_count = 0

    def _flapping(return_id, tax_year):
        nonlocal call_count
        call_count += 1
        if call_count % 2 == 1:
            raise mock_irs.IRSConnectionError("simulated isolated failure")
        return _valid_payload()

    monkeypatch.setattr(mock_irs, "get_status", _flapping)

    for _ in range(20):
        irs_integration.fetch_status(_RETURN_ID, _TAX_YEAR)
        assert CircuitBreaker().state == BreakerState.CLOSED
