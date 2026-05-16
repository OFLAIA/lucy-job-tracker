"""Daily motivational quote picker, per user.

Reads the bank from config/quotes.json and biases the daily pick toward
tones the user has previously thumbed-up. Tone weights live in each user's
feedback.json and update when they click the buttons in the email.

Picker logic:
  1. Each quote scores: sum(tone_weights[t] for t in quote.tones).
  2. We exclude any quote shown in the user's last 30 picks (no immediate repeats).
  3. Weighted random pick.
"""
from __future__ import annotations

import json
import logging
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import state

LOG = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent
QUOTES_PATH = REPO_ROOT / "config" / "quotes.json"
RECENT_WINDOW = 30


def _load_quotes() -> list[dict[str, Any]]:
    with QUOTES_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)["quotes"]


def _history_path(user_id: str) -> Path:
    return state.USERS_DIR / user_id / "quote_history.json"


def _load_history(user_id: str) -> list[str]:
    path = _history_path(user_id)
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as f:
        return json.load(f).get("recent_ids", [])


def _save_history(user_id: str, ids: list[str]) -> None:
    path = _history_path(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump({"recent_ids": ids[-RECENT_WINDOW:]}, f, indent=2)


def _score_quote(q: dict[str, Any], tone_weights: dict[str, float]) -> float:
    return sum(tone_weights.get(t, 1.0) for t in q.get("tones", []))


def pick_today(user_id: str) -> dict[str, Any]:
    """Return the quote for today's email for this user, and record it in their history."""
    quotes = _load_quotes()
    feedback = state.load_feedback(user_id)
    weights = feedback.get("tone_weights", {})
    history = _load_history(user_id)
    candidates = [q for q in quotes if q["id"] not in history]
    if not candidates:
        candidates = quotes
        history = []
    weighted = [(q, max(0.01, _score_quote(q, weights))) for q in candidates]
    total = sum(w for _, w in weighted)
    r = random.uniform(0, total)
    acc = 0.0
    pick = weighted[0][0]
    for q, w in weighted:
        acc += w
        if acc >= r:
            pick = q
            break
    history.append(pick["id"])
    _save_history(user_id, history)
    LOG.info("[%s] today's quote: %s (id=%s)", user_id, pick.get("text", "")[:60], pick["id"])
    return {
        **pick,
        "picked_at": datetime.now(timezone.utc).isoformat(),
    }
