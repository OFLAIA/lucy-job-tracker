"""User config loading and validation.

Each user is a JSON file in users/{user_id}.json with this shape:

  {
    "id": "lucy",
    "name": "Lucy",
    "email": "lucysitunes1@gmail.com",
    "industry": "insurance",
    "region": "uk",
    "locations": ["London", "Essex", "Hybrid - London", "Remote - UK"],
    "seniority": "school_leaver",     // school_leaver | graduate | junior | mid | senior
    "role_focus": "underwriting, broking, claims",  // free text, optional
    "active": true,
    "paused_until": null,             // ISO date or null
    "created_at": "2026-05-01T..."
  }

The 'id' is used for filenames and state paths. Conventionally a URL-safe slug
of their first name (handles collisions with -2, -3 suffix).
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime
from pathlib import Path
from typing import Any

LOG = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent
USERS_DIR = REPO_ROOT / "users"

VALID_SENIORITY = {"school_leaver", "graduate", "junior", "mid", "senior"}


def list_users() -> list[dict[str, Any]]:
    """Return every user config in users/, sorted by id."""
    if not USERS_DIR.exists():
        return []
    out = []
    for f in sorted(USERS_DIR.glob("*.json")):
        try:
            with f.open("r", encoding="utf-8") as fp:
                cfg = json.load(fp)
            cfg.setdefault("id", f.stem)  # filename is the canonical id
            out.append(cfg)
        except (json.JSONDecodeError, OSError) as e:
            LOG.warning("Skipping %s: %s", f, e)
    return out


def is_active(cfg: dict[str, Any]) -> bool:
    """Active if 'active' is true AND not currently paused."""
    if not cfg.get("active", False):
        return False
    paused_until = cfg.get("paused_until")
    if paused_until:
        try:
            until = datetime.fromisoformat(paused_until).date()
        except (ValueError, TypeError):
            return True  # bad date format, treat as not paused
        if date.today() <= until:
            return False
    return True


def active_users() -> list[dict[str, Any]]:
    return [u for u in list_users() if is_active(u)]


def validate(cfg: dict[str, Any]) -> list[str]:
    """Return a list of validation errors (empty if valid)."""
    errors = []
    for required in ("id", "name", "email", "industry", "region", "locations", "seniority"):
        if required not in cfg or cfg.get(required) in (None, "", []):
            errors.append(f"missing required field: {required}")
    if cfg.get("seniority") not in VALID_SENIORITY:
        errors.append(f"seniority must be one of {sorted(VALID_SENIORITY)}")
    if not isinstance(cfg.get("locations", []), list):
        errors.append("locations must be a list of strings")
    return errors


# Seniority phrasing used in the AI prompt. Keep this single source of truth.
SENIORITY_DESCRIPTIONS = {
    "school_leaver": "school-leaver / apprentice / no prior work experience",
    "graduate": "recent graduate / on a structured graduate scheme / 0-1 years experience",
    "junior": "1-3 years of professional experience",
    "mid": "3-7 years of professional experience",
    "senior": "7+ years of professional experience or department-lead level",
}


def seniority_description(level: str) -> str:
    return SENIORITY_DESCRIPTIONS.get(level, level)
