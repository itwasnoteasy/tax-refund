"""Layer 2 -- KVStore's get/set/ttl contract against the real Vercel KV
(Upstash Redis) backend. Makes a genuine network call, unlike
test_unit_kv_store.py -- see docs/spec/07_TESTING_STRATEGY.md.

Skipped entirely when KV credentials aren't present in this
environment, per the task that introduced this test: "a Layer 2
integration test specifically for the KV backend if credentials are
available in this environment."
"""
import os

import pytest

from app.kv_store import KVStore
from tests._kv_contract import assert_kv_contract

_HAS_KV_CREDENTIALS = bool(
    (os.environ.get("KV_REST_API_URL") and os.environ.get("KV_REST_API_TOKEN"))
    or (
        os.environ.get("UPSTASH_REDIS_REST_URL")
        and os.environ.get("UPSTASH_REDIS_REST_TOKEN")
    )
)


@pytest.mark.skipif(
    not _HAS_KV_CREDENTIALS,
    reason=(
        "No Vercel KV / Upstash credentials in this environment -- "
        "see app/kv_store.py's local fallback for why that's expected "
        "outside of a Vercel deployment or a developer's own KV instance."
    ),
)
def test_kv_store_real_backend_satisfies_contract() -> None:
    """Against a real Vercel KV instance, KVStore satisfies the same
    get/set/ttl contract as the local fallback -- confirming the two
    backends are actually interchangeable, not just interface-compatible
    on paper.
    """
    store = KVStore()
    assert store._backend.__class__.__name__ == "_UpstashBackend"
    assert_kv_contract(store)
