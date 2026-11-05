"""JD parsing + fit scoring. Each job is parsed/scored once (keyed by content hash);
re-scoring only happens on explicit request or when the JD text changes."""

from __future__ import annotations

import logging
import re
from typing import Any

from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import utcnow
from ..llm.client import LLMClient
from ..llm.schemas import FitScoreOut, JDParse, verdict_for_score
from ..models import FitScore, JDParseRecord, Job, Profile
from .filtering import contains_term
from .profile import profile_as_text, profile_hash

log = logging.getLogger(__name__)

PARSE_SYSTEM = """You extract structured facts from a job description.
Rules: only report what the JD states. Use null when something is not stated. Never guess compensation.
required_skills = explicit must-haves; nice_to_haves = preferred/bonus. Keep each item a short phrase."""

SCORE_SYSTEM = """You are a blunt, accurate career advisor scoring how well a candidate fits a job.
Scoring rubric (0-100): 40 pts required-skill coverage, 20 pts seniority/years match, 20 pts domain fit
(applied LLM / agents / production ML), 10 pts location/remote fit vs candidate preferences, 10 pts nice-to-haves.
Verdict: Strong if score >= 75, Stretch if 50-74, Skip if < 50.
Hard rules:
- Strengths must cite experience that literally appears in the candidate background. Never invent experience.
- Gaps must be honest: if the JD asks for something the background does not show, it is a gap.
- Exactly 3 strengths and exactly 3 gaps. The candidate profile is in the system context."""


def _job_header(job: Job) -> str:
    return (
        f"Company: {job.company.name}\nTitle: {job.title}\nLocation: {job.location or ''} "
        f"(remote={job.is_remote}, workplace={job.workplace_type or 'unspecified'})\n"
        f"Listed compensation: {job.comp_text or 'not listed'}"
    )


def parse_job(db: Session, llm: LLMClient, job: Job, *, interactive: bool = True) -> JDParse:
    rec = job.parse
    if rec is not None and rec.content_hash == job.content_hash:
        return JDParse.model_validate(rec.data)
    model = get_settings().llm_model_parse
    out = llm.generate(
        task="jd_parse",
        schema=JDParse,
        system=PARSE_SYSTEM,
        user=f"{_job_header(job)}\n\nJOB DESCRIPTION:\n<<<\n{job.description_text[:30000]}\n>>>",
        model=model,
        effort="low",
        max_tokens=4000,
        cache_key_parts=[job.content_hash],
        interactive=interactive,
        db=db,
    )
    if rec is None:
        rec = JDParseRecord(job_id=job.id, content_hash=job.content_hash, model=model, data=out.model_dump(mode="json"))
        db.add(rec)
    else:
        rec.content_hash, rec.model, rec.data = job.content_hash, model, out.model_dump(mode="json")
    db.commit()
    db.refresh(job)
    return out


def score_job(
    db: Session,
    llm: LLMClient,
    job: Job,
    profile: Profile,
    *,
    force: bool = False,
    interactive: bool = True,
) -> FitScore:
    """Return the cached fit score or compute one (LLM if configured, else heuristic)."""
    phash = profile_hash(profile)
    existing = job.fit
    # Cached. A profile change marks it stale in the UI but does not auto-spend.
    if (
        existing is not None
        and not force
        and existing.content_hash == job.content_hash
        and (existing.method == "llm" or not llm.configured)
    ):
        return existing

    if llm.configured:
        parsed = parse_job(db, llm, job, interactive=interactive)
        model = get_settings().llm_model_default
        user = (
            f"{_job_header(job)}\n\nPARSED JD:\n{parsed.model_dump_json(indent=1)}\n\n"
            f"FULL JD (for nuance):\n<<<\n{job.description_text[:20000]}\n>>>"
        )
        out = llm.generate(
            task="fit_score",
            schema=FitScoreOut,
            system=SCORE_SYSTEM,
            cached_context="CANDIDATE BACKGROUND:\n" + profile_as_text(profile),
            user=user,
            model=model,
            effort="medium",
            max_tokens=4000,
            # force => bypass cache by salting the key with the current score row id/time
            cache_key_parts=[job.content_hash, phash] + (["force", utcnow().isoformat()] if force else []),
            interactive=interactive,
            db=db,
        )
        data: dict[str, Any] = out.model_dump(mode="json")
        data["parsed"] = parsed.model_dump(mode="json")
        method, score, verdict = "llm", out.score, out.verdict
    else:
        h = heuristic_score(job, profile)
        data, method, model, score, verdict = h, "heuristic", None, h["score"], h["verdict"]

    if existing is None:
        existing = FitScore(job_id=job.id)
        db.add(existing)
    existing.content_hash = job.content_hash
    existing.profile_hash = phash
    existing.method = method
    existing.model = model
    existing.score = score
    existing.verdict = verdict
    existing.data = data
    existing.created_at = utcnow()
    db.commit()
    db.refresh(existing)
    return existing


# ---------------------------------------------------------------- heuristic

SKILL_LEXICON: dict[str, list[str]] = {
    "Python": ["python"],
    "LLMs / generative AI": ["llm", "llms", "large language model", "generative ai", "genai", "gpt", "claude"],
    "Agents / multi-agent systems": ["agent", "agents", "agentic", "multi-agent", "tool use", "function calling"],
    "RAG / retrieval": ["rag", "retrieval", "vector database", "embeddings", "semantic search"],
    "Evals": ["eval", "evals", "evaluation", "benchmark"],
    "Fine-tuning": ["fine-tuning", "fine tuning", "finetuning", "rlhf", "lora", "post-training"],
    "PyTorch": ["pytorch", "torch"],
    "Distributed systems": ["distributed systems", "distributed system", "scalab"],
    "Data engineering": ["data engineering", "data pipeline", "etl", "spark", "airflow", "kafka"],
    "Cloud / infra": ["aws", "gcp", "azure", "kubernetes", "docker", "terraform"],
    "Production ML / MLOps": ["mlops", "production", "deploy", "serving", "inference"],
    "Customer-facing / forward deployed": ["customer", "forward deployed", "solutions", "stakeholder"],
    "TypeScript / full-stack": ["typescript", "javascript", "react", "node"],
    "SQL": ["sql"],
    "Go / Rust / Java": ["golang", " go ", "rust", "java"],
}


_PREFIX_TERMS = {"scalab"}


def _hits(text: str, terms: list[str]) -> bool:
    t = f" {text.lower()} "
    return any((term in t) if (term.startswith(" ") or term in _PREFIX_TERMS) else contains_term(t, term) for term in terms)


def heuristic_score(job: Job, profile: Profile) -> dict[str, Any]:
    """Keyword-overlap estimate used when no API key is configured. Clearly labeled as such."""
    jd = f"{job.title}\n{job.description_text}"
    resume = profile_as_text(profile)
    wanted = [k for k, terms in SKILL_LEXICON.items() if _hits(jd, terms)]
    have = [k for k in wanted if _hits(resume, SKILL_LEXICON[k])]
    missing = [k for k in wanted if k not in have]
    coverage = len(have) / len(wanted) if wanted else 0.5
    years = re.findall(r"(\d{1,2})\+?\s*(?:years|yrs)", jd.lower())
    years_req = max((int(y) for y in years if int(y) < 30), default=None)
    years_bonus = 15 if (years_req is None or years_req <= 12) else 5
    score = round(min(100, 15 + coverage * 70 + years_bonus))
    verdict = verdict_for_score(score)
    strengths = [f"Resume mentions {k} which the JD asks for" for k in have[:3]]
    while len(strengths) < 3:
        strengths.append("No further keyword overlap detected (heuristic)")
    gaps = [f"JD mentions {k}; not found in your resume text" for k in missing[:3]]
    while len(gaps) < 3:
        gaps.append("No further keyword gaps detected (heuristic)")
    return {
        "score": score,
        "strengths": strengths,
        "gaps": gaps,
        "verdict": verdict,
        "verdict_line": f"Heuristic keyword estimate ({len(have)}/{len(wanted)} skill areas). Add ANTHROPIC_API_KEY for a real assessment.",
        "years_required": years_req,
    }
