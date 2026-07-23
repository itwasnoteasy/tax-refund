"""Layer 1 -- storage.py's get/set (upsert) behavior and idempotency
guarantees, exercised directly against the module. No network, no
other component involved -- see docs/spec/07_TESTING_STRATEGY.md.
"""
from datetime import datetime

import pytest

from app import storage


@pytest.fixture(autouse=True)
def _reset_storage():
    """Each test starts from, and leaves behind, the same known seed
    state -- explicit here rather than in conftest.py, per
    04_CODING_STANDARDS.md's preference for fixtures a reader can
    understand from the test file alone.
    """
    storage.reset_seed_data()
    yield
    storage.reset_seed_data()


def test_get_tax_return_returns_seeded_return() -> None:
    tax_return = storage.get_tax_return("RET-2025-00001", 2025)
    assert tax_return is not None
    assert tax_return.filing_status == "single"
    assert tax_return.has_eitc_ctc_flag is False


def test_get_tax_return_unknown_return_id_returns_none() -> None:
    assert storage.get_tax_return("RET-9999-99999", 2025) is None


def test_get_tax_return_same_return_id_wrong_tax_year_returns_none() -> None:
    """FR-1: the year must be explicit -- a (return_id, tax_year) pair
    not in storage is never implicitly resolved to some other year's
    data for the same return_id.
    """
    assert storage.get_tax_return("RET-2025-00001", 2024) is None


def test_get_refund_status_returns_seeded_status() -> None:
    status = storage.get_refund_status("RET-2025-00002", 2025)
    assert status is not None
    assert status.status_code == storage.StatusCode.APPROVED


def test_get_refund_status_unknown_return_returns_none() -> None:
    assert storage.get_refund_status("RET-9999-99999", 2025) is None


def test_upsert_refund_status_replaces_not_duplicates() -> None:
    """CLAUDE.md's idempotency requirement: upsert by return_id, not
    insert -- reprocessing a status update replaces the existing
    record rather than creating a second one alongside it.
    """
    return_id, tax_year = "RET-2025-00001", 2025

    original = storage.get_refund_status(return_id, tax_year)
    assert original is not None
    assert original.status_code == storage.StatusCode.RECEIVED

    storage.upsert_refund_status(
        storage.RefundStatus(
            return_id=return_id,
            tax_year=tax_year,
            status_code=storage.StatusCode.APPROVED,
            status_last_updated_at=datetime(2026, 7, 23, 8, 0, 0),
        )
    )

    updated = storage.get_refund_status(return_id, tax_year)
    assert updated is not None
    assert updated.status_code == storage.StatusCode.APPROVED
    assert updated.status_last_updated_at == datetime(2026, 7, 23, 8, 0, 0)


def test_record_notification_event_is_idempotent_by_event_id() -> None:
    event = storage.NotificationEvent(
        event_id="evt-001",
        return_id="RET-2025-00001",
        channel="email",
        opted_in=True,
        created_at=datetime(2026, 7, 23, 8, 0, 0),
    )

    first_result = storage.record_notification_event(event)
    second_result = storage.record_notification_event(event)

    assert first_result is True
    assert second_result is False  # duplicate event_id must not re-fire


def test_append_and_get_audit_log_round_trip() -> None:
    assert storage.get_audit_log() == []

    entry = storage.AuditLogEntry(
        timestamp=datetime(2026, 7, 23, 8, 0, 0),
        return_id="RET-2025-00001",
        action="refund_status_lookup",
        detail="Status checked for return RET-2025-00001",
    )
    storage.append_audit_log(entry)

    assert storage.get_audit_log() == [entry]


def test_get_audit_log_returns_defensive_copy() -> None:
    storage.append_audit_log(
        storage.AuditLogEntry(
            timestamp=datetime(2026, 7, 23, 8, 0, 0),
            return_id="RET-2025-00001",
            action="refund_status_lookup",
            detail="Status checked",
        )
    )

    log_copy = storage.get_audit_log()
    log_copy.clear()

    assert len(storage.get_audit_log()) == 1


def test_seed_data_covers_all_three_seed_returns() -> None:
    for return_id in ("RET-2025-00001", "RET-2025-00002", "RET-2025-00003"):
        assert storage.get_tax_return(return_id, 2025) is not None
        assert storage.get_refund_status(return_id, 2025) is not None


def test_balance_due_return_has_no_refund_pending_status_and_no_amount() -> None:
    """Acceptance-criteria row 6: a distinct, explicit shape -- not a
    null/empty version of the normal response.
    """
    tax_return = storage.get_tax_return("RET-2025-00003", 2025)
    status = storage.get_refund_status("RET-2025-00003", 2025)
    assert tax_return is not None and status is not None
    assert tax_return.expected_refund_amount is None
    assert status.status_code == storage.StatusCode.NO_REFUND_PENDING
