"""Cache-aside orchestration for the refund-status core.

Built against docs/spec/01_SPEC.md and docs/spec/02_TECHNICAL_DESIGN.md
§2, as committed at eb360db.

Wires together cache_layer.py (durable value + freshness TTL, backed by
kv_store.py), irs_integration.py (validation + 3-state circuit breaker),
refund_logic.py (explanation/prediction, no LLM), and audit.py
(PII-redacted logging) into the single flow 02_TECHNICAL_DESIGN.md §2
describes:

  1. Fresh cache hit -> return immediately, no IRS call, stale=False.
  2. Cache miss -> attempt irs_integration.fetch_status():
     a. SUCCESS -> build a fresh view, cache it, return stale=False.
     b. RETURN_NOT_FOUND -> propagate as ReturnNotFoundError.
     c. Anything else (CIRCUIT_OPEN, CONNECTION_FAILURE, QUARANTINED,
        RATE_LIMITED, UNKNOWN_STATUS_CODE) -> this attempt did not
        produce a trustworthy fresh answer. Fall back to the durable
        last-known-good cached value, marked stale=True, regardless of
        its own freshness TTL having lapsed -- graceful degradation,
        never a broken page, per CLAUDE.md. If nothing has ever been
        cached either, there is genuinely nothing to serve.
"""
from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional

from app import audit, cache_layer, irs_integration, refund_logic, storage
from app.refund_logic import PredictedWindow
from app.storage import ReturnNotFoundError

_TERMINAL_STATUSES = (storage.StatusCode.SENT, storage.StatusCode.NO_REFUND_PENDING)


class RefundDataUnavailableError(Exception):
    """No fresh IRS data could be obtained, and no cached fallback exists.

    Distinct from ReturnNotFoundError: the return_id/tax_year is real
    (storage.py knows about it) -- there simply isn't yet any validated
    status to show, fresh or stale.
    """


@dataclass(frozen=True)
class RefundStatusView:
    """The fully assembled refund-status response, ready for the (not
    yet built) API layer to serialize -- mirrors
    03_API_CONTRACT.yaml's RefundStatusResponse exactly.
    """

    return_id: str
    tax_year: int
    status_code: storage.StatusCode
    filing_status: str
    expected_refund_amount: Optional[float]
    predicted_window: Optional[PredictedWindow]
    explanation: Optional[str]
    stale: bool
    last_checked: datetime


def get_refund_status_view(return_id: str, tax_year: int) -> RefundStatusView:
    """Fetch a return's status via cache-aside, falling back gracefully.

    Args:
        return_id: The return's unique identifier.
        tax_year: The tax year to look up -- required explicitly per
            FR-1 in 01_SPEC.md ("no single implicit 'latest' return").

    Returns:
        A RefundStatusView, fresh or (if the IRS couldn't be reached
        and a fallback exists) marked stale.

    Raises:
        ReturnNotFoundError: No such return_id/tax_year is known to
            this system at all.
        RefundDataUnavailableError: The IRS couldn't be reached and
            nothing has ever been successfully cached to fall back on.
    """
    tax_return = storage.get_tax_return(return_id, tax_year)
    if tax_return is None:
        raise ReturnNotFoundError(f"No return {return_id}/{tax_year}.")

    fresh_data = cache_layer.get_fresh_cached_data(return_id, tax_year)
    if fresh_data is not None:
        # FAILURE MODE (not a failure -- the happy path): a fresh cache
        # hit within its filing-method TTL. No IRS call is made at all.
        view = _assemble_view(tax_return, fresh_data, stale=False)
        audit.log_refund_status_lookup(tax_return, view.status_code, stale=False)
        return view

    fetch_result = irs_integration.fetch_status(return_id, tax_year)

    if fetch_result.outcome == irs_integration.IRSOutcome.SUCCESS:
        data = _build_cached_data(tax_return, fetch_result.status)
        cache_layer.store_data(return_id, tax_year, data, tax_return.filing_method)
        view = _assemble_view(tax_return, data, stale=False)
        audit.log_refund_status_lookup(tax_return, view.status_code, stale=False)
        return view

    if fetch_result.outcome == irs_integration.IRSOutcome.RETURN_NOT_FOUND:
        # FAILURE MODE: the mock IRS has no record of a return this
        # system's own seed data knows about -- treated the same as a
        # genuinely unknown return, since there's nothing to show either
        # way.
        raise ReturnNotFoundError(f"No IRS record for {return_id}/{tax_year}.")

    # FAILURE MODE: every remaining outcome (CIRCUIT_OPEN,
    # CONNECTION_FAILURE, QUARANTINED, RATE_LIMITED, UNKNOWN_STATUS_CODE)
    # means this attempt did not produce a trustworthy fresh answer. Fall
    # back to the durable last-known-good value regardless of its own
    # freshness TTL having lapsed -- this is the "serve stale cache +
    # last_validated_at" behavior from 02_TECHNICAL_DESIGN.md §2, and
    # applies identically whether the breaker is fully OPEN or this was
    # just one failed attempt short of that threshold.
    last_known_data = cache_layer.get_last_known_data(return_id, tax_year)
    if last_known_data is not None:
        view = _assemble_view(tax_return, last_known_data, stale=True)
        audit.log_refund_status_lookup(tax_return, view.status_code, stale=True)
        return view

    # FAILURE MODE: no fresh data, and nothing has ever been successfully
    # cached for this return either -- genuinely nothing to serve. Not
    # logged to storage's audit log, since no status was actually shown.
    raise RefundDataUnavailableError(
        f"No IRS data available (outcome={fetch_result.outcome.value}) and "
        f"no cached fallback exists for {return_id}/{tax_year}."
    )


def _build_cached_data(
    tax_return: storage.TaxReturn, status: storage.RefundStatus
) -> cache_layer.CachedRefundData:
    """Assemble the cacheable, IRS-derived part of a response.

    Terminal statuses (SENT, NO_REFUND_PENDING) get no prediction and
    no explanation -- there's nothing left to predict or explain.
    Non-terminal statuses get both, computed once here (not
    recomputed on every cache hit), matching 01_SPEC.md's NFR table:
    "Prediction latency ... Precomputed, not generated live."
    """
    if status.status_code in _TERMINAL_STATUSES:
        predicted_window = None
        explanation = None
    else:
        predicted_window = refund_logic.predict_window(
            status.status_code, tax_return.filing_method, date.today()
        )
        explanation = refund_logic.explain_delay(
            {"has_eitc_ctc_flag": tax_return.has_eitc_ctc_flag}
        )

    return cache_layer.CachedRefundData(
        status_code=status.status_code,
        predicted_window=predicted_window,
        explanation=explanation,
        last_checked=datetime.now(),
    )


def _assemble_view(
    tax_return: storage.TaxReturn, data: cache_layer.CachedRefundData, stale: bool
) -> RefundStatusView:
    """Combine cached, IRS-derived data with storage's static filer
    facts into the final response view.
    """
    return RefundStatusView(
        return_id=tax_return.return_id,
        tax_year=tax_return.tax_year,
        status_code=data.status_code,
        filing_status=tax_return.filing_status,
        expected_refund_amount=tax_return.expected_refund_amount,
        predicted_window=data.predicted_window,
        explanation=data.explanation,
        stale=stale,
        last_checked=data.last_checked,
    )
