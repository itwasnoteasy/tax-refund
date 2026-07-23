"""Vercel KV wrapper (get/set/ttl) -- not a plain Python dict.

Built against docs/spec/01_SPEC.md and docs/spec/02_TECHNICAL_DESIGN.md
(distilled spec set, per CLAUDE.md's provenance convention -- see the
Open Question resolution in docs/spec/06_SCOPE.md for why there's no
deeper versioned reference than that).

This module is the one place the cache, the circuit breaker's state,
and the mock IRS mode toggle are read/written, because those three
specifically must survive across what may be separate serverless
invocations (see CLAUDE.md's Vercel section and
02_TECHNICAL_DESIGN.md §1). Everything else in this system's core data
(seed returns, predictions) deliberately does NOT go through this
module -- see storage.py.

The interface (get/set/ttl) is Redis-shaped by design, matching the
choice recorded in DECISIONS.md ("Cache: in-memory TTL dict, not real
Redis" / "State persistence: Vercel KV, not in-memory dict") -- values
are always strings, mirroring Redis semantics; callers are responsible
for serializing structured data (e.g. JSON) before calling set().
"""
import math
import os
import time
from typing import Dict, Optional, Tuple

# DECISION: env var names checked, in priority order. KV_REST_API_URL /
# KV_REST_API_TOKEN are what Vercel's own KV integration injects into the
# function's environment. UPSTASH_REDIS_REST_URL / UPSTASH_REDIS_REST_TOKEN
# are checked as a fallback for local development against a real Upstash
# database (e.g. via `vercel env pull` using different names, or a
# developer-owned Upstash instance used purely for local testing).
# See DECISIONS.md for the full reasoning.
_KV_URL_ENV_VARS = ("KV_REST_API_URL", "UPSTASH_REDIS_REST_URL")
_KV_TOKEN_ENV_VARS = ("KV_REST_API_TOKEN", "UPSTASH_REDIS_REST_TOKEN")

# DECISION: module-level, not a _LocalBackend instance attribute. The real
# backend is safe to reconstruct freely because the state lives in the
# remote KV service, not in the client object -- callers (e.g. mock_irs.py)
# rely on being able to construct a fresh KVStore() per call the same way.
# A per-instance dict here would silently break that: two separately
# constructed KVStores would each see an empty store, contradicting this
# class's own "single-process dict" premise. See DECISIONS.md.
_LOCAL_STORE: Dict[str, Tuple[str, Optional[float]]] = {}


def _first_env(names: Tuple[str, ...]) -> Optional[str]:
    """Return the value of the first set environment variable in `names`.

    Args:
        names: Environment variable names to check, in priority order.

    Returns:
        The first non-empty value found, or None if none are set.
    """
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return None


class _LocalBackend:
    """In-memory fallback backend -- single-process dict, no persistence.

    # FAILURE MODE: this backend exists so local development doesn't
    # require a live KV instance for basic iteration. It does NOT have
    # the cross-invocation persistence that is the entire reason
    # cache/breaker/demo-mode state was moved to Vercel KV in the first
    # place (see CLAUDE.md) -- state here is lost the moment the process
    # exits, and is never shared between separate processes or serverless
    # instances. It must never be relied on once deployed; KVStore only
    # selects this backend when no KV credentials are present in the
    # environment (see KVStore.__init__), which should never be true on
    # Vercel itself.
    """

    def __init__(self) -> None:
        self._data = _LOCAL_STORE

    def get(self, key: str) -> Optional[str]:
        """Fetch a value by key, honoring TTL expiry.

        Args:
            key: The key to look up.

        Returns:
            The stored value, or None if the key is missing or expired.
        """
        entry = self._data.get(key)
        if entry is None:
            return None
        value, expires_at = entry
        if expires_at is not None and time.monotonic() >= expires_at:
            del self._data[key]
            return None
        return value

    def set(self, key: str, value: str, ttl_seconds: Optional[int] = None) -> None:
        """Upsert a key's value, optionally with a TTL.

        Args:
            key: The key to write.
            value: The string value to store.
            ttl_seconds: Seconds until expiry, or None for no expiry.
        """
        expires_at = (
            time.monotonic() + ttl_seconds if ttl_seconds is not None else None
        )
        self._data[key] = (value, expires_at)

    def ttl(self, key: str) -> Optional[int]:
        """Return remaining seconds until a key expires.

        Args:
            key: The key to check.

        Returns:
            Remaining whole seconds, or None if the key doesn't exist,
            has already expired, or was set with no TTL.
        """
        entry = self._data.get(key)
        if entry is None:
            return None
        _, expires_at = entry
        if expires_at is None:
            return None
        remaining = expires_at - time.monotonic()
        if remaining <= 0:
            del self._data[key]
            return None
        # Round up rather than truncate: a key set 0.3s ago with a 1s TTL
        # has ~0.7s left, which should read as "1 second remaining," not
        # "0" -- truncating would make a still-valid key look expired.
        return math.ceil(remaining)


class _UpstashBackend:
    """Real Vercel KV backend, via the Upstash Redis REST client.

    Vercel KV is Upstash Redis under the hood and exposes REST-based
    credentials rather than a raw TCP connection string (see
    DECISIONS.md's "Vercel KV client" entry) -- this backend talks to
    it over HTTPS, which is what makes it safe to construct fresh on
    every serverless invocation without connection-pooling concerns.
    """

    def __init__(self, url: str, token: str) -> None:
        # Deferred import: upstash-redis is only required when real KV
        # credentials are present, so local-only development never needs
        # the package installed.
        from upstash_redis import Redis

        self._client = Redis(url=url, token=token)

    def get(self, key: str) -> Optional[str]:
        """Fetch a value by key from Vercel KV.

        Args:
            key: The key to look up.

        Returns:
            The stored value, or None if the key is missing or expired.
        """
        value = self._client.get(key)
        return None if value is None else str(value)

    def set(self, key: str, value: str, ttl_seconds: Optional[int] = None) -> None:
        """Upsert a key's value in Vercel KV, optionally with a TTL.

        Args:
            key: The key to write.
            value: The string value to store.
            ttl_seconds: Seconds until expiry, or None for no expiry.
        """
        if ttl_seconds is not None:
            self._client.set(key, value, ex=ttl_seconds)
        else:
            self._client.set(key, value)

    def ttl(self, key: str) -> Optional[int]:
        """Return remaining seconds until a key expires in Vercel KV.

        Args:
            key: The key to check.

        Returns:
            Remaining whole seconds, or None if the key doesn't exist
            or was set with no TTL.
        """
        remaining = self._client.ttl(key)
        # FAILURE MODE: Redis's TTL command uses -1 ("no expiry set") and
        # -2 ("key doesn't exist") as sentinel return values rather than
        # raising. Normalizing both to None gives callers a single "no
        # TTL" signal instead of two magic negative integers to remember.
        if remaining is None or remaining < 0:
            return None
        return int(remaining)


class KVStore:
    """Redis-shaped get/set/ttl interface, backed by Vercel KV.

    Automatically falls back to an in-memory backend (see
    `_LocalBackend`) when KV credentials aren't present in the
    environment, so local development and this test suite's Layer 1
    tests don't require a live KV instance. That fallback does not
    persist across process restarts or separate invocations -- see
    `_LocalBackend`'s docstring for why that matters here specifically.
    """

    def __init__(self) -> None:
        """Select a backend based on the presence of KV credentials."""
        url = _first_env(_KV_URL_ENV_VARS)
        token = _first_env(_KV_TOKEN_ENV_VARS)

        if url and token:
            self._backend = _UpstashBackend(url, token)
        else:
            # DECISION: fall back to an in-memory backend rather than
            # raising, so `import app.kv_store` and basic local
            # iteration work with zero setup. See DECISIONS.md.
            self._backend = _LocalBackend()

    def get(self, key: str) -> Optional[str]:
        """Fetch a value by key.

        Args:
            key: The key to look up.

        Returns:
            The stored value, or None if the key is missing or expired.
        """
        return self._backend.get(key)

    def set(self, key: str, value: str, ttl_seconds: Optional[int] = None) -> None:
        """Upsert a key's value, optionally with a TTL.

        Args:
            key: The key to write.
            value: The string value to store.
            ttl_seconds: Seconds until expiry, or None for no expiry.
        """
        self._backend.set(key, value, ttl_seconds)

    def ttl(self, key: str) -> Optional[int]:
        """Return remaining seconds until a key expires.

        Args:
            key: The key to check.

        Returns:
            Remaining whole seconds, or None if the key doesn't exist,
            has already expired, or was set with no TTL.
        """
        return self._backend.ttl(key)


def reset_local_backend() -> None:
    """Clear the shared local in-memory fallback store.

    Test-support only -- lets tests that exercise the local backend
    (directly, or indirectly via a caller like mock_irs.py) start from
    a known empty state without restarting the process. Has no effect
    on the real Upstash backend, and is never called from application
    code.
    """
    _LOCAL_STORE.clear()
