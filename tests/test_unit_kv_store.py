"""Layer 1 -- KVStore's get/set/ttl contract against the local fallback
backend. No network, no live KV instance, no other component involved
-- see docs/spec/07_TESTING_STRATEGY.md.
"""
import pytest

from app.kv_store import KVStore
from tests._kv_contract import assert_kv_contract

_KV_ENV_VARS = (
    "KV_REST_API_URL",
    "KV_REST_API_TOKEN",
    "UPSTASH_REDIS_REST_URL",
    "UPSTASH_REDIS_REST_TOKEN",
)


def test_kv_store_local_backend_satisfies_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no KV credentials in the environment, KVStore falls back to
    the local in-memory backend and still satisfies the full get/set/ttl
    contract.
    """
    for name in _KV_ENV_VARS:
        monkeypatch.delenv(name, raising=False)

    store = KVStore()
    assert store._backend.__class__.__name__ == "_LocalBackend"
    assert_kv_contract(store)
