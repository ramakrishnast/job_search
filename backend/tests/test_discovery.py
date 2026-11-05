import copy

import httpx

from app.ats import PoliteClient
from app.models import Application, Company, DiscoveryRun, Job, Notification
from app.services.discovery import run_discovery

from .conftest import load_fixture


def write_companies(tmp, monkeypatch):
    path = tmp / "companies.yaml"
    path.write_text(
        "companies:\n"
        "  - {name: GH Co, ats: greenhouse, slug: ghco}\n"
        "  - {name: Lever Co, ats: lever, slug: leverco}\n"
        "  - {name: Ashby Co, ats: ashby, slug: ashbyco}\n"
        "  - {name: Broken Co, ats: greenhouse, slug: broken}\n"
        "  - {name: Manual Co, ats: manual, careers_url: 'https://example.com'}\n"
    )
    monkeypatch.setenv("COMPANIES_FILE", str(path))
    monkeypatch.setenv("COMPANIES_LOCAL_FILE", str(tmp / "none.yaml"))
    from app.config import get_settings

    get_settings.cache_clear()


def make_client(payloads):
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        path = req.url.path
        if "broken" in path:
            return httpx.Response(404)
        if req.url.host == "boards-api.greenhouse.io":
            assert req.url.params.get("content") == "true"
            return httpx.Response(200, json=payloads["gh"])
        if req.url.host == "api.lever.co":
            return httpx.Response(200, json=payloads["lever"])
        if req.url.host == "api.ashbyhq.com":
            return httpx.Response(200, json=payloads["ashby"])
        return httpx.Response(404)

    return PoliteClient(user_agent="test", min_interval=0, transport=httpx.MockTransport(handler), max_retries=0)


def test_discovery_end_to_end(isolated, monkeypatch, db):
    write_companies(isolated, monkeypatch)
    payloads = {"gh": load_fixture("greenhouse.json"), "lever": load_fixture("lever.json"), "ashby": load_fixture("ashby.json")}

    run_id = run_discovery("manual", client=make_client(payloads))
    run = db.get(DiscoveryRun, run_id)
    assert run.status == "partial"  # Broken Co 404s, others ok
    assert run.fetched_count == 3 + 2 + 2
    titles = {j.title for j in db.query(Job).filter(Job.matches_filter.is_(True))}
    assert titles == {"Software Engineer, Applied AI", "Staff AI Engineer, Agents", "Forward Deployed Engineer"}
    assert run.new_matching_count == 3
    assert db.query(Application).count() == 3
    assert all(a.stage == "discovered" for a in db.query(Application))
    broken = db.query(Company).filter_by(name="Broken Co").one()
    assert broken.last_status == "error" and "not found" in broken.last_error
    kinds = {n.kind for n in db.query(Notification)}
    assert {"new_jobs", "discovery_error"} <= kinds
    assert run.scored_count == 3  # heuristic auto-score without a key

    # Second run: one GH job disappears (closed), one new Lever job appears.
    p2 = copy.deepcopy(payloads)
    p2["gh"]["jobs"] = [j for j in p2["gh"]["jobs"] if j["id"] != 4001]
    p2["lever"].append({**payloads["lever"][0], "id": "zz9", "text": "Senior Applied AI Engineer"})
    db.query(Application).join(Job).filter(Job.external_id == "4001").one().stage = "applied"
    db.commit()
    run2 = db.get(DiscoveryRun, run_discovery("scheduled", client=make_client(p2)))
    db.expire_all()
    assert run2.new_count == 1 and run2.closed_count == 1 and run2.new_matching_count == 1
    closed = db.query(Job).filter_by(external_id="4001").one()
    assert closed.closed_at is not None
    assert db.query(Notification).filter_by(kind="closed_jobs").count() == 1

    # Third run: the posting comes back → reopened
    run_discovery("manual", client=make_client(payloads))
    db.expire_all()
    assert db.query(Job).filter_by(external_id="4001").one().closed_at is None


def test_failed_fetch_does_not_close_jobs(isolated, monkeypatch, db):
    write_companies(isolated, monkeypatch)
    payloads = {"gh": load_fixture("greenhouse.json"), "lever": load_fixture("lever.json"), "ashby": load_fixture("ashby.json")}
    run_discovery("manual", client=make_client(payloads))

    def down(req):
        return httpx.Response(404) if req.url.path == "/robots.txt" else httpx.Response(503)

    rid = run_discovery("manual", client=PoliteClient(user_agent="t", min_interval=0, transport=httpx.MockTransport(down), max_retries=0))
    db.expire_all()
    assert db.get(DiscoveryRun, rid).status == "error"
    assert db.query(Job).filter(Job.closed_at.is_not(None)).count() == 0
