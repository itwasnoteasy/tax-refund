"""PII-redacted audit logging.

Built against docs/spec/01_SPEC.md and docs/spec/02_TECHNICAL_DESIGN.md,
as committed at eb360db.

SSN and bank account fields must be redacted in every logged output,
without exception (CLAUDE.md). Two independent layers enforce this:
  1. This module never interpolates storage.TaxReturn.ssn or
     .bank_account_number into a log message -- it builds the message
     from named fields explicitly, never from the object as a whole.
  2. storage.TaxReturn.__repr__ is itself redacted, so even an
     accidental `logger.info(some_tax_return)` elsewhere in the
     codebase can't leak these fields either. See DECISIONS.md.

Writes to both Python's standard `logging` module (so log output is
genuinely grep-testable, per 05_ACCEPTANCE_CRITERIA.md's audit-logging
criterion) and storage.py's in-memory audit log (so entries are
queryable within this process without depending on a real log sink).
"""
import logging
from datetime import datetime

from app import storage

logger = logging.getLogger("app.audit")

_REDACTED = "[REDACTED]"


def log_refund_status_lookup(
    tax_return: storage.TaxReturn, status_code: storage.StatusCode, stale: bool
) -> None:
    """Record an audit entry for one refund-status lookup.

    Args:
        tax_return: The return that was looked up. Only its non-PII
            fields (return_id, tax_year, filing_status) are logged --
            ssn and bank_account_number are explicitly redacted, never
            read from the object into the message.
        status_code: The status served to the caller.
        stale: Whether this was served as a circuit-breaker fallback.
    """
    detail = (
        f"Refund status lookup: return_id={tax_return.return_id} "
        f"tax_year={tax_return.tax_year} "
        f"filing_status={tax_return.filing_status} "
        f"status_code={status_code.value} stale={stale} "
        f"ssn={_REDACTED} bank_account_number={_REDACTED}"
    )
    logger.info(detail)
    storage.append_audit_log(
        storage.AuditLogEntry(
            timestamp=datetime.now(),
            return_id=tax_return.return_id,
            action="refund_status_lookup",
            detail=detail,
        )
    )
