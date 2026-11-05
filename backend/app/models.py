"""SQLAlchemy ORM models."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base, utcnow

STAGES = [
    "discovered",
    "shortlisted",
    "applied",
    "recruiter_screen",
    "interviewing",
    "offer",
    "rejected",
    "withdrawn",
]
TERMINAL_STAGES = {"offer", "rejected", "withdrawn"}
STAGE_ORDER = {s: i for i, s in enumerate(STAGES)}


class Company(Base):
    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    ats: Mapped[str] = mapped_column(String(32))  # greenhouse | lever | ashby | manual
    slug: Mapped[str | None] = mapped_column(String(200))
    region: Mapped[str | None] = mapped_column(String(16))  # lever: "eu" for api.eu.lever.co
    careers_url: Mapped[str | None] = mapped_column(String(500))
    category: Mapped[str | None] = mapped_column(String(64))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    source: Mapped[str] = mapped_column(String(16), default="config")  # config | ui
    last_fetch_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_status: Mapped[str | None] = mapped_column(String(32))  # ok | error | skipped
    last_error: Mapped[str | None] = mapped_column(Text)
    last_job_count: Mapped[int | None] = mapped_column(Integer)

    jobs: Mapped[list[Job]] = relationship(back_populates="company")


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (UniqueConstraint("company_id", "external_id", name="uq_job_company_ext"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    source: Mapped[str] = mapped_column(String(32))  # greenhouse | lever | ashby | manual
    external_id: Mapped[str] = mapped_column(String(200))
    title: Mapped[str] = mapped_column(String(500))
    location: Mapped[str | None] = mapped_column(String(500))
    locations: Mapped[list[str]] = mapped_column(JSON, default=list)
    departments: Mapped[list[str]] = mapped_column(JSON, default=list)
    workplace_type: Mapped[str | None] = mapped_column(String(32))  # remote | hybrid | onsite
    is_remote: Mapped[bool] = mapped_column(Boolean, default=False)
    url: Mapped[str | None] = mapped_column(String(1000))
    apply_url: Mapped[str | None] = mapped_column(String(1000))
    description_html: Mapped[str | None] = mapped_column(Text)
    description_text: Mapped[str] = mapped_column(Text, default="")
    comp_text: Mapped[str | None] = mapped_column(String(500))
    content_hash: Mapped[str] = mapped_column(String(64))
    posted_at: Mapped[datetime | None] = mapped_column(DateTime)
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    matches_filter: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    filter_reasons: Mapped[list[str]] = mapped_column(JSON, default=list)
    location_tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    seniority: Mapped[str | None] = mapped_column(String(32))
    archived: Mapped[bool] = mapped_column(Boolean, default=False)

    company: Mapped[Company] = relationship(back_populates="jobs")
    application: Mapped[Application | None] = relationship(back_populates="job", uselist=False, cascade="all, delete-orphan")
    fit: Mapped[FitScore | None] = relationship(uselist=False, cascade="all, delete-orphan")
    parse: Mapped[JDParseRecord | None] = relationship(uselist=False, cascade="all, delete-orphan")


class Application(Base):
    """Pipeline entry for a job (1:1)."""

    __tablename__ = "applications"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), unique=True)
    stage: Mapped[str] = mapped_column(String(32), default="discovered", index=True)
    position: Mapped[float] = mapped_column(Float, default=0.0)  # ordering within a kanban column
    notes: Mapped[str] = mapped_column(Text, default="")
    next_action: Mapped[str | None] = mapped_column(String(500))
    next_action_date: Mapped[datetime | None] = mapped_column(DateTime)
    follow_up_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    job: Mapped[Job] = relationship(back_populates="application")
    events: Mapped[list[StageEvent]] = relationship(cascade="all, delete-orphan", order_by="StageEvent.at")
    contacts: Mapped[list[Contact]] = relationship(cascade="all, delete-orphan")


class StageEvent(Base):
    __tablename__ = "stage_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"), index=True)
    from_stage: Mapped[str | None] = mapped_column(String(32))
    to_stage: Mapped[str] = mapped_column(String(32))
    at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Contact(Base):
    __tablename__ = "contacts"

    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    role: Mapped[str | None] = mapped_column(String(200))
    email: Mapped[str | None] = mapped_column(String(200))
    linkedin_url: Mapped[str | None] = mapped_column(String(500))
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Profile(Base):
    """Single-row table (id=1) holding the user's profile and resume."""

    __tablename__ = "profile"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    headline: Mapped[str] = mapped_column(String(500), default="")
    email: Mapped[str | None] = mapped_column(String(200))
    phone: Mapped[str | None] = mapped_column(String(64))
    location: Mapped[str | None] = mapped_column(String(200))
    links: Mapped[list[str]] = mapped_column(JSON, default=list)
    summary: Mapped[str] = mapped_column(Text, default="")
    highlights: Mapped[list[str]] = mapped_column(JSON, default=list)
    target_roles: Mapped[list[str]] = mapped_column(JSON, default=list)
    preferences: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    resume_text: Mapped[str] = mapped_column(Text, default="")
    structured_resume: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    structured_method: Mapped[str | None] = mapped_column(String(16))  # llm | heuristic | manual
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class JDParseRecord(Base):
    __tablename__ = "jd_parses"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), unique=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(64))
    data: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class FitScore(Base):
    __tablename__ = "fit_scores"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), unique=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    profile_hash: Mapped[str] = mapped_column(String(64))
    method: Mapped[str] = mapped_column(String(16))  # llm | heuristic
    model: Mapped[str | None] = mapped_column(String(64))
    score: Mapped[int] = mapped_column(Integer, index=True)
    verdict: Mapped[str] = mapped_column(String(16), index=True)
    data: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Tailoring(Base):
    __tablename__ = "tailorings"
    __table_args__ = (UniqueConstraint("job_id", "kind", name="uq_tailoring_job_kind"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # resume | kit
    content_hash: Mapped[str] = mapped_column(String(64))
    profile_hash: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(64))
    data: Mapped[dict[str, Any]] = mapped_column(JSON)
    warnings: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class LLMCache(Base):
    """Content-addressed cache of validated LLM outputs (cost guard)."""

    __tablename__ = "llm_cache"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(64), unique=True)
    task: Mapped[str] = mapped_column(String(64), index=True)
    model: Mapped[str] = mapped_column(String(64))
    data: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class LLMCall(Base):
    """Usage log for every Claude API request (for spend tracking + budget)."""

    __tablename__ = "llm_calls"

    id: Mapped[int] = mapped_column(primary_key=True)
    task: Mapped[str] = mapped_column(String(64), index=True)
    model: Mapped[str] = mapped_column(String(64))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_write_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    error: Mapped[str | None] = mapped_column(Text)
    request_id: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class DiscoveryRun(Base):
    __tablename__ = "discovery_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    trigger: Mapped[str] = mapped_column(String(16))  # scheduled | manual | demo | startup
    status: Mapped[str] = mapped_column(String(16), default="running")  # running | ok | partial | error
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    fetched_count: Mapped[int] = mapped_column(Integer, default=0)
    new_count: Mapped[int] = mapped_column(Integer, default=0)
    new_matching_count: Mapped[int] = mapped_column(Integer, default=0)
    closed_count: Mapped[int] = mapped_column(Integer, default=0)
    scored_count: Mapped[int] = mapped_column(Integer, default=0)
    company_stats: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    error: Mapped[str | None] = mapped_column(Text)


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(32))  # new_jobs | closed_jobs | follow_up | discovery_error
    title: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text, default="")
    link: Mapped[str | None] = mapped_column(String(300))
    dedupe_key: Mapped[str | None] = mapped_column(String(200), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime)


# ---------------------------------------------------------------- interview prep


class Story(Base):
    """Story bank entry: raw project input + generated STAR story."""

    __tablename__ = "stories"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(300))
    project: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # raw user input fields
    situation: Mapped[str] = mapped_column(Text, default="")
    task: Mapped[str] = mapped_column(Text, default="")
    action: Mapped[str] = mapped_column(Text, default="")
    result: Mapped[str] = mapped_column(Text, default="")
    competencies: Mapped[list[str]] = mapped_column(JSON, default=list)
    question_ids: Mapped[list[str]] = mapped_column(JSON, default=list)  # behavioral question keys
    method: Mapped[str] = mapped_column(String(16), default="manual")  # llm | manual
    warnings: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Question(Base):
    __tablename__ = "questions"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(100), unique=True)  # stable id for seeds
    category: Mapped[str] = mapped_column(String(32), index=True)
    topic: Mapped[str] = mapped_column(String(100), index=True)
    difficulty: Mapped[int] = mapped_column(Integer, default=2)  # 1 easy, 2 medium, 3 hard
    prompt: Mapped[str] = mapped_column(Text)
    rubric_hints: Mapped[list[str]] = mapped_column(JSON, default=list)
    follow_up_hints: Mapped[list[str]] = mapped_column(JSON, default=list)
    source: Mapped[str] = mapped_column(String(16), default="seed")  # seed | user | job
    job_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"))
    # coding problems
    function_name: Mapped[str | None] = mapped_column(String(100))
    starter_code: Mapped[str | None] = mapped_column(Text)
    tests: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    review: Mapped[ReviewState | None] = relationship(uselist=False, cascade="all, delete-orphan")


class ReviewState(Base):
    """SM-2 spaced-repetition state per question."""

    __tablename__ = "review_states"

    id: Mapped[int] = mapped_column(primary_key=True)
    question_id: Mapped[int] = mapped_column(ForeignKey("questions.id", ondelete="CASCADE"), unique=True)
    ease: Mapped[float] = mapped_column(Float, default=2.5)
    interval_days: Mapped[float] = mapped_column(Float, default=0.0)
    repetitions: Mapped[int] = mapped_column(Integer, default=0)
    lapses: Mapped[int] = mapped_column(Integer, default=0)
    due_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    last_score: Mapped[float | None] = mapped_column(Float)  # 0-10
    last_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime)
    attempts: Mapped[int] = mapped_column(Integer, default=0)


class InterviewSession(Base):
    __tablename__ = "interview_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)  # category or "company"
    job_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(16), default="active")  # active | completed | abandoned
    difficulty: Mapped[int | None] = mapped_column(Integer)
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    plan: Mapped[list[int]] = mapped_column(JSON, default=list)  # question ids, in order
    current_index: Mapped[int] = mapped_column(Integer, default=0)
    followups_on_current: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime)
    scorecard: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    overall_score: Mapped[int | None] = mapped_column(Integer)
    technical_depth: Mapped[int | None] = mapped_column(Integer)
    communication: Mapped[int | None] = mapped_column(Integer)
    structure: Mapped[int | None] = mapped_column(Integer)
    tradeoffs: Mapped[int | None] = mapped_column(Integer)

    turns: Mapped[list[InterviewTurn]] = relationship(cascade="all, delete-orphan", order_by="InterviewTurn.seq")


class InterviewTurn(Base):
    __tablename__ = "interview_turns"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("interview_sessions.id", ondelete="CASCADE"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(String(16))  # interviewer | candidate
    kind: Mapped[str] = mapped_column(String(16))  # question | follow_up | answer | wrap_up
    question_index: Mapped[int] = mapped_column(Integer)
    question_id: Mapped[int | None] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text, default="")
    code: Mapped[str | None] = mapped_column(Text)
    code_result: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    whiteboard_scene: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    whiteboard_image: Mapped[str | None] = mapped_column(String(300))  # path under data/uploads
    time_taken_s: Mapped[int | None] = mapped_column(Integer)
    input_mode: Mapped[str | None] = mapped_column(String(16))  # text | voice
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
