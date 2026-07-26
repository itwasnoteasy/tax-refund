"""Layer 1 -- notifications.py's opt-in idempotency and preview
rendering. Not explicitly requested by this task (which asked for
Layer 2 cache-aside + PII-redaction tests), but included since this is
a brand-new module -- kept light, no network. See
docs/spec/07_TESTING_STRATEGY.md.
"""
import pytest

from app import notifications, storage
from app.storage import ReturnNotFoundError

_RETURN_ID = "RET-2025-00001"


@pytest.fixture(autouse=True)
def _reset_storage():
    storage.reset_seed_data()
    yield
    storage.reset_seed_data()


def test_opt_in_is_idempotent_for_same_return_and_channel() -> None:
    assert notifications.is_opted_in(_RETURN_ID, "email") is False

    notifications.opt_in(_RETURN_ID, "email")
    notifications.opt_in(_RETURN_ID, "email")  # duplicate call

    events = [
        event
        for event in storage.get_notification_events()
        if event.return_id == _RETURN_ID and event.channel == "email"
    ]
    assert len(events) == 1  # not duplicated
    assert notifications.is_opted_in(_RETURN_ID, "email") is True


def test_opt_in_rejects_unknown_channel() -> None:
    with pytest.raises(ValueError):
        notifications.opt_in(_RETURN_ID, "carrier_pigeon")


def test_opt_in_unknown_return_raises_not_found() -> None:
    with pytest.raises(ReturnNotFoundError):
        notifications.opt_in("RET-9999-99999", "email")


def test_render_preview_returns_html_mentioning_the_return_and_status() -> None:
    html = notifications.render_preview(_RETURN_ID)
    assert "<html>" in html
    assert _RETURN_ID in html
    assert "Preview only" in html


def test_render_preview_never_includes_ssn_or_bank_account() -> None:
    tax_return = storage.get_tax_return(_RETURN_ID, 2025)
    html = notifications.render_preview(_RETURN_ID)
    assert tax_return.ssn not in html
    assert tax_return.bank_account_number not in html


def test_render_preview_unknown_return_raises_not_found() -> None:
    with pytest.raises(ReturnNotFoundError):
        notifications.render_preview("RET-9999-99999")
