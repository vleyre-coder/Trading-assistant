#!/usr/bin/env python3
"""Analyse complete d'un ou plusieurs univers, en ligne de commande.

Concu pour une execution planifiee (cron sous Linux/macOS, Taches planifiees
sous Windows) : enregistre les scores en base, evalue les regles d'alerte et
envoie les notifications.

Exemples :
    python scripts/run_screening.py                       # univers par defaut
    python scripts/run_screening.py --universes cac40
    python scripts/run_screening.py --tickers MSFT,AIR.PA --no-cache
    python scripts/run_screening.py --top 20 --no-alerts
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from investassist.alerts import Notifier, evaluate_rules  # noqa: E402
from investassist.alerts.rules import attach_earnings_dates  # noqa: E402
from investassist.config import load_scoring, load_settings, load_universes  # noqa: E402
from investassist.disclaimers import MAIN  # noqa: E402
from investassist.screener import Screener  # noqa: E402
from investassist.storage import Database  # noqa: E402

log = logging.getLogger("screening")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--universes", default="", help="Liste separee par des virgules (defaut : config)")
    parser.add_argument("--tickers", default="", help="Liste de tickers, ignore --universes")
    parser.add_argument("--no-cache", action="store_true", help="Force la relecture des sources")
    parser.add_argument("--no-alerts", action="store_true", help="N'evalue pas les regles d'alerte")
    parser.add_argument("--no-persist", action="store_true", help="N'ecrit pas en base")
    parser.add_argument("--top", type=int, default=15, help="Nombre de lignes affichees")
    parser.add_argument("--quiet", action="store_true", help="N'affiche que le classement")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.WARNING if args.quiet else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )

    settings, config = load_settings(), load_scoring()
    database = Database(settings.database_path)
    screener = Screener(settings, config, database=database)

    tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    if args.universes:
        universes = [u.strip() for u in args.universes.split(",") if u.strip()]
    else:
        universes = load_universes().get("default_selection") or []

    started = time.time()
    result = screener.run(
        universes,
        tickers=tickers or None,
        use_cache=not args.no_cache,
        persist=not args.no_persist,
        progress=None if args.quiet else (
            lambda done, total, ticker: log.info("[%d/%d] %s", done, total, ticker)
        ),
    )

    print(f"\n⚠️  {MAIN}\n")
    print(f"Classement d'adéquation aux critères fondamentaux — "
          f"{len(result.ranked)} titres classés en {time.time() - started:.0f} s")
    print(f"{'Rang':>4} {'Ticker':<10} {'Score':>6} {'Fen.':>5} {'Cvg':>5}  Nom")
    for index, score in enumerate(result.ranked[: args.top], start=1):
        print(f"{index:>4} {score.ticker:<10} {score.composite:>6.1f} "
              f"{score.window_years:>4}a {score.coverage * 100:>4.0f}%  {(score.name or '')[:38]}")

    if result.excluded:
        print(f"\n{len(result.excluded)} titre(s) exclu(s) pour données fondamentales incomplètes :")
        for score in result.excluded[:10]:
            print(f"  - {score.ticker:<10} {score.exclusion_reason}")
        if len(result.excluded) > 10:
            print(f"  … et {len(result.excluded) - 10} autre(s)")

    if result.failures:
        print(f"\n{len(result.failures)} échec(s) de récupération : "
              + ", ".join(sorted(result.failures)))

    if not args.no_alerts and result.run_id:
        previous = database.previous_snapshot(result.run_id)
        previous_ranks = {t: v["rank"] for t, v in previous.items() if v.get("rank")}
        attach_earnings_dates(result.scores, result.last_earnings)
        events = evaluate_rules(
            database, result.scores, config,
            previous=previous, ranks=result.ranks, previous_ranks=previous_ranks,
        )
        if events:
            status = Notifier(settings).dispatch(events)
            print(f"\n{len(events)} alerte(s) déclenchée(s) — canaux : {status or 'aucun'}")
            for event in events:
                print(f"  • {event.message}")
        else:
            print("\nAucune alerte déclenchée.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
