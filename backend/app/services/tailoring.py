"""Per-job tailoring: resume bullets (grounded in real experience) + application kit."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import utcnow
from ..llm.client import LLMClient
from ..llm.schemas import ApplicationKitOut, ResumeBullet, ResumeExperience, StructuredResume, TailoredResumeOut
from ..models import Job, Profile, Tailoring
from .profile import base_resume, profile_as_text, profile_hash, structured_to_text
from .scoring import parse_job

RESUME_SYSTEM = """You tailor a resume to a specific job description.
ABSOLUTE RULES (outputs are machine-checked; violations are rejected):
- Only reorder, select and reframe the candidate's EXISTING bullets. Every output bullet must cite the
  id(s) of the original bullet(s) it is derived from (source_bullet_ids) within the same experience.
- Never invent employers, titles, tools, metrics, team sizes, dates or outcomes. Do not add any number
  that is not in the cited source bullets.
- Skills: reorder/select only skills already in the base resume.
- Emphasize what the JD asks for using the JD's vocabulary where it truthfully applies.
- Keep bullets crisp: strong verb, what you built, how, impact (only if stated in source).
- Include every experience_id from the base resume (you may drop weak bullets but keep at least one per role)."""

KIT_SYSTEM = """You write application materials for a senior applied AI engineer.
Rules:
- Use only experience present in the candidate background. Never invent metrics, employers or projects.
- Cover letter: under 250 words, specific to this company and role, no clichés, no "I am writing to apply".
- why_company: 3-6 concise notes grounded in the JD text. If you rely on general knowledge about the
  company, prefix that note with "(verify)".
- interview_questions: exactly 5 likely questions for this role, each with talking points that map to
  real experience from the background.
- outreach: a LinkedIn connection note (<= 300 chars) and a short email (<= 150 words) to a hiring
  manager or recruiter. Do not claim a referral or prior contact."""


def _get(db: Session, job_id: int, kind: str) -> Tailoring | None:
    return db.scalar(select(Tailoring).where(Tailoring.job_id == job_id, Tailoring.kind == kind))


def _store(db: Session, job: Job, kind: str, phash: str, model: str, data: dict[str, Any], warnings: list[str]) -> Tailoring:
    t = _get(db, job.id, kind)
    if t is None:
        t = Tailoring(job_id=job.id, kind=kind)
        db.add(t)
    t.content_hash = job.content_hash
    t.profile_hash = phash
    t.model = model
    t.data = data
    t.warnings = warnings
    t.created_at = utcnow()
    db.commit()
    db.refresh(t)
    return t


def tailor_resume(db: Session, llm: LLMClient, job: Job, profile: Profile, *, force: bool = False) -> Tailoring:
    phash = profile_hash(profile)
    cur = _get(db, job.id, "resume")
    if cur and not force and cur.content_hash == job.content_hash and cur.profile_hash == phash:
        return cur
    base = base_resume(profile)
    if not base.experience:
        raise ValueError("Add your resume on the Profile page before tailoring.")
    parsed = parse_job(db, llm, job)
    model = get_settings().llm_model_default
    out = llm.generate(
        task="tailor_resume",
        schema=TailoredResumeOut,
        system=RESUME_SYSTEM,
        cached_context="BASE RESUME (ids in brackets/parentheses):\n" + structured_to_text(base),
        user=(
            f"Company: {job.company.name}\nRole: {job.title}\n\nPARSED JD:\n{parsed.model_dump_json(indent=1)}\n\n"
            f"JD TEXT:\n<<<\n{job.description_text[:15000]}\n>>>"
        ),
        model=model,
        effort="medium",
        max_tokens=12000,
        validation_context={"base_resume": base, "resume_text": profile.resume_text},
        cache_key_parts=[job.content_hash, phash] + (["force", utcnow().isoformat()] if force else []),
        db=db,
    )
    return _store(db, job, "resume", phash, model, out.model_dump(mode="json"), [])


def tailor_kit(db: Session, llm: LLMClient, job: Job, profile: Profile, *, force: bool = False) -> Tailoring:
    phash = profile_hash(profile)
    cur = _get(db, job.id, "kit")
    if cur and not force and cur.content_hash == job.content_hash and cur.profile_hash == phash:
        return cur
    parsed = parse_job(db, llm, job)
    background = profile_as_text(profile)
    model = get_settings().llm_model_default
    out = llm.generate(
        task="application_kit",
        schema=ApplicationKitOut,
        system=KIT_SYSTEM,
        cached_context="CANDIDATE BACKGROUND:\n" + background,
        user=(
            f"Company: {job.company.name}\nRole: {job.title}\nLocation: {job.location}\n\n"
            f"PARSED JD:\n{parsed.model_dump_json(indent=1)}\n\nJD TEXT:\n<<<\n{job.description_text[:15000]}\n>>>"
        ),
        model=model,
        effort="medium",
        max_tokens=12000,
        validation_context={"grounding_text": background + "\n" + job.description_text},
        cache_key_parts=[job.content_hash, phash] + (["force", utcnow().isoformat()] if force else []),
        db=db,
    )
    return _store(db, job, "kit", phash, model, out.model_dump(mode="json"), [])


def assemble_tailored_resume(profile: Profile, tailored: dict[str, Any] | None) -> StructuredResume:
    """Merge tailored bullets/summary/skills into the base resume for export."""
    base = base_resume(profile)
    if not tailored:
        return base
    t = TailoredResumeOut.model_validate(tailored)
    by_id = {e.experience_id: e for e in t.experience}
    exps: list[ResumeExperience] = []
    for e in base.experience:
        te = by_id.get(e.id)
        if te is None or not te.bullets:
            exps.append(e)
            continue
        bullets = [ResumeBullet(id=f"{e.id}t{i + 1}", text=b.text) for i, b in enumerate(te.bullets)]
        exps.append(e.model_copy(update={"bullets": bullets}))
    return base.model_copy(update={"summary": t.summary or base.summary, "experience": exps, "skills": t.skills or base.skills})
