"""Shared ATS fetcher primitives: normalized posting model, polite HTTP client, HTML→text."""

from __future__ import annotations

import hashlib
import html
import logging
import re
import threading
import time
import urllib.robotparser
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import httpx

log = logging.getLogger(__name__)


class FetchError(Exception):
    """Raised when a board cannot be fetched (404, network, robots disallow...)."""

    def __init__(self, message: str, *, status: int | None = None, not_found: bool = False) -> None:
        super().__init__(message)
        self.status = status
        self.not_found = not_found


@dataclass
class NormalizedPosting:
    external_id: str
    title: str
    location: str | None
    locations: list[str] = field(default_factory=list)
    departments: list[str] = field(default_factory=list)
    url: str | None = None
    apply_url: str | None = None
    description_html: str | None = None
    description_text: str = ""
    posted_at: datetime | None = None
    updated_at: datetime | None = None
    workplace_type: str | None = None  # remote | hybrid | onsite
    is_remote: bool = False
    comp_text: str | None = None

    @property
    def content_hash(self) -> str:
        h = hashlib.sha256()
        for part in (self.title, self.location or "", self.description_text):
            h.update(part.encode("utf-8"))
            h.update(b"\x00")
        return h.hexdigest()


# ------------------------------------------------------------------ HTML → text

_BLOCK_TAGS = re.compile(r"</?(p|div|br|li|ul|ol|h[1-6]|tr|section|article|header|footer)[^>]*>", re.I)
_LI_OPEN = re.compile(r"<li[^>]*>", re.I)
_TAG = re.compile(r"<[^>]+>")
_SCRIPT = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.I | re.S)


def html_to_text(raw: str | None) -> str:
    """Convert (possibly entity-escaped) HTML to readable plain text."""
    if not raw:
        return ""
    s = raw
    # Greenhouse returns entity-encoded HTML (&lt;p&gt;...). Unescape until stable.
    for _ in range(3):
        u = html.unescape(s)
        if u == s:
            break
        s = u
    s = _SCRIPT.sub(" ", s)
    s = _LI_OPEN.sub("\n• ", s)
    s = _BLOCK_TAGS.sub("\n", s)
    s = _TAG.sub("", s)
    s = html.unescape(s).replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in s.splitlines()]
    out: list[str] = []
    for ln in lines:
        if ln == "•":
            continue
        if not ln and out and not out[-1]:
            continue
        out.append(ln)
    return "\n".join(out).strip()


def parse_iso(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        # epoch milliseconds (Lever)
        return datetime.fromtimestamp(value / 1000, tz=UTC).replace(tzinfo=None)
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(UTC).replace(tzinfo=None)
    return dt


# ------------------------------------------------------------------ polite HTTP


class PoliteClient:
    """httpx wrapper: identifies itself, honors robots.txt, rate-limits per host,
    retries 429/5xx with backoff (respecting Retry-After)."""

    def __init__(
        self,
        *,
        user_agent: str,
        min_interval: float = 1.0,
        timeout: float = 30.0,
        max_retries: int = 3,
        transport: httpx.BaseTransport | None = None,
        check_robots: bool = True,
    ) -> None:
        self.user_agent = user_agent
        self.min_interval = min_interval
        self.max_retries = max_retries
        self.check_robots = check_robots
        self._client = httpx.Client(
            headers={"User-Agent": user_agent, "Accept": "application/json"},
            timeout=timeout,
            follow_redirects=True,
            transport=transport,
        )
        self._last_hit: dict[str, float] = {}
        self._host_locks: dict[str, threading.Lock] = {}
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self._crawl_delay: dict[str, float] = {}
        self._meta_lock = threading.Lock()

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> PoliteClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _host_lock(self, host: str) -> threading.Lock:
        with self._meta_lock:
            return self._host_locks.setdefault(host, threading.Lock())

    def _allowed(self, url: str) -> bool:
        if not self.check_robots:
            return True
        p = urlparse(url)
        base = f"{p.scheme}://{p.netloc}"
        with self._meta_lock:
            known = base in self._robots
            rp = self._robots.get(base)
        if not known:
            # RFC 9309 §2.3.1: 2xx → parse rules; 4xx ("unavailable", e.g. API hosts that answer 401/404
            # for /robots.txt) → no restrictions; 5xx/network error → assume complete disallow for now.
            rp = urllib.robotparser.RobotFileParser()
            try:
                r = self._client.get(f"{base}/robots.txt", headers={"Accept": "text/plain"})
                if r.status_code < 300:
                    rp.parse(r.text.splitlines())
                    delay = rp.crawl_delay(self.user_agent)
                    if delay:
                        with self._meta_lock:
                            self._crawl_delay[p.netloc] = float(delay)
                elif 400 <= r.status_code < 500:
                    rp = None
                else:
                    raise FetchError(f"robots.txt for {base} returned HTTP {r.status_code}; not crawling", status=r.status_code)
            except httpx.HTTPError as e:
                raise FetchError(f"could not fetch robots.txt for {base}: {e}") from e
            with self._meta_lock:
                self._robots[base] = rp
        return rp is None or rp.can_fetch(self.user_agent, url)

    def get_json(self, url: str, params: dict[str, Any] | None = None) -> Any:
        if not self._allowed(url):
            raise FetchError(f"robots.txt disallows {url}")
        host = urlparse(url).netloc
        lock = self._host_lock(host)
        attempt = 0
        while True:
            with lock:
                interval = max(self.min_interval, self._crawl_delay.get(host, 0.0))
                wait = interval - (time.monotonic() - self._last_hit.get(host, 0.0))
                if wait > 0:
                    time.sleep(wait)
                try:
                    r = self._client.get(url, params=params)
                except httpx.HTTPError as e:
                    self._last_hit[host] = time.monotonic()
                    if attempt < self.max_retries:
                        attempt += 1
                        time.sleep(min(2**attempt, 20))
                        continue
                    raise FetchError(f"network error for {url}: {e}") from e
                self._last_hit[host] = time.monotonic()
            if r.status_code == 404:
                raise FetchError(f"board not found: {url}", status=404, not_found=True)
            if r.status_code == 429 or r.status_code >= 500:
                if attempt < self.max_retries:
                    attempt += 1
                    retry_after = r.headers.get("retry-after")
                    delay = float(retry_after) if retry_after and retry_after.isdigit() else 2**attempt
                    time.sleep(min(delay, 60))
                    continue
                raise FetchError(f"HTTP {r.status_code} for {url}", status=r.status_code)
            if r.status_code >= 400:
                raise FetchError(f"HTTP {r.status_code} for {url}", status=r.status_code)
            try:
                return r.json()
            except ValueError as e:
                raise FetchError(f"invalid JSON from {url}") from e
