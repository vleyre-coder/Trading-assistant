#!/usr/bin/env python3
"""Execution periodique via APScheduler (alternative a cron).

Lance une analyse complete a heure fixe, evalue les alertes et notifie.
A garder ouvert dans un terminal, ou a remplacer par une tache planifiee
du systeme (voir README).

    python scripts/scheduler.py --hour 19 --minute 30
"""
from __future__ import annotations

import argparse
import logging
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")
log = logging.getLogger("scheduler")


def run_once(extra: list[str]) -> None:
    command = [sys.executable, str(ROOT / "scripts" / "run_screening.py"), *extra]
    log.info("Lancement : %s", " ".join(command))
    completed = subprocess.run(command, cwd=ROOT, check=False)
    log.info("Terminé avec le code %s", completed.returncode)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hour", type=int, default=19, help="Heure d'execution (0-23)")
    parser.add_argument("--minute", type=int, default=0, help="Minute d'execution")
    parser.add_argument("--days", default="mon-fri", help="Jours (cron APScheduler)")
    parser.add_argument("--now", action="store_true", help="Execute immediatement puis planifie")
    parser.add_argument("--universes", default="", help="Transmis a run_screening.py")
    args = parser.parse_args()

    extra = ["--quiet"] + (["--universes", args.universes] if args.universes else [])

    if args.now:
        run_once(extra)

    from apscheduler.schedulers.blocking import BlockingScheduler

    scheduler = BlockingScheduler()
    scheduler.add_job(
        run_once, "cron", args=[extra],
        day_of_week=args.days, hour=args.hour, minute=args.minute,
        id="screening", misfire_grace_time=3600,
    )
    log.info(
        "Planification active : %s à %02d:%02d. Ctrl+C pour arrêter.",
        args.days, args.hour, args.minute,
    )
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("Arrêt demandé.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
