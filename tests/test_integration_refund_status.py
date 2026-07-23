"""Layer 2 -- the full cache-aside flow: refund_status.py wired together
with cache_layer.py, irs_integration.py, mock_irs.py, and storage.py for
real (mock IRS not faked here, unlike test_unit_irs_integration.py) --
this is exactly what 07_TESTING_STRATEGY.md scopes to Layer 2:
"components wired together ... through the mock IRS Integration
Service." See docs/spec/07_TESTING_STRATEGY.md.
"""
from datetime import date, datetime

import pytest

from app import cache_layer, irs_integration, kv_store, mock_irs, refund_status, storage

_KV_ENV_VARS = (
    "KV_REST_API_URL",
    "KV_REST_API_TOKEN",
    "UPSTASH_REDIS_REST_URL",
    "UPSTASH_REDIS_REST_TOKEN",
)
_RETURN_ID, _TAX_YEAR = "RET-2025-00001", 2025  # the normal, unflagged seed return


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch: pytest.MonkeyPatch):
    for name in _KV_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    storage.reset_seed_data()
    kv_store.reset_local_backend()
    monkeypatch.setattr(irs_integration, "CIRCUIT_BREAKER_COOLDOWN_SECONDS", 0.05)
    mock_irs.set_mode(mock_irs.IRSMode.NORMAL)
    yield
    kv_store.reset_local_backend()


def test_cache_miss_calls_irs_exactly_once_and_populates_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    call_count = 0
    original_get_status = mock_irs.get_status

    def _counting_get_status(return_id, tax_year):
        nonlocal call_count
        call_count += 1
        return original_get_status(return_id, tax_year)

    monkeypatch.setattr(mock_irs, "get_status", _counting_get_status)

    assert cache_layer.get_fresh_cached_data(_RETURN_ID, _TAX_YEAR) is None  # cold

    view = refund_status.get_refund_status_view(_RETURN_ID, _TAX_YEAR)

    assert call_count == 1
    assert view.stale is False
    assert view.status_code == storage.StatusCode.RECEIVED
    assert cache_layer.get_fresh_cached_data(_RETURN_ID, _TAX_YEAR) is not None


def test_cache_hit_returns_without_calling_irs_at_all(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    refund_status.get_refund_status_view(_RETURN_ID, _TAX_YEAR)  # warms the cache

    def _fail_if_called(return_id, tax_year):
        raise AssertionError("mock_irs.get_status must not be called on a cache hit")

    monkeypatch.setattr(mock_irs, "get_status", _fail_if_called)

    view = refund_status.get_refund_status_view(_RETURN_ID, _TAX_YEAR)

    assert view.stale is False
    assert view.status_code == storage.StatusCode.RECEIVED


def test_ttl_differs_by_filing_method() -> None:
    """05_ACCEPTANCE_CRITERIA.md: assert this directly, not just by code
    inspection.
    """
    current_year_ttl = cache_layer.ttl_seconds_for_filing_method(
        storage.FilingMethod.EFILE_CURRENT_YEAR
    )
    prior_year_ttl = cache_layer.ttl_seconds_for_filing_method(
        storage.FilingMethod.EFILE_PRIOR_YEAR
    )
    transitional_ttl = cache_layer.ttl_seconds_for_filing_method(
        storage.FilingMethod.TRANSITIONAL
    )
    paper_ttl = cache_layer.ttl_seconds_for_filing_method(storage.FilingMethod.PAPER)

    assert len({current_year_ttl, prior_year_ttl, transitional_ttl, paper_ttl}) == 4
    assert transitional_ttl < current_year_ttl < prior_year_ttl < paper_ttl


def test_every_response_has_stale_and_last_checked_even_when_fresh() -> None:
    view = refund_status.get_refund_status_view(_RETURN_ID, _TAX_YEAR)
    assert view.stale is False
    assert view.last_checked is not None


def _prime_cache_with_short_ttl(monkeypatch: pytest.MonkeyPatch) -> datetime:
    """Warm the cache via a real successful lookup, with the freshness
    TTL monkeypatched down to 1s so it naturally expires shortly after
    -- via cache_layer's public TTL table, not by poking its private KV
    key-naming scheme directly (which would make this test brittle
    against an internal refactor).

    Returns:
        The last_checked timestamp the cache was warmed with, so the
        caller can assert the stale fallback preserves it unchanged.
    """
    monkeypatch.setitem(
        cache_layer._TTL_SECONDS_BY_FILING_METHOD,
        storage.FilingMethod.EFILE_CURRENT_YEAR,
        1,
    )
    view = refund_status.get_refund_status_view(_RETURN_ID, _TAX_YEAR)
    assert view.stale is False
    return view.last_checked


def test_circuit_opens_after_three_failures_and_serves_stale_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import time

    last_checked = _prime_cache_with_short_ttl(monkeypatch)
    time.sleep(1.2)  # let the freshness TTL lapse -- a genuine cache miss next

    mock_irs.set_mode(mock_irs.IRSMode.FAILING)

    for _ in range(irs_integration.CIRCUIT_BREAKER_FAILURE_THRESHOLD):
        view = refund_status.get_refund_status_view(_RETURN_ID, _TAX_YEAR)
        # FAILURE MODE: IRS unreachable -- serve the last-known-good
        # value, marked stale, with its original last_checked timestamp
        # (not "now"), instead of an error page.
        assert view.stale is True
        assert view.status_code == storage.StatusCode.RECEIVED
        assert view.last_checked == last_checked

    assert irs_integration.CircuitBreaker().state == irs_integration.BreakerState.OPEN


def test_circuit_open_short_circuits_without_attempting_irs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import time

    _prime_cache_with_short_ttl(monkeypatch)
    time.sleep(1.2)

    mock_irs.set_mode(mock_irs.IRSMode.FAILING)
    for _ in range(irs_integration.CIRCUIT_BREAKER_FAILURE_THRESHOLD):
        refund_status.get_refund_status_view(_RETURN_ID, _TAX_YEAR)
    assert irs_integration.CircuitBreaker().state == irs_integration.BreakerState.OPEN

    def _fail_if_called(return_id, tax_year):
        raise AssertionError("must not attempt IRS while circuit is OPEN")

    monkeypatch.setattr(mock_irs, "get_status", _fail_if_called)

    view = refund_status.get_refund_status_view(_RETURN_ID, _TAX_YEAR)
    assert view.stale is True


def test_flapping_mode_never_opens_circuit_across_repeated_lookups() -> None:
    mock_irs.set_mode(mock_irs.IRSMode.FLAPPING)
    mock_irs.reset_flapping_counter()

    for _ in range(10):
        # Force a miss every time by clearing the freshness marker, so
        # each call genuinely goes through fetch_status() again.
        kv_store.reset_local_backend()
        refund_status.get_refund_status_view(_RETURN_ID, _TAX_YEAR)
        assert irs_integration.CircuitBreaker().state != irs_integration.BreakerState.OPEN


def test_no_refund_pending_return_has_no_prediction_or_explanation() -> None:
    """Acceptance-criteria row 6: a distinct, explicit response shape."""
    view = refund_status.get_refund_status_view("RET-2025-00003", 2025)
    assert view.status_code == storage.StatusCode.NO_REFUND_PENDING
    assert view.predicted_window is None
    assert view.explanation is None
    assert view.expected_refund_amount is None


def test_paper_filed_return_has_no_predicted_window_but_has_explanation() -> None:
    """Found while building the frontend: showing a confident-looking
    date range next to "paper returns take weeks longer, no detail yet"
    looked contradictory on screen. Real paper processing gives no
    meaningful timeline at all -- explanation still applies normally
    (this return has no EITC/CTC flag, so "still processing"), only
    the window is suppressed.
    """
    view = refund_status.get_refund_status_view("RET-2025-00004", 2025)
    assert view.status_code == storage.StatusCode.RECEIVED
    assert view.predicted_window is None
    assert view.explanation == "still processing"


def test_eitc_ctc_return_gets_path_act_explanation() -> None:
    """Acceptance-criteria row 4."""
    view = refund_status.get_refund_status_view("RET-2025-00002", 2025)
    assert view.explanation is not None
    assert "PATH Act" in view.explanation


def test_unknown_return_raises_not_found() -> None:
    with pytest.raises(storage.ReturnNotFoundError):
        refund_status.get_refund_status_view("RET-9999-99999", 2025)
