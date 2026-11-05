"""SM-2 style spaced repetition over interview questions."""

from __future__ import annotations

from datetime import datetime, timedelta

from ..db import utcnow
from ..models import ReviewState


def quality_from_score(score_0_10: float) -> int:
    """Map a 0-10 interview score to SM-2 quality 0-5."""
    return max(0, min(5, round(score_0_10 / 2)))


def update_review(state: ReviewState, score_0_10: float, now: datetime | None = None) -> ReviewState:
    now = now or utcnow()
    q = quality_from_score(score_0_10)
    if state.ease is None:
        state.ease = 2.5
    state.repetitions = state.repetitions or 0
    state.interval_days = state.interval_days or 0.0
    state.lapses = state.lapses or 0
    state.attempts = (state.attempts or 0) + 1
    if q < 3:
        state.repetitions = 0
        state.interval_days = 1.0
        state.lapses += 1
    else:
        state.repetitions += 1
        if state.repetitions == 1:
            state.interval_days = 2.0
        elif state.repetitions == 2:
            state.interval_days = 6.0
        else:
            state.interval_days = round(state.interval_days * state.ease, 1)
    state.ease = max(1.3, state.ease + 0.1 - (5 - q) * (0.08 + (5 - q) * 0.02))
    state.due_at = now + timedelta(days=state.interval_days)
    state.last_score = float(score_0_10)
    state.last_reviewed_at = now
    return state
