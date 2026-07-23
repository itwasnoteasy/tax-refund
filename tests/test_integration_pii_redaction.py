"""Layer 2 -- the PII-redaction guarantee, exercised across audit.py +
storage.py + refund_status.py together (a real log sink via pytest's
caplog, not just an in-memory string check). See
docs/spec/05_ACCEPTANCE_CRITERIA.md's audit-logging criterion:
"Grep-testing the entire log output for SSN-like patterns or bank
account numbers returns zero matches, even under a test that
deliberately includes such data in a fixture."
"""
import logging
import re

import pytest

from app import audit, kv_store, mock_irs, refund_status, storage

_SSN_PATTERN = re.compile(r"\d{3}-\d{2}-\d{4}")

_KV_ENV_VARS = (
    "KV_REST_API_URL",
    "KV_REST_API_TOKEN",
    "UPSTASH_REDIS_REST_URL",
    "UPSTASH_REDIS_REST_TOKEN",
)


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch: pytest.MonkeyPatch):
    for name in _KV_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    storage.reset_seed_data()
    kv_store.reset_local_backend()
    mock_irs.set_mode(mock_irs.IRSMode.NORMAL)
    yield
    kv_store.reset_local_backend()


def _fixture_return_with_pii() -> storage.TaxReturn:
    """A fixture deliberately carrying realistic-looking SSN/bank data,
    per the acceptance criterion's own wording -- distinct from the
    seeded returns, so this test's fixture data is visibly its own,
    not accidentally reusing (and thus depending on) storage.py's seed
    values.
    """
    return storage.TaxReturn(
        return_id="RET-2025-99999",
        tax_year=2025,
        filing_status="single",
        filing_method=storage.FilingMethod.EFILE_CURRENT_YEAR,
        expected_refund_amount=2500.00,
        has_eitc_ctc_flag=False,
        ssn="987-65-4321",
        bank_account_number="000998877665",
    )


def test_audit_log_output_never_contains_raw_ssn_or_bank_account(
    caplog: pytest.LogCaptureFixture,
) -> None:
    fixture_return = _fixture_return_with_pii()

    with caplog.at_level(logging.INFO, logger="app.audit"):
        audit.log_refund_status_lookup(
            fixture_return, storage.StatusCode.RECEIVED, stale=False
        )

    assert _SSN_PATTERN.search(caplog.text) is None
    assert fixture_return.ssn not in caplog.text
    assert fixture_return.bank_account_number not in caplog.text
    assert "[REDACTED]" in caplog.text  # the log line exists and says so


def test_tax_return_repr_never_contains_raw_ssn_or_bank_account() -> None:
    """Defense in depth: even if something logs the object directly
    (`logger.info(tax_return)`) rather than going through audit.py's
    careful message-building, __repr__ itself must not leak these
    fields -- see the DECISIONS.md entry for storage.TaxReturn.__repr__.
    """
    fixture_return = _fixture_return_with_pii()

    rendered = repr(fixture_return)

    assert _SSN_PATTERN.search(rendered) is None
    assert fixture_return.ssn not in rendered
    assert fixture_return.bank_account_number not in rendered
    assert str(fixture_return) == rendered  # dataclasses' __str__ delegates to __repr__


def test_full_refund_status_lookup_produces_no_pii_in_log_output(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """End-to-end through refund_status.py's real orchestration (not
    audit.py called directly) -- exercises the seeded returns, which
    themselves carry realistic-looking SSN/bank data (see
    storage.py's _seed()).
    """
    with caplog.at_level(logging.INFO):
        for return_id in (
            "RET-2025-00001",
            "RET-2025-00002",
            "RET-2025-00003",
            "RET-2025-00004",
            "RET-2025-00005",
        ):
            refund_status.get_refund_status_view(return_id, 2025)

    assert _SSN_PATTERN.search(caplog.text) is None

    all_seeded_returns = [
        storage.get_tax_return(return_id, 2025)
        for return_id in (
            "RET-2025-00001",
            "RET-2025-00002",
            "RET-2025-00003",
            "RET-2025-00004",
            "RET-2025-00005",
        )
    ]
    for tax_return in all_seeded_returns:
        assert tax_return.ssn not in caplog.text
        if tax_return.bank_account_number is not None:
            assert tax_return.bank_account_number not in caplog.text


def test_audit_log_entries_are_still_recorded_despite_redaction() -> None:
    """Redaction must not come at the cost of losing the audit trail
    entirely -- 05_ACCEPTANCE_CRITERIA.md requires an entry per lookup,
    just a PII-free one.
    """
    storage.reset_seed_data()  # clears any audit entries from other tests too

    refund_status.get_refund_status_view("RET-2025-00001", 2025)

    log = storage.get_audit_log()
    assert len(log) == 1
    assert log[0].return_id == "RET-2025-00001"
    assert log[0].action == "refund_status_lookup"
    assert "123-45-6789" not in log[0].detail  # this seed return's real SSN value
