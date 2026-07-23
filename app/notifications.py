"""Notification email template rendering -- preview-only, never live-sent.

Built against docs/spec/01_SPEC.md FR-4 and docs/spec/02_TECHNICAL_DESIGN.md,
as committed at eb360db. See docs/spec/06_SCOPE.md for why live send is
out of scope for this PoC -- the same graceful-degradation philosophy
applied to the IRS integration, applied here to the demo itself.

No code path in this module makes an outbound network call to an
actual email-sending service -- it only builds an HTML string in
memory. See DECISIONS.md.
"""
from datetime import datetime

from app import storage
from app.storage import ReturnNotFoundError

_VALID_CHANNELS = {"email", "push"}


def opt_in(return_id: str, channel: str) -> bool:
    """Opt a return in to status-change notifications.

    03_API_CONTRACT.yaml's NotificationOptInRequest takes only
    return_id and channel -- no tax_year, unlike /refund-status. See
    storage.find_tax_return_by_id for the resulting limitation.

    Args:
        return_id: The return's unique identifier.
        channel: "email" or "push".

    Returns:
        True -- the return is opted in for this channel, whether this
        call newly recorded that or it was already the case.

    Raises:
        ValueError: channel isn't a recognized value.
        ReturnNotFoundError: No return with this return_id exists.
    """
    if channel not in _VALID_CHANNELS:
        raise ValueError(f"Unsupported notification channel: {channel!r}")

    tax_return = storage.find_tax_return_by_id(return_id)
    if tax_return is None:
        raise ReturnNotFoundError(f"No return {return_id}.")

    # DECISION: a deterministic event_id (return_id + channel), not a
    # freshly generated one per call. Opting in twice for the same
    # return/channel must not create duplicate state
    # (05_ACCEPTANCE_CRITERIA.md) -- a deterministic key makes that
    # idempotent for free via storage.record_notification_event's
    # existing dedup-by-event_id logic, with no separate "already
    # opted in?" check needed here. See DECISIONS.md.
    event_id = f"optin:{return_id}:{channel}"
    storage.record_notification_event(
        storage.NotificationEvent(
            event_id=event_id,
            return_id=return_id,
            channel=channel,
            opted_in=True,
            created_at=datetime.now(),
        )
    )
    return True


def is_opted_in(return_id: str, channel: str) -> bool:
    """Check whether a return is currently opted in for a channel.

    Test-support / convenience only -- not part of 03_API_CONTRACT.yaml's
    surface, which has no corresponding read endpoint.

    Args:
        return_id: The return's unique identifier.
        channel: "email" or "push".

    Returns:
        True if opt_in() has ever been called for this exact
        (return_id, channel) pair.
    """
    event_id = f"optin:{return_id}:{channel}"
    # DECISION: record_notification_event() is the only mutation storage.py
    # exposes for this data, and it returns False for an already-present
    # event_id -- calling it again here would be a no-op read disguised
    # as a write, so this checks presence directly instead.
    return any(
        event.event_id == event_id for event in storage.get_notification_events()
    )


def render_preview(return_id: str) -> str:
    """Render the notification email HTML that would be sent on a
    status change, for display only -- never sent.

    Args:
        return_id: The return's unique identifier (03_API_CONTRACT.yaml's
            /notifications/preview/{return_id} takes only this).

    Returns:
        A styled HTML string, safe to embed directly in an API response
        for the demo's "email preview" panel.

    Raises:
        ReturnNotFoundError: No return with this return_id exists.
    """
    tax_return = storage.find_tax_return_by_id(return_id)
    if tax_return is None:
        raise ReturnNotFoundError(f"No return {return_id}.")

    status = storage.get_refund_status(return_id, tax_return.tax_year)
    status_label = status.status_code.value if status is not None else "PROCESSING"

    return _render_email_html(tax_return, status_label)


def _render_email_html(tax_return: storage.TaxReturn, status_label: str) -> str:
    """Build the preview email's HTML.

    Deliberately does not reference tax_return.ssn or
    .bank_account_number -- a status-change notification has no reason
    to include either, so they're simply never read here, on top of
    (not instead of) storage.TaxReturn.__repr__'s own redaction.
    """
    return f"""\
<html>
  <body style="font-family: Arial, sans-serif; color: #1a1a1a; background: #f7fafc; padding: 24px;">
    <div style="max-width: 480px; margin: 0 auto; border: 1px solid #e2e8f0; border-radius: 8px; padding: 24px; background: #ffffff;">
      <h2 style="color: #2b6cb0; margin-top: 0;">Your refund status has been updated</h2>
      <p>Return <strong>{tax_return.return_id}</strong> ({tax_return.tax_year}) is now:</p>
      <p style="font-size: 1.25em; font-weight: bold; color: #2d3748;">{status_label}</p>
      <p style="color: #718096; font-size: 0.85em; border-top: 1px solid #e2e8f0; padding-top: 12px;">
        Preview only -- no live send in this PoC. See DECISIONS.md.
      </p>
    </div>
  </body>
</html>
"""
