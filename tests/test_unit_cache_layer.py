"""Layer 1 -- cache_layer.py's store/clear behavior, against the local
KV fallback (no network). See docs/spec/07_TESTING_STRATEGY.md.
"""
from datetime import datetime

import pytest

from app import cache_layer, kv_store, storage

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
    kv_store.reset_local_backend()
    yield
    kv_store.reset_local_backend()


def _sample_data() -> cache_layer.CachedRefundData:
    return cache_layer.CachedRefundData(
        status_code=storage.StatusCode.RECEIVED,
        predicted_window=None,
        explanation="still processing",
        last_checked=datetime(2026, 7, 20, 9, 0, 0),
    )


def test_clear_cache_removes_both_fresh_and_durable_data() -> None:
    cache_layer.store_data(
        _RETURN_ID, _TAX_YEAR, _sample_data(), storage.FilingMethod.EFILE_CURRENT_YEAR
    )
    assert cache_layer.get_fresh_cached_data(_RETURN_ID, _TAX_YEAR) is not None
    assert cache_layer.get_last_known_data(_RETURN_ID, _TAX_YEAR) is not None

    cache_layer.clear_cache(_RETURN_ID, _TAX_YEAR)

    assert cache_layer.get_fresh_cached_data(_RETURN_ID, _TAX_YEAR) is None
    assert cache_layer.get_last_known_data(_RETURN_ID, _TAX_YEAR) is None


def test_clear_cache_on_never_cached_return_is_a_no_op() -> None:
    cache_layer.clear_cache("RET-NEVER-CACHED", 2025)  # must not raise
    assert cache_layer.get_last_known_data("RET-NEVER-CACHED", 2025) is None
