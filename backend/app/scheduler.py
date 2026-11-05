"""APScheduler wiring: daily discovery + hourly follow-up reminders."""

from __future__ import annotations

import logging
import threading
from datetime import timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select

from .config import get_settings
from .db import session_scope, utcnow
from .models import Application, DiscoveryRun
from .services.discovery import notify, run_discovery

log = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None


def follow_up_reminders() -> int:
    """Create (deduplicated) notifications for follow-ups / next actions due within 24h."""
    n = 0
    with session_scope() as db:
        horizon = utcnow() + timedelta(hours=24)
        apps = db.scalars(select(Application).where(Application.stage.notin_(["offer", "rejected", "withdrawn"]))).all()
        for a in apps:
            for kind, when in (("follow_up", a.follow_up_at), ("next_action", a.next_action_date)):
                if when and when <= horizon:
                    label = "Follow up" if kind == "follow_up" else (a.next_action or "Next action")
                    notify(
                        db,
                        "follow_up",
                        f"{label}: {a.job.title} @ {a.job.company.name}",
                        f"Due {when:%Y-%m-%d %H:%M} UTC",
                        f"/jobs/{a.job_id}",
                        f"{kind}:{a.id}:{when.isoformat()}",
                    )
                    n += 1
    return n


def _stale_catch_up() -> None:
    hours = get_settings().discovery_on_startup_if_stale_hours
    if hours <= 0:
        return
    with session_scope() as db:
        last = db.scalar(select(DiscoveryRun).where(DiscoveryRun.status.in_(["ok", "partial"])).order_by(DiscoveryRun.started_at.desc()))
        stale = last is None or last.started_at < utcnow() - timedelta(hours=hours)
    if stale:
        log.info("last successful discovery is stale; running catch-up in background")
        threading.Thread(target=run_discovery, args=("startup",), daemon=True, name="discovery-catchup").start()


def start_scheduler() -> BackgroundScheduler | None:
    global _scheduler
    s = get_settings()
    if not s.discovery_schedule_enabled:
        log.info("scheduler disabled (DISCOVERY_SCHEDULE_ENABLED=false)")
        return None
    sched = BackgroundScheduler(timezone=s.timezone, job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 6 * 3600})
    sched.add_job(
        run_discovery,
        CronTrigger(hour=s.discovery_hour, minute=s.discovery_minute, timezone=s.timezone),
        id="daily_discovery",
        kwargs={"trigger": "scheduled"},
        replace_existing=True,
    )
    sched.add_job(follow_up_reminders, CronTrigger(minute=7, timezone=s.timezone), id="follow_up_reminders", replace_existing=True)
    sched.start()
    _scheduler = sched
    _stale_catch_up()
    log.info("scheduler started; next discovery at %s", sched.get_job("daily_discovery").next_run_time)
    return sched


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None


def next_discovery_time() -> str | None:
    if _scheduler is None:
        return None
    job = _scheduler.get_job("daily_discovery")
    return job.next_run_time.isoformat() if job and job.next_run_time else None
