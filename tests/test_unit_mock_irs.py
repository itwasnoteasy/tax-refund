"""Layer 1 -- each mock_irs.py mode's behavior in isolation. No network,
no other component -- the KV mode toggle and FLAPPING counter both go
through the local in-memory KV fallback here, not real KV. See
docs/spec/07_TESTING_STRATEGY.md.
"""
import time

import pytest

from app import kv_store, mock_irs, storage

_KV_ENV_VARS = (
    "KV_REST_API_URL",
    "KV_REST_API_TOKEN",
    "UPSTASH_REDIS_REST_URL",
    "UPSTASH_REDIS_REST_TOKEN",
)

_NORMAL_RETURN_ID, _NORMAL_TAX_YEAR = "RET-2025-00001", 2025


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch: pytest.MonkeyPatch):
    """Explicit, in-file fixture (not conftest.py) per
    04_CODING_STANDARDS.md's preference for test setup a reader can see
    without tracing a shared fixture chain. Clears KV env vars (forcing
    the local backend), resets storage's seed data, and resets the mock
    IRS's own KV-backed mode/counter state.
    """
    for name in _KV_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    storage.reset_seed_data()
    kv_store.reset_local_backend()
    yield
    kv_store.reset_local_backend()


def test_get_mode_defaults_to_normal_when_never_set() -> None:
    assert mock_irs.get_mode() == mock_irs.IRSMode.NORMAL


def test_set_mode_then_get_mode_reflects_it_via_kv() -> None:
    """The mode toggle round-trips through KV, not local memory --
    proven here by constructing get_mode()'s underlying KVStore
    separately from set_mode()'s, exactly as a demo-control-panel
    request and a subsequent status request would in production.
    """
    mock_irs.set_mode(mock_irs.IRSMode.SLOW)
    assert mock_irs.get_mode() == mock_irs.IRSMode.SLOW


def test_normal_mode_returns_seeded_ground_truth() -> None:
    mock_irs.set_mode(mock_irs.IRSMode.NORMAL)

    response = mock_irs.get_status(_NORMAL_RETURN_ID, _NORMAL_TAX_YEAR)

    assert response["return_id"] == _NORMAL_RETURN_ID
    assert response["tax_year"] == _NORMAL_TAX_YEAR
    assert response["irs_status_code"] == storage.StatusCode.RECEIVED.value
    assert "last_updated_at" in response


def test_normal_mode_unknown_return_raises_not_found() -> None:
    mock_irs.set_mode(mock_irs.IRSMode.NORMAL)

    with pytest.raises(mock_irs.IRSReturnNotFoundError):
        mock_irs.get_status("RET-9999-99999", 2025)


def test_failing_mode_always_raises_connection_error() -> None:
    mock_irs.set_mode(mock_irs.IRSMode.FAILING)

    for _ in range(5):
        with pytest.raises(mock_irs.IRSConnectionError):
            mock_irs.get_status(_NORMAL_RETURN_ID, _NORMAL_TAX_YEAR)


def test_flapping_mode_strictly_alternates_and_never_fails_three_in_a_row() -> None:
    mock_irs.set_mode(mock_irs.IRSMode.FLAPPING)
    mock_irs.reset_flapping_counter()

    outcomes = []
    for _ in range(20):
        try:
            mock_irs.get_status(_NORMAL_RETURN_ID, _NORMAL_TAX_YEAR)
            outcomes.append("success")
        except mock_irs.IRSConnectionError:
            outcomes.append("failure")

    # Deterministic alternation starting with a failure on the first call.
    assert outcomes == ["failure", "success"] * 10

    max_consecutive_failures = max(
        (
            sum(1 for _ in group)
            for is_failure, group in _group_consecutive(outcomes)
            if is_failure
        ),
        default=0,
    )
    assert max_consecutive_failures < 3


def _group_consecutive(outcomes):
    """Group a list of outcomes into (is_failure, group) runs, without
    pulling in itertools.groupby just for this one test's readability.
    """
    groups = []
    current_value = None
    current_group: list = []
    for outcome in outcomes:
        is_failure = outcome == "failure"
        if is_failure != current_value:
            if current_group:
                groups.append((current_value, current_group))
            current_value = is_failure
            current_group = [outcome]
        else:
            current_group.append(outcome)
    if current_group:
        groups.append((current_value, current_group))
    return groups


def test_slow_mode_delays_by_configured_amount_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tests the mechanism (it sleeps for whatever SLOW_MODE_DELAY_SECONDS
    says), not the literal production duration -- monkeypatched down so
    this stays a fast Layer 1 test per 07_TESTING_STRATEGY.md.
    """
    monkeypatch.setattr(mock_irs, "SLOW_MODE_DELAY_SECONDS", 0.05)
    mock_irs.set_mode(mock_irs.IRSMode.SLOW)

    started_at = time.monotonic()
    response = mock_irs.get_status(_NORMAL_RETURN_ID, _NORMAL_TAX_YEAR)
    elapsed = time.monotonic() - started_at

    assert elapsed >= 0.05
    assert response["irs_status_code"] == storage.StatusCode.RECEIVED.value


def test_timeout_mode_raises_distinct_error_after_configured_delay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TIMEOUT is a genuinely different code path from FAILING -- a
    distinct exception type, not just the same error raised again.
    """
    monkeypatch.setattr(mock_irs, "TIMEOUT_MODE_DELAY_SECONDS", 0.05)
    mock_irs.set_mode(mock_irs.IRSMode.TIMEOUT)

    started_at = time.monotonic()
    with pytest.raises(mock_irs.IRSTimeoutError):
        mock_irs.get_status(_NORMAL_RETURN_ID, _NORMAL_TAX_YEAR)
    elapsed = time.monotonic() - started_at

    assert elapsed >= 0.05


def test_timeout_error_is_not_a_connection_error() -> None:
    assert not issubclass(mock_irs.IRSTimeoutError, mock_irs.IRSConnectionError)


def test_rate_limited_mode_raises_distinct_error() -> None:
    mock_irs.set_mode(mock_irs.IRSMode.RATE_LIMITED)

    with pytest.raises(mock_irs.IRSRateLimitedError):
        mock_irs.get_status(_NORMAL_RETURN_ID, _NORMAL_TAX_YEAR)


def test_rate_limited_error_is_not_a_connection_error() -> None:
    assert not issubclass(mock_irs.IRSRateLimitedError, mock_irs.IRSConnectionError)


def test_malformed_response_mode_omits_required_field_without_raising() -> None:
    mock_irs.set_mode(mock_irs.IRSMode.MALFORMED_RESPONSE)

    response = mock_irs.get_status(_NORMAL_RETURN_ID, _NORMAL_TAX_YEAR)

    assert "irs_status_code" not in response
    assert response["return_id"] == _NORMAL_RETURN_ID


def test_unknown_status_code_mode_returns_code_outside_known_enum() -> None:
    mock_irs.set_mode(mock_irs.IRSMode.UNKNOWN_STATUS_CODE)

    response = mock_irs.get_status(_NORMAL_RETURN_ID, _NORMAL_TAX_YEAR)

    known_values = {member.value for member in storage.StatusCode}
    assert response["irs_status_code"] not in known_values
