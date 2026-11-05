"""ORM → JSON-ready dict serializers."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .models import (
    Application,
    Company,
    Contact,
    DiscoveryRun,
    InterviewSession,
    InterviewTurn,
    Job,
    Notification,
    Profile,
    Question,
    Story,
    Tailoring,
)


def iso(d: datetime | None) -> str | None:
    return d.isoformat() if d else None


def company(c: Company) -> dict[str, Any]:
    return {
        "id": c.id,
        "name": c.name,
        "ats": c.ats,
        "slug": c.slug,
        "region": c.region,
        "careers_url": c.careers_url,
        "category": c.category,
        "enabled": c.enabled,
        "source": c.source,
        "last_fetch_at": iso(c.last_fetch_at),
        "last_status": c.last_status,
        "last_error": c.last_error,
        "last_job_count": c.last_job_count,
    }


def fit(job: Job, current_profile_hash: str | None = None) -> dict[str, Any] | None:
    f = job.fit
    if f is None:
        return None
    return {
        "score": f.score,
        "verdict": f.verdict,
        "method": f.method,
        "model": f.model,
        "strengths": f.data.get("strengths", []),
        "gaps": f.data.get("gaps", []),
        "verdict_line": f.data.get("verdict_line", ""),
        "parsed": f.data.get("parsed"),
        "created_at": iso(f.created_at),
        "stale": bool(current_profile_hash and f.profile_hash != current_profile_hash) or f.content_hash != job.content_hash,
    }


def application(a: Application | None, with_detail: bool = False) -> dict[str, Any] | None:
    if a is None:
        return None
    d = {
        "id": a.id,
        "job_id": a.job_id,
        "stage": a.stage,
        "position": a.position,
        "notes": a.notes,
        "next_action": a.next_action,
        "next_action_date": iso(a.next_action_date),
        "follow_up_at": iso(a.follow_up_at),
        "applied_at": iso(a.applied_at),
        "created_at": iso(a.created_at),
        "updated_at": iso(a.updated_at),
    }
    if with_detail:
        d["events"] = [{"from": e.from_stage, "to": e.to_stage, "at": iso(e.at)} for e in a.events]
        d["contacts"] = [contact(c) for c in a.contacts]
    return d


def contact(c: Contact) -> dict[str, Any]:
    return {"id": c.id, "name": c.name, "role": c.role, "email": c.email, "linkedin_url": c.linkedin_url, "notes": c.notes}


def job_summary(j: Job, phash: str | None = None) -> dict[str, Any]:
    return {
        "id": j.id,
        "company": j.company.name,
        "company_id": j.company_id,
        "source": j.source,
        "title": j.title,
        "location": j.location,
        "locations": j.locations,
        "location_tags": j.location_tags,
        "is_remote": j.is_remote,
        "workplace_type": j.workplace_type,
        "seniority": j.seniority,
        "url": j.url,
        "apply_url": j.apply_url,
        "comp_text": j.comp_text,
        "posted_at": iso(j.posted_at),
        "first_seen_at": iso(j.first_seen_at),
        "last_seen_at": iso(j.last_seen_at),
        "closed_at": iso(j.closed_at),
        "matches_filter": j.matches_filter,
        "filter_reasons": j.filter_reasons,
        "archived": j.archived,
        "fit": fit(j, phash),
        "stage": j.application.stage if j.application else None,
        "application_id": j.application.id if j.application else None,
    }


def job_detail(j: Job, phash: str | None = None) -> dict[str, Any]:
    d = job_summary(j, phash)
    d.update(
        description_text=j.description_text,
        departments=j.departments,
        parse=j.parse.data if j.parse else None,
        application=application(j.application, with_detail=True),
    )
    return d


def tailoring(t: Tailoring | None, phash: str | None = None, content_hash: str | None = None) -> dict[str, Any] | None:
    if t is None:
        return None
    return {
        "kind": t.kind,
        "data": t.data,
        "warnings": t.warnings,
        "model": t.model,
        "created_at": iso(t.created_at),
        "stale": (phash is not None and t.profile_hash != phash) or (content_hash is not None and t.content_hash != content_hash),
    }


def profile(p: Profile) -> dict[str, Any]:
    return {
        "name": p.name,
        "headline": p.headline,
        "email": p.email,
        "phone": p.phone,
        "location": p.location,
        "links": p.links,
        "summary": p.summary,
        "highlights": p.highlights,
        "target_roles": p.target_roles,
        "preferences": p.preferences,
        "resume_text": p.resume_text,
        "structured_resume": p.structured_resume,
        "structured_method": p.structured_method,
        "updated_at": iso(p.updated_at),
    }


def run(r: DiscoveryRun) -> dict[str, Any]:
    return {
        "id": r.id,
        "trigger": r.trigger,
        "status": r.status,
        "started_at": iso(r.started_at),
        "finished_at": iso(r.finished_at),
        "fetched_count": r.fetched_count,
        "new_count": r.new_count,
        "new_matching_count": r.new_matching_count,
        "closed_count": r.closed_count,
        "scored_count": r.scored_count,
        "company_stats": r.company_stats,
        "error": r.error,
    }


def notification(n: Notification) -> dict[str, Any]:
    return {
        "id": n.id,
        "kind": n.kind,
        "title": n.title,
        "body": n.body,
        "link": n.link,
        "created_at": iso(n.created_at),
        "read": n.read_at is not None,
    }


def story(s: Story) -> dict[str, Any]:
    return {
        "id": s.id,
        "title": s.title,
        "project": s.project,
        "situation": s.situation,
        "task": s.task,
        "action": s.action,
        "result": s.result,
        "competencies": s.competencies,
        "question_ids": s.question_ids,
        "method": s.method,
        "warnings": s.warnings,
        "updated_at": iso(s.updated_at),
    }


def question(q: Question, include_tests: bool = False) -> dict[str, Any]:
    r = q.review
    d = {
        "id": q.id,
        "key": q.key,
        "category": q.category,
        "topic": q.topic,
        "difficulty": q.difficulty,
        "prompt": q.prompt,
        "rubric_hints": q.rubric_hints,
        "follow_up_hints": q.follow_up_hints,
        "source": q.source,
        "job_id": q.job_id,
        "active": q.active,
        "function_name": q.function_name,
        "starter_code": q.starter_code,
        "has_tests": bool(q.tests),
        "review": None
        if r is None
        else {
            "due_at": iso(r.due_at),
            "last_score": r.last_score,
            "attempts": r.attempts,
            "interval_days": r.interval_days,
            "ease": round(r.ease, 2),
            "lapses": r.lapses,
        },
    }
    if include_tests:
        d["tests"] = q.tests
    return d


def turn(t: InterviewTurn) -> dict[str, Any]:
    return {
        "id": t.id,
        "seq": t.seq,
        "role": t.role,
        "kind": t.kind,
        "question_index": t.question_index,
        "question_id": t.question_id,
        "content": t.content,
        "code": t.code,
        "code_result": t.code_result,
        "whiteboard_image": f"/api/files/{t.whiteboard_image}" if t.whiteboard_image else None,
        "time_taken_s": t.time_taken_s,
        "input_mode": t.input_mode,
        "created_at": iso(t.created_at),
    }


def session(s: InterviewSession, questions: list[Question] | None = None, full: bool = False) -> dict[str, Any]:
    d = {
        "id": s.id,
        "kind": s.kind,
        "job_id": s.job_id,
        "status": s.status,
        "difficulty": s.difficulty,
        "config": s.config,
        "current_index": s.current_index,
        "num_questions": len(s.plan),
        "started_at": iso(s.started_at),
        "ended_at": iso(s.ended_at),
        "overall_score": s.overall_score,
        "rubric": {
            "technical_depth": s.technical_depth,
            "communication": s.communication,
            "structure": s.structure,
            "tradeoffs": s.tradeoffs,
        },
    }
    if full:
        d["turns"] = [turn(t) for t in s.turns]
        d["scorecard"] = s.scorecard
        d["questions"] = [question(q) for q in (questions or [])]
    return d
