"""Shared get/set/ttl behavioral contract for KVStore.

Not a test module itself (no test_/​_test filename match, so pytest
won't collect it) -- imported explicitly by test_unit_kv_store.py
(Layer 1, local backend) and test_integration_kv_store.py (Layer 2,
real Vercel KV backend) so both backends are held to identical
semantics, per 07_TESTING_STRATEGY.md's Layer 2 note that contract
compliance is the highest-value kind of test in that layer.
"""
import time

from app.kv_store import KVStore


def assert_kv_contract(store: KVStore) -> None:
    """Assert a KVStore instance satisfies the full get/set/ttl contract.

    Args:
        store: A KVStore backed by either the local or the real KV
            backend -- this function makes no assumption about which.
    """
    missing_key = f"test-missing-{time.monotonic_ns()}"
    assert store.get(missing_key) is None
    assert store.ttl(missing_key) is None

    key = f"test-key-{time.monotonic_ns()}"
    store.set(key, "hello")
    assert store.get(key) == "hello"
    assert store.ttl(key) is None  # no TTL was set

    store.set(key, "updated")  # set() is upsert, not insert-only
    assert store.get(key) == "updated"

    ttl_key = f"test-ttl-{time.monotonic_ns()}"
    store.set(ttl_key, "expiring", ttl_seconds=1)
    remaining = store.ttl(ttl_key)
    assert remaining is not None and 0 < remaining <= 1
    assert store.get(ttl_key) == "expiring"

    time.sleep(1.2)
    assert store.get(ttl_key) is None
    assert store.ttl(ttl_key) is None
