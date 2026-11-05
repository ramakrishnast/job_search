"""ATS fetcher registry."""

from __future__ import annotations

from collections.abc import Callable

from .ashby import fetch_ashby
from .base import FetchError, NormalizedPosting, PoliteClient
from .greenhouse import fetch_greenhouse
from .lever import fetch_lever

Fetcher = Callable[..., list[NormalizedPosting]]

FETCHERS: dict[str, Fetcher] = {
    "greenhouse": fetch_greenhouse,
    "lever": fetch_lever,
    "ashby": fetch_ashby,
}


def detect_ats(client: PoliteClient, slug: str) -> tuple[str, int] | None:
    """Try each public ATS for a board slug; return (ats, job_count) for the first that responds."""
    for name, fn in FETCHERS.items():
        try:
            jobs = fn(client, slug)
        except FetchError:
            continue
        return name, len(jobs)
    return None


__all__ = ["FETCHERS", "FetchError", "NormalizedPosting", "PoliteClient", "detect_ats"]
