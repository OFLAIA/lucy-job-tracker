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
SYSTEM_PROMPT = """You are helping Lucy, a school-leaver in the UK, find her first insurance job.

She is looking for genuinely entry-level roles - apprenticeships, trainee schemes, junior assistants - where no prior work experience is required. She lives near London/Essex.

Target role types: {target_roles}
Fuzzy categories that also count: {fuzzy_categories}

For each job posting you receive, return a verdict:
  loves_this    - Clear match. Title fits a target role, no experience required, location works.
  worth_a_look  - Plausible match worth a quick read. Borderline experience requirement, slightly off-target title, or unclear from the description.
  skip          - Not a match. Wrong role type, requires experience/qualifications, wrong location, or doesn't fit the spirit of what Lucy is looking for.

Be honest, not generous - skipping a marginal role is better than wasting Lucy's morning on a mismatch. Default to 'skip' if you're unsure.

Important context: these jobs Lucy has previously rejected as not matches:
{rejected_examples}

If a new posting strongly resembles any of those, lean toward 'skip' or at most 'worth_a_look'.

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
        # Fail open: mark all as worth_a_look so Lucy still sees them and
        # can judge for herself. Better than silently dropping a real match.
        for j in jobs:
            j["verdict"] = "worth_a_look"
            j["verdict_reason"] = "AI scoring unavailable, please review manually"
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
