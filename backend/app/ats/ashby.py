"""Ashby public Job Posting API — https://developers.ashbyhq.com/docs/public-job-posting-api

GET https://api.ashbyhq.com/posting-api/job-board/{job_board_name}?includeCompensation=true
"""

from __future__ import annotations

from typing import Any

from .base import NormalizedPosting, PoliteClient, html_to_text, parse_iso

BASE = "https://api.ashbyhq.com/posting-api/job-board"


def board_url(slug: str) -> str:
    return f"{BASE}/{slug}"


def _comp(c: dict[str, Any] | None) -> str | None:
    if not c:
        return None
    return c.get("scrapeableCompensationSalarySummary") or c.get("compensationTierSummary") or None


def parse_ashby(payload: dict[str, Any]) -> list[NormalizedPosting]:
    out: list[NormalizedPosting] = []
    for j in payload.get("jobs", []) or []:
        if j.get("isListed") is False:
            continue
        loc = j.get("location")
        locations = [loc] if loc else []
        for sl in j.get("secondaryLocations") or []:
            name = sl.get("location") if isinstance(sl, dict) else sl
            if name and name not in locations:
                locations.append(name)
        workplace = (j.get("workplaceType") or "").lower() or None
        if workplace == "onsite" or workplace == "on-site":
            workplace = "onsite"
        is_remote = bool(j.get("isRemote")) or workplace == "remote"
        desc_html = j.get("descriptionHtml") or ""
        text = j.get("descriptionPlain") or html_to_text(desc_html)
        out.append(
            NormalizedPosting(
                external_id=str(j.get("id") or j.get("jobUrl")),
                title=(j.get("title") or "").strip(),
                location=loc,
                locations=locations,
                departments=[d for d in (j.get("department"), j.get("team")) if d],
                url=j.get("jobUrl"),
                apply_url=j.get("applyUrl") or j.get("jobUrl"),
                description_html=desc_html,
                description_text=text.strip(),
                posted_at=parse_iso(j.get("publishedAt")),
                updated_at=parse_iso(j.get("updatedAt")) if j.get("updatedAt") else None,
                workplace_type=workplace,
                is_remote=is_remote,
                comp_text=_comp(j.get("compensation")),
            )
        )
    return out


def fetch_ashby(client: PoliteClient, slug: str, **_: Any) -> list[NormalizedPosting]:
    payload = client.get_json(board_url(slug), params={"includeCompensation": "true"})
    return parse_ashby(payload)
