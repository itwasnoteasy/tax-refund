"""TTL-by-filing-method cache logic, backed by kv_store.py.

Built against docs/spec/02_TECHNICAL_DESIGN.md §2, as committed at
eb360db (see refund_logic.py's provenance note for why that's the
reference used instead of a version number).

Stores two things per (return_id, tax_year), not one:
  - A durable "last known good" value, with no TTL -- never expires on
    its own, so it's still there to serve as a stale fallback when the
    circuit breaker is open, no matter how long ago it was written.
  - A "freshness" marker, with the real filing-method TTL from
    02_TECHNICAL_DESIGN.md §2's table -- its mere presence means "don't
    bother refreshing yet."

# DECISION: two keys, not one. A single KV entry can't both expire (to
# trigger a refresh attempt) and durably persist (to serve as a stale
# fallback once IRS is unreachable) -- those are two different
# lifetimes for the same underlying data. Splitting them is what makes
# "cache-aside with graceful degradation" actually work past the TTL
# boundary, rather than the cached value vanishing entirely the moment
# it goes stale, which would leave nothing to fall back on. See
# DECISIONS.md.
"""
import json
from dataclasses import dataclass
from datetime import date, datetime
from typing import Dict, Optional

from app.kv_store import KVStore
from app.refund_logic import PredictedWindow
from app.storage import FilingMethod, StatusCode

# DECISION: midpoints of 02_TECHNICAL_DESIGN.md §2's TTL ranges. PAPER's
# "weeks" is given the longest tier -- once a paper return does get a
# status, it changes rarely enough that refreshing daily would be
# wasted effort. See DECISIONS.md.
_TTL_SECONDS_BY_FILING_METHOD: Dict[FilingMethod, int] = {
    FilingMethod.EFILE_CURRENT_YEAR: 22 * 3600,  # ~20-24h
    FilingMethod.EFILE_PRIOR_YEAR: 3 * 24 * 3600,  # ~3 days
    FilingMethod.TRANSITIONAL: 3 * 3600,  # 2-4h
    FilingMethod.PAPER: 7 * 24 * 3600,  # "weeks"
}

_DATA_KEY_PREFIX = "cache:data"
_FRESH_KEY_PREFIX = "cache:fresh"


@dataclass(frozen=True)
class CachedRefundData:
    """The IRS-derived part of a refund status response.

    Deliberately excludes filing_status/expected_refund_amount -- those
    come straight from storage.py's TaxReturn, which is already a
    zero-latency in-memory lookup with nothing to cache. Only what
    actually required a validated IRS round trip (or this system's own
    derived explanation/prediction) is cached here.
    """

    status_code: StatusCode
    predicted_window: Optional[PredictedWindow]
    explanation: Optional[str]
    last_checked: datetime


def ttl_seconds_for_filing_method(filing_method: FilingMethod) -> int:
    """Look up the cache TTL for a filing method.

    Args:
        filing_method: The return's filing method.

    Returns:
        TTL in seconds, per 02_TECHNICAL_DESIGN.md §2's table.
    """
    return _TTL_SECONDS_BY_FILING_METHOD[filing_method]


def get_fresh_cached_data(
    return_id: str, tax_year: int
) -> Optional[CachedRefundData]:
    """Return cached data only if it's still within its freshness TTL.

    Args:
        return_id: The return's unique identifier.
        tax_year: The tax year to look up.

    Returns:
        The cached data if a fast cache hit applies; None if the
        freshness window has lapsed (a refresh should be attempted) or
        nothing has ever been cached.
    """
    kv = KVStore()
    if kv.get(_fresh_key(return_id, tax_year)) is None:
        # FAILURE MODE (not a failure -- ordinary cache-miss path): no
        # unexpired freshness marker, so the caller should attempt an
        # IRS refresh rather than serve this as a fast hit.
        return None
    return get_last_known_data(return_id, tax_year)


def get_last_known_data(return_id: str, tax_year: int) -> Optional[CachedRefundData]:
    """Return the durable last-known-good cached data, ignoring freshness.

    Used both by get_fresh_cached_data() above (once freshness is
    confirmed) and directly by refund_status.py's stale-fallback path,
    where data past its TTL is exactly what needs to be served.

    Args:
        return_id: The return's unique identifier.
        tax_year: The tax year to look up.

    Returns:
        The cached data, or None if nothing has ever been cached for
        this return/tax year.
    """
    raw = KVStore().get(_data_key(return_id, tax_year))
    return None if raw is None else _deserialize(raw)


def store_data(
    return_id: str,
    tax_year: int,
    data: CachedRefundData,
    filing_method: FilingMethod,
) -> None:
    """Durably store cached data and (re)start its freshness TTL.

    Args:
        return_id: The return's unique identifier.
        tax_year: The tax year this data belongs to.
        data: The freshly validated data to store.
        filing_method: Selects the freshness TTL to apply.
    """
    kv = KVStore()
    # No ttl_seconds here: this copy is durable on purpose, so it's
    # still retrievable as a stale fallback long after the freshness
    # marker below has expired -- see this module's docstring.
    kv.set(_data_key(return_id, tax_year), _serialize(data))
    ttl = ttl_seconds_for_filing_method(filing_method)
    kv.set(_fresh_key(return_id, tax_year), "1", ttl_seconds=ttl)


def _data_key(return_id: str, tax_year: int) -> str:
    return f"{_DATA_KEY_PREFIX}:{return_id}:{tax_year}"


def _fresh_key(return_id: str, tax_year: int) -> str:
    return f"{_FRESH_KEY_PREFIX}:{return_id}:{tax_year}"


def _serialize(data: CachedRefundData) -> str:
    predicted_window = None
    if data.predicted_window is not None:
        predicted_window = {
            "start_date": data.predicted_window.start_date.isoformat(),
            "end_date": data.predicted_window.end_date.isoformat(),
            "confidence_level": data.predicted_window.confidence_level,
        }
    payload = {
        "status_code": data.status_code.value,
        "predicted_window": predicted_window,
        "explanation": data.explanation,
        "last_checked": data.last_checked.isoformat(),
    }
    return json.dumps(payload)


def _deserialize(raw: str) -> CachedRefundData:
    payload = json.loads(raw)
    predicted_window = None
    if payload["predicted_window"] is not None:
        window_payload = payload["predicted_window"]
        predicted_window = PredictedWindow(
            start_date=date.fromisoformat(window_payload["start_date"]),
            end_date=date.fromisoformat(window_payload["end_date"]),
            confidence_level=window_payload["confidence_level"],
        )
    return CachedRefundData(
        status_code=StatusCode(payload["status_code"]),
        predicted_window=predicted_window,
        explanation=payload["explanation"],
        last_checked=datetime.fromisoformat(payload["last_checked"]),
    )
