"""Demo-only customer directory, backing the two-screen demo split.

Built to support separating the single combined demo page into a
customer-facing screen (Check Refund Status, notifications, Tax Help
Assistant) and an admin/demo-control screen (mock IRS mode, circuit
breaker indicator, force-cache-miss, and -- new here -- which demo
customer is "logged in"). Neither screen is part of
docs/spec/03_API_CONTRACT.yaml; this module is the same category of
demo scaffolding as mock_irs.py's mode toggle, not a new core entity.

# DECISION: a separate DEMO_USERS directory, not a `name` field bolted
# onto storage.TaxReturn. TaxReturn is the real entity 03_API_CONTRACT.yaml
# and 02_TECHNICAL_DESIGN.md's entity model are built around; "which
# named person is currently driving the demo" is a presentation-layer
# concept this PoC's admin screen needs, not a fact the refund-status
# core has any use for. Keeping it here (like mock_irs.py's mode, not
# in storage.py) keeps that boundary visible in the file tree, not just
# in prose -- see 04_CODING_STANDARDS.md §7. See DECISIONS.md.

The active user is one **global** selection, stored in Vercel KV (not
per-browser-tab): this demo runs as a single shared instance during a
live walkthrough, so "the admin tab picks the user, every customer tab
reflects it" is a deliberate, disclosed trade-off, not an oversight --
see DECISIONS.md. It would not extend to a real multi-user product,
where each customer would authenticate to their own return -- also
already flagged as an open gap in docs/spec/06_SCOPE.md's "Users and
roles" note.
"""
from dataclasses import dataclass
from typing import Dict, List

from app import storage
from app.kv_store import KVStore

_ACTIVE_USER_KEY = "demo:active_user_id"


@dataclass(frozen=True)
class DemoUser:
    """A named demo customer, mapped to one of storage.py's seed returns.

    Attributes:
        user_id: Stable key used by the admin screen's dropdown and the
            /demo/set-active-user request body.
        name: First name shown on the customer screen's "Welcome, X"
            greeting.
        demo_label: Longer, scenario-identifying label shown only in
            the admin screen's dropdown -- so the demo operator can
            tell users apart by scenario, not just by name.
        return_id: The seeded TaxReturn this user's "own return" is.
        tax_year: The tax year of that return.
    """

    user_id: str
    name: str
    demo_label: str
    return_id: str
    tax_year: int


# DECISION: one demo user per existing seed return (storage.py's five),
# rather than inventing new seed data. Reuses the exact scenarios
# 05_ACCEPTANCE_CRITERIA.md and DEMO_WALKTHROUGH_SCRIPT.md already
# cover -- a demo user is just a friendly name/greeting layered on top
# of a return that already exists and is already tested. See DECISIONS.md.
DEMO_USERS: Dict[str, DemoUser] = {
    "user1": DemoUser(
        user_id="user1",
        name="John",
        demo_label="John — Normal e-file (in progress)",
        return_id="RET-2025-00001",
        tax_year=2025,
    ),
    "user2": DemoUser(
        user_id="user2",
        name="Maria",
        demo_label="Maria — EITC/CTC (PATH Act hold)",
        return_id="RET-2025-00002",
        tax_year=2025,
    ),
    "user3": DemoUser(
        user_id="user3",
        name="Robert",
        demo_label="Robert — Balance due (no refund)",
        return_id="RET-2025-00003",
        tax_year=2025,
    ),
    "user4": DemoUser(
        user_id="user4",
        name="Susan",
        demo_label="Susan — Paper-filed",
        return_id="RET-2025-00004",
        tax_year=2025,
    ),
    "user5": DemoUser(
        user_id="user5",
        name="David",
        demo_label="David — Refund sent",
        return_id="RET-2025-00005",
        tax_year=2025,
    ),
    "user6": DemoUser(
        user_id="user6",
        name="Karen",
        demo_label="Karen — Approved, past the predicted window",
        return_id="RET-2025-00006",
        tax_year=2025,
    ),
}

DEFAULT_USER_ID = "user1"


class UnknownDemoUserError(Exception):
    """A user_id that isn't a key in DEMO_USERS -- surfaced as a 400 by
    the API layer, the same shape as InvalidTaxYearError.
    """


def list_demo_users() -> List[DemoUser]:
    """Return every demo user, for the admin screen's dropdown.

    Returns:
        All DemoUser entries, in DEMO_USERS' declared order.
    """
    return list(DEMO_USERS.values())


def get_active_user_id() -> str:
    """Get the currently active demo user's id.

    Read from Vercel KV, not local memory -- the admin screen's
    set_active_user_id() call and a customer screen's subsequent
    "Check Refund Status" click may run in genuinely different
    serverless invocations, the same cross-invocation concern
    mock_irs.py's mode toggle already has to account for.

    Returns:
        The active user_id, or DEFAULT_USER_ID if none has been set yet
        (e.g. a fresh KV, or the admin screen was never opened).
    """
    raw = KVStore().get(_ACTIVE_USER_KEY)
    return raw if raw is not None and raw in DEMO_USERS else DEFAULT_USER_ID


def set_active_user_id(user_id: str) -> None:
    """Set the currently active demo user.

    Args:
        user_id: Must be a key in DEMO_USERS.

    Raises:
        UnknownDemoUserError: user_id isn't a recognized demo user.
    """
    if user_id not in DEMO_USERS:
        raise UnknownDemoUserError(f"No demo user '{user_id}'.")
    KVStore().set(_ACTIVE_USER_KEY, user_id)


def filing_description(tax_return: storage.TaxReturn) -> str:
    """A short, human-readable description of how a return was filed.

    Derived live from the real TaxReturn (filing_method + whether a
    bank account is on file for direct deposit) rather than stored as
    a second, hand-written copy of the same fact -- so this can never
    drift from storage.py's actual seed data if it's ever changed.

    Args:
        tax_return: The return to describe.

    Returns:
        A phrase like "e-file with direct deposit", suitable for
        "Your {filing_description} was accepted on ..." on the
        customer screen.
    """
    if tax_return.filing_method == storage.FilingMethod.PAPER:
        return "paper-filed return"
    if tax_return.bank_account_number is not None:
        return "e-file with direct deposit"
    return "e-file with a paper check"
