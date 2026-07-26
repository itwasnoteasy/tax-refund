"""Delay explanation and ETA prediction -- rule-based, no LLM.

Built against docs/spec/01_SPEC.md §3.3 and docs/spec/02_TECHNICAL_DESIGN.md
§2/§4, as committed at eb360db. Unchanged by the Vercel deployment or the
mock IRS's 8-mode expansion -- see 02_TECHNICAL_DESIGN.md §4: "Nothing
about the Vercel deployment or the new mock IRS states changes this
reasoning."

explain_delay() is the single most load-bearing behavioral contract in
this system: it must never fabricate a plausible-sounding reason for a
delay. Only one cause is currently inferable from known return data
(EITC/CTC -> PATH Act); every other case says exactly "still
processing," never a guess. No LLM anywhere in this file -- see
CLAUDE.md's non-negotiable behaviors and 02_TECHNICAL_DESIGN.md §4 for
the full defense of that choice.

predict_window() is a rule-based stand-in for the real survival-
analysis model described in the full system design -- see DECISIONS.md
for the honest gap this leaves.
"""
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Dict, Tuple

from app.storage import FilingMethod, StatusCode

# DECISION: exact wording of the PATH Act explanation is my own
# construction, not copied from the actual interview presentation slides
# (which aren't in this repo -- see DECISIONS.md). The one behavioral
# requirement that IS load-bearing is that this exact string, and only
# this string or STILL_PROCESSING_TEXT, is ever returned -- see
# test_unit_refund_logic.py's "no third output path" test.
PATH_ACT_EXPLANATION_TEXT = (
    "Your refund includes the Earned Income Tax Credit and/or the "
    "Additional Child Tax Credit. By law, the IRS cannot issue these "
    "refunds before mid-February, even if you filed early. This is a "
    "standard hold under the PATH Act, not a problem with your return."
)

# 01_SPEC.md §3.3: "do not attempt to infer or generate any other
# explanation" -- this exact string, verbatim, for every other
# still-processing case.
STILL_PROCESSING_TEXT = "still processing"


def explain_delay(return_flags: dict) -> str:
    """Explain a still-processing return's delay, or say there's none known.

    # DECISION: rule-based template lookup, not an LLM call. The
    # reason-set for delay explanations is small, known, and
    # deterministic (currently: exactly one inferable case, the PATH Act
    # hold) -- a template is simpler, fully auditable, and has zero
    # hallucination risk, which matters more here than almost anywhere
    # else in this system: the entire design philosophy rests on never
    # presenting a guess as a known fact. See DECISIONS.md.

    Only ever called for a return whose status is still processing
    (non-terminal) -- the caller (refund_status.py, a later phase)
    decides whether an explanation applies at all before invoking this;
    this function has exactly two possible outputs and no notion of
    "not applicable."

    Args:
        return_flags: Known flags about the return. Currently inspects
            only "has_eitc_ctc_flag" -- any other keys are ignored, and
            a missing or falsy "has_eitc_ctc_flag" is treated the same
            as an explicit False.

    Returns:
        PATH_ACT_EXPLANATION_TEXT if has_eitc_ctc_flag is truthy;
        STILL_PROCESSING_TEXT otherwise. No other string is ever
        returned -- see test_unit_refund_logic.py's exhaustive-input test.
    """
    if return_flags.get("has_eitc_ctc_flag"):
        # FAILURE MODE (not a failure, but the one branch point in this
        # function): this is the only known, inferable cause -- every
        # other combination of flags, however constructed, falls through
        # to the branch below rather than attempting to infer anything
        # else. See 01_SPEC.md §3.3.
        return PATH_ACT_EXPLANATION_TEXT
    return STILL_PROCESSING_TEXT


@dataclass(frozen=True)
class PredictedWindow:
    """A predicted refund delivery window -- always a range.

    Deliberately not storage.RefundPrediction: that dataclass also
    carries return_id/tax_year identity, which this pure function has
    no need to know about. The caller (refund_status.py, a later phase)
    is responsible for combining this with a return's identity into a
    storage.RefundPrediction if/when one needs to be persisted.
    """

    start_date: date
    end_date: date
    confidence_level: float


# DECISION: rule-based stub, not a trained survival-analysis model --
# clearly labeled here and in DECISIONS.md. Demonstrates the
# architectural pattern (a status-tiered window, a filing-method-tiered
# confidence score) without requiring real historical outcome data.
_WINDOW_DAYS_BY_STATUS: Dict[StatusCode, Tuple[int, int]] = {
    StatusCode.RECEIVED: (21, 35),
    StatusCode.APPROVED: (7, 14),
}

_CONFIDENCE_BY_FILING_METHOD: Dict[FilingMethod, float] = {
    FilingMethod.EFILE_CURRENT_YEAR: 0.85,
    FilingMethod.EFILE_PRIOR_YEAR: 0.75,
    FilingMethod.TRANSITIONAL: 0.6,
    FilingMethod.PAPER: 0.5,
}


def predict_window(
    status_code: StatusCode, filing_method: FilingMethod, as_of: date
) -> PredictedWindow:
    """Predict a refund delivery window for a non-terminal return.

    Args:
        status_code: Must be RECEIVED or APPROVED -- FR-2 in 01_SPEC.md
            only calls for a predicted window "when a refund has not
            yet been sent." Terminal statuses (SENT, NO_REFUND_PENDING)
            have no window to predict; the caller is expected not to
            call this function for those, mirroring explain_delay()'s
            contract.
        filing_method: Drives the confidence score's tier.
        as_of: The date to compute the window relative to -- passed in
            explicitly (never datetime.now() read internally) so this
            function stays a pure, deterministic, easily testable rule.

    Returns:
        A PredictedWindow -- always a start/end range, per FR-2, never
        a single point-estimate date.

    Raises:
        ValueError: status_code is not RECEIVED or APPROVED.
    """
    if status_code not in _WINDOW_DAYS_BY_STATUS:
        raise ValueError(
            f"predict_window is only defined for a non-terminal status "
            f"(RECEIVED or APPROVED); got {status_code}."
        )

    min_days, max_days = _WINDOW_DAYS_BY_STATUS[status_code]
    confidence_level = _CONFIDENCE_BY_FILING_METHOD.get(filing_method, 0.5)

    return PredictedWindow(
        start_date=as_of + timedelta(days=min_days),
        end_date=as_of + timedelta(days=max_days),
        confidence_level=confidence_level,
    )
