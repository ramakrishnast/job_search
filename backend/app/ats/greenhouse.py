"""Greenhouse Job Board API — https://developers.greenhouse.io/job-board.html

GET https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs?content=true
"""

from __future__ import annotations

from typing import Any

from .base import NormalizedPosting, PoliteClient, html_to_text, parse_iso

BASE = "https://boards-api.greenhouse.io/v1/boards"


def board_url(slug: str) -> str:
    return f"{BASE}/{slug}/jobs"


def parse_greenhouse(payload: dict[str, Any]) -> list[NormalizedPosting]:
    out: list[NormalizedPosting] = []
    for j in payload.get("jobs", []) or []:
        loc = (j.get("location") or {}).get("name")
        offices = [o.get("name") for o in j.get("offices") or [] if o.get("name")]
        locations = [x.strip() for x in (loc or "").replace(";", "|").split("|") if x.strip()]
        for o in offices:
            if o not in locations:
                locations.append(o)
        desc_html = j.get("content") or ""
        text = html_to_text(desc_html)
        lower_loc = (loc or "").lower()
        is_remote = "remote" in lower_loc
        comp = None
        for pr in j.get("pay_input_ranges") or []:
            lo, hi, cur = pr.get("min_cents"), pr.get("max_cents"), pr.get("currency_type", "USD")
            if lo and hi:
                comp = f"{cur} {lo // 100:,}–{hi // 100:,}"
                break
        out.append(
            NormalizedPosting(
                external_id=str(j["id"]),
                title=(j.get("title") or "").strip(),
                location=loc,
                locations=locations,
                departments=[d.get("name") for d in j.get("departments") or [] if d.get("name")],
                url=j.get("absolute_url"),
                apply_url=j.get("absolute_url"),
                description_html=desc_html,
                description_text=text,
                posted_at=parse_iso(j.get("first_published")) or parse_iso(j.get("updated_at")),
                updated_at=parse_iso(j.get("updated_at")),
                workplace_type="remote" if is_remote else None,
                is_remote=is_remote,
                comp_text=comp,
            )
        )
    return out


def fetch_greenhouse(client: PoliteClient, slug: str, **_: Any) -> list[NormalizedPosting]:
    payload = client.get_json(board_url(slug), params={"content": "true"})
    return parse_greenhouse(payload)
