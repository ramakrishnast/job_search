"""Companies, discovery, jobs, pipeline, tailoring and export endpoints."""

from __future__ import annotations

import hashlib
import threading
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from .. import serializers as ser
from ..ats import FETCHERS, PoliteClient, detect_ats
from ..config import get_settings
from ..db import get_db, utcnow
from ..llm.client import get_llm
from ..models import STAGES, Application, Company, Contact, DiscoveryRun, Job, StageEvent, Tailoring
from ..services import export
from ..services.discovery import apply_filter, ensure_application, is_running, run_discovery, sync_companies
from ..services.filtering import load_filter_config
from ..services.profile import get_profile, profile_hash
from ..services.scoring import score_job
from ..services.tailoring import assemble_tailored_resume, tailor_kit, tailor_resume

router = APIRouter(prefix="/api", tags=["jobs"])


def _job(db: Session, job_id: int) -> Job:
    j = db.get(Job, job_id)
    if j is None:
        raise HTTPException(404, "job not found")
    return j


# ------------------------------------------------------------------ companies


class CompanyIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    ats: Literal["greenhouse", "lever", "ashby", "manual", "auto"] = "auto"
    slug: str | None = None
    region: str | None = None
    careers_url: str | None = None
    category: str | None = None


@router.get("/companies")
def list_companies(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    sync_companies(db)
    return [ser.company(c) for c in db.scalars(select(Company).order_by(Company.category, Company.name))]


@router.post("/companies", status_code=201)
def add_company(body: CompanyIn, db: Session = Depends(get_db)) -> dict[str, Any]:
    if db.scalar(select(Company).where(Company.name == body.name)):
        raise HTTPException(409, "company already exists")
    ats = body.ats
    if ats == "auto":
        if not body.slug:
            raise HTTPException(400, "slug is required to auto-detect the ATS")
        s = get_settings()
        with PoliteClient(user_agent=s.http_user_agent, min_interval=s.http_min_interval_seconds) as client:
            found = detect_ats(client, body.slug)
        if not found:
            raise HTTPException(422, f"no public Greenhouse/Lever/Ashby board found for slug '{body.slug}'")
        ats = found[0]  # type: ignore[assignment]
    c = Company(
        name=body.name,
        ats=ats,
        slug=body.slug,
        region=body.region,
        careers_url=body.careers_url,
        category=body.category or "custom",
        source="ui",
    )
    db.add(c)
    db.commit()
    return ser.company(c)


class CompanyPatch(BaseModel):
    enabled: bool | None = None


@router.patch("/companies/{company_id}")
def patch_company(company_id: int, body: CompanyPatch, db: Session = Depends(get_db)) -> dict[str, Any]:
    c = db.get(Company, company_id)
    if c is None:
        raise HTTPException(404, "company not found")
    if body.enabled is not None:
        c.enabled = body.enabled
    db.commit()
    return ser.company(c)


@router.delete("/companies/{company_id}", status_code=204)
def delete_company(company_id: int, db: Session = Depends(get_db)) -> Response:
    c = db.get(Company, company_id)
    if c is None:
        raise HTTPException(404, "company not found")
    if c.source != "ui":
        raise HTTPException(400, "config companies are managed in config/companies.yaml (set enabled: false)")
    db.delete(c)
    db.commit()
    return Response(status_code=204)


@router.post("/companies/verify")
def verify_companies(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    """Probe each configured board; report which ATS (if any) answers for its slug."""
    s = get_settings()
    out = []
    with PoliteClient(user_agent=s.http_user_agent, min_interval=s.http_min_interval_seconds) as client:
        for c in sync_companies(db):
            if c.ats not in FETCHERS or not c.slug:
                out.append({"company": c.name, "configured": c.ats, "detected": None, "jobs": None, "ok": c.ats == "manual"})
                continue
            found = detect_ats(client, c.slug)
            out.append(
                {
                    "company": c.name,
                    "configured": c.ats,
                    "detected": found[0] if found else None,
                    "jobs": found[1] if found else None,
                    "ok": bool(found and found[0] == c.ats),
                }
            )
    return out


# ------------------------------------------------------------------ discovery


@router.post("/discovery/run", status_code=202)
def trigger_discovery() -> dict[str, Any]:
    if is_running():
        return {"started": False, "message": "a discovery run is already in progress"}
    threading.Thread(target=run_discovery, args=("manual",), daemon=True, name="discovery-manual").start()
    return {"started": True}


@router.get("/discovery/runs")
def discovery_runs(limit: int = 20, db: Session = Depends(get_db)) -> dict[str, Any]:
    runs = db.scalars(select(DiscoveryRun).order_by(DiscoveryRun.started_at.desc()).limit(min(limit, 100))).all()
    return {"running": is_running(), "runs": [ser.run(r) for r in runs]}


@router.post("/discovery/refilter")
def refilter(db: Session = Depends(get_db)) -> dict[str, int]:
    """Re-apply config/filters.yaml to all stored jobs (after editing filters)."""
    cfg = load_filter_config()
    n = 0
    for j in db.scalars(select(Job)):
        before = j.matches_filter
        apply_filter(j, cfg)
        if j.matches_filter and not before and j.closed_at is None:
            ensure_application(db, j)
            n += 1
    db.commit()
    return {"newly_matching": n}


# ------------------------------------------------------------------ jobs


@router.get("/jobs")
def list_jobs(
    db: Session = Depends(get_db),
    q: str | None = None,
    matching: bool = True,
    status: Literal["open", "closed", "all"] = "open",
    new_days: int | None = None,
    company_id: int | None = None,
    verdict: str | None = None,
    min_score: int | None = None,
    location_tag: str | None = None,
    archived: bool = False,
    sort: Literal["score", "newest", "company"] = "score",
    limit: int = Query(200, le=1000),
    offset: int = 0,
) -> dict[str, Any]:
    from ..models import FitScore

    stmt = select(Job).options(selectinload(Job.company), selectinload(Job.fit), selectinload(Job.application))
    stmt = stmt.outerjoin(FitScore, FitScore.job_id == Job.id)
    if matching:
        stmt = stmt.where(Job.matches_filter.is_(True))
    if status == "open":
        stmt = stmt.where(Job.closed_at.is_(None))
    elif status == "closed":
        stmt = stmt.where(Job.closed_at.is_not(None))
    stmt = stmt.where(Job.archived.is_(archived))
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.join(Company, Company.id == Job.company_id).where(
            or_(func.lower(Job.title).like(like), func.lower(Company.name).like(like), func.lower(Job.location).like(like))
        )
    if new_days:
        from datetime import timedelta

        stmt = stmt.where(Job.first_seen_at >= utcnow() - timedelta(days=new_days))
    if company_id:
        stmt = stmt.where(Job.company_id == company_id)
    if verdict:
        stmt = stmt.where(FitScore.verdict == verdict)
    if min_score is not None:
        stmt = stmt.where(FitScore.score >= min_score)
    total = db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery()))
    if sort == "score":
        stmt = stmt.order_by(FitScore.score.desc().nulls_last(), Job.first_seen_at.desc())
    elif sort == "newest":
        stmt = stmt.order_by(Job.posted_at.desc().nulls_last(), Job.first_seen_at.desc())
    else:
        stmt = stmt.order_by(Job.company_id, Job.title)
    jobs = db.scalars(stmt.offset(offset).limit(limit)).unique().all()
    if location_tag:
        jobs = [j for j in jobs if location_tag in (j.location_tags or [])]
    ph = profile_hash(get_profile(db))
    return {"total": total, "jobs": [ser.job_summary(j, ph) for j in jobs]}


@router.get("/jobs/{job_id}")
def get_job(job_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    j = _job(db, job_id)
    ph = profile_hash(get_profile(db))
    d = ser.job_detail(j, ph)
    ts = {t.kind: t for t in db.scalars(select(Tailoring).where(Tailoring.job_id == j.id))}
    d["tailoring"] = {k: ser.tailoring(ts.get(k), ph, j.content_hash) for k in ("resume", "kit")}
    return d


class ManualJobIn(BaseModel):
    company: str = Field(min_length=1)
    title: str = Field(min_length=1)
    url: str | None = None
    location: str | None = None
    description: str = Field(min_length=20)
    is_remote: bool = False


@router.post("/jobs", status_code=201)
def add_manual_job(body: ManualJobIn, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Add a posting found elsewhere (e.g. big-tech career sites) by pasting its JD."""
    c = db.scalar(select(Company).where(Company.name == body.company))
    if c is None:
        c = Company(name=body.company, ats="manual", source="ui", category="custom")
        db.add(c)
        db.flush()
    ext = "manual-" + hashlib.sha1(f"{body.title}|{body.url}|{body.description[:200]}".encode()).hexdigest()[:16]
    if db.scalar(select(Job).where(Job.company_id == c.id, Job.external_id == ext)):
        raise HTTPException(409, "this job already exists")
    h = hashlib.sha256(f"{body.title}\x00{body.location or ''}\x00{body.description}".encode()).hexdigest()
    j = Job(
        company_id=c.id,
        source="manual",
        external_id=ext,
        title=body.title,
        location=body.location,
        locations=[body.location] if body.location else [],
        is_remote=body.is_remote,
        url=body.url,
        apply_url=body.url,
        description_text=body.description,
        content_hash=h,
    )
    db.add(j)
    db.flush()
    apply_filter(j, load_filter_config())
    ensure_application(db, j, "shortlisted")
    db.commit()
    return ser.job_detail(j)


class JobPatch(BaseModel):
    archived: bool | None = None


@router.patch("/jobs/{job_id}")
def patch_job(job_id: int, body: JobPatch, db: Session = Depends(get_db)) -> dict[str, Any]:
    j = _job(db, job_id)
    if body.archived is not None:
        j.archived = body.archived
    db.commit()
    return ser.job_summary(j)


@router.post("/jobs/{job_id}/score")
def score(job_id: int, force: bool = False, db: Session = Depends(get_db)) -> dict[str, Any]:
    j = _job(db, job_id)
    p = get_profile(db)
    score_job(db, get_llm(), j, p, force=force)
    db.refresh(j)
    return ser.job_detail(j, profile_hash(p))


class BatchScoreIn(BaseModel):
    job_ids: list[int] = Field(max_length=50)


@router.post("/jobs/score-batch")
def score_batch(body: BatchScoreIn, db: Session = Depends(get_db)) -> dict[str, Any]:
    p = get_profile(db)
    llm = get_llm()
    done, errors = [], []
    for jid in body.job_ids:
        j = db.get(Job, jid)
        if j is None:
            continue
        try:
            f = score_job(db, llm, j, p)
            done.append({"job_id": jid, "score": f.score, "verdict": f.verdict})
        except Exception as e:  # report per-job failures without aborting the batch
            errors.append({"job_id": jid, "error": str(e)})
    return {"scored": done, "errors": errors}


@router.post("/jobs/{job_id}/apply-opened")
def apply_opened(job_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Record that the user opened the official apply link. Nothing is submitted on their behalf."""
    j = _job(db, job_id)
    a = ensure_application(db, j)
    if a.stage == "discovered":
        _move(db, a, "shortlisted")
    db.commit()
    return {"apply_url": j.apply_url or j.url, "application": ser.application(a)}


# ------------------------------------------------------------------ pipeline


@router.get("/pipeline")
def pipeline(db: Session = Depends(get_db), include_closed_discovered: bool = False) -> dict[str, Any]:
    ph = profile_hash(get_profile(db))
    apps = db.scalars(
        select(Application)
        .options(selectinload(Application.job).selectinload(Job.company), selectinload(Application.job).selectinload(Job.fit))
        .join(Job)
        .where(Job.archived.is_(False))
        .order_by(Application.position, Application.id)
    ).all()
    columns: dict[str, list[dict[str, Any]]] = {s: [] for s in STAGES}
    for a in apps:
        if a.stage == "discovered" and a.job.closed_at is not None and not include_closed_discovered:
            continue
        item = ser.job_summary(a.job, ph)
        item["application"] = ser.application(a)
        columns[a.stage].append(item)
    columns["discovered"].sort(key=lambda x: -(x["fit"]["score"] if x["fit"] else -1))
    return {"stages": STAGES, "columns": columns}


def _move(db: Session, a: Application, stage: str) -> None:
    if stage not in STAGES:
        raise HTTPException(400, f"invalid stage '{stage}'")
    if stage == a.stage:
        return
    db.add(StageEvent(application_id=a.id, from_stage=a.stage, to_stage=stage))
    if stage == "applied" and a.applied_at is None:
        a.applied_at = utcnow()
    a.stage = stage


@router.post("/jobs/{job_id}/application", status_code=201)
def add_to_pipeline(job_id: int, stage: str = "shortlisted", db: Session = Depends(get_db)) -> dict[str, Any]:
    j = _job(db, job_id)
    a = ensure_application(db, j, stage if stage in STAGES else "shortlisted")
    db.commit()
    return ser.application(a, with_detail=True) or {}


class ApplicationPatch(BaseModel):
    stage: str | None = None
    position: float | None = None
    notes: str | None = None
    next_action: str | None = None
    next_action_date: datetime | None = None
    follow_up_at: datetime | None = None
    applied_at: datetime | None = None
    clear: list[Literal["next_action", "next_action_date", "follow_up_at", "applied_at"]] = []


def _naive_utc(d: datetime | None) -> datetime | None:
    if d is None:
        return None
    if d.tzinfo is not None:
        from datetime import UTC

        d = d.astimezone(UTC).replace(tzinfo=None)
    return d


@router.patch("/applications/{app_id}")
def patch_application(app_id: int, body: ApplicationPatch, db: Session = Depends(get_db)) -> dict[str, Any]:
    a = db.get(Application, app_id)
    if a is None:
        raise HTTPException(404, "application not found")
    if body.stage is not None:
        _move(db, a, body.stage)
    if body.position is not None:
        a.position = body.position
    if body.notes is not None:
        a.notes = body.notes
    if body.next_action is not None:
        a.next_action = body.next_action
    for f in ("next_action_date", "follow_up_at", "applied_at"):
        v = getattr(body, f)
        if v is not None:
            setattr(a, f, _naive_utc(v))
    for f in body.clear:
        setattr(a, f, None)
    db.commit()
    db.refresh(a)
    return ser.application(a, with_detail=True) or {}


class ContactIn(BaseModel):
    name: str = Field(min_length=1)
    role: str | None = None
    email: str | None = None
    linkedin_url: str | None = None
    notes: str = ""


@router.post("/applications/{app_id}/contacts", status_code=201)
def add_contact(app_id: int, body: ContactIn, db: Session = Depends(get_db)) -> dict[str, Any]:
    if db.get(Application, app_id) is None:
        raise HTTPException(404, "application not found")
    c = Contact(application_id=app_id, **body.model_dump())
    db.add(c)
    db.commit()
    return ser.contact(c)


@router.delete("/contacts/{contact_id}", status_code=204)
def delete_contact(contact_id: int, db: Session = Depends(get_db)) -> Response:
    c = db.get(Contact, contact_id)
    if c is None:
        raise HTTPException(404, "contact not found")
    db.delete(c)
    db.commit()
    return Response(status_code=204)


# ------------------------------------------------------------------ tailoring


@router.post("/jobs/{job_id}/tailor")
def tailor(job_id: int, force: bool = False, parts: str = "resume,kit", db: Session = Depends(get_db)) -> dict[str, Any]:
    j = _job(db, job_id)
    p = get_profile(db)
    llm = get_llm()
    wanted = {x.strip() for x in parts.split(",")}
    out: dict[str, Any] = {}
    try:
        if "resume" in wanted:
            out["resume"] = ser.tailoring(tailor_resume(db, llm, j, p, force=force))
        if "kit" in wanted:
            out["kit"] = ser.tailoring(tailor_kit(db, llm, j, p, force=force))
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return out


DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _file(data: bytes, filename: str, media: str) -> Response:
    return Response(content=data, media_type=media, headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/jobs/{job_id}/export/resume.{fmt}")
def export_job_resume(job_id: int, fmt: Literal["docx", "pdf"], db: Session = Depends(get_db)) -> Response:
    j = _job(db, job_id)
    p = get_profile(db)
    t = db.scalar(select(Tailoring).where(Tailoring.job_id == j.id, Tailoring.kind == "resume"))
    r = assemble_tailored_resume(p, t.data if t else None)
    name = export.safe_filename(p.name or "resume", j.company.name, "resume")
    if fmt == "docx":
        return _file(export.resume_docx(r), f"{name}.docx", DOCX)
    return _file(export.resume_pdf(r), f"{name}.pdf", "application/pdf")


@router.get("/jobs/{job_id}/export/cover-letter.docx")
def export_cover_letter(job_id: int, db: Session = Depends(get_db)) -> Response:
    j = _job(db, job_id)
    p = get_profile(db)
    t = db.scalar(select(Tailoring).where(Tailoring.job_id == j.id, Tailoring.kind == "kit"))
    if t is None:
        raise HTTPException(404, "generate the application kit first")
    data = export.cover_letter_docx(p.name, j.company.name, j.title, t.data["cover_letter"])
    return _file(data, f"{export.safe_filename(p.name or 'cover_letter', j.company.name, 'cover_letter')}.docx", DOCX)
