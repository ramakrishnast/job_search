"""Command-line entry points: `python -m app.cli <command>`."""

from __future__ import annotations

import argparse
import logging
import shutil
import sqlite3
import sys
from datetime import datetime

from .config import get_settings


def _bootstrap() -> None:
    from .db import init_engine
    from .main import bootstrap

    logging.basicConfig(level=get_settings().log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    init_engine()
    bootstrap()


def cmd_discover(args: argparse.Namespace) -> int:
    _bootstrap()
    from .db import SessionLocal
    from .models import DiscoveryRun
    from .services.discovery import run_discovery

    rid = run_discovery(args.trigger)
    if rid is None:
        print("A discovery run is already in progress.")
        return 1
    db = SessionLocal()
    r = db.get(DiscoveryRun, rid)
    assert r is not None
    print(f"\nDiscovery run {r.id}: {r.status}")
    print(
        f"  fetched={r.fetched_count} new={r.new_count} new_matching={r.new_matching_count} closed={r.closed_count} scored={r.scored_count}"
    )
    for s in r.company_stats:
        if s["status"] == "ok":
            print(f"  ✓ {s['company']:<22} {s['ats']:<10} fetched={s.get('fetched', 0):<4} matching_open={s.get('matching_open', 0)}")
        else:
            print(f"  ✗ {s['company']:<22} {s['ats']:<10} {s['status']}: {s.get('error')}")
    db.close()
    return 0 if r.status in ("ok", "partial") else 2


def cmd_verify(_: argparse.Namespace) -> int:
    from .ats import FETCHERS, PoliteClient, detect_ats
    from .services.discovery import load_company_config

    s = get_settings()
    bad = 0
    with PoliteClient(user_agent=s.http_user_agent, min_interval=s.http_min_interval_seconds) as client:
        for c in load_company_config():
            ats, slug = (c.get("ats") or "manual").lower(), c.get("slug")
            if ats not in FETCHERS or not slug:
                print(f"  - {c['name']:<22} manual")
                continue
            found = detect_ats(client, slug)
            if found and found[0] == ats:
                print(f"  ✓ {c['name']:<22} {ats}/{slug}: {found[1]} postings")
            elif found:
                bad += 1
                print(f"  ! {c['name']:<22} configured {ats}, but slug '{slug}' answers on {found[0]} ({found[1]} postings)")
            else:
                bad += 1
                print(f"  ✗ {c['name']:<22} no public Greenhouse/Lever/Ashby board for slug '{slug}'")
    return 1 if bad else 0


def cmd_backup(_: argparse.Namespace) -> int:
    s = get_settings()
    src = s.data_dir / "jobpilot.db"
    if not src.exists():
        print("no database yet")
        return 1
    dest = s.data_dir / "backups" / f"jobpilot-{datetime.now():%Y%m%d-%H%M%S}.db"
    with sqlite3.connect(src) as a, sqlite3.connect(dest) as b:
        a.backup(b)  # consistent online backup (safe while the app runs)
    print(f"backup written: {dest}")
    backups = sorted((s.data_dir / "backups").glob("jobpilot-*.db"))
    for old in backups[:-14]:
        old.unlink()
    return 0


def cmd_migrate(_: argparse.Namespace) -> int:
    _bootstrap()
    print("database is up to date")
    return 0


def cmd_reset(_: argparse.Namespace) -> int:
    s = get_settings()
    if input(f"Delete ALL local data in {s.data_dir}? Type 'yes': ") != "yes":
        return 1
    shutil.rmtree(s.data_dir)
    print("deleted")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="jobpilot")
    sub = p.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("discover", help="run discovery once against all enabled companies")
    d.add_argument("--trigger", default="manual", choices=["manual", "demo", "scheduled"])
    d.set_defaults(fn=cmd_discover)
    sub.add_parser("verify-companies", help="check each configured board slug against the public ATS APIs").set_defaults(fn=cmd_verify)
    sub.add_parser("backup", help="online backup of the SQLite DB to data/backups (keeps 14)").set_defaults(fn=cmd_backup)
    sub.add_parser("migrate", help="apply database migrations and seed data").set_defaults(fn=cmd_migrate)
    sub.add_parser("reset", help="delete all local data (asks for confirmation)").set_defaults(fn=cmd_reset)
    args = p.parse_args(argv)
    return int(args.fn(args))


if __name__ == "__main__":
    sys.exit(main())
