"""Layer 1 -- KVStore's get/set/ttl contract against the local fallback
backend. No network, no live KV instance, no other component involved
-- see docs/spec/07_TESTING_STRATEGY.md.
"""
import pytest

from app import kv_store
from app.kv_store import KVStore
from tests._kv_contract import assert_kv_contract

_KV_ENV_VARS = (
    "KV_REST_API_URL",
    "KV_REST_API_TOKEN",
    "UPSTASH_REDIS_REST_URL",
    "UPSTASH_REDIS_REST_TOKEN",
)


@pytest.fixture(autouse=True)
def _clear_kv_env_and_local_backend(monkeypatch: pytest.MonkeyPatch):
    for name in _KV_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    kv_store.reset_local_backend()
    yield
    kv_store.reset_local_backend()


def test_kv_store_local_backend_satisfies_contract() -> None:
    """With no KV credentials in the environment, KVStore falls back to
    the local in-memory backend and still satisfies the full get/set/ttl
    contract.
    """
    store = KVStore()
    assert store._backend.__class__.__name__ == "_LocalBackend"
    assert_kv_contract(store)


def test_local_backend_is_shared_across_separately_constructed_stores() -> None:
    """A caller (e.g. mock_irs.py) that constructs a fresh KVStore() per
    call -- exactly how a serverless invocation would use it -- must
    still see state written by a different KVStore() instance. A
    per-instance local dict would silently break this even though
    nothing here talks to real KV.
    """
    key = "cross-instance-test-key"
    KVStore().set(key, "written-by-first-instance")

    assert KVStore().get(key) == "written-by-first-instance"
