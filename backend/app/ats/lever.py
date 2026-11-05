"""Lever Postings API — https://github.com/lever/postings-api

GET https://api.lever.co/v0/postings/{site}?mode=json   (EU: api.eu.lever.co)
"""

from __future__ import annotations

from typing import Any

from .base import NormalizedPosting, PoliteClient, html_to_text, parse_iso


def board_url(slug: str, region: str | None = None) -> str:
    host = "api.eu.lever.co" if (region or "").lower() == "eu" else "api.lever.co"
    return f"https://{host}/v0/postings/{slug}"


def _salary(sr: dict[str, Any] | None) -> str | None:
    if not sr:
        return None
    lo, hi = sr.get("min"), sr.get("max")
    if lo is None and hi is None:
        return None
    cur = sr.get("currency") or ""
    interval = (sr.get("interval") or "").replace("-", " ")
    rng = f"{lo:,}–{hi:,}" if lo is not None and hi is not None else f"{lo or hi:,}"
    return f"{cur} {rng} {interval}".strip()


def parse_lever(payload: list[dict[str, Any]]) -> list[NormalizedPosting]:
    out: list[NormalizedPosting] = []
    for j in payload or []:
        cats = j.get("categories") or {}
        loc = cats.get("location")
        all_locs = [x for x in (cats.get("allLocations") or []) if x]
        locations = all_locs or ([loc] if loc else [])
        parts_html = [j.get("description") or ""]
        parts_text = [j.get("descriptionPlain") or html_to_text(j.get("description"))]
        for lst in j.get("lists") or []:
            heading = lst.get("text") or ""
            content = lst.get("content") or ""
            parts_html.append(f"<h3>{heading}</h3><ul>{content}</ul>")
            parts_text.append(f"{heading}\n{html_to_text('<ul>' + content + '</ul>')}")
        if j.get("additional") or j.get("additionalPlain"):
            parts_html.append(j.get("additional") or "")
            parts_text.append(j.get("additionalPlain") or html_to_text(j.get("additional")))
        workplace = (j.get("workplaceType") or "").lower() or None
        if workplace == "unspecified":
            workplace = None
        is_remote = workplace == "remote" or "remote" in (loc or "").lower()
        dept = [d for d in (cats.get("department"), cats.get("team")) if d]
        out.append(
            NormalizedPosting(
                external_id=str(j["id"]),
                title=(j.get("text") or "").strip(),
                location=loc,
                locations=locations,
                departments=dept,
                url=j.get("hostedUrl"),
                apply_url=j.get("applyUrl") or j.get("hostedUrl"),
                description_html="\n".join(p for p in parts_html if p),
                description_text="\n\n".join(p.strip() for p in parts_text if p and p.strip()),
                posted_at=parse_iso(j.get("createdAt")),
                updated_at=parse_iso(j.get("updatedAt")) if j.get("updatedAt") else None,
                workplace_type=workplace,
                is_remote=is_remote,
                comp_text=_salary(j.get("salaryRange")),
            )
        )
    return out


def fetch_lever(client: PoliteClient, slug: str, region: str | None = None, **_: Any) -> list[NormalizedPosting]:
    payload = client.get_json(board_url(slug, region), params={"mode": "json"})
    if not isinstance(payload, list):
        return []
    return parse_lever(payload)
