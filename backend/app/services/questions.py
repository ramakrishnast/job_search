"""Question bank: seeding, selection (spaced repetition + weak topics), topic stats."""

from __future__ import annotations

import random
from collections import defaultdict
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import utcnow
from ..models import Question, ReviewState, Story

SEED_FILE = Path(__file__).resolve().parents[1] / "data" / "questions.yaml"
CATEGORIES = ["system_design", "ml_fundamentals", "behavioral", "coding"]


@lru_cache
def load_seed() -> dict[str, Any]:
    with open(SEED_FILE, encoding="utf-8") as f:
        return yaml.safe_load(f)


def competencies() -> list[str]:
    return list(load_seed().get("competencies", []))


def seed_questions(db: Session) -> int:
    """Insert new seed questions and refresh seed text (user edits live in source='user')."""
    n = 0
    existing = {q.key: q for q in db.scalars(select(Question).where(Question.source == "seed"))}
    for item in load_seed()["questions"]:
        q = existing.get(item["key"])
        if q is None:
            q = Question(key=item["key"], source="seed")
            db.add(q)
            n += 1
        q.category = item["category"]
        q.topic = item["topic"]
        q.difficulty = int(item.get("difficulty", 2))
        q.prompt = item["prompt"].strip()
        q.rubric_hints = item.get("rubric_hints", [])
        q.follow_up_hints = item.get("follow_up_hints", [])
        q.function_name = item.get("function_name")
        q.starter_code = item.get("starter_code")
        q.tests = item.get("tests")
    db.commit()
    return n


def behavioral_catalog(db: Session) -> list[dict[str, str]]:
    return [
        {"key": q.key, "prompt": q.prompt, "topic": q.topic}
        for q in db.scalars(
            select(Question)
            .where(Question.category == "behavioral", Question.active.is_(True), Question.job_id.is_(None))
            .order_by(Question.id)
        )
    ]


def topic_stats(db: Session) -> list[dict[str, Any]]:
    rows = db.execute(
        select(Question.category, Question.topic, ReviewState.last_score, ReviewState.attempts, ReviewState.due_at).join(
            ReviewState, ReviewState.question_id == Question.id
        )
    ).all()
    agg: dict[tuple[str, str], dict[str, Any]] = defaultdict(lambda: {"scores": [], "attempts": 0, "due": 0})
    now = utcnow()
    for cat, topic, score, attempts, due in rows:
        a = agg[(cat, topic)]
        if score is not None:
            a["scores"].append(score)
        a["attempts"] += attempts or 0
        if due and due <= now:
            a["due"] += 1
    out = []
    for (cat, topic), a in agg.items():
        avg = sum(a["scores"]) / len(a["scores"]) if a["scores"] else None
        out.append({"category": cat, "topic": topic, "avg_score": avg, "attempts": a["attempts"], "due": a["due"]})
    out.sort(key=lambda x: x["avg_score"] if x["avg_score"] is not None else 99)
    return out


def select_questions(
    db: Session,
    category: str,
    n: int,
    difficulty: int | None = None,
    topic: str | None = None,
    rng: random.Random | None = None,
) -> list[Question]:
    """Pick n questions: due-for-review first, then weak topics, then unseen, then the rest."""
    rng = rng or random.Random()
    stmt = select(Question).where(Question.category == category, Question.active.is_(True))
    if difficulty:
        stmt = stmt.where(Question.difficulty == difficulty)
    if topic:
        stmt = stmt.where(Question.topic == topic)
    pool = list(db.scalars(stmt))
    if not pool and difficulty:
        return select_questions(db, category, n, None, topic, rng)
    now = utcnow()
    weak_topics = {t["topic"] for t in topic_stats(db) if t["category"] == category and t["avg_score"] is not None and t["avg_score"] < 6}
    story_keys: set[str] = set()
    if category == "behavioral":
        for s in db.scalars(select(Story)):
            story_keys.update(s.question_ids or [])

    def bucket(q: Question) -> int:
        r = q.review
        if r is not None and r.due_at <= now:
            return 0
        if q.topic in weak_topics:
            return 1
        if r is None:
            return 2 if category != "behavioral" or q.key in story_keys or not story_keys else 3
        return 4

    rng.shuffle(pool)
    pool.sort(key=lambda q: (bucket(q), q.review.due_at if q.review else now))
    # avoid repeating the same topic back-to-back when possible
    picked: list[Question] = []
    topics_used: set[str] = set()
    for q in pool:
        if len(picked) >= n:
            break
        if q.topic in topics_used and len(pool) - len(picked) > n - len(picked):
            continue
        picked.append(q)
        topics_used.add(q.topic)
    for q in pool:
        if len(picked) >= n:
            break
        if q not in picked:
            picked.append(q)
    return picked
