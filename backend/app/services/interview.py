"""Live mock-interview engine.

Flow: start → (interviewer asks question i) → candidate answers → Claude decides follow-up vs.
next question (bounded by max_followups) → ... → finish → scorecard + spaced-repetition update.
The server owns the plan/state machine; the model only decides how to probe and writes feedback.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import utcnow
from ..llm.client import LLMClient
from ..llm.schemas import CompanyLoopOut, InterviewerDecision, ScorecardOut
from ..models import InterviewSession, InterviewTurn, Job, Profile, Question, ReviewState, Story
from .profile import profile_as_text, profile_hash
from .questions import select_questions
from .spaced_rep import update_review
from .stories import story_as_text

log = logging.getLogger(__name__)

KIND_LABEL = {
    "system_design": "LLM system design",
    "ml_fundamentals": "ML fundamentals",
    "behavioral": "behavioral",
    "coding": "coding",
    "company": "company-specific loop",
}

INTERVIEWER_SYSTEM = """You are a senior interviewer at a top AI company running a {label} interview for a
senior applied AI / AI engineer candidate. Behave like a real, demanding but fair interviewer:
- Ask ONE thing at a time. Never answer your own question, never teach, never reveal the model answer.
- Push deeper where the answer was vague, hand-wavy, or missed key trade-offs (scale, failure modes,
  evals, cost, latency, security). For behavioral answers, probe for the candidate's personal actions,
  specifics, and measurable results; check consistency with their story bank.
- Move on (next_question) when the answer is thorough, or when further probing has low value.
- Keep acknowledgements short and neutral ("Got it." / "Okay, makes sense."). No praise inflation."""

SCORECARD_SYSTEM = """You are a calibrated interview evaluator for senior applied AI engineering roles.
Score the full transcript. Calibrate to a senior bar at a frontier AI lab / top tech company:
5/10 = meets a mid-level bar, 7/10 = solid senior, 9/10 = exceptional. Be specific and honest.
Rubric dimensions (1-10 each): technical_depth, communication, structure, tradeoffs.
per_question: one entry per question_index (0-based) in the transcript, with a concise but strong
model answer a senior candidate could give (use bullet-style lines inside the string).
Judge only what the candidate actually said or drew; do not credit things they did not say."""

LOOP_SYSTEM = """You design a realistic interview loop for a specific job, for a senior applied AI engineer candidate.
Mix categories the way this company would likely interview for this role (system design, ML fundamentals,
behavioral, coding). Make questions specific to the JD's domain and responsibilities, and include at least one
question that targets the candidate's likely gaps for this role. Questions must be answerable verbally in 10-15 min."""


# ------------------------------------------------------------------ helpers


def _q(db: Session, qid: int) -> Question:
    q = db.get(Question, qid)
    if q is None:
        raise ValueError(f"question {qid} missing")
    return q


def _add_turn(db: Session, s: InterviewSession, **kw: Any) -> InterviewTurn:
    seq = (max((t.seq for t in s.turns), default=0)) + 1
    t = InterviewTurn(session_id=s.id, seq=seq, **kw)
    db.add(t)
    s.turns.append(t)
    db.flush()
    return t


def _question_turn(db: Session, s: InterviewSession, idx: int, prefix: str = "") -> InterviewTurn:
    q = _q(db, s.plan[idx])
    total = len(s.plan)
    text = f"{prefix}{'' if not prefix else ' '}**Question {idx + 1} of {total}.** {q.prompt}"
    return _add_turn(db, s, role="interviewer", kind="question", question_index=idx, question_id=q.id, content=text)


def _save_whiteboard(session_id: int, seq: int, b64: str | None) -> str | None:
    if not b64:
        return None
    if "," in b64 and b64.strip().startswith("data:"):
        b64 = b64.split(",", 1)[1]
    try:
        raw = base64.b64decode(b64, validate=True)
    except (binascii.Error, ValueError):
        return None
    if len(raw) > 4 * 1024 * 1024 or not raw.startswith(b"\x89PNG"):
        return None
    rel = f"uploads/wb_s{session_id}_t{seq}.png"
    (get_settings().data_dir / rel).write_bytes(raw)
    return rel


def _image_block(rel: str | None) -> dict[str, Any] | None:
    if not rel:
        return None
    path = get_settings().data_dir / rel
    if not path.exists():
        return None
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png", "data": base64.b64encode(path.read_bytes()).decode()},
    }


def transcript_text(s: InterviewSession, only_index: int | None = None, extra: list[InterviewTurn] | None = None) -> str:
    lines = []
    for t in [*s.turns, *(extra or [])]:
        if only_index is not None and t.question_index != only_index:
            continue
        who = "INTERVIEWER" if t.role == "interviewer" else "CANDIDATE"
        body = t.content
        if t.code:
            body += f"\n```python\n{t.code}\n```"
        if t.code_result:
            cr = t.code_result
            body += f"\n[tests: {cr.get('passed', 0)}/{cr.get('total', 0)} passed]"
        if t.whiteboard_image:
            body += "\n[candidate attached a whiteboard diagram]"
        if t.time_taken_s:
            body += f"\n[answer time: {t.time_taken_s}s]"
        lines.append(f"[Q{t.question_index + 1}] {who} ({t.kind}): {body}")
    return "\n\n".join(lines)


def _context_blob(db: Session, s: InterviewSession, profile: Profile) -> str:
    parts = ["CANDIDATE BACKGROUND:\n" + profile_as_text(profile)]
    if s.kind in ("behavioral", "company"):
        stories = list(db.scalars(select(Story)))
        if stories:
            parts.append("CANDIDATE STORY BANK:\n" + "\n\n".join(story_as_text(x) for x in stories))
    if s.job_id:
        job = db.get(Job, s.job_id)
        if job:
            parts.append(f"TARGET JOB: {job.title} at {job.company.name}\n{job.description_text[:8000]}")
    return "\n\n".join(parts)


# ------------------------------------------------------------------ lifecycle


def build_company_loop(db: Session, llm: LLMClient, job: Job, profile: Profile, n: int) -> list[Question]:
    fit_gaps = (job.fit.data.get("gaps") if job.fit else None) or []
    out = llm.generate(
        task="company_loop",
        schema=CompanyLoopOut,
        system=LOOP_SYSTEM,
        cached_context="CANDIDATE BACKGROUND:\n" + profile_as_text(profile),
        user=(
            f"Company: {job.company.name}\nRole: {job.title}\nKnown fit gaps: {json.dumps(fit_gaps)}\n"
            f"Number of questions: {n}\n\nJD:\n<<<\n{job.description_text[:15000]}\n>>>"
        ),
        model=get_settings().llm_model_default,
        effort="medium",
        max_tokens=6000,
        cache_key_parts=[job.content_hash, profile_hash(profile), str(n)],
        db=db,
    )
    qs: list[Question] = []
    for i, lq in enumerate(out.questions[:n]):
        key = f"job{job.id}-" + hashlib.sha1(f"{job.content_hash}:{i}:{lq.prompt}".encode()).hexdigest()[:12]
        q = db.scalar(select(Question).where(Question.key == key))
        if q is None:
            q = Question(
                key=key,
                category=lq.category,
                topic=lq.topic,
                difficulty=lq.difficulty,
                prompt=lq.prompt,
                rubric_hints=lq.rubric_hints,
                follow_up_hints=[lq.why_asked],
                source="job",
                job_id=job.id,
            )
            db.add(q)
        qs.append(q)
    db.flush()
    return qs


def start_session(
    db: Session,
    llm: LLMClient,
    *,
    kind: str,
    profile: Profile,
    job_id: int | None = None,
    difficulty: int | None = None,
    num_questions: int = 3,
    timer_seconds: int | None = None,
    max_followups: int = 2,
    question_ids: list[int] | None = None,
    topic: str | None = None,
) -> InterviewSession:
    if kind not in KIND_LABEL:
        raise ValueError(f"unknown interview kind '{kind}'")
    num_questions = max(1, min(8, num_questions))
    if question_ids:
        plan_qs = [_q(db, i) for i in question_ids]
    elif kind == "company":
        if not job_id:
            raise ValueError("company interviews need a job_id")
        job = db.get(Job, job_id)
        if job is None:
            raise ValueError("job not found")
        plan_qs = build_company_loop(db, llm, job, profile, num_questions)
    else:
        plan_qs = select_questions(db, kind, num_questions, difficulty, topic)
    if not plan_qs:
        raise ValueError("no questions available for this selection")
    s = InterviewSession(
        kind=kind,
        job_id=job_id,
        difficulty=difficulty,
        plan=[q.id for q in plan_qs],
        config={"num_questions": len(plan_qs), "timer_seconds": timer_seconds, "max_followups": max(0, min(4, max_followups))},
    )
    db.add(s)
    db.flush()
    label = KIND_LABEL[kind]
    intro = (
        f"Hi! Let's do a {label} interview — {len(plan_qs)} question{'s' if len(plan_qs) > 1 else ''}. Think out loud; I'll ask follow-ups."
    )
    _question_turn(db, s, 0, prefix=intro)
    db.commit()
    db.refresh(s)
    return s


def submit_answer(
    db: Session,
    llm: LLMClient,
    s: InterviewSession,
    profile: Profile,
    *,
    text: str,
    code: str | None = None,
    code_result: dict[str, Any] | None = None,
    whiteboard_png_b64: str | None = None,
    whiteboard_scene: dict[str, Any] | None = None,
    time_taken_s: int | None = None,
    input_mode: str | None = None,
) -> list[InterviewTurn]:
    if s.status != "active":
        raise ValueError("session is not active")
    if s.current_index >= len(s.plan):
        raise ValueError("all questions answered — finish the session for a scorecard")
    if not (text or "").strip() and not code and not whiteboard_png_b64:
        raise ValueError("empty answer")
    idx = s.current_index
    q = _q(db, s.plan[idx])
    # Build the answer turn but don't write it yet: the interviewer call logs usage on its own DB
    # connection, and an open write transaction here would block it (SQLite single-writer).
    seq = (max((t.seq for t in s.turns), default=0)) + 1
    ans = InterviewTurn(
        session_id=s.id,
        seq=seq,
        role="candidate",
        kind="answer",
        question_index=idx,
        question_id=q.id,
        content=(text or "").strip(),
        code=code,
        code_result=code_result,
        whiteboard_scene=whiteboard_scene,
        time_taken_s=time_taken_s,
        input_mode=input_mode,
    )
    ans.whiteboard_image = _save_whiteboard(s.id, seq, whiteboard_png_b64)
    max_f = int(s.config.get("max_followups", 2))

    new_turns: list[InterviewTurn] = []
    if s.followups_on_current >= max_f:
        decision = InterviewerDecision(action="next_question", acknowledgement="Thanks — let's move on.")
    else:
        context = _context_blob(db, s, profile)
        user_blocks: list[dict[str, Any]] = []
        img = _image_block(ans.whiteboard_image)
        if img:
            user_blocks.append(img)
        user_blocks.append(
            {
                "type": "text",
                "text": (
                    f"CURRENT QUESTION ({idx + 1}/{len(s.plan)}, topic={q.topic}, difficulty={q.difficulty}):\n{q.prompt}\n\n"
                    f"What a strong answer covers (private): {json.dumps(q.rubric_hints)}\n"
                    f"Possible follow-ups (private): {json.dumps(q.follow_up_hints)}\n"
                    f"Follow-ups used on this question: {s.followups_on_current} of max {max_f}\n\n"
                    f"EXCHANGE SO FAR ON THIS QUESTION:\n{transcript_text(s, only_index=idx, extra=[ans])}\n\n"
                    "Decide: follow_up (one probing question) or next_question."
                ),
            }
        )
        db.rollback()  # end the read transaction before the LLM call (which may take a while)
        decision = llm.generate(
            task="interview_turn",
            schema=InterviewerDecision,
            system=INTERVIEWER_SYSTEM.format(label=KIND_LABEL[s.kind]),
            cached_context=context,
            user=user_blocks,
            model=get_settings().interview_model,
            effort="low",
            max_tokens=2000,
        )
    db.add(ans)
    s.turns.append(ans)
    db.flush()

    if decision.action == "follow_up" and decision.follow_up_question:
        s.followups_on_current += 1
        msg = " ".join(x for x in [decision.acknowledgement.strip(), decision.follow_up_question.strip()] if x)
        new_turns.append(_add_turn(db, s, role="interviewer", kind="follow_up", question_index=idx, question_id=q.id, content=msg))
    else:
        s.current_index += 1
        s.followups_on_current = 0
        ack = decision.acknowledgement.strip() or "Thanks."
        if s.current_index < len(s.plan):
            new_turns.append(_question_turn(db, s, s.current_index, prefix=ack))
        else:
            new_turns.append(
                _add_turn(
                    db,
                    s,
                    role="interviewer",
                    kind="wrap_up",
                    question_index=idx,
                    question_id=None,
                    content=f"{ack} That's all my questions — thanks! Click **Finish & score** for your scorecard.",
                )
            )
    db.commit()
    return new_turns


def skip_question(db: Session, s: InterviewSession) -> list[InterviewTurn]:
    if s.status != "active" or s.current_index >= len(s.plan):
        raise ValueError("nothing to skip")
    idx = s.current_index
    _add_turn(db, s, role="candidate", kind="answer", question_index=idx, question_id=s.plan[idx], content="(skipped)")
    s.current_index += 1
    s.followups_on_current = 0
    turns: list[InterviewTurn] = []
    if s.current_index < len(s.plan):
        turns.append(_question_turn(db, s, s.current_index, prefix="No problem, let's move on."))
    else:
        turns.append(
            _add_turn(
                db, s, role="interviewer", kind="wrap_up", question_index=idx, content="That's all my questions. Click **Finish & score**."
            )
        )
    db.commit()
    return turns


def finish_session(db: Session, llm: LLMClient, s: InterviewSession, profile: Profile) -> InterviewSession:
    if s.status == "completed" and s.scorecard:
        return s
    asked = sorted({t.question_index for t in s.turns if t.role == "candidate" and t.content != "(skipped)"} | set())
    answered_any = any(t.role == "candidate" and ((t.content and t.content != "(skipped)") or t.code) for t in s.turns)
    if not answered_any:
        s.status = "abandoned"
        s.ended_at = utcnow()
        db.commit()
        return s
    # Score every question that was presented (skipped ones score 0).
    presented = sorted({t.question_index for t in s.turns if t.role == "interviewer" and t.kind == "question"})
    n = len(presented)
    questions_text = "\n".join(f"Q{i}: {_q(db, s.plan[i]).prompt}" for i in presented)
    blocks: list[dict[str, Any]] = []
    for t in s.turns:
        img = _image_block(t.whiteboard_image)
        if img:
            blocks.append({"type": "text", "text": f"Whiteboard for question_index {t.question_index}:"})
            blocks.append(img)
    blocks.append(
        {
            "type": "text",
            "text": (
                f"Interview type: {KIND_LABEL[s.kind]}\nQuestions (question_index: prompt):\n{questions_text}\n\n"
                f"Use question_index values 0..{n - 1} in per_question.\n\nTRANSCRIPT:\n{transcript_text(s)}"
            ),
        }
    )
    card = llm.generate(
        task="scorecard",
        schema=ScorecardOut,
        system=SCORECARD_SYSTEM,
        cached_context=_context_blob(db, s, profile),
        user=blocks,
        model=get_settings().llm_model_default,
        effort="medium",
        max_tokens=16000,
        validation_context={"num_questions": n},
        db=db,
    )
    s.scorecard = card.model_dump(mode="json")
    s.overall_score = card.overall_score
    s.technical_depth = card.rubric.technical_depth.score
    s.communication = card.rubric.communication.score
    s.structure = card.rubric.structure.score
    s.tradeoffs = card.rubric.tradeoffs.score
    s.status = "completed"
    s.ended_at = utcnow()
    for fb in card.per_question:
        if fb.question_index >= len(s.plan):
            continue
        q = _q(db, s.plan[fb.question_index])
        st = q.review or ReviewState(question_id=q.id)
        if q.review is None:
            db.add(st)
            q.review = st
        update_review(st, fb.score)
    del asked
    db.commit()
    db.refresh(s)
    return s
