"""Title / seniority / location filtering for discovered postings."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from ..config import get_settings


@dataclass
class FilterConfig:
    title_include: list[str]
    title_exclude: list[str]
    title_weak_include: list[str]
    role_words: list[str]
    require_role_word: bool
    allow_unleveled: bool
    seniority_include: list[str]
    seniority_exclude: list[str]
    location_tags: dict[str, list[str]]
    allow_unqualified_remote: bool
    us_markers: list[str]
    non_us_markers: list[str]

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> FilterConfig:
        t = d.get("title", {}) or {}
        s = d.get("seniority", {}) or {}
        loc = d.get("location", {}) or {}
        rem = loc.get("remote", {}) or {}
        return cls(
            title_include=[x.lower() for x in t.get("include", [])],
            title_exclude=[x.lower() for x in t.get("exclude", [])],
            title_weak_include=[x.lower() for x in t.get("weak_include", [])],
            role_words=[x.lower() for x in t.get("role_words", [])],
            require_role_word=bool(t.get("require_role_word", False)),
            allow_unleveled=bool(s.get("allow_unleveled", True)),
            seniority_include=[str(x).lower() for x in s.get("include", [])],
            seniority_exclude=[str(x).lower() for x in s.get("exclude", [])],
            location_tags={k: [x.lower() for x in (v or [])] for k, v in (loc.get("tags", {}) or {}).items()},
            allow_unqualified_remote=bool(rem.get("allow_unqualified_remote", True)),
            us_markers=[x.lower() for x in rem.get("us_markers", [])],
            non_us_markers=[x.lower() for x in rem.get("non_us_markers", [])],
        )


def load_filter_config(path: Path | None = None) -> FilterConfig:
    path = path or get_settings().filters_file
    with open(path, encoding="utf-8") as f:
        return FilterConfig.from_dict(yaml.safe_load(f) or {})


@lru_cache(maxsize=4096)
def _pattern(term: str) -> re.Pattern[str]:
    # word-boundary match that tolerates punctuation inside the term (e.g. "ai/ml", "u.s.")
    return re.compile(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])")


def contains_term(text: str, term: str) -> bool:
    return bool(_pattern(term).search(text))


def _any(text: str, terms: list[str]) -> str | None:
    for t in terms:
        if contains_term(text, t):
            return t
    return None


_ROMAN_LEVEL = re.compile(r"(?<![a-z0-9])(i{1,3}|iv|v)\s*$")


@dataclass
class FilterResult:
    passed: bool
    title_ok: bool
    seniority_ok: bool
    location_ok: bool
    seniority: str
    location_tags: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)


def classify_seniority(title: str, cfg: FilterConfig) -> str:
    t = title.lower()
    m = _ROMAN_LEVEL.search(t.split(",")[0].strip())
    if m and m.group(1) in ("i", "ii"):
        return "junior" if m.group(1) == "i" else "mid"
    if _any(t, cfg.seniority_exclude):
        return "junior"
    if contains_term(t, "principal") or contains_term(t, "distinguished"):
        return "principal"
    if contains_term(t, "staff") and not contains_term(t, "member of technical staff"):
        return "staff"
    if _any(t, cfg.seniority_include):
        return "senior"
    if m and m.group(1) in ("iii", "iv", "v"):
        return "senior"
    return "unleveled"


def match_location(locations: list[str], is_remote: bool, cfg: FilterConfig) -> tuple[bool, list[str], str]:
    """Return (ok, tags, reason)."""
    tags: list[str] = []
    joined = " | ".join(locations).lower()
    for tag, terms in cfg.location_tags.items():
        if terms and _any(joined, terms):
            tags.append(tag)
    remote_mentioned = is_remote or "remote" in joined
    if remote_mentioned:
        remote_parts = [p.lower() for p in locations if "remote" in p.lower()] or [joined]
        us_ok = False
        for part in remote_parts:
            has_us = _any(part, cfg.us_markers) is not None
            has_non_us = _any(part, cfg.non_us_markers) is not None
            if has_us or (not has_non_us and cfg.allow_unqualified_remote):
                us_ok = True
                break
        if us_ok:
            tags.append("remote_us")
    if not locations and not is_remote:
        return False, [], "no location listed"
    if tags:
        return True, sorted(set(tags)), ""
    return False, [], f"location not in target set ({', '.join(locations)[:120]})"


def evaluate(
    title: str,
    locations: list[str],
    is_remote: bool,
    cfg: FilterConfig,
) -> FilterResult:
    t = title.lower()
    reasons: list[str] = []
    has_role = _any(t, cfg.role_words) is not None
    inc = _any(t, cfg.title_include)
    if inc is None and has_role and _any(t, cfg.title_weak_include):
        inc = "weak+role"
    if inc is not None and cfg.require_role_word and not has_role:
        reasons.append("title is not an engineering role")
        inc = None
    exc = _any(t, cfg.title_exclude)
    title_ok = inc is not None and exc is None
    if inc is None and not reasons:
        reasons.append("title has no target keyword")
    if exc is not None:
        reasons.append(f"title excluded by '{exc}'")

    seniority = classify_seniority(title, cfg)
    seniority_ok = seniority in ("senior", "staff", "principal") or (seniority == "unleveled" and cfg.allow_unleveled)
    if not seniority_ok:
        reasons.append(f"seniority '{seniority}' not targeted")

    loc_ok, tags, loc_reason = match_location(locations, is_remote, cfg)
    if not loc_ok:
        reasons.append(loc_reason)

    return FilterResult(
        passed=title_ok and seniority_ok and loc_ok,
        title_ok=title_ok,
        seniority_ok=seniority_ok,
        location_ok=loc_ok,
        seniority=seniority,
        location_tags=tags,
        reasons=reasons,
    )
