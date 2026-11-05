"""Health, status, dashboard, notifications and file serving."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from .. import __version__
from .. import serializers as ser
from ..config import get_settings
from ..db import get_db, utcnow
from ..llm.client import LLMClient, get_llm
from ..models import Application, DiscoveryRun, Job, Notification, Question, Story
from ..scheduler import next_discovery_time
from ..services import dashboard as dash
from ..services.discovery import is_running

router = APIRouter(prefix="/api", tags=["system"])


@router.get("/health")
def health(db: Session = Depends(get_db)) -> dict[str, Any]:
    db.execute(text("SELECT 1"))
    return {"status": "ok", "version": __version__}


@router.get("/status")
def status(db: Session = Depends(get_db)) -> dict[str, Any]:
    s = get_settings()
    last = db.scalar(select(DiscoveryRun).order_by(DiscoveryRun.started_at.desc()))
    budget = s.llm_daily_budget_usd
    return {
        "version": __version__,
        "llm": {
            "configured": get_llm().configured,
            "model_parse": s.llm_model_parse,
            "model_default": s.llm_model_default,
            "model_interview": s.interview_model,
            "daily_budget_usd": budget,
            "spent_24h_usd": round(LLMClient.spent_last_24h(db), 4),
        },
        "discovery": {
            "running": is_running(),
            "schedule_enabled": s.discovery_schedule_enabled,
            "next_run": next_discovery_time(),
            "schedule": f"daily {s.discovery_hour:02d}:{s.discovery_minute:02d} {s.timezone}",
            "last_run": ser.run(last) if last else None,
        },
        "counts": {
            "jobs": db.scalar(select(func.count()).select_from(Job)) or 0,
            "matching_open": db.scalar(select(func.count()).select_from(Job).where(Job.matches_filter.is_(True), Job.closed_at.is_(None)))
            or 0,
            "applications": db.scalar(select(func.count()).select_from(Application)) or 0,
            "stories": db.scalar(select(func.count()).select_from(Story)) or 0,
            "questions": db.scalar(select(func.count()).select_from(Question)) or 0,
        },
        "data_dir": str(s.data_dir),
    }


@router.get("/dashboard")
def dashboard(db: Session = Depends(get_db)) -> dict[str, Any]:
    return {
        "pipeline": dash.pipeline_metrics(db),
        "jobs": dash.jobs_metrics(db),
        "interviews": dash.interview_metrics(db),
        "llm": dash.llm_metrics(db),
    }


@router.get("/notifications")
def notifications(db: Session = Depends(get_db), unread_only: bool = False, limit: int = 50) -> dict[str, Any]:
    stmt = select(Notification).order_by(Notification.created_at.desc()).limit(min(limit, 200))
    if unread_only:
        stmt = stmt.where(Notification.read_at.is_(None))
    unread = db.scalar(select(func.count()).select_from(Notification).where(Notification.read_at.is_(None))) or 0
    return {"unread": unread, "items": [ser.notification(n) for n in db.scalars(stmt)]}


@router.post("/notifications/read-all")
def read_all(db: Session = Depends(get_db)) -> dict[str, int]:
    now = utcnow()
    n = 0
    for x in db.scalars(select(Notification).where(Notification.read_at.is_(None))):
        x.read_at = now
        n += 1
    db.commit()
    return {"marked": n}


@router.get("/files/uploads/{name}")
def uploaded_file(name: str) -> FileResponse:
    if "/" in name or "\\" in name or ".." in name or not name.endswith(".png"):
        raise HTTPException(400, "invalid file name")
    path = get_settings().data_dir / "uploads" / name
    if not path.is_file():
        raise HTTPException(404, "not found")
    return FileResponse(path, media_type="image/png")
