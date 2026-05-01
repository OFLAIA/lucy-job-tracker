"""Daily motivational quote picker.

Reads the bank from config/quotes.json and biases the daily pick toward
tones Lucy has previously thumbed-up. Tone weights live in feedback.json
and update when she clicks the buttons in the email.

Picker logic:
  1. Each quote scores: sum(tone_weights[t] for t in quote.tones).
  2. We exclude any quote shown in the last 30 picks (no immediate repeats).
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
RECENT_HISTORY_PATH = REPO_ROOT / "state" / "quote_history.json"
RECENT_WINDOW = 30


def _load_quotes() -> list[dict[str, Any]]:
    with QUOTES_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)["quotes"]


def _load_history() -> list[str]:
    if not RECENT_HISTORY_PATH.exists():
        return []
    with RECENT_HISTORY_PATH.open("r", encoding="utf-8") as f:
        return json.load(f).get("recent_ids", [])


def _save_history(ids: list[str]) -> None:
    RECENT_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with RECENT_HISTORY_PATH.open("w", encoding="utf-8") as f:
        json.dump({"recent_ids": ids[-RECENT_WINDOW:]}, f, indent=2)


def _score_quote(q: dict[str, Any], tone_weights: dict[str, float]) -> float:
    return sum(tone_weights.get(t, 1.0) for t in q.get("tones", []))


def pick_today() -> dict[str, Any]:
    """Return the quote for today's email and record it in the history."""
    quotes = _load_quotes()
    feedback = state.load_feedback()
    weights = feedback.get("tone_weights", {})
    history = _load_history()
    candidates = [q for q in quotes if q["id"] not in history]
    if not candidates:
        # Everything has been shown recently. Reset.
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
    _save_history(history)
    LOG.info("Today's quote: %s (id=%s)", pick.get("text", "")[:60], pick["id"])
    return {
        **pick,
        "picked_at": datetime.now(timezone.utc).isoformat(),
    }
