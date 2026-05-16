"""Persistent state for the job tracker.

State is split into per-user and per-company files, all under /state/:

  state/users/{user_id}/seen_jobs.json       per-user dedup memory
  state/users/{user_id}/feedback.json        per-user rejections + tone weights
  state/users/{user_id}/quote_history.json   per-user quote rotation
  state/scrape_cache/{company_short}.json    shared raw scrape results, refreshed daily

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
USERS_DIR = STATE_DIR / "users"
SCRAPE_CACHE_DIR = STATE_DIR / "scrape_cache"


# --- generic helpers ---------------------------------------------------------

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
    """Stable id for a job, derived from its URL."""
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]


def _user_dir(user_id: str) -> Path:
    return USERS_DIR / user_id


# --- per-user: seen_jobs -----------------------------------------------------

def _seen_path(user_id: str) -> Path:
    return _user_dir(user_id) / "seen_jobs.json"


def load_seen(user_id: str) -> dict[str, dict[str, Any]]:
    data = _load(_seen_path(user_id))
    return data.get("jobs", {})


def mark_seen(user_id: str, jobs: list[dict[str, Any]]) -> None:
    """Record a batch of jobs as seen by this user."""
    path = _seen_path(user_id)
    data = _load(path)
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
    _save(path, data)


def is_new_for(user_id: str, job: dict[str, Any], seen_index: dict[str, Any] | None = None) -> bool:
    """Check whether a job is new for this user. Pass in pre-loaded seen_index
    to avoid re-reading the file in a tight loop."""
    if seen_index is None:
        seen_index = load_seen(user_id)
    return job_id(job["url"]) not in seen_index


# --- per-user: feedback ------------------------------------------------------

def _feedback_path(user_id: str) -> Path:
    return _user_dir(user_id) / "feedback.json"


def load_feedback(user_id: str) -> dict[str, Any]:
    data = _load(_feedback_path(user_id))
    data.setdefault("rejected_jobs", [])
    data.setdefault("loved_jobs", [])
    data.setdefault("quote_thumbs_up", [])
    data.setdefault("quote_thumbs_down", [])
    data.setdefault("tone_weights", {})
    return data


def save_feedback(user_id: str, data: dict[str, Any]) -> None:
    _save(_feedback_path(user_id), data)


def record_rejection(user_id: str, job: dict[str, Any], reason: str = "") -> None:
    fb = load_feedback(user_id)
    fb["rejected_jobs"].append({
        "id": job_id(job["url"]),
        "title": job.get("title"),
        "company": job.get("company"),
        "url": job.get("url"),
        "reason": reason,
        "rejected_at": datetime.now(timezone.utc).isoformat(),
    })
    save_feedback(user_id, fb)


def record_quote_thumb(user_id: str, quote_id: str, direction: str, tones: list[str]) -> None:
    if direction not in ("up", "down"):
        raise ValueError("direction must be 'up' or 'down'")
    fb = load_feedback(user_id)
    target = fb["quote_thumbs_up" if direction == "up" else "quote_thumbs_down"]
    target.append({"id": quote_id, "at": datetime.now(timezone.utc).isoformat()})
    delta = 0.15 if direction == "up" else -0.15
    weights = fb["tone_weights"]
    for t in tones:
        weights[t] = max(0.1, min(3.0, weights.get(t, 1.0) + delta))
    save_feedback(user_id, fb)


# --- shared: scrape cache ----------------------------------------------------

def _scrape_cache_path(company_short: str) -> Path:
    safe = company_short.replace("/", "_").replace(" ", "_")
    return SCRAPE_CACHE_DIR / f"{safe}.json"


def save_scrape_cache(company_short: str, jobs: list[dict[str, Any]]) -> None:
    """Cache one company's raw scrape results. Called once per company per day."""
    _save(_scrape_cache_path(company_short), {
        "company": company_short,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "job_count": len(jobs),
        "jobs": jobs,
    })


def load_scrape_cache(company_short: str) -> list[dict[str, Any]]:
    """Read a company's cached scrape. Returns [] if no cache yet."""
    data = _load(_scrape_cache_path(company_short))
    return data.get("jobs", [])


def cache_age_hours(company_short: str) -> float | None:
    """How many hours since this company was last scraped, or None if never."""
    data = _load(_scrape_cache_path(company_short))
    fetched_at = data.get("fetched_at")
    if not fetched_at:
        return None
    fetched = datetime.fromisoformat(fetched_at.replace("Z", "+00:00"))
    delta = datetime.now(timezone.utc) - fetched
    return delta.total_seconds() / 3600.0
