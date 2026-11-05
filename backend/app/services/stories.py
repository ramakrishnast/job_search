"""Story bank: turn project notes into STAR stories mapped to behavioral questions."""

from __future__ import annotations

from sqlalchemy.orm import Session

from ..config import get_settings
from ..llm.client import LLMClient
from ..llm.schemas import StarStoryOut
from ..models import Story
from .questions import behavioral_catalog, competencies

STORY_SYSTEM = """You turn an engineer's project notes into a STAR interview story.
Rules:
- Use ONLY facts from the project notes. Never invent metrics, team sizes, timelines, or outcomes.
  If a result is not quantified in the notes, describe it qualitatively.
- First person, concise, spoken style. Situation+Task ~2-3 sentences, Action is the longest part and
  must make the candidate's personal contribution explicit ("I ..."), Result ~2 sentences.
- question_ids: choose every behavioral question (by id) this story can credibly answer.
- competencies: choose only from the provided list."""

PROJECT_FIELDS = ["context", "role", "actions", "outcome", "metrics", "tech", "challenges"]


def project_text(story: Story) -> str:
    p = story.project or {}
    return "\n".join(f"{k}: {p.get(k, '')}" for k in PROJECT_FIELDS if p.get(k)) or story.title


def generate_star(db: Session, llm: LLMClient, story: Story) -> Story:
    catalog = behavioral_catalog(db)
    comps = competencies()
    notes = f"title: {story.title}\n{project_text(story)}"
    q_list = "\n".join(f"- {q['key']}: {q['prompt']}" for q in catalog)
    out = llm.generate(
        task="star_story",
        schema=StarStoryOut,
        system=STORY_SYSTEM,
        user=(f"PROJECT NOTES:\n<<<\n{notes}\n>>>\n\nBEHAVIORAL QUESTIONS (id: prompt):\n{q_list}\n\nCOMPETENCIES: {', '.join(comps)}"),
        model=get_settings().llm_model_default,
        effort="medium",
        max_tokens=6000,
        validation_context={
            "valid_question_ids": [q["key"] for q in catalog],
            "valid_competencies": comps,
            "project_text": notes,
        },
        cache_key_parts=[notes, ",".join(q["key"] for q in catalog)],
        db=db,
    )
    story.situation = out.situation
    story.task = out.task
    story.action = out.action
    story.result = out.result
    story.competencies = out.competencies
    story.question_ids = out.question_ids
    story.method = "llm"
    db.commit()
    db.refresh(story)
    return story


def story_as_text(s: Story) -> str:
    return (
        f"[story {s.id}] {s.title}\nS: {s.situation}\nT: {s.task}\nA: {s.action}\nR: {s.result}\n"
        f"(answers: {', '.join(s.question_ids or [])})"
    )
