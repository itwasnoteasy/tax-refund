"""In-memory entity dataclasses and seed data for the refund-status core.

Built against docs/spec/01_SPEC.md and docs/spec/02_TECHNICAL_DESIGN.md.
No `turbotax_entity_model.mermaid` exists in this repo to define these
fields concretely (see the resolved Open Question in
docs/spec/06_SCOPE.md) -- every field below is derived directly from
the functional requirements and technical design in those two files,
not copied from an entity diagram. See DECISIONS.md for the specific
derivation notes.

Read-heavy, in-memory per CLAUDE.md's Stack section: this data is
seeded once and, aside from the upsert/dedup operations idempotency
requires, not mutated by user action -- so it doesn't need Vercel KV's
cross-invocation guarantees the way cache/breaker/demo-mode state does.
See kv_store.py for the state that does need them.

`ModelCalibrationRecord` is deliberately not implemented here -- see
docs/spec/06_SCOPE.md's scope table.
"""
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Dict, List, Optional, Tuple


class StatusCode(str, Enum):
    """Mirrors RefundStatusResponse.status_code's enum in
    docs/spec/03_API_CONTRACT.yaml exactly, so no translation step is
    needed when building the API response later.
    """

    RECEIVED = "RECEIVED"
    APPROVED = "APPROVED"
    SENT = "SENT"
    NO_REFUND_PENDING = "NO_REFUND_PENDING"


class FilingMethod(str, Enum):
    """Drives cache TTL selection (cache_layer.py) per the table in
    02_TECHNICAL_DESIGN.md §2. Internal only -- not part of the public
    API contract (contrast with StatusCode above, which is).
    """

    EFILE_CURRENT_YEAR = "efile_current_year"
    EFILE_PRIOR_YEAR = "efile_prior_year"
    PAPER = "paper"
    TRANSITIONAL = "transitional"


@dataclass(frozen=True)
class TaxReturn:
    """Static, filer-level facts about a single tax return.

    Attributes:
        return_id: Unique identifier for the return.
        tax_year: The tax year this return covers.
        filing_status: Tax filing status (e.g. "married_filing_jointly").
            Free-form per 03_API_CONTRACT.yaml, which declares this
            field as a plain string with an example, not an enum --
            deliberately not constrained to a Python Enum here either,
            to avoid inventing a value set the API contract doesn't
            actually declare.
        filing_method: Drives cache TTL selection; internal only, not
            exposed in the API response.
        expected_refund_amount: None when no refund is pending (e.g. a
            balance-due return) -- see FR-5 in 01_SPEC.md.
        has_eitc_ctc_flag: The one flag explain_delay() (refund_logic.py)
            is allowed to infer a delay reason from -- see 01_SPEC.md §3.3.
        ssn: Deliberately realistic-looking fake data, never a real SSN,
            so audit.py's redaction logic has a genuine target to prove
            against -- see 05_ACCEPTANCE_CRITERIA.md's audit-log
            grep-test requirement.
        bank_account_number: Same reasoning as `ssn`, for the
            direct-deposit destination; None for a return with no
            refund pending.
    """

    return_id: str
    tax_year: int
    filing_status: str
    filing_method: FilingMethod
    expected_refund_amount: Optional[float]
    has_eitc_ctc_flag: bool
    # DECISION: fake but realistic-looking PII, not omitted entirely --
    # see DECISIONS.md for why this module needs a genuine redaction
    # target rather than an already-safe placeholder.
    ssn: str
    bank_account_number: Optional[str]

    def __repr__(self) -> str:
        # DECISION: a custom __repr__ that redacts ssn/bank_account_number,
        # rather than relying on every caller to remember never to log this
        # object directly. CLAUDE.md's redaction rule is "without
        # exception" -- audit.py's own careful message-building is the
        # primary defense, but this is a second, structural one: even an
        # accidental `logger.info(some_tax_return)` anywhere in the
        # codebase, now or in the future, can't leak these two fields.
        # See DECISIONS.md.
        return (
            f"TaxReturn(return_id={self.return_id!r}, tax_year={self.tax_year!r}, "
            f"filing_status={self.filing_status!r}, "
            f"filing_method={self.filing_method!r}, "
            f"expected_refund_amount={self.expected_refund_amount!r}, "
            f"has_eitc_ctc_flag={self.has_eitc_ctc_flag!r}, "
            f"ssn='[REDACTED]', bank_account_number='[REDACTED]')"
        )


@dataclass(frozen=True)
class RefundStatus:
    """The IRS-reported status fact for a return, as last known.

    Distinct from a cache's `last_checked`/staleness timestamp -- that's
    this system's own freshness bookkeeping (computed later in
    cache_layer.py/refund_status.py). `status_last_updated_at` here is
    when the (mock) IRS itself last updated this status.

    Attributes:
        return_id: The return this status belongs to.
        tax_year: The tax year this status belongs to.
        status_code: Current status in the Received -> Approved -> Sent
            pipeline, or an exception state (NO_REFUND_PENDING).
        status_last_updated_at: When the IRS itself last updated this
            status (ground truth, not cache freshness).
    """

    return_id: str
    tax_year: int
    status_code: StatusCode
    status_last_updated_at: datetime


@dataclass(frozen=True)
class RefundPrediction:
    """A predicted delivery window for a return.

    Always a range (start_date/end_date), per FR-2 in 01_SPEC.md --
    never a single point-estimate date.

    No seed instances of this entity exist: predictions are produced by
    predict_window() (refund_logic.py, not yet implemented) rather than
    being ground-truth seed facts the way TaxReturn/RefundStatus are.
    """

    return_id: str
    tax_year: int
    start_date: date
    end_date: date
    confidence_level: float


@dataclass(frozen=True)
class NotificationEvent:
    """A single notification opt-in/delivery event.

    `event_id` is the idempotency key: processing the same event twice
    must not double-fire a notification -- see 01_SPEC.md §3.4. No seed
    instances exist; these are created when a user opts in
    (notifications.py, not yet implemented).

    Attributes:
        event_id: Idempotency key for delivery deduplication.
        return_id: The return this notification concerns.
        channel: Delivery channel -- matches
            NotificationOptInRequest.channel's enum ("email" | "push")
            in 03_API_CONTRACT.yaml.
        opted_in: Whether the user is currently opted in.
        created_at: When this event was recorded.
    """

    event_id: str
    return_id: str
    channel: str
    opted_in: bool
    created_at: datetime


class ReturnNotFoundError(Exception):
    """No return_id/tax_year known to this system.

    Defined here (not in each caller) so refund_status.py and
    notifications.py raise the same exception for the same underlying
    condition, rather than duplicating near-identical exception classes.
    storage.py's own lookup functions still return None on a miss (their
    established, non-raising contract, unchanged) -- callers that decide
    absence is an error raise this themselves.
    """


@dataclass(frozen=True)
class AuditLogEntry:
    """A single, already-redacted audit log entry.

    `detail` must never contain a raw SSN or bank account number --
    audit.py (not yet implemented) is responsible for redacting before
    constructing this entry; this module does not redact anything
    itself. No seed instances exist; these are created per-request as
    the system runs.
    """

    timestamp: datetime
    return_id: str
    action: str
    detail: str


# --- In-memory storage --------------------------------------------------
#
# DECISION: keyed by (return_id, tax_year) tuples throughout, not by
# return_id alone. FR-1 in 01_SPEC.md is explicit that "there is no
# single implicit 'latest' return; the year must be explicit whenever
# more than one exists," so tax_year is treated as part of every
# entity's identity rather than assuming return_id alone is unique
# across years. See DECISIONS.md.

_TAX_RETURNS: Dict[Tuple[str, int], TaxReturn] = {}
_REFUND_STATUSES: Dict[Tuple[str, int], RefundStatus] = {}
_NOTIFICATION_EVENTS: Dict[str, NotificationEvent] = {}
_AUDIT_LOG: List[AuditLogEntry] = []


def get_tax_return(return_id: str, tax_year: int) -> Optional[TaxReturn]:
    """Fetch the seeded TaxReturn for a given return/tax year.

    Args:
        return_id: The return's unique identifier.
        tax_year: The tax year to look up.

    Returns:
        The matching TaxReturn, or None if no such return/year exists.
    """
    return _TAX_RETURNS.get((return_id, tax_year))


def find_tax_return_by_id(return_id: str) -> Optional[TaxReturn]:
    """Find a TaxReturn by return_id alone, without a tax_year.

    # DECISION: needed because 03_API_CONTRACT.yaml's notification
    # endpoints (opt-in, preview) take only return_id -- unlike
    # /refund-status, which requires tax_year explicitly per FR-1's "the
    # year must be explicit" rule. Safe for this PoC's seed data, where
    # each return_id happens to be unique across tax years; would be
    # genuinely ambiguous (which year?) for a return_id that existed
    # under more than one, a gap inherited from the API contract itself,
    # not invented here. See DECISIONS.md.

    Args:
        return_id: The return's unique identifier.

    Returns:
        The first matching TaxReturn found, or None if none exists.
    """
    for tax_return in _TAX_RETURNS.values():
        if tax_return.return_id == return_id:
            return tax_return
    return None


def get_refund_status(return_id: str, tax_year: int) -> Optional[RefundStatus]:
    """Fetch the current known RefundStatus for a given return/tax year.

    Args:
        return_id: The return's unique identifier.
        tax_year: The tax year to look up.

    Returns:
        The matching RefundStatus, or None if none is recorded.
    """
    return _REFUND_STATUSES.get((return_id, tax_year))


def upsert_refund_status(status: RefundStatus) -> None:
    """Idempotently record a return's current status.

    Args:
        status: The status to store, replacing any existing record for
            the same (return_id, tax_year).
    """
    # DECISION: upsert by (return_id, tax_year), never insert -- so
    # reprocessing the same (mock) IRS response twice replaces the
    # existing record instead of creating a duplicate. See CLAUDE.md's
    # idempotency requirement and DECISIONS.md.
    _REFUND_STATUSES[(status.return_id, status.tax_year)] = status


def record_notification_event(event: NotificationEvent) -> bool:
    """Idempotently record a notification event, deduped by event_id.

    Args:
        event: The event to record.

    Returns:
        True if this event_id was newly recorded; False if it was
        already present, meaning the caller must not re-fire the
        notification -- see 01_SPEC.md §3.4.
    """
    if event.event_id in _NOTIFICATION_EVENTS:
        return False
    _NOTIFICATION_EVENTS[event.event_id] = event
    return True


def get_notification_events() -> List[NotificationEvent]:
    """Return a defensive copy of all recorded notification events.

    Returns:
        A new list; mutating it does not affect stored state.
    """
    return list(_NOTIFICATION_EVENTS.values())


def append_audit_log(entry: AuditLogEntry) -> None:
    """Append an already-redacted entry to the in-memory audit log.

    Args:
        entry: The entry to append. Callers are responsible for
            redacting SSN/bank fields before constructing it -- see
            AuditLogEntry's docstring.
    """
    _AUDIT_LOG.append(entry)


def get_audit_log() -> List[AuditLogEntry]:
    """Return a defensive copy of the audit log recorded so far.

    Returns:
        A new list; mutating it does not affect the stored log.
    """
    return list(_AUDIT_LOG)


def reset_seed_data() -> None:
    """Reset in-memory storage to its initial seed state.

    Test-support only -- lets each test start from a known, isolated
    state without restarting the process. Never called from
    application code.
    """
    _TAX_RETURNS.clear()
    _REFUND_STATUSES.clear()
    _NOTIFICATION_EVENTS.clear()
    _AUDIT_LOG.clear()
    _seed()


def _seed() -> None:
    """Populate the three seed returns covering all six
    05_ACCEPTANCE_CRITERIA.md demo scenarios.

    Three distinct returns are enough: rows 1, 2, 3, and 5 all exercise
    the same normal, unflagged return -- cache hit/miss and IRS-failure
    behavior are properties of the demo control panel and cache state,
    not of the seed data itself, and "still processing" is this
    return's correct, non-fabricated explanation. Row 4 needs the
    EITC/CTC-flagged return; row 6 needs the balance-due return. See
    DECISIONS.md for the full mapping and why a "SENT" terminal-state
    return and a paper-filed return (present in the demo walkthrough's
    narration but not in the 6-row acceptance table) aren't seeded here.
    """
    normal_return = TaxReturn(
        return_id="RET-2025-00001",
        tax_year=2025,
        filing_status="single",
        filing_method=FilingMethod.EFILE_CURRENT_YEAR,
        expected_refund_amount=1834.00,
        has_eitc_ctc_flag=False,
        ssn="123-45-6789",
        bank_account_number="000123456789",
    )
    eitc_ctc_return = TaxReturn(
        return_id="RET-2025-00002",
        tax_year=2025,
        filing_status="head_of_household",
        filing_method=FilingMethod.EFILE_CURRENT_YEAR,
        expected_refund_amount=4210.00,
        has_eitc_ctc_flag=True,
        ssn="234-56-7890",
        bank_account_number="000234567890",
    )
    balance_due_return = TaxReturn(
        return_id="RET-2025-00003",
        tax_year=2025,
        filing_status="married_filing_jointly",
        filing_method=FilingMethod.EFILE_CURRENT_YEAR,
        expected_refund_amount=None,
        has_eitc_ctc_flag=False,
        ssn="345-67-8901",
        bank_account_number=None,
    )

    for tax_return in (normal_return, eitc_ctc_return, balance_due_return):
        _TAX_RETURNS[(tax_return.return_id, tax_return.tax_year)] = tax_return

    upsert_refund_status(
        RefundStatus(
            return_id=normal_return.return_id,
            tax_year=normal_return.tax_year,
            status_code=StatusCode.RECEIVED,
            status_last_updated_at=datetime(2026, 7, 20, 9, 0, 0),
        )
    )
    upsert_refund_status(
        RefundStatus(
            return_id=eitc_ctc_return.return_id,
            tax_year=eitc_ctc_return.tax_year,
            status_code=StatusCode.APPROVED,
            status_last_updated_at=datetime(2026, 7, 21, 14, 30, 0),
        )
    )
    upsert_refund_status(
        RefundStatus(
            return_id=balance_due_return.return_id,
            tax_year=balance_due_return.tax_year,
            status_code=StatusCode.NO_REFUND_PENDING,
            status_last_updated_at=datetime(2026, 7, 18, 10, 0, 0),
        )
    )


_seed()
