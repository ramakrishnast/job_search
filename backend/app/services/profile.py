"""Profile + resume handling: seeding, hashing, heuristic and LLM structuring."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any

import yaml
from sqlalchemy.orm import Session

from ..config import get_settings
from ..llm.client import LLMClient
from ..llm.schemas import ResumeParseOut, StructuredResume
from ..models import Profile

log = logging.getLogger(__name__)


def load_default_profile() -> dict[str, Any]:
    s = get_settings()
    for path in (s.profile_file, s.profile_example_file):
        if path.exists():
            with open(path, encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
    return {}


def get_profile(db: Session) -> Profile:
    p = db.get(Profile, 1)
    if p is None:
        d = load_default_profile()
        p = Profile(
            id=1,
            name=d.get("name") or "",
            headline=d.get("headline") or "",
            email=d.get("email") or None,
            phone=d.get("phone") or None,
            location=d.get("location") or None,
            links=d.get("links") or [],
            summary=d.get("summary") or "",
            highlights=d.get("highlights") or [],
            target_roles=d.get("target_roles") or [],
            preferences=d.get("preferences") or {},
        )
        db.add(p)
        db.commit()
        db.refresh(p)
    return p


def profile_hash(p: Profile) -> str:
    payload = json.dumps(
        {
            "summary": p.summary,
            "highlights": p.highlights,
            "resume_text": p.resume_text,
            "structured": p.structured_resume,
            "targets": p.target_roles,
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def profile_as_text(p: Profile) -> str:
    """Canonical text of the candidate's background used in every prompt (stable → cacheable)."""
    parts = [f"Name: {p.name or 'Candidate'}", f"Headline: {p.headline}", f"Location: {p.location or ''}"]
    if p.preferences:
        parts.append(f"Preferences: {json.dumps(p.preferences, sort_keys=True)}")
    if p.target_roles:
        parts.append("Target roles: " + "; ".join(p.target_roles))
    parts.append(f"Summary: {p.summary}")
    if p.highlights:
        parts.append("Key facts:\n" + "\n".join(f"- {h}" for h in p.highlights))
    if p.resume_text.strip():
        parts.append("FULL RESUME (authoritative source of experience):\n" + p.resume_text.strip())
    elif p.structured_resume:
        parts.append("RESUME:\n" + structured_to_text(StructuredResume.model_validate(p.structured_resume)))
    return "\n\n".join(parts)


def structured_to_text(r: StructuredResume) -> str:
    lines = [r.contact.name, r.summary, ""]
    for e in r.experience:
        lines.append(f"[{e.id}] {e.title} — {e.company} ({e.start or ''} – {e.end or ''})")
        for b in e.bullets:
            lines.append(f"  ({b.id}) {b.text}")
    for ed in r.education:
        lines.append(f"Education: {ed.degree or ''} {ed.field or ''}, {ed.school} {ed.year or ''}")
    for g in r.skills:
        lines.append(f"Skills — {g.category}: {', '.join(g.items)}")
    return "\n".join(lines)


def base_resume(p: Profile) -> StructuredResume:
    """Structured resume to tailor/export; falls back to one built from profile highlights."""
    if p.structured_resume:
        return StructuredResume.model_validate(p.structured_resume)
    return StructuredResume.model_validate(
        {
            "contact": {"name": p.name, "email": p.email, "phone": p.phone, "location": p.location, "links": p.links},
            "summary": p.summary,
            "experience": [
                {
                    "id": "e1",
                    "company": "",
                    "title": p.headline or "Experience highlights",
                    "bullets": [{"id": f"e1b{i + 1}", "text": h} for i, h in enumerate(p.highlights)],
                }
            ]
            if p.highlights
            else [],
            "education": [],
            "skills": [],
        }
    )


# ------------------------------------------------------------ heuristic parser

_SECTION = re.compile(
    r"^\s*(professional\s+)?(summary|profile|experience|work experience|professional experience|employment|"
    r"education|skills|technical skills|core skills|certifications|projects)\s*:?\s*$",
    re.I,
)
_BULLET = re.compile(r"^\s*([•\-\*·▪◦●]|\d+\.)\s+")
_DATE = re.compile(
    r"((jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+)?(19|20)\d\d\s*[-–—to]+\s*"
    r"(((jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+)?(19|20)\d\d|present|current|now)",
    re.I,
)


def heuristic_structure(text: str, contact_name: str = "") -> StructuredResume:
    """Best-effort structuring without an LLM. Bullets are copied verbatim."""
    section = "header"
    summary: list[str] = []
    experiences: list[dict[str, Any]] = []
    education: list[dict[str, Any]] = []
    skills: list[dict[str, Any]] = []
    certs: list[str] = []
    pending_header: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = _SECTION.match(line)
        if m:
            key = m.group(2).lower()
            section = (
                "experience"
                if "experience" in key or key == "employment"
                else "skills"
                if "skill" in key
                else "summary"
                if key in ("summary", "profile")
                else key
            )
            pending_header = []
            continue
        is_bullet = bool(_BULLET.match(line))
        clean = _BULLET.sub("", line).strip()
        if section in ("header", "summary"):
            if section == "summary" or (section == "header" and len(line) > 80):
                summary.append(clean)
        elif section == "experience":
            if is_bullet:
                if not experiences:
                    experiences.append({"id": "e1", "company": "", "title": " ".join(pending_header), "bullets": []})
                exp = experiences[-1]
                exp["bullets"].append({"id": f"{exp['id']}b{len(exp['bullets']) + 1}", "text": clean})
                pending_header = []
            else:
                if experiences and not experiences[-1]["bullets"] and len(pending_header) < 2:
                    pass
                pending_header.append(clean)
                dm = _DATE.search(clean)
                if dm or len(pending_header) >= 2:
                    header = " | ".join(pending_header)
                    parts = [x.strip() for x in re.split(r"\s[|—–-]\s|,\s(?=[A-Z])|\t", header) if x.strip()]
                    date_txt = dm.group(0) if dm else ""
                    start, _, end = date_txt.partition("–") if "–" in date_txt else date_txt.partition("-")
                    eid = f"e{len(experiences) + 1}"
                    non_date = [x for x in parts if not _DATE.search(x)]
                    experiences.append(
                        {
                            "id": eid,
                            "title": non_date[0] if non_date else header,
                            "company": non_date[1] if len(non_date) > 1 else "",
                            "start": start.strip() or None,
                            "end": end.strip() or None,
                            "bullets": [],
                        }
                    )
                    pending_header = []
        elif section == "education":
            education.append({"school": clean})
        elif section == "skills":
            if ":" in clean:
                cat, _, items = clean.partition(":")
                skills.append({"category": cat.strip(), "items": [x.strip() for x in re.split(r"[,;|]", items) if x.strip()]})
            else:
                skills.append({"category": "Skills", "items": [x.strip() for x in re.split(r"[,;|]", clean) if x.strip()]})
        elif section == "certifications":
            certs.append(clean)
    experiences = [e for e in experiences if e["bullets"] or e["company"]]
    return StructuredResume.model_validate(
        {
            "contact": {"name": contact_name},
            "summary": " ".join(summary)[:1500],
            "experience": experiences,
            "education": education,
            "skills": skills,
            "certifications": certs,
        }
    )


RESUME_SYSTEM = """You convert a pasted resume into structured JSON.
Rules:
- Copy every bullet VERBATIM (fix only whitespace). Never add, merge, embellish, or invent content.
- Assign ids: experiences e1, e2, ... (most recent first); bullets e1b1, e1b2, ...
- Keep dates as written (e.g. "Dec 2021", "Present").
- Skills: group as they appear in the resume; do not add skills that are not written there."""


def structure_resume(db: Session, llm: LLMClient, p: Profile) -> tuple[StructuredResume, str]:
    """Structure p.resume_text. Returns (resume, method)."""
    text = p.resume_text.strip()
    if not text:
        return base_resume(p), "heuristic"
    if llm.configured:
        out = llm.generate(
            task="resume_parse",
            schema=ResumeParseOut,
            system=RESUME_SYSTEM,
            user=f"RESUME TEXT:\n<<<\n{text}\n>>>",
            model=get_settings().llm_model_default,
            effort="low",
            max_tokens=16000,
            validation_context={"resume_text": text},
            cache_key_parts=[text],
            db=db,
        )
        r = StructuredResume.model_validate(out.model_dump())
        if not r.contact.name:
            r.contact.name = p.name
        return r, "llm"
    return heuristic_structure(text, p.name), "heuristic"
