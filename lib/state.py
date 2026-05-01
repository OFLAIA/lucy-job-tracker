"""Persistent state for the job tracker.

Two files live in /state/:
  seen_jobs.json   - jobs already shown to Lucy (so we don't re-alert)
  feedback.json    - rejected jobs, loved jobs, quote thumbs, learned tone weights

In production these are committed back to the GitHub repo on each run, so
state survives across runs even though GitHub Actions is stateless.
"""
from __future__ import annotations

import json
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = REPO_ROOT / "state"
SEEN_PATH = STATE_DIR / "seen_jobs.json"
FEEDBACK_PATH = STATE_DIR / "feedback.json"


def _load(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _save(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False, sort_keys=False)


def job_id(url: str) -> str:
    """Stable id for a job, derived from its URL.

    Companies sometimes change titles or descriptions on a posting without
    changing the URL, so URL-hashing is the most reliable de-dup key.
    """
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]


# --- seen_jobs ----------------------------------------------------------------

def load_seen() -> dict[str, dict[str, Any]]:
    data = _load(SEEN_PATH)
    return data.get("jobs", {})


def mark_seen(jobs: list[dict[str, Any]]) -> None:
    """Record a batch of jobs as seen. Each job needs at least 'url'."""
    data = _load(SEEN_PATH)
    seen = data.setdefault("jobs", {})
    now = datetime.now(timezone.utc).isoformat()
    for job in jobs:
        jid = job_id(job["url"])
        seen[jid] = {
            "title": job.get("title"),
            "company": job.get("company"),
            "first_seen": seen.get(jid, {}).get("first_seen", now),
            "last_seen": now,
        }
    data["jobs"] = seen
    _save(SEEN_PATH, data)


def is_new(job: dict[str, Any]) -> bool:
    seen = load_seen()
    return job_id(job["url"]) not in seen


# --- feedback ----------------------------------------------------------------

def load_feedback() -> dict[str, Any]:
    data = _load(FEEDBACK_PATH)
    # Defensive defaults so a hand-edited feedback.json doesn't crash the run.
    data.setdefault("rejected_jobs", [])
    data.setdefault("loved_jobs", [])
    data.setdefault("quote_thumbs_up", [])
    data.setdefault("quote_thumbs_down", [])
    data.setdefault("tone_weights", {})
    return data


def save_feedback(data: dict[str, Any]) -> None:
    _save(FEEDBACK_PATH, data)


def record_rejection(job: dict[str, Any], reason: str = "") -> None:
    fb = load_feedback()
    fb["rejected_jobs"].append({
        "id": job_id(job["url"]),
        "title": job.get("title"),
        "company": job.get("company"),
        "url": job.get("url"),
        "reason": reason,
        "rejected_at": datetime.now(timezone.utc).isoformat(),
    })
    save_feedback(fb)


def record_quote_thumb(quote_id: str, direction: str, tones: list[str]) -> None:
    """direction is 'up' or 'down'. We update both the per-quote list and the
    learned tone weights so future picks lean toward Lucy's preferred tones."""
    if direction not in ("up", "down"):
        raise ValueError("direction must be 'up' or 'down'")
    fb = load_feedback()
    target = fb["quote_thumbs_up" if direction == "up" else "quote_thumbs_down"]
    target.append({"id": quote_id, "at": datetime.now(timezone.utc).isoformat()})
    delta = 0.15 if direction == "up" else -0.15
    weights = fb["tone_weights"]
    for t in tones:
        weights[t] = max(0.1, min(3.0, weights.get(t, 1.0) + delta))
    save_feedback(fb)
