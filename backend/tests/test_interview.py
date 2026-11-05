import base64
from datetime import timedelta

import pytest

from app.db import utcnow
from app.models import Question, ReviewState
from app.services import interview
from app.services.profile import get_profile
from app.services.spaced_rep import quality_from_score, update_review

FOLLOW = {
    "action": "follow_up",
    "acknowledgement": "Got it.",
    "follow_up_question": "How do you enforce ACLs at retrieval time?",
    "probe_reason": "security",
}
NEXT = {"action": "next_question", "acknowledgement": "Okay."}


def card(n, score=4):
    item = {"score": 6, "justification": "j"}
    return {
        "overall_score": 61,
        "hire_signal": "lean_hire",
        "summary": "Solid but shallow on evals.",
        "rubric": {"technical_depth": item, "communication": {"score": 8, "justification": "clear"}, "structure": item, "tradeoffs": item},
        "strengths": ["clear structure"],
        "gaps": ["evals"],
        "per_question": [
            {"question_index": i, "score": score, "went_well": [], "to_improve": ["x"], "model_answer": "Strong answer..."}
            for i in range(n)
        ],
        "topics_to_review": ["evals"],
    }


def test_full_session_flow(fake_llm, db):
    p = fake_llm({"interview_turn": [FOLLOW, NEXT, NEXT], "scorecard": [card(2)]})
    from app.llm.client import get_llm

    prof = get_profile(db)
    s = interview.start_session(db, get_llm(), kind="system_design", profile=prof, num_questions=2, max_followups=2)
    assert len(s.plan) == 2 and s.turns[0].kind == "question" and "Question 1 of 2" in s.turns[0].content

    png = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * 64).decode()
    t = interview.submit_answer(
        db,
        get_llm(),
        s,
        prof,
        text="I'd use hybrid retrieval...",
        whiteboard_png_b64="data:image/png;base64," + png,
        time_taken_s=95,
        input_mode="voice",
    )
    assert t[0].kind == "follow_up" and "ACLs" in t[0].content
    # whiteboard image is sent to Claude as an image block
    req = p.calls[-1]
    assert req.messages[-1]["content"][0]["type"] == "image"
    assert s.followups_on_current == 1

    t = interview.submit_answer(db, get_llm(), s, prof, text="Filter by ACL ids in the index.")
    assert t[0].kind == "question" and s.current_index == 1 and "Question 2 of 2" in t[0].content
    t = interview.submit_answer(db, get_llm(), s, prof, text="Answer 2")
    assert t[0].kind == "wrap_up"
    with pytest.raises(ValueError):
        interview.submit_answer(db, get_llm(), s, prof, text="extra")

    s = interview.finish_session(db, get_llm(), s, prof)
    assert s.status == "completed" and s.overall_score == 61 and s.communication == 8
    assert len(s.scorecard["per_question"]) == 2
    reviews = db.query(ReviewState).all()
    assert len(reviews) == 2 and all(r.lapses == 1 for r in reviews)  # score 4 → quality 2 → lapse
    from app.models import LLMCall

    db.expire_all()
    assert db.query(LLMCall).count() == len(p.calls)  # usage logging never blocked by the open answer txn
    sc_req = next(c for c in p.calls if c.task == "scorecard")
    assert any(b.get("type") == "image" for b in sc_req.messages[-1]["content"])


def test_max_followups_enforced_without_llm(fake_llm, db):
    p = fake_llm({"interview_turn": [FOLLOW]})
    from app.llm.client import get_llm

    prof = get_profile(db)
    s = interview.start_session(db, get_llm(), kind="ml_fundamentals", profile=prof, num_questions=2, max_followups=1)
    interview.submit_answer(db, get_llm(), s, prof, text="a")
    t = interview.submit_answer(db, get_llm(), s, prof, text="b")
    assert t[0].kind == "question" and p.count("interview_turn") == 1


def test_skip_and_abandon(db):
    from app.llm.client import get_llm

    prof = get_profile(db)
    s = interview.start_session(db, get_llm(), kind="behavioral", profile=prof, num_questions=1)
    interview.skip_question(db, s)
    s = interview.finish_session(db, get_llm(), s, prof)
    assert s.status == "abandoned"


def test_company_loop_creates_job_questions(fake_llm, db):
    from app.models import Company, Job

    loop = {
        "focus_summary": "agents",
        "questions": [
            {
                "category": "system_design",
                "topic": "agents",
                "difficulty": 3,
                "prompt": "Design agent tool calling for CRM",
                "why_asked": "core",
            },
            {
                "category": "behavioral",
                "topic": "ownership",
                "difficulty": 2,
                "prompt": "Tell me about owning prod",
                "why_asked": "culture",
            },
            {"category": "ml_fundamentals", "topic": "evals", "difficulty": 2, "prompt": "How do you eval agents?", "why_asked": "gap"},
        ],
    }
    fake_llm({"company_loop": [loop]})
    from app.llm.client import get_llm

    c = Company(name="Acme", ats="manual")
    db.add(c)
    db.flush()
    j = Job(company_id=c.id, source="manual", external_id="m1", title="FDE", description_text="Agents for CRM", content_hash="x")
    db.add(j)
    db.commit()
    s = interview.start_session(db, get_llm(), kind="company", profile=get_profile(db), job_id=j.id, num_questions=3)
    qs = [db.get(Question, i) for i in s.plan]
    assert [q.source for q in qs] == ["job"] * 3 and qs[0].job_id == j.id


def test_selection_prefers_due_questions(db):
    from app.services.questions import select_questions

    qs = db.query(Question).filter_by(category="ml_fundamentals").all()
    target = qs[-1]
    db.add(ReviewState(question_id=target.id, due_at=utcnow() - timedelta(days=1), last_score=8))
    db.commit()
    picked = select_questions(db, "ml_fundamentals", 3)
    assert picked[0].id == target.id and len(picked) == 3


def test_sm2_progression():
    st = ReviewState(ease=2.5, interval_days=0, repetitions=0, lapses=0, attempts=0)
    now = utcnow()
    update_review(st, 9, now)
    assert st.interval_days == 2 and st.repetitions == 1
    update_review(st, 8, now)
    assert st.interval_days == 6
    update_review(st, 10, now)
    assert st.interval_days > 6 and st.due_at > now
    ease = st.ease
    update_review(st, 2, now)
    assert st.repetitions == 0 and st.interval_days == 1 and st.lapses == 1 and st.ease < ease
    assert [quality_from_score(x) for x in (0, 5, 10)] == [0, 2, 5]
