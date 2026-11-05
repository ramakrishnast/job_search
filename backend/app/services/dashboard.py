"""Dashboard metrics: pipeline funnel + interview progress + LLM spend."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import utcnow
from ..models import (
    STAGE_ORDER,
    STAGES,
    Application,
    FitScore,
    InterviewSession,
    Job,
    LLMCall,
    StageEvent,
)

RESPONDED_STAGES = {"recruiter_screen", "interviewing", "offer"}
FUNNEL = ["shortlisted", "applied", "recruiter_screen", "interviewing", "offer"]


def _week_start(d):  # type: ignore[no-untyped-def]
    d0 = d - timedelta(days=d.weekday())
    return d0.replace(hour=0, minute=0, second=0, microsecond=0)


def pipeline_metrics(db: Session, weeks: int = 12) -> dict[str, Any]:
    apps = list(db.scalars(select(Application)))
    events = list(db.scalars(select(StageEvent)))
    by_app: dict[int, list[StageEvent]] = defaultdict(list)
    for e in events:
        by_app[e.application_id].append(e)

    # Highest funnel stage ever reached per application (based on events + current stage).
    reached: dict[int, set[str]] = {}
    for a in apps:
        stages = {e.to_stage for e in by_app.get(a.id, [])} | {a.stage}
        max_idx = max((STAGE_ORDER[s] for s in stages if s in FUNNEL), default=-1)
        # reaching a later stage implies the earlier funnel stages (e.g. applied implies shortlisted)
        implied = {s for s in FUNNEL if STAGE_ORDER[s] <= max_idx}
        reached[a.id] = stages | implied

    applied = [a for a in apps if "applied" in reached[a.id] or a.applied_at is not None]
    responded = [
        a
        for a in applied
        if reached[a.id] & RESPONDED_STAGES or ("rejected" in reached[a.id] and any(e.to_stage == "rejected" for e in by_app.get(a.id, [])))
    ]
    # rejections after applying count as a response (the company replied)
    response_rate = (len(responded) / len(applied)) if applied else None

    conversion = []
    for i in range(len(FUNNEL) - 1):
        a_s, b_s = FUNNEL[i], FUNNEL[i + 1]
        n_a = sum(1 for a in apps if a_s in reached[a.id])
        n_b = sum(1 for a in apps if b_s in reached[a.id])
        conversion.append({"from": a_s, "to": b_s, "from_count": n_a, "to_count": n_b, "rate": (n_b / n_a) if n_a else None})

    now = utcnow()
    start = _week_start(now) - timedelta(weeks=weeks - 1)
    per_week: Counter[str] = Counter()
    for a in applied:
        when = a.applied_at or next((e.at for e in by_app.get(a.id, []) if e.to_stage == "applied"), None)
        if when and when >= start:
            per_week[_week_start(when).date().isoformat()] += 1
    weekly = []
    for i in range(weeks):
        wk = (start + timedelta(weeks=i)).date().isoformat()
        weekly.append({"week": wk, "applications": per_week.get(wk, 0)})

    stage_counts = Counter(a.stage for a in apps)
    horizon = now + timedelta(days=14)
    upcoming = []
    for a in apps:
        if a.stage in ("offer", "rejected", "withdrawn"):
            continue
        for kind, when in (("follow_up", a.follow_up_at), ("next_action", a.next_action_date)):
            if when and when <= horizon:
                upcoming.append(
                    {
                        "application_id": a.id,
                        "job_id": a.job_id,
                        "title": a.job.title,
                        "company": a.job.company.name,
                        "stage": a.stage,
                        "kind": kind,
                        "label": a.next_action if kind == "next_action" else "Follow up",
                        "due": when.isoformat(),
                        "overdue": when < now,
                    }
                )
    upcoming.sort(key=lambda x: x["due"])
    return {
        "stage_counts": {s: stage_counts.get(s, 0) for s in STAGES},
        "applied_total": len(applied),
        "responded_total": len(responded),
        "response_rate": response_rate,
        "conversion": conversion,
        "applications_per_week": weekly,
        "upcoming": upcoming,
    }


def jobs_metrics(db: Session) -> dict[str, Any]:
    open_matching = db.scalar(
        select(func.count()).select_from(Job).where(Job.matches_filter.is_(True), Job.closed_at.is_(None), Job.archived.is_(False))
    )
    since = utcnow() - timedelta(days=7)
    new_week = db.scalar(select(func.count()).select_from(Job).where(Job.matches_filter.is_(True), Job.first_seen_at >= since))
    verdicts = dict(db.execute(select(FitScore.verdict, func.count()).group_by(FitScore.verdict)).all())
    return {"open_matching": open_matching or 0, "new_matching_7d": new_week or 0, "verdicts": verdicts}


def interview_metrics(db: Session) -> dict[str, Any]:
    sessions = list(db.scalars(select(InterviewSession).where(InterviewSession.status == "completed").order_by(InterviewSession.ended_at)))
    series = [
        {
            "id": s.id,
            "date": (s.ended_at or s.started_at).isoformat(),
            "kind": s.kind,
            "overall": s.overall_score,
            "technical_depth": s.technical_depth,
            "communication": s.communication,
            "structure": s.structure,
            "tradeoffs": s.tradeoffs,
        }
        for s in sessions
    ]
    by_kind: dict[str, list[int]] = defaultdict(list)
    for s in sessions:
        if s.overall_score is not None:
            by_kind[s.kind].append(s.overall_score)
    averages = {k: round(sum(v) / len(v), 1) for k, v in by_kind.items()}
    from .questions import topic_stats

    return {"sessions": series, "averages_by_kind": averages, "topics": topic_stats(db)[:12], "completed": len(sessions)}


def llm_metrics(db: Session) -> dict[str, Any]:
    since = utcnow() - timedelta(days=30)
    rows = db.execute(
        select(LLMCall.task, func.count(), func.sum(LLMCall.cost_usd), func.sum(LLMCall.input_tokens), func.sum(LLMCall.output_tokens))
        .where(LLMCall.created_at >= since)
        .group_by(LLMCall.task)
    ).all()
    by_task = [
        {"task": t, "calls": c, "cost_usd": round(float(cost or 0), 4), "input_tokens": int(i or 0), "output_tokens": int(o or 0)}
        for t, c, cost, i, o in rows
    ]
    day = utcnow() - timedelta(hours=24)
    last24 = db.scalar(select(func.coalesce(func.sum(LLMCall.cost_usd), 0.0)).where(LLMCall.created_at >= day)) or 0.0
    return {"by_task_30d": by_task, "cost_30d": round(sum(x["cost_usd"] for x in by_task), 4), "cost_24h": round(float(last24), 4)}
