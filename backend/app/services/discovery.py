"""Discovery: fetch public ATS boards, filter, upsert jobs, flag new/closed, auto-score."""

from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..ats import FETCHERS, FetchError, NormalizedPosting, PoliteClient
from ..config import get_settings
from ..db import SessionLocal, utcnow
from ..llm.client import LLMBudgetExceeded, LLMError, get_llm
from ..models import Application, Company, DiscoveryRun, Job, Notification, StageEvent
from .filtering import FilterConfig, evaluate, load_filter_config
from .profile import get_profile
from .scoring import score_job

log = logging.getLogger(__name__)

_run_lock = threading.Lock()


# ------------------------------------------------------------------ companies


def _read_company_file(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return list(data.get("companies") or [])


def load_company_config() -> list[dict[str, Any]]:
    s = get_settings()
    merged: dict[str, dict[str, Any]] = {}
    for entry in _read_company_file(s.companies_file) + _read_company_file(s.companies_local_file):
        if not entry.get("name"):
            continue
        merged[entry["name"]] = entry  # local file overrides seed by name
    return list(merged.values())


def sync_companies(db: Session) -> list[Company]:
    """Upsert companies from config files. UI-added companies are left untouched."""
    for entry in load_company_config():
        c = db.scalar(select(Company).where(Company.name == entry["name"]))
        if c is None:
            c = Company(name=entry["name"], source="config")
            db.add(c)
        if c.source != "config":
            continue
        c.ats = (entry.get("ats") or "manual").lower()
        c.slug = entry.get("slug")
        c.region = entry.get("region")
        c.careers_url = entry.get("careers_url")
        c.category = entry.get("category")
        c.enabled = bool(entry.get("enabled", True))
    db.commit()
    return list(db.scalars(select(Company).order_by(Company.name)))


# ------------------------------------------------------------------ helpers


def apply_filter(job: Job, cfg: FilterConfig) -> None:
    res = evaluate(job.title, job.locations or ([job.location] if job.location else []), job.is_remote, cfg)
    job.matches_filter = res.passed
    job.filter_reasons = res.reasons
    job.location_tags = res.location_tags
    job.seniority = res.seniority


def ensure_application(db: Session, job: Job, stage: str = "discovered") -> Application:
    if job.application is None:
        app = Application(job_id=job.id, stage=stage, position=-job.id)
        db.add(app)
        db.flush()
        db.add(StageEvent(application_id=app.id, from_stage=None, to_stage=stage))
        job.application = app
    return job.application


def notify(db: Session, kind: str, title: str, body: str = "", link: str | None = None, dedupe_key: str | None = None) -> None:
    if dedupe_key and db.scalar(select(Notification).where(Notification.dedupe_key == dedupe_key)):
        return
    db.add(Notification(kind=kind, title=title, body=body, link=link, dedupe_key=dedupe_key))


@dataclass
class CompanyResult:
    company_id: int
    name: str
    status: str
    postings: list[NormalizedPosting]
    error: str | None = None


def _fetch_company(client: PoliteClient, c: Company) -> CompanyResult:
    fn = FETCHERS.get(c.ats)
    if fn is None or not c.slug:
        return CompanyResult(c.id, c.name, "skipped", [], None)
    try:
        postings = fn(client, c.slug, region=c.region)
        return CompanyResult(c.id, c.name, "ok", postings)
    except FetchError as e:
        return CompanyResult(c.id, c.name, "error", [], str(e))
    except Exception as e:  # parser bug or unexpected payload — isolate per company
        log.exception("unexpected error fetching %s", c.name)
        return CompanyResult(c.id, c.name, "error", [], f"{type(e).__name__}: {e}")


def upsert_postings(db: Session, company: Company, postings: list[NormalizedPosting], cfg: FilterConfig, now: Any) -> dict[str, Any]:
    existing = {j.external_id: j for j in db.scalars(select(Job).where(Job.company_id == company.id))}
    seen: set[str] = set()
    new_jobs: list[Job] = []
    reopened = 0
    for p in postings:
        if not p.external_id or not p.title:
            continue
        if p.external_id in seen:
            continue
        seen.add(p.external_id)
        job = existing.get(p.external_id)
        if job is None:
            job = Job(company_id=company.id, source=company.ats, external_id=p.external_id, first_seen_at=now)
            db.add(job)
            new_jobs.append(job)
        elif job.closed_at is not None:
            job.closed_at = None
            reopened += 1
        job.title = p.title[:500]
        job.location = (p.location or "")[:500] or None
        job.locations = p.locations
        job.departments = p.departments
        job.workplace_type = p.workplace_type
        job.is_remote = p.is_remote
        job.url = p.url
        job.apply_url = p.apply_url
        job.description_html = p.description_html
        job.description_text = p.description_text
        job.comp_text = p.comp_text
        job.content_hash = p.content_hash
        job.posted_at = p.posted_at
        job.source_updated_at = p.updated_at
        job.last_seen_at = now
        apply_filter(job, cfg)
    db.flush()

    closed: list[Job] = []
    for ext_id, job in existing.items():
        if ext_id not in seen and job.closed_at is None and job.source != "manual":
            job.closed_at = now
            closed.append(job)

    new_matching = [j for j in new_jobs if j.matches_filter]
    # Many companies post one role per city ("Sr. FDE - Retail" × 12 locations). Every posting stays
    # browsable under Jobs, but only the first gets a pipeline card so "Discovered" stays a usable inbox.
    tracked_titles = {
        t.lower()
        for (t,) in db.execute(select(Job.title).join(Application, Application.job_id == Job.id).where(Job.company_id == company.id))
    }
    carded: list[Job] = []
    for j in new_matching:
        key = j.title.lower()
        if key in tracked_titles:
            continue
        tracked_titles.add(key)
        ensure_application(db, j)
        carded.append(j)
    return {
        "new": new_jobs,
        "new_matching": new_matching,
        "carded": carded,
        "closed": closed,
        "reopened": reopened,
        "seen": len(seen),
    }


# ------------------------------------------------------------------ main entry


def run_discovery(trigger: str = "manual", *, client: PoliteClient | None = None, company_ids: list[int] | None = None) -> int | None:
    """Run one discovery pass. Returns the DiscoveryRun id, or None if a run is already in progress."""
    if not _run_lock.acquire(blocking=False):
        log.info("discovery already running; skipping trigger=%s", trigger)
        return None
    s = get_settings()
    db = SessionLocal()
    own_client = client is None
    client = client or PoliteClient(user_agent=s.http_user_agent, min_interval=s.http_min_interval_seconds, timeout=s.http_timeout_seconds)
    run = DiscoveryRun(trigger=trigger, status="running")
    db.add(run)
    db.commit()
    try:
        companies = sync_companies(db)
        if company_ids:
            companies = [c for c in companies if c.id in company_ids]
        targets = [c for c in companies if c.enabled and c.ats in FETCHERS and c.slug]
        cfg = load_filter_config()
        now = utcnow()
        stats: list[dict[str, Any]] = []
        all_new_matching: list[Job] = []
        all_carded: list[Job] = []
        closed_in_pipeline: list[Job] = []
        totals = {"fetched": 0, "new": 0, "closed": 0}

        with ThreadPoolExecutor(max_workers=max(1, s.discovery_max_workers)) as pool:
            futures = [pool.submit(_fetch_company, client, c) for c in targets]
            results = [f.result() for f in as_completed(futures)]

        by_id = {c.id: c for c in targets}
        for r in sorted(results, key=lambda x: x.name):
            c = by_id[r.company_id]
            c.last_fetch_at = now
            c.last_status = r.status
            c.last_error = r.error
            entry: dict[str, Any] = {"company": r.name, "ats": c.ats, "status": r.status, "error": r.error}
            if r.status == "ok":
                res = upsert_postings(db, c, r.postings, cfg, now)
                c.last_job_count = res["seen"]
                entry.update(
                    fetched=res["seen"],
                    new=len(res["new"]),
                    new_matching=len(res["new_matching"]),
                    closed=len(res["closed"]),
                    matching_open=db.scalar(
                        select(func.count())
                        .select_from(Job)
                        .where(Job.company_id == c.id, Job.matches_filter.is_(True), Job.closed_at.is_(None))
                    ),
                )
                totals["fetched"] += res["seen"]
                totals["new"] += len(res["new"])
                totals["closed"] += len(res["closed"])
                all_new_matching += res["new_matching"]
                all_carded += res["carded"]
                closed_in_pipeline += [j for j in res["closed"] if j.application is not None and j.application.stage not in ("discovered",)]
            stats.append(entry)
            db.commit()

        if all_new_matching:
            names = ", ".join(sorted({j.company.name for j in all_new_matching}))
            notify(db, "new_jobs", f"{len(all_new_matching)} new matching posting(s)", names, "/jobs?new=1")
        for j in closed_in_pipeline:
            notify(
                db,
                "closed_jobs",
                f"Posting closed: {j.title} @ {j.company.name}",
                f"Stage: {j.application.stage if j.application else '-'}",
                f"/jobs/{j.id}",
                f"closed:{j.id}:{j.closed_at}",
            )
        errors = [x for x in stats if x["status"] == "error"]
        if errors:
            notify(
                db,
                "discovery_error",
                f"{len(errors)} board(s) failed to fetch",
                "; ".join(f"{e['company']}: {e['error']}" for e in errors)[:2000],
                "/companies",
            )

        run.fetched_count = totals["fetched"]
        run.new_count = totals["new"]
        run.new_matching_count = len(all_new_matching)
        run.closed_count = totals["closed"]
        run.company_stats = stats
        ok_count = sum(1 for x in stats if x["status"] == "ok")
        run.status = "ok" if not errors else ("partial" if ok_count else "error")
        if errors and not ok_count:
            run.error = "all boards failed — check network access to the ATS APIs"
        db.commit()

        # Auto-score new matching jobs (bounded — cost guard).
        if s.auto_score_new and all_carded:
            run.scored_count = _auto_score(db, all_carded, s.max_auto_scores_per_run)
        run.finished_at = utcnow()
        db.commit()
        log.info(
            "discovery %s done: fetched=%d new=%d matching_new=%d closed=%d status=%s",
            run.id,
            run.fetched_count,
            run.new_count,
            run.new_matching_count,
            run.closed_count,
            run.status,
        )
        return run.id
    except Exception as e:
        log.exception("discovery run failed")
        db.rollback()
        run.status = "error"
        run.error = f"{type(e).__name__}: {e}"
        run.finished_at = utcnow()
        db.add(run)
        db.commit()
        return run.id
    finally:
        if own_client:
            client.close()
        db.close()
        _run_lock.release()


def _auto_score(db: Session, jobs: list[Job], limit: int) -> int:
    llm = get_llm()
    profile = get_profile(db)
    jobs = sorted(jobs, key=lambda j: j.posted_at or j.first_seen_at, reverse=True)[: max(0, limit)]
    n = 0
    failures = 0
    for j in jobs:
        try:
            score_job(db, llm, j, profile, interactive=False)
            n += 1
        except LLMBudgetExceeded as e:
            log.warning("auto-score stopped: %s", e)
            notify(db, "discovery_error", "Auto-scoring paused (budget)", str(e), "/settings")
            db.commit()
            break
        except LLMError as e:
            log.warning("auto-score failed for job %s: %s", j.id, e)
            failures += 1
            if failures >= 3:  # bad key / provider outage: don't burn through the whole batch
                notify(db, "discovery_error", "Auto-scoring stopped after repeated LLM errors", str(e)[:500], "/profile")
                db.commit()
                break
            continue
        failures = 0
    return n


def is_running() -> bool:
    return _run_lock.locked()
