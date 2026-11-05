import httpx
import pytest

from app.ats import FETCHERS, FetchError, PoliteClient, detect_ats
from app.ats.ashby import parse_ashby
from app.ats.base import html_to_text, parse_iso
from app.ats.greenhouse import parse_greenhouse
from app.ats.lever import board_url as lever_url
from app.ats.lever import parse_lever

from .conftest import load_fixture


def test_greenhouse_parser():
    jobs = parse_greenhouse(load_fixture("greenhouse.json"))
    assert len(jobs) == 3
    j = jobs[0]
    assert j.external_id == "4001"
    assert j.title == "Software Engineer, Applied AI"
    assert j.locations == ["San Francisco, CA", "New York City, NY"]
    assert j.departments == ["Applied AI"]
    assert "Build LLM-powered agents" in j.description_text
    assert "• 5+ years of software engineering" in j.description_text
    assert "<" not in j.description_text and "&lt;" not in j.description_text
    assert j.apply_url == "https://job-boards.greenhouse.io/anthropic/jobs/4001"
    assert j.posted_at is not None and j.posted_at.year == 2026
    assert len(j.content_hash) == 64
    assert jobs[2].is_remote is True


def test_lever_parser():
    jobs = parse_lever(load_fixture("lever.json"))
    assert len(jobs) == 2
    j = jobs[0]
    assert j.title == "Staff AI Engineer, Agents"
    assert j.locations == ["Remote - US", "New York, NY"]
    assert j.workplace_type == "remote" and j.is_remote
    assert j.apply_url.endswith("/apply")
    assert "Requirements" in j.description_text and "LLM evals" in j.description_text
    assert "great benefits" in j.description_text
    assert j.comp_text == "USD 250,000–320,000 per year salary"
    assert j.posted_at is not None
    assert jobs[1].workplace_type == "onsite" and not jobs[1].is_remote


def test_lever_eu_region():
    assert lever_url("mistral", "eu").startswith("https://api.eu.lever.co/")
    assert lever_url("mistral").startswith("https://api.lever.co/")


def test_ashby_parser_skips_unlisted():
    jobs = parse_ashby(load_fixture("ashby.json"))
    assert [j.external_id for j in jobs] == ["8c3f-01", "8c3f-02"]
    fde = jobs[0]
    assert fde.locations == ["San Francisco", "Atlanta, GA"]
    assert fde.workplace_type == "hybrid"
    assert fde.comp_text == "$220K - $300K"
    assert fde.apply_url.endswith("/application")
    assert jobs[1].is_remote


def test_html_to_text_handles_double_escaping():
    assert html_to_text("&amp;lt;p&amp;gt;Hi &amp;amp; bye&amp;lt;/p&amp;gt;") == "Hi & bye"
    assert html_to_text("<script>x()</script><p>a</p><p>b</p>").split() == ["a", "b"]
    assert html_to_text(None) == ""


def test_parse_iso_variants():
    assert parse_iso("2026-10-20T09:00:00-04:00").hour == 13
    assert parse_iso(1761000000000).year == 2025
    assert parse_iso("garbage") is None and parse_iso(None) is None


def _client(handler):
    return PoliteClient(user_agent="test", min_interval=0, transport=httpx.MockTransport(handler), max_retries=2)


def test_polite_client_404_and_retry(monkeypatch):
    monkeypatch.setattr("app.ats.base.time.sleep", lambda s: None)
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        if "missing" in req.url.path:
            return httpx.Response(404)
        calls["n"] += 1
        if calls["n"] < 2:
            return httpx.Response(429, headers={"retry-after": "1"})
        return httpx.Response(200, json={"jobs": []})

    c = _client(handler)
    assert c.get_json("https://boards-api.greenhouse.io/v1/boards/x/jobs") == {"jobs": []}
    assert calls["n"] == 2
    with pytest.raises(FetchError) as ei:
        c.get_json("https://boards-api.greenhouse.io/v1/boards/missing/jobs")
    assert ei.value.not_found


def test_polite_client_respects_robots():
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /private/\n")
        return httpx.Response(200, json={})

    c = _client(handler)
    with pytest.raises(FetchError, match="robots"):
        c.get_json("https://example.com/private/feed")
    assert c.get_json("https://example.com/public/feed") == {}


def test_polite_client_sends_user_agent():
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["ua"] = req.headers.get("user-agent")
        return httpx.Response(404) if req.url.path == "/robots.txt" else httpx.Response(200, json=[])

    _client(handler).get_json("https://api.lever.co/v0/postings/acme")
    assert seen["ua"] == "test"


def test_detect_ats():
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        if req.url.host == "api.ashbyhq.com":
            return httpx.Response(200, json=load_fixture("ashby.json"))
        return httpx.Response(404)

    assert detect_ats(_client(handler), "acme") == ("ashby", 2)
    assert set(FETCHERS) == {"greenhouse", "lever", "ashby"}


def test_robots_4xx_means_no_restrictions_and_5xx_blocks():
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/robots.txt":
            return httpx.Response(401 if req.url.host == "api.ashbyhq.com" else 503)
        return httpx.Response(200, json={"jobs": []})

    c = _client(handler)
    assert c.get_json("https://api.ashbyhq.com/posting-api/job-board/x") == {"jobs": []}
    with pytest.raises(FetchError, match="robots"):
        c.get_json("https://flaky.example.com/feed")


def test_crawl_delay_is_honored():
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\nCrawl-delay: 1\n")
        return httpx.Response(200, json=[])

    c = _client(handler)
    c.get_json("https://api.lever.co/v0/postings/a")
    assert c._crawl_delay["api.lever.co"] == 1.0
