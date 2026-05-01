"""AI relevance scoring via the Anthropic API.

Each surviving job (after location and hard-exclusion filters) is shown to
Claude with the target roles and Lucy's previously rejected jobs. Claude
returns one of three verdicts plus a one-line reason that we surface in the
email:

  loves_this    - Strong match. Card highlighted in pink.
  worth_a_look  - Possible match. Card highlighted in peach.
  skip          - Not a match. Dropped silently.

We batch up to 10 jobs per API call to keep cost down. At our volume this
is one-to-three calls per run; cost is fractions of a penny per day.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

LOG = logging.getLogger(__name__)

MODEL = "claude-sonnet-4-5"
MAX_BATCH = 10
SYSTEM_PROMPT = """You are helping Lucy, a school-leaver in the UK, find her first job in INSURANCE specifically. She is not looking for tech, design, marketing, communications, legal, finance, HR, or any other function - even at insurance companies.

She lives near London/Essex. She has no prior work experience.

WHAT COUNTS AS A MATCH

A role is a match only if BOTH of these are true:

1. The role is in insurance work itself - underwriting, broking, claims, insurance operations, insurance account handling, risk, reinsurance. Roles supporting or trainee-ing into those functions. Things that put her on a path to becoming an insurance professional.

2. The role is genuinely entry-level - apprenticeship, trainee, graduate scheme open to school-leavers, junior, assistant, or any role explicitly stating no prior experience required.

THE TEST: would someone two years into this role be doing insurance work day to day? If yes, it's relevant. If they'd be writing code, designing interfaces, running marketing campaigns, doing legal review, or handling HR - it is NOT a match, regardless of which company posted it.

Examples of MATCHES (return loves_this or worth_a_look):
  - Junior Insurance Broker
  - Junior Commercial Insurance Broker
  - Underwriting Apprentice
  - Underwriting Assistant
  - Claims Trainee
  - Claims Handler (entry-level)
  - Account Handler
  - Insurance Operations Trainee
  - Trainee Broker
  - Graduate Insurance Programme (school-leaver friendly)
  - Junior Risk Analyst (insurance context)

Examples of NON-MATCHES (return skip):
  - Full Stack Engineer / Software Developer / Data Scientist (any tech)
  - UI/UX Designer / Product Designer (any design)
  - Communications Agent / Communications Officer / PR
  - Marketing Manager / Brand Manager / Social Media
  - HR Business Partner / Talent / Recruiter
  - Paralegal / Solicitor / Legal Counsel
  - Accountant / Finance Manager / Auditor
  - Customer Service Agent (unless explicitly insurance-focused with clear path into broking/claims)
  - Sales Development Rep (generic, not insurance-broking)
  - Senior Underwriter / Lead Account Manager (any role requiring 1+ years experience)
  - Anything titled "Senior", "Manager", "Lead", "Head of" or "Director"

Target role types Lucy explicitly listed: {target_roles}
Broader categories that also count: {fuzzy_categories}

VERDICTS

  loves_this    - Clear match. Insurance role, clearly entry-level, location works. The title closely matches a target role or fuzzy category.
  worth_a_look  - Plausible match worth a closer read. Insurance-adjacent but borderline on experience or title. Or insurance role where the entry-level signal is unclear from the description.
  skip          - Not a match. Wrong function (tech, design, marketing, etc.), or insurance but too senior, or location doesn't work.

Default to skip if you're not confident. Lucy's morning is better spent on three real matches than ten vague ones.

CONTEXT FROM PREVIOUS REJECTIONS

These are jobs Lucy has previously marked as not a match:
{rejected_examples}

If a new posting strongly resembles any of those, lean toward skip.

OUTPUT

Return only valid JSON, no preamble. Format:
{{"results": [{{"index": 0, "verdict": "loves_this", "reason": "one line explaining why"}}, ...]}}
"""


def _client():
    """Lazy import so the module loads even before anthropic is installed."""
    import anthropic
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set")
    return anthropic.Anthropic(api_key=api_key)


def _format_rejected(rejected_jobs: list[dict[str, Any]], limit: int = 8) -> str:
    """Show the most recent rejections to Claude so it learns the pattern."""
    if not rejected_jobs:
        return "  (none yet)"
    recent = rejected_jobs[-limit:]
    lines = []
    for r in recent:
        title = r.get("title", "?")
        company = r.get("company", "?")
        reason = r.get("reason", "no reason given")
        lines.append(f"  - {title} at {company} (rejected because: {reason})")
    return "\n".join(lines)


def _format_jobs_for_prompt(jobs: list[dict[str, Any]]) -> str:
    """Compact representation - title, location, first 600 chars of description."""
    parts = []
    for i, j in enumerate(jobs):
        desc = (j.get("description") or "").strip()
        if len(desc) > 600:
            desc = desc[:600] + "..."
        parts.append(
            f"[{i}] {j.get('title', '?')} at {j.get('company', '?')} "
            f"({j.get('location', 'location unknown')})\n{desc}"
        )
    return "\n\n---\n\n".join(parts)


def _parse_response(text: str, batch_size: int) -> list[dict[str, Any]]:
    """Parse the model's JSON. Be defensive - models occasionally wrap JSON
    in prose despite the prompt. Fall back to all-skip if we can't parse."""
    try:
        # Try to find a JSON block
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1:
            raise ValueError("no JSON in response")
        data = json.loads(text[start : end + 1])
        results = data.get("results", [])
        if not isinstance(results, list):
            raise ValueError("results not a list")
        return results
    except (json.JSONDecodeError, ValueError) as e:
        LOG.warning("Failed to parse scorer response: %s. Defaulting all to skip.", e)
        return [{"index": i, "verdict": "skip", "reason": "scoring parse error"}
                for i in range(batch_size)]


def score_batch(
    jobs: list[dict[str, Any]],
    target_roles: list[str],
    fuzzy_categories: list[str],
    rejected_jobs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Score one batch (up to MAX_BATCH jobs). Returns the input list with
    'verdict' and 'verdict_reason' fields added to each job."""
    if not jobs:
        return []
    client = _client()
    system = SYSTEM_PROMPT.format(
        target_roles=", ".join(target_roles),
        fuzzy_categories=", ".join(fuzzy_categories),
        rejected_examples=_format_rejected(rejected_jobs),
    )
    user_message = f"Score these {len(jobs)} postings:\n\n{_format_jobs_for_prompt(jobs)}"
    try:
        resp = client.messages.create(
            model=MODEL,
            max_tokens=1500,
            system=system,
            messages=[{"role": "user", "content": user_message}],
        )
        text = resp.content[0].text
    except Exception as e:  # noqa: BLE001
        LOG.exception("Anthropic call failed: %s", e)
        # Fail closed: mark all as skip. The AI is now the sole judge of
        # relevance, so a transient API failure must not flood Lucy with
        # un-judged postings (the regex blacklists used to catch the worst
        # of these, but they've been removed). Better to send a quiet email
        # for one day than to send 50 irrelevant tech roles.
        for j in jobs:
            j["verdict"] = "skip"
            j["verdict_reason"] = "AI scoring unavailable, dropped this run"
        return jobs

    parsed = _parse_response(text, len(jobs))
    by_index = {r.get("index"): r for r in parsed if isinstance(r, dict)}
    out = []
    for i, j in enumerate(jobs):
        result = by_index.get(i, {"verdict": "skip", "reason": "no result returned"})
        verdict = result.get("verdict", "skip")
        if verdict not in ("loves_this", "worth_a_look", "skip"):
            verdict = "skip"
        j["verdict"] = verdict
        j["verdict_reason"] = result.get("reason", "")
        out.append(j)
    return out


def score_all(
    jobs: list[dict[str, Any]],
    target_roles: list[str],
    fuzzy_categories: list[str],
    rejected_jobs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Score every job, batching for efficiency."""
    out: list[dict[str, Any]] = []
    for i in range(0, len(jobs), MAX_BATCH):
        batch = jobs[i : i + MAX_BATCH]
        out.extend(score_batch(batch, target_roles, fuzzy_categories, rejected_jobs))
    LOG.info(
        "Scored %d jobs: %d loves, %d worth, %d skip",
        len(out),
        sum(1 for j in out if j.get("verdict") == "loves_this"),
        sum(1 for j in out if j.get("verdict") == "worth_a_look"),
        sum(1 for j in out if j.get("verdict") == "skip"),
    )
    return out
