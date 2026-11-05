import pytest
from pydantic import ValidationError

from app.llm.schemas import (
    ApplicationKitOut,
    FitScoreOut,
    InterviewerDecision,
    JDParse,
    ScorecardOut,
    StarStoryOut,
    StructuredResume,
    TailoredResumeOut,
    novel_numbers,
    verdict_for_score,
)

GOOD_FIT = {
    "score": 82,
    "strengths": ["Built supervisor-subagent system in prod", "A2A CRM write service", "12+ years ML"],
    "gaps": ["No stated fine-tuning at scale", "No TypeScript", "No public evals work"],
    "verdict": "Strong",
    "verdict_line": "Strong match for applied agent work.",
}


def test_fit_score_valid():
    assert FitScoreOut.model_validate(GOOD_FIT).score == 82


@pytest.mark.parametrize(
    "patch,msg",
    [
        ({"score": 101}, "less than or equal"),
        ({"strengths": ["a", "b"]}, "at least 3"),
        ({"gaps": ["a", "b", "c", "d"]}, "at most 3"),
        ({"verdict": "Maybe"}, "Strong"),
        ({"verdict": "Skip"}, "inconsistent"),
        ({"verdict_line": "word " * 50}, "short line"),
    ],
)
def test_fit_score_rejects(patch, msg):
    with pytest.raises(ValidationError, match=msg):
        FitScoreOut.model_validate({**GOOD_FIT, **patch})


def test_verdict_thresholds():
    assert [verdict_for_score(s) for s in (49, 50, 74, 75)] == ["Skip", "Stretch", "Stretch", "Strong"]


def test_jd_parse_requires_skills_and_comp_order():
    base = {
        "title_normalized": "AI Eng",
        "seniority": "senior",
        "required_skills": ["Python"],
        "nice_to_haves": [],
        "remote_policy": "hybrid",
        "role_summary": "x",
    }
    assert JDParse.model_validate(base).required_skills == ["Python"]
    with pytest.raises(ValidationError):
        JDParse.model_validate({**base, "required_skills": []})
    with pytest.raises(ValidationError):
        JDParse.model_validate({**base, "comp_min": 300000, "comp_max": 200000})


def test_novel_numbers():
    assert novel_numbers("Cut latency 40% for 3 teams", "reduced latency by 40%") == {"3"}
    assert novel_numbers("Since 2021, led 2x growth", "2x growth") == set()
    assert novel_numbers("$1.2M saved", "saved $1.2M annually") == set()


BASE = StructuredResume.model_validate(
    {
        "contact": {"name": "A"},
        "summary": "AI engineer with 12+ years.",
        "experience": [
            {
                "id": "e1",
                "company": "Workday",
                "title": "Senior AI Engineer",
                "bullets": [
                    {"id": "e1b1", "text": "Built multi-agent system used by 500 sellers"},
                    {"id": "e1b2", "text": "Sole engineer on A2A action service"},
                ],
            }
        ],
        "skills": [{"category": "Core", "items": ["Python", "LLMs", "Agents"]}],
    }
)


def tailored(
    text="Architected multi-agent system for 500 sellers",
    ids=("e1b1",),
    skills=("Agents", "Python"),
    summary="Applied AI engineer, 12+ years.",
):
    return {
        "summary": summary,
        "experience": [{"experience_id": "e1", "bullets": [{"text": text, "source_bullet_ids": list(ids)}]}],
        "skills": [{"category": "Core", "items": list(skills)}],
    }


def test_tailored_resume_grounding_ok():
    out = TailoredResumeOut.model_validate(tailored(), context={"base_resume": BASE})
    assert out.experience[0].bullets[0].source_bullet_ids == ["e1b1"]


@pytest.mark.parametrize(
    "kwargs,msg",
    [
        ({"text": "Built multi-agent system for 5000 sellers"}, "introduces numbers"),
        ({"ids": ("e9b9",)}, "not in experience"),
        ({"skills": ("Rust",)}, "not in the base resume"),
        ({"summary": "Engineer with 20 years"}, "summary introduces numbers"),
    ],
)
def test_tailored_resume_rejects_fabrication(kwargs, msg):
    with pytest.raises(ValidationError, match=msg):
        TailoredResumeOut.model_validate(tailored(**kwargs), context={"base_resume": BASE})


def _kit(words=180, li="Hi — I build production agent systems.", grounding_num=""):
    return {
        "cover_letter": ("word " * words) + grounding_num,
        "why_company": ["a", "b", "c"],
        "interview_questions": [{"question": f"q{i}", "why_they_ask": "x", "talking_points": ["a", "b"]} for i in range(5)],
        "outreach": {"linkedin_note": li, "email_subject": "Hello", "email_body": "Short note."},
    }


def test_kit_limits():
    ApplicationKitOut.model_validate(_kit())
    with pytest.raises(ValidationError, match="under 250"):
        ApplicationKitOut.model_validate(_kit(words=260))
    with pytest.raises(ValidationError, match="300"):
        ApplicationKitOut.model_validate(_kit(li="x" * 301))
    with pytest.raises(ValidationError, match="not found in the resume"):
        ApplicationKitOut.model_validate(_kit(grounding_num=" improved 73%"), context={"grounding_text": "nothing numeric"})
    bad = _kit()
    bad["interview_questions"] = bad["interview_questions"][:4]
    with pytest.raises(ValidationError):
        ApplicationKitOut.model_validate(bad)


def test_star_story_validates_ids_and_numbers():
    d = {
        "title": "t",
        "situation": "s",
        "task": "t",
        "action": "I did it",
        "result": "cut cost 30%",
        "competencies": ["ownership"],
        "question_ids": ["beh-incident"],
    }
    ctx = {"valid_question_ids": ["beh-incident"], "valid_competencies": ["ownership"], "project_text": "cost down 30%"}
    StarStoryOut.model_validate(d, context=ctx)
    with pytest.raises(ValidationError, match="unknown question_ids"):
        StarStoryOut.model_validate({**d, "question_ids": ["nope"]}, context=ctx)
    with pytest.raises(ValidationError, match="introduces numbers"):
        StarStoryOut.model_validate({**d, "result": "cut cost 45%"}, context=ctx)


def test_interviewer_decision_requires_question():
    with pytest.raises(ValidationError):
        InterviewerDecision.model_validate({"action": "follow_up"})
    assert InterviewerDecision.model_validate({"action": "next_question"}).acknowledgement == ""


def _card(indices):
    item = {"score": 7, "justification": "ok"}
    return {
        "overall_score": 70,
        "hire_signal": "lean_hire",
        "summary": "s",
        "rubric": {"technical_depth": item, "communication": item, "structure": item, "tradeoffs": item},
        "strengths": ["a"],
        "gaps": ["b"],
        "per_question": [{"question_index": i, "score": 7, "model_answer": "m"} for i in indices],
    }


def test_scorecard_requires_every_question():
    ScorecardOut.model_validate(_card([0, 1]), context={"num_questions": 2})
    with pytest.raises(ValidationError, match="exactly one entry"):
        ScorecardOut.model_validate(_card([0]), context={"num_questions": 2})
    with pytest.raises(ValidationError):
        ScorecardOut.model_validate({**_card([0]), "overall_score": 150})
