"""Render the daily email by feeding the day's data into templates/email.html."""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import state

LOG = logging.getLogger(__name__)
REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_DIR = REPO_ROOT / "templates"


def _intro_line(match_count: int, is_first_run: bool) -> str:
    if is_first_run:
        if match_count == 0:
            return "First check complete. No live matches in the last fortnight - we'll keep looking from now on."
        if match_count == 1:
            return "First check complete. One role on the table from the last fortnight - have a look."
        return f"First check complete. {match_count} roles from the last fortnight have caught the AI's eye - take a look below."
    if match_count == 0:
        return "No new matches overnight, but the system is still watching."
    if match_count == 1:
        return "One fresh opening landed overnight - have a look."
    return f"{match_count} fresh openings landed overnight - a few look genuinely promising."


def _closing_line(match_count: int) -> str:
    if match_count == 0:
        return "Take the morning back."
    if match_count >= 5:
        return "You've got this."
    return "Have a good day."


def render_email(
    matches: list[dict[str, Any]],
    considered_count: int,
    reached: list[str],
    unreachable: list[str],
    quote: dict[str, Any],
    recipient_name: str,
    recipient_email: str,
    is_first_run: bool,
) -> tuple[str, str]:
    """Returns (subject, html_body)."""
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(["html"]),
    )
    template = env.get_template("email.html")

    # Annotate each match with a stable id we can use in the mailto subject.
    for m in matches:
        m["id"] = state.job_id(m["url"])

    today = datetime.now()
    date_long = today.strftime("%A, %-d %B %Y") if hasattr(today, "strftime") else str(today)

    match_count = len(matches)
    if match_count == 0:
        subject = "No new insurance roles today"
    elif match_count == 1:
        subject = "1 new insurance role today"
    else:
        subject = f"{match_count} new insurance roles today"

    html = template.render(
        subject=subject,
        date_long=date_long,
        recipient_name=recipient_name,
        recipient_email=recipient_email,
        intro_line=_intro_line(match_count, is_first_run),
        closing_line=_closing_line(match_count),
        match_count=match_count,
        checked_count=len(reached) + len(unreachable),
        considered_count=considered_count,
        matches=matches,
        reached_companies=reached,
        unreachable_companies=unreachable,
        quote=quote,
    )
    return subject, html
