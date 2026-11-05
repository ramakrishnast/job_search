"""Profile, story bank, question bank, code runner and interview endpoints."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import serializers as ser
from ..db import get_db
from ..llm.client import get_llm
from ..llm.schemas import StructuredResume
from ..models import InterviewSession, Question, Story
from ..services import export, interview
from ..services.code_runner import run_tests
from ..services.profile import base_resume, get_profile, structure_resume
from ..services.questions import CATEGORIES, behavioral_catalog, competencies, topic_stats
from ..services.stories import PROJECT_FIELDS, generate_star

router = APIRouter(prefix="/api", tags=["prep"])


# ------------------------------------------------------------------ profile


class ProfileIn(BaseModel):
    name: str | None = None
    headline: str | None = None
    email: str | None = None
    phone: str | None = None
    location: str | None = None
    links: list[str] | None = None
    summary: str | None = None
    highlights: list[str] | None = None
    target_roles: list[str] | None = None
    preferences: dict[str, Any] | None = None


@router.get("/profile")
def read_profile(db: Session = Depends(get_db)) -> dict[str, Any]:
    p = get_profile(db)
    d = ser.profile(p)
    d["base_resume"] = base_resume(p).model_dump()
    return d


@router.put("/profile")
def update_profile(body: ProfileIn, db: Session = Depends(get_db)) -> dict[str, Any]:
    p = get_profile(db)
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(p, k, v)
    db.commit()
    return read_profile(db)


class ResumeIn(BaseModel):
    resume_text: str = Field(min_length=50, max_length=60000)


@router.post("/profile/resume")
def set_resume(body: ResumeIn, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Store pasted resume text and structure it (Claude if configured, heuristic otherwise)."""
    p = get_profile(db)
    p.resume_text = body.resume_text
    db.commit()
    r, method = structure_resume(db, get_llm(), p)
    p.structured_resume = r.model_dump()
    p.structured_method = method
    if r.contact.name and not p.name:
        p.name = r.contact.name
    db.commit()
    return read_profile(db)


@router.put("/profile/structured")
def set_structured(body: StructuredResume, db: Session = Depends(get_db)) -> dict[str, Any]:
    p = get_profile(db)
    p.structured_resume = body.model_dump()
    p.structured_method = "manual"
    db.commit()
    return read_profile(db)


@router.get("/profile/export/resume.{fmt}")
def export_base_resume(fmt: Literal["docx", "pdf"], db: Session = Depends(get_db)) -> Response:
    p = get_profile(db)
    r = base_resume(p)
    name = export.safe_filename(p.name or "resume", "base")
    if fmt == "docx":
        data, media = export.resume_docx(r), "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    else:
        data, media = export.resume_pdf(r), "application/pdf"
    return Response(content=data, media_type=media, headers={"Content-Disposition": f'attachment; filename="{name}.{fmt}"'})


# ------------------------------------------------------------------ stories


class StoryIn(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    project: dict[str, str] = Field(default_factory=dict, description=f"fields: {PROJECT_FIELDS}")
    situation: str | None = None
    task: str | None = None
    action: str | None = None
    result: str | None = None
    competencies: list[str] | None = None
    question_ids: list[str] | None = None


@router.get("/stories")
def list_stories(db: Session = Depends(get_db)) -> dict[str, Any]:
    stories = db.scalars(select(Story).order_by(Story.updated_at.desc())).all()
    catalog = behavioral_catalog(db)
    coverage = {q["key"]: [s.id for s in stories if q["key"] in (s.question_ids or [])] for q in catalog}
    return {
        "stories": [ser.story(s) for s in stories],
        "behavioral_questions": catalog,
        "competencies": competencies(),
        "coverage": coverage,
        "project_fields": PROJECT_FIELDS,
    }


@router.post("/stories", status_code=201)
def create_story(body: StoryIn, generate: bool = True, db: Session = Depends(get_db)) -> dict[str, Any]:
    s = Story(title=body.title, project={k: v for k, v in body.project.items() if k in PROJECT_FIELDS})
    for f in ("situation", "task", "action", "result", "competencies", "question_ids"):
        v = getattr(body, f)
        if v is not None:
            setattr(s, f, v)
    db.add(s)
    db.commit()
    llm = get_llm()
    if generate and llm.configured:
        generate_star(db, llm, s)
    return ser.story(s)


@router.put("/stories/{story_id}")
def update_story(story_id: int, body: StoryIn, db: Session = Depends(get_db)) -> dict[str, Any]:
    s = db.get(Story, story_id)
    if s is None:
        raise HTTPException(404, "story not found")
    s.title = body.title
    s.project = {k: v for k, v in body.project.items() if k in PROJECT_FIELDS}
    for f in ("situation", "task", "action", "result", "competencies", "question_ids"):
        v = getattr(body, f)
        if v is not None:
            setattr(s, f, v)
    if any(getattr(body, f) is not None for f in ("situation", "task", "action", "result")):
        s.method = "manual"
    db.commit()
    return ser.story(s)


@router.post("/stories/{story_id}/generate")
def regenerate_story(story_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    s = db.get(Story, story_id)
    if s is None:
        raise HTTPException(404, "story not found")
    return ser.story(generate_star(db, get_llm(), s))


@router.delete("/stories/{story_id}", status_code=204)
def delete_story(story_id: int, db: Session = Depends(get_db)) -> Response:
    s = db.get(Story, story_id)
    if s is None:
        raise HTTPException(404, "story not found")
    db.delete(s)
    db.commit()
    return Response(status_code=204)


# ------------------------------------------------------------------ questions


@router.get("/questions")
def list_questions(
    db: Session = Depends(get_db),
    category: str | None = None,
    difficulty: int | None = None,
    topic: str | None = None,
    due: bool = False,
    include_job: bool = False,
) -> dict[str, Any]:
    stmt = select(Question).where(Question.active.is_(True))
    if not include_job:
        stmt = stmt.where(Question.job_id.is_(None))
    if category:
        stmt = stmt.where(Question.category == category)
    if difficulty:
        stmt = stmt.where(Question.difficulty == difficulty)
    if topic:
        stmt = stmt.where(Question.topic == topic)
    qs = list(db.scalars(stmt.order_by(Question.category, Question.difficulty, Question.id)))
    if due:
        from ..db import utcnow

        now = utcnow()
        qs = [q for q in qs if q.review is not None and q.review.due_at <= now]
    return {"questions": [ser.question(q) for q in qs], "categories": CATEGORIES, "topics": topic_stats(db)}


@router.get("/questions/{question_id}")
def get_question(question_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    q = db.get(Question, question_id)
    if q is None:
        raise HTTPException(404, "question not found")
    return ser.question(q, include_tests=True)


class QuestionIn(BaseModel):
    category: Literal["system_design", "ml_fundamentals", "behavioral", "coding"]
    topic: str = Field(min_length=1, max_length=100)
    difficulty: int = Field(2, ge=1, le=3)
    prompt: str = Field(min_length=10)
    rubric_hints: list[str] = []
    follow_up_hints: list[str] = []


@router.post("/questions", status_code=201)
def add_question(body: QuestionIn, db: Session = Depends(get_db)) -> dict[str, Any]:
    import hashlib

    key = "user-" + hashlib.sha1(body.prompt.encode()).hexdigest()[:12]
    if db.scalar(select(Question).where(Question.key == key)):
        raise HTTPException(409, "question already exists")
    q = Question(key=key, source="user", **body.model_dump())
    db.add(q)
    db.commit()
    return ser.question(q)


class QuestionPatch(BaseModel):
    active: bool | None = None
    difficulty: int | None = Field(None, ge=1, le=3)


@router.patch("/questions/{question_id}")
def patch_question(question_id: int, body: QuestionPatch, db: Session = Depends(get_db)) -> dict[str, Any]:
    q = db.get(Question, question_id)
    if q is None:
        raise HTTPException(404, "question not found")
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(q, k, v)
    db.commit()
    return ser.question(q)


class CodeRunIn(BaseModel):
    question_id: int
    code: str = Field(max_length=100_000)


@router.post("/code/run")
def code_run(body: CodeRunIn, db: Session = Depends(get_db)) -> dict[str, Any]:
    q = db.get(Question, body.question_id)
    if q is None or not q.tests:
        raise HTTPException(404, "coding question with tests not found")
    return run_tests(body.code, q.tests)


# ------------------------------------------------------------------ interviews


class StartIn(BaseModel):
    kind: Literal["system_design", "ml_fundamentals", "behavioral", "coding", "company"]
    job_id: int | None = None
    difficulty: int | None = Field(None, ge=1, le=3)
    num_questions: int = Field(3, ge=1, le=8)
    timer_seconds: int | None = Field(None, ge=30, le=3600)
    max_followups: int = Field(2, ge=0, le=4)
    question_ids: list[int] | None = None
    topic: str | None = None


def _session(db: Session, sid: int) -> InterviewSession:
    s = db.get(InterviewSession, sid)
    if s is None:
        raise HTTPException(404, "session not found")
    return s


def _full(db: Session, s: InterviewSession) -> dict[str, Any]:
    qs = [db.get(Question, qid) for qid in s.plan]
    return ser.session(s, [q for q in qs if q is not None], full=True)


@router.post("/interviews", status_code=201)
def start_interview(body: StartIn, db: Session = Depends(get_db)) -> dict[str, Any]:
    p = get_profile(db)
    try:
        s = interview.start_session(db, get_llm(), profile=p, **body.model_dump())
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return _full(db, s)


@router.get("/interviews")
def list_interviews(db: Session = Depends(get_db), limit: int = 50) -> list[dict[str, Any]]:
    rows = db.scalars(select(InterviewSession).order_by(InterviewSession.started_at.desc()).limit(min(limit, 200)))
    return [ser.session(s) for s in rows]


@router.get("/interviews/{sid}")
def get_interview(sid: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    return _full(db, _session(db, sid))


class AnswerIn(BaseModel):
    text: str = Field("", max_length=30000)
    code: str | None = Field(None, max_length=100_000)
    run_tests: bool = True
    whiteboard_png: str | None = Field(None, description="base64 PNG (data URL ok)")
    whiteboard_scene: dict[str, Any] | None = None
    time_taken_s: int | None = None
    input_mode: Literal["text", "voice", "mixed"] | None = None


@router.post("/interviews/{sid}/answer")
def answer(sid: int, body: AnswerIn, db: Session = Depends(get_db)) -> dict[str, Any]:
    s = _session(db, sid)
    code_result = None
    if body.code and body.run_tests and s.current_index < len(s.plan):
        q = db.get(Question, s.plan[s.current_index])
        if q is not None and q.tests:
            code_result = run_tests(body.code, q.tests)
    try:
        interview.submit_answer(
            db,
            get_llm(),
            s,
            get_profile(db),
            text=body.text,
            code=body.code,
            code_result=code_result,
            whiteboard_png_b64=body.whiteboard_png,
            whiteboard_scene=body.whiteboard_scene,
            time_taken_s=body.time_taken_s,
            input_mode=body.input_mode,
        )
    except ValueError as e:
        db.rollback()
        raise HTTPException(400, str(e)) from e
    except Exception:
        db.rollback()  # the candidate's answer is not persisted if the interviewer call fails — they can resubmit
        raise
    return _full(db, s)


@router.post("/interviews/{sid}/skip")
def skip(sid: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    s = _session(db, sid)
    try:
        interview.skip_question(db, s)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return _full(db, s)


@router.post("/interviews/{sid}/finish")
def finish(sid: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    s = _session(db, sid)
    interview.finish_session(db, get_llm(), s, get_profile(db))
    return _full(db, s)


@router.delete("/interviews/{sid}", status_code=204)
def delete_interview(sid: int, db: Session = Depends(get_db)) -> Response:
    db.delete(_session(db, sid))
    db.commit()
    return Response(status_code=204)
