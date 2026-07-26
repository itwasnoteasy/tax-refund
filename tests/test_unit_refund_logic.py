"""Layer 1 -- explain_delay() and predict_window(), tested directly with
no network, no other component. See docs/spec/07_TESTING_STRATEGY.md
and docs/spec/05_ACCEPTANCE_CRITERIA.md's Explanation/Prediction Logic
checklists.
"""
from datetime import date

import pytest

from app.refund_logic import (
    PATH_ACT_EXPLANATION_TEXT,
    STILL_PROCESSING_TEXT,
    PredictedWindow,
    explain_delay,
    predict_window,
)
from app.storage import FilingMethod, StatusCode

# --- explain_delay() -----------------------------------------------------


def test_explain_delay_eitc_ctc_flag_true_returns_path_act_text() -> None:
    assert explain_delay({"has_eitc_ctc_flag": True}) == PATH_ACT_EXPLANATION_TEXT


def test_explain_delay_eitc_ctc_flag_false_returns_still_processing_exactly() -> None:
    """05_ACCEPTANCE_CRITERIA.md: assert this string exactly, not just
    "is non-null".
    """
    result = explain_delay({"has_eitc_ctc_flag": False})
    assert result == "still processing"
    assert result == STILL_PROCESSING_TEXT


@pytest.mark.parametrize(
    "return_flags",
    [
        {},
        {"has_eitc_ctc_flag": False},
        {"has_eitc_ctc_flag": None},
        {"has_eitc_ctc_flag": 0},
        {"has_eitc_ctc_flag": ""},
        {"unexpected_flag": True},
        {"has_eitc_ctc_flag": False, "some_other_flag": True},
        {"HAS_EITC_CTC_FLAG": True},  # wrong key casing -- must not match
        {"has_eitc_ctc_flag_typo": True},
    ],
)
def test_explain_delay_no_third_output_path_for_non_flagged_inputs(
    return_flags: dict,
) -> None:
    """05_ACCEPTANCE_CRITERIA.md's hard constraint: no code path in this
    function can return anything other than PATH_ACT_EXPLANATION_TEXT or
    STILL_PROCESSING_TEXT. This test actively tries a batch of inputs
    designed to probe for a third branch (missing key, falsy variants,
    wrong casing, unrelated keys) and confirms every one collapses to
    STILL_PROCESSING_TEXT.
    """
    result = explain_delay(return_flags)
    assert result == STILL_PROCESSING_TEXT
    assert result in {PATH_ACT_EXPLANATION_TEXT, STILL_PROCESSING_TEXT}


@pytest.mark.parametrize(
    "return_flags",
    [
        {"has_eitc_ctc_flag": True},
        {"has_eitc_ctc_flag": 1},
        {"has_eitc_ctc_flag": "yes"},
        {"has_eitc_ctc_flag": True, "some_other_flag": False},
        {"has_eitc_ctc_flag": [1]},  # truthy non-empty list
    ],
)
def test_explain_delay_no_third_output_path_for_truthy_flag_variants(
    return_flags: dict,
) -> None:
    """Same hard constraint, from the other direction: any truthy
    representation of the flag must collapse to PATH_ACT_EXPLANATION_TEXT
    -- never some other, differently-worded string.
    """
    result = explain_delay(return_flags)
    assert result == PATH_ACT_EXPLANATION_TEXT
    assert result in {PATH_ACT_EXPLANATION_TEXT, STILL_PROCESSING_TEXT}


def test_explain_delay_only_two_possible_outputs_exist_at_all() -> None:
    """A direct check on the constraint itself: across every input this
    module's other tests exercise, the total set of distinct outputs
    ever produced has exactly two members.
    """
    exhaustive_inputs = [
        {},
        {"has_eitc_ctc_flag": True},
        {"has_eitc_ctc_flag": False},
        {"has_eitc_ctc_flag": None},
        {"has_eitc_ctc_flag": 1},
        {"has_eitc_ctc_flag": 0},
        {"unrelated": "value"},
    ]
    outputs = {explain_delay(flags) for flags in exhaustive_inputs}
    assert outputs == {PATH_ACT_EXPLANATION_TEXT, STILL_PROCESSING_TEXT}


# --- predict_window() ----------------------------------------------------


def test_predict_window_returns_a_range_never_a_single_date() -> None:
    window = predict_window(StatusCode.RECEIVED, FilingMethod.EFILE_CURRENT_YEAR, date(2026, 7, 23))
    assert isinstance(window, PredictedWindow)
    assert window.start_date < window.end_date


def test_predict_window_has_start_end_and_confidence() -> None:
    window = predict_window(StatusCode.APPROVED, FilingMethod.EFILE_CURRENT_YEAR, date(2026, 7, 23))
    assert isinstance(window.start_date, date)
    assert isinstance(window.end_date, date)
    assert 0.0 <= window.confidence_level <= 1.0


def test_predict_window_is_relative_to_the_given_as_of_date() -> None:
    as_of = date(2026, 1, 1)
    window = predict_window(StatusCode.RECEIVED, FilingMethod.EFILE_CURRENT_YEAR, as_of)
    assert window.start_date > as_of
    assert window.end_date > window.start_date


def test_predict_window_confidence_varies_by_filing_method() -> None:
    as_of = date(2026, 7, 23)
    efile_window = predict_window(StatusCode.RECEIVED, FilingMethod.EFILE_CURRENT_YEAR, as_of)
    paper_window = predict_window(StatusCode.RECEIVED, FilingMethod.PAPER, as_of)
    assert efile_window.confidence_level != paper_window.confidence_level


@pytest.mark.parametrize(
    "terminal_status",
    [StatusCode.SENT, StatusCode.NO_REFUND_PENDING],
)
def test_predict_window_raises_for_terminal_status(terminal_status: StatusCode) -> None:
    """FR-2: a window only applies "when a refund has not yet been
    sent" -- calling this for a terminal status is a contract violation
    the caller (refund_status.py) is expected never to trigger.
    """
    with pytest.raises(ValueError):
        predict_window(terminal_status, FilingMethod.EFILE_CURRENT_YEAR, date(2026, 7, 23))
