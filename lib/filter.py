"""Pre-AI filtering: date, location, role relevance, hard exclusions.

Four stages, run in order:
  1. by_date           - drop anything older than the lookback window
  2. by_location       - keep only London/Essex (or remote-UK)
  3. by_role_relevance - drop obviously off-topic titles (developer, designer,
                         lawyer etc.) so they never appear in Set Aside
  4. hard_exclusions   - drop anything explicitly requiring experience or
                         senior qualifications (saves AI tokens, removes noise)

Stage order matters: cheap regex filters run before AI scoring.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

LOG = logging.getLogger(__name__)


# --- date parsing ------------------------------------------------------------

_RELATIVE_TODAY = re.compile(r"posted\s+today", re.IGNORECASE)
_RELATIVE_YESTERDAY = re.compile(r"posted\s+yesterday", re.IGNORECASE)
_RELATIVE_DAYS = re.compile(r"posted\s+(\d+)\+?\s+days?\s+ago", re.IGNORECASE)
_RELATIVE_DAYS_30PLUS = re.compile(r"posted\s+30\+?\s+days?\s+ago", re.IGNORECASE)
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def parse_posted_age_days(posted_on: str) -> float | None:
    """Best-effort parse. Returns age in days (float), or None if unknown.

    Handles Workday relative strings ('Posted Today', 'Posted 3 Days Ago',
    'Posted 30+ Days Ago') and ISO timestamps from SmartRecruiters.
    """
    if not posted_on:
        return None
    s = posted_on.strip()
    if _RELATIVE_TODAY.search(s):
        return 0.0
    if _RELATIVE_YESTERDAY.search(s):
        return 1.0
    if _RELATIVE_DAYS_30PLUS.search(s):
        return 30.0
    m = _RELATIVE_DAYS.search(s)
    if m:
        return float(m.group(1))
    m = _ISO_DATE.search(s)
    if m:
        try:
            posted = datetime.fromisoformat(m.group(0)).replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            return max(0.0, (now - posted).total_seconds() / 86400.0)
        except ValueError:
            pass
    return None


def by_date(jobs: list[dict[str, Any]], max_age_days: float) -> list[dict[str, Any]]:
    """Keep jobs posted within max_age_days. Jobs with unknown posted_on are
    kept (better to surface and let the AI judge than silently drop)."""
    out = []
    for j in jobs:
        age = parse_posted_age_days(j.get("posted_on", ""))
        if age is None or age <= max_age_days:
            out.append(j)
    LOG.info("Date filter: kept %d/%d (max age %s days)", len(out), len(jobs), max_age_days)
    return out


# --- location ----------------------------------------------------------------

def by_location(
    jobs: list[dict[str, Any]],
    include: list[str],
    exclude: list[str],
) -> list[dict[str, Any]]:
    """Keep jobs whose location matches an include term and not an exclude term.

    A blank location passes (custom-scrape adaptors sometimes don't have it).
    Match is case-insensitive substring.
    """
    inc_lower = [s.lower() for s in include]
    exc_lower = [s.lower() for s in exclude]
    out = []
    for j in jobs:
        loc = (j.get("location") or "").lower()
        if loc and any(x in loc for x in exc_lower):
            continue
        if not loc or any(x in loc for x in inc_lower):
            out.append(j)
    LOG.info("Location filter: kept %d/%d", len(out), len(jobs))
    return out


# --- role relevance ----------------------------------------------------------

def by_role_relevance(
    jobs: list[dict[str, Any]],
    anti_keywords: list[str],
) -> list[dict[str, Any]]:
    """Drop jobs whose title contains an off-topic anti_keyword.

    This runs before hard_exclusions, so dropped jobs never appear in the
    Set Aside section of the email - they're filtered out silently.

    Match is case-insensitive substring on the title field. We don't look
    at the description here on purpose: a software engineer role is a
    software engineer role regardless of what the description says.
    """
    anti_lower = [s.lower() for s in anti_keywords]
    out: list[dict[str, Any]] = []
    dropped: list[tuple[str, str]] = []
    for j in jobs:
        title = (j.get("title") or "").lower()
        hit = next((kw for kw in anti_lower if kw in title), None)
        if hit:
            dropped.append((j.get("title", ""), hit))
        else:
            out.append(j)
    if dropped:
        LOG.info("Role-relevance filter dropped %d off-topic roles:", len(dropped))
        for t, kw in dropped[:15]:
            LOG.info("  drop %r (matched %r)", t, kw)
    LOG.info("Role-relevance filter: kept %d/%d", len(out), len(jobs))
    return out


# --- hard exclusions ---------------------------------------------------------

def _compile_patterns(patterns: list[str]) -> list[re.Pattern[str]]:
    return [re.compile(p, re.IGNORECASE) for p in patterns]


def hard_exclusions(
    jobs: list[dict[str, Any]],
    experience_patterns: list[str],
    qualification_patterns: list[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Returns (kept, excluded) where excluded carries a 'reason' for the email.

    Match is against title + description concatenated. Title-only matches with
    no description are conservative: we keep them and let the AI judge so we
    don't drop e.g. 'Senior Underwriting Apprentice' (an unlikely but possible
    title) without a closer look.
    """
    exp = _compile_patterns(experience_patterns)
    qual = _compile_patterns(qualification_patterns)
    kept: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for j in jobs:
        text_parts = [j.get("title") or "", j.get("description") or ""]
        text = "\n".join(text_parts)
        if not j.get("description"):
            kept.append(j)  # let AI scorer judge title-only postings
            continue
        reason = None
        for p in exp:
            if p.search(text):
                reason = f"requires experience ({p.pattern})"
                break
        if reason is None:
            for p in qual:
                if p.search(text):
                    reason = f"requires qualification ({p.pattern})"
                    break
        if reason:
            j_with_reason = dict(j)
            j_with_reason["exclusion_reason"] = reason
            excluded.append(j_with_reason)
        else:
            kept.append(j)
    LOG.info("Hard exclusion: kept %d, excluded %d", len(kept), len(excluded))
    return kept, excluded
