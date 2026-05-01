"""Send the daily email via Resend.

Uses Resend's default sender (onboarding@resend.dev) since we're on the
free tier without a verified domain. Lucy will need to whitelist this once
to keep it out of spam - the README explains how.
"""
from __future__ import annotations

import logging
import os

LOG = logging.getLogger(__name__)

DEFAULT_FROM = "Lucy's job tracker <onboarding@resend.dev>"


def send(
    subject: str,
    html: str,
    to_email: str,
    to_name: str,
    from_address: str = DEFAULT_FROM,
) -> dict:
    """Send the email. Returns Resend's response dict; raises on failure."""
    import resend
    api_key = os.environ.get("RESEND_API_KEY")
    if not api_key:
        raise RuntimeError("RESEND_API_KEY not set")
    resend.api_key = api_key

    resp = resend.Emails.send({
        "from": from_address,
        "to": [to_email],
        "subject": subject,
        "html": html,
        "reply_to": [os.environ.get("RECIPIENT_EMAIL", to_email)],
    })
    LOG.info("Resend send OK: id=%s to=%s subject=%s", resp.get("id"), to_email, subject)
    return resp
