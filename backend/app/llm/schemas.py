"""Pydantic schemas for every structured LLM output.

Each schema is used three ways:
  1. its JSON schema is sent to Claude (structured outputs + in-prompt),
  2. the response is validated against it (with optional validation *context*
     for cross-checks such as "no invented numbers"),
  3. on failure the validation errors are fed back to the model for a retry.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator

_NUM_RE = re.compile(r"(?<![A-Za-z])\$?\d[\d,]*(?:\.\d+)?\s?(?:%|x|k|K|M|B|\+)?")


def extract_numbers(text: str) -> set[str]:
    """Return normalized numeric tokens found in text (e.g. '40%', '3x', '10M', '12')."""
    out: set[str] = set()
    for m in _NUM_RE.finditer(text or ""):
        tok = m.group(0).replace(",", "").replace("$", "").replace(" ", "").lower()
        tok = tok.rstrip("+")
        if tok:
            out.add(tok)
            # also add the bare number so "40" in source matches "40%" in output and vice versa
            bare = re.sub(r"[^\d.]", "", tok)
            if bare:
                out.add(bare)
    return out


def novel_numbers(candidate: str, source: str) -> set[str]:
    """Numbers present in candidate text but absent from source text.

    Small integers (<= 12, e.g. "2 teams", "3 engineers") are commonly paraphrased
    numerals and are still checked — fabricated scale is exactly what we guard against.
    Years (19xx/20xx) are ignored as they are usually dates copied from the source context.
    """
    src = extract_numbers(source)
    found: set[str] = set()
    for tok in extract_numbers(candidate):
        bare = re.sub(r"[^\d.]", "", tok)
        if not bare or bare in src or tok in src:
            continue
        if re.fullmatch(r"(19|20)\d\d", bare):
            continue
        found.add(tok)
    return found


class _Strict(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


# --------------------------------------------------------------------- JD parse


class JDParse(_Strict):
    title_normalized: str = Field(description="Role title without location/team noise")
    seniority: Literal["intern", "junior", "mid", "senior", "staff", "principal", "manager", "unspecified"]
    required_skills: list[str] = Field(description="Hard requirements stated in the JD, short phrases")
    nice_to_haves: list[str] = Field(description="Preferred / bonus qualifications")
    years_experience_min: int | None = Field(default=None, description="Minimum years if stated, else null")
    location: str | None = None
    remote_policy: Literal["remote", "hybrid", "onsite", "unspecified"]
    comp_min: int | None = Field(default=None, description="Annual base salary lower bound in USD if stated")
    comp_max: int | None = Field(default=None, description="Annual base salary upper bound in USD if stated")
    comp_currency: str | None = None
    comp_raw: str | None = Field(default=None, description="Compensation text exactly as written in the JD")
    responsibilities: list[str] = Field(default_factory=list, description="Key responsibilities, max 6")
    role_summary: str = Field(description="One or two sentences describing the role")

    @field_validator("required_skills")
    @classmethod
    def _need_skills(cls, v: list[str]) -> list[str]:
        v = [s for s in (x.strip() for x in v) if s]
        if not v:
            raise ValueError("required_skills must list at least one requirement from the JD")
        return v[:25]

    @field_validator("nice_to_haves", "responsibilities")
    @classmethod
    def _trim(cls, v: list[str]) -> list[str]:
        return [s for s in (x.strip() for x in v) if s][:25]

    @model_validator(mode="after")
    def _comp_order(self) -> JDParse:
        if self.comp_min is not None and self.comp_max is not None and self.comp_min > self.comp_max:
            raise ValueError("comp_min must be <= comp_max")
        return self


# --------------------------------------------------------------------- Fit score

Verdict = Literal["Strong", "Stretch", "Skip"]


class FitScoreOut(_Strict):
    score: int = Field(ge=0, le=100, description="0-100 fit score")
    strengths: list[str] = Field(min_length=3, max_length=3, description="Top 3 matching strengths, each citing real resume evidence")
    gaps: list[str] = Field(min_length=3, max_length=3, description="Top 3 honest gaps vs the JD")
    verdict: Verdict
    verdict_line: str = Field(description="One-line verdict, max ~25 words")

    @model_validator(mode="after")
    def _verdict_matches_score(self) -> FitScoreOut:
        expected = verdict_for_score(self.score)
        if self.verdict != expected:
            raise ValueError(
                f"verdict '{self.verdict}' inconsistent with score {self.score}; "
                f"use Strong for >=75, Stretch for 50-74, Skip for <50 (expected '{expected}')"
            )
        if len(self.verdict_line.split()) > 40:
            raise ValueError("verdict_line must be a single short line (<= 40 words)")
        return self


def verdict_for_score(score: int) -> Verdict:
    if score >= 75:
        return "Strong"
    if score >= 50:
        return "Stretch"
    return "Skip"


# ------------------------------------------------------------------ Resume model


class ResumeBullet(_Strict):
    id: str
    text: str


class ResumeExperience(_Strict):
    id: str
    company: str
    title: str
    location: str | None = None
    start: str | None = None
    end: str | None = None
    bullets: list[ResumeBullet] = Field(default_factory=list)


class ResumeEducation(_Strict):
    school: str
    degree: str | None = None
    field: str | None = None
    year: str | None = None


class SkillGroup(_Strict):
    category: str
    items: list[str]


class ResumeContact(_Strict):
    name: str = ""
    email: str | None = None
    phone: str | None = None
    location: str | None = None
    links: list[str] = Field(default_factory=list)


class StructuredResume(_Strict):
    contact: ResumeContact = Field(default_factory=ResumeContact)
    summary: str = ""
    experience: list[ResumeExperience] = Field(default_factory=list)
    education: list[ResumeEducation] = Field(default_factory=list)
    skills: list[SkillGroup] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_ids(self) -> StructuredResume:
        exp_ids = [e.id for e in self.experience]
        if len(exp_ids) != len(set(exp_ids)):
            raise ValueError("experience ids must be unique")
        b_ids = [b.id for e in self.experience for b in e.bullets]
        if len(b_ids) != len(set(b_ids)):
            raise ValueError("bullet ids must be unique across the resume")
        return self

    def all_text(self) -> str:
        parts = [self.summary]
        for e in self.experience:
            parts += [e.company, e.title, *(b.text for b in e.bullets)]
        parts += [s for g in self.skills for s in g.items]
        parts += [f"{ed.school} {ed.degree or ''} {ed.field or ''}" for ed in self.education]
        return "\n".join(parts)


class ResumeParseOut(StructuredResume):
    """LLM output when structuring pasted resume text. Bullets must come from the source."""

    @model_validator(mode="after")
    def _no_invention(self, info: ValidationInfo) -> ResumeParseOut:
        ctx = info.context or {}
        source = ctx.get("resume_text")
        if source:
            invented = novel_numbers(self.all_text(), source)
            if invented:
                raise ValueError(f"numbers {sorted(invented)} do not appear in the resume text; copy bullets verbatim")
        return self


# -------------------------------------------------------------- Tailored resume


class TailoredBullet(_Strict):
    text: str
    source_bullet_ids: list[str] = Field(min_length=1, description="ids of the original bullets this reframes")


class TailoredExperience(_Strict):
    experience_id: str
    bullets: list[TailoredBullet]


class TailoredResumeOut(_Strict):
    summary: str = Field(description="2-3 sentence professional summary aimed at this JD")
    experience: list[TailoredExperience]
    skills: list[SkillGroup] = Field(description="Re-ordered skill groups; only skills from the base resume")
    keywords_covered: list[str] = Field(default_factory=list, description="JD keywords now addressed")
    notes: str = Field(default="", description="What was emphasized and why (for the user)")

    @model_validator(mode="after")
    def _grounded(self, info: ValidationInfo) -> TailoredResumeOut:
        ctx = info.context or {}
        base: StructuredResume | None = ctx.get("base_resume")
        if base is None:
            return self
        exp_map = {e.id: e for e in base.experience}
        errors: list[str] = []
        for te in self.experience:
            exp = exp_map.get(te.experience_id)
            if exp is None:
                errors.append(f"unknown experience_id '{te.experience_id}'")
                continue
            src_bullets = {b.id: b.text for b in exp.bullets}
            for tb in te.bullets:
                bad = [i for i in tb.source_bullet_ids if i not in src_bullets]
                if bad:
                    errors.append(f"bullet cites ids {bad} not in experience '{exp.id}'")
                    continue
                src_text = " ".join(src_bullets[i] for i in tb.source_bullet_ids)
                invented = novel_numbers(tb.text, src_text)
                if invented:
                    errors.append(f"bullet '{tb.text[:60]}...' introduces numbers {sorted(invented)} not present in its source bullets")
        base_skills = {s.lower() for g in base.skills for s in g.items}
        if base_skills:
            extra = [s for g in self.skills for s in g.items if s.lower() not in base_skills]
            if extra:
                errors.append(f"skills {extra[:8]} are not in the base resume; only reorder existing skills")
        invented_summary = novel_numbers(self.summary, base.all_text() + " " + ctx.get("resume_text", ""))
        if invented_summary:
            errors.append(f"summary introduces numbers {sorted(invented_summary)} not in the resume")
        if errors:
            raise ValueError("; ".join(errors))
        return self


# ------------------------------------------------------------- Application kit


class InterviewPrepQuestion(_Strict):
    question: str
    why_they_ask: str
    talking_points: list[str] = Field(min_length=2, max_length=5)


class Outreach(_Strict):
    linkedin_note: str = Field(description="LinkedIn connection note, max 300 characters")
    email_subject: str
    email_body: str = Field(description="Short email, max 150 words")

    @field_validator("linkedin_note")
    @classmethod
    def _li_len(cls, v: str) -> str:
        if len(v) > 300:
            raise ValueError(f"linkedin_note is {len(v)} chars; must be <= 300")
        return v

    @field_validator("email_body")
    @classmethod
    def _email_len(cls, v: str) -> str:
        if len(v.split()) > 150:
            raise ValueError("email_body must be <= 150 words")
        return v


class ApplicationKitOut(_Strict):
    cover_letter: str = Field(description="Under 250 words, specific to company and role")
    why_company: list[str] = Field(min_length=3, max_length=6)
    interview_questions: list[InterviewPrepQuestion] = Field(min_length=5, max_length=5)
    outreach: Outreach

    @field_validator("cover_letter")
    @classmethod
    def _cl_len(cls, v: str) -> str:
        n = len(v.split())
        if n >= 250:
            raise ValueError(f"cover_letter is {n} words; must be under 250")
        if n < 80:
            raise ValueError(f"cover_letter is only {n} words; write 150-240 words")
        return v

    @model_validator(mode="after")
    def _grounded(self, info: ValidationInfo) -> ApplicationKitOut:
        ctx = info.context or {}
        source = ctx.get("grounding_text")
        if source:
            invented = novel_numbers(self.cover_letter, source)
            if invented:
                raise ValueError(f"cover_letter cites numbers {sorted(invented)} not found in the resume or JD")
        return self


# --------------------------------------------------------------- Story bank


class StarStoryOut(_Strict):
    title: str
    situation: str
    task: str
    action: str
    result: str
    competencies: list[str] = Field(min_length=1, max_length=6)
    question_ids: list[str] = Field(min_length=1, description="ids of behavioral questions this story answers")

    @model_validator(mode="after")
    def _check(self, info: ValidationInfo) -> StarStoryOut:
        ctx = info.context or {}
        valid_q: set[str] = set(ctx.get("valid_question_ids", []))
        valid_c: set[str] = set(ctx.get("valid_competencies", []))
        errors = []
        if valid_q:
            bad = [q for q in self.question_ids if q not in valid_q]
            if bad:
                errors.append(f"unknown question_ids {bad}; choose only from the provided list")
        if valid_c:
            bad_c = [c for c in self.competencies if c not in valid_c]
            if bad_c:
                errors.append(f"unknown competencies {bad_c}; choose only from the provided list")
        source = ctx.get("project_text")
        if source:
            text = " ".join([self.situation, self.task, self.action, self.result])
            invented = novel_numbers(text, source)
            if invented:
                errors.append(f"story introduces numbers {sorted(invented)} not in the project notes")
        if errors:
            raise ValueError("; ".join(errors))
        return self


# --------------------------------------------------------------- Interview


class InterviewerDecision(_Strict):
    action: Literal["follow_up", "next_question"]
    acknowledgement: str = Field(default="", description="Brief neutral acknowledgement (<= 1 sentence). Never reveal the answer.")
    follow_up_question: str | None = Field(default=None, description="Required when action is follow_up: one probing question")
    probe_reason: str = Field(default="", description="Internal note: what gap or depth you are probing")

    @model_validator(mode="after")
    def _needs_q(self) -> InterviewerDecision:
        if self.action == "follow_up" and not (self.follow_up_question or "").strip():
            raise ValueError("follow_up_question is required when action is follow_up")
        return self


class RubricItem(_Strict):
    score: int = Field(ge=1, le=10)
    justification: str


class Rubric(_Strict):
    technical_depth: RubricItem
    communication: RubricItem
    structure: RubricItem
    tradeoffs: RubricItem


class QuestionFeedback(_Strict):
    question_index: int
    score: int = Field(ge=0, le=10)
    went_well: list[str] = Field(default_factory=list)
    to_improve: list[str] = Field(default_factory=list)
    model_answer: str = Field(description="A strong senior-level answer, concise, structured")


class ScorecardOut(_Strict):
    overall_score: int = Field(ge=0, le=100)
    hire_signal: Literal["strong_hire", "hire", "lean_hire", "lean_no_hire", "no_hire"]
    summary: str
    rubric: Rubric
    strengths: list[str] = Field(min_length=1, max_length=5)
    gaps: list[str] = Field(min_length=1, max_length=5)
    per_question: list[QuestionFeedback]
    topics_to_review: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _all_questions(self, info: ValidationInfo) -> ScorecardOut:
        ctx = info.context or {}
        n = ctx.get("num_questions")
        if n is not None:
            got = sorted({q.question_index for q in self.per_question})
            if got != list(range(n)):
                raise ValueError(f"per_question must contain exactly one entry for each question_index 0..{n - 1}; got {got}")
        return self


class LoopQuestion(_Strict):
    category: Literal["system_design", "ml_fundamentals", "behavioral", "coding"]
    topic: str
    difficulty: int = Field(ge=1, le=3)
    prompt: str
    why_asked: str
    rubric_hints: list[str] = Field(default_factory=list, max_length=6)


class CompanyLoopOut(_Strict):
    focus_summary: str
    questions: list[LoopQuestion] = Field(min_length=3, max_length=8)


def schema_for_prompt(model: type[BaseModel]) -> dict[str, Any]:
    return model.model_json_schema()
