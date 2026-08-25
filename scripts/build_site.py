#!/usr/bin/env python3
"""Analyse complete puis generation des donnees du site statique.

Point d'entree de l'execution planifiee (GitHub Actions) :
  1. relit l'analyse precedente publiee (elle tient lieu de memoire, le site
     etant statique et sans base de donnees) ;
  2. relance l'analyse de l'univers ;
  3. ecrit les fichiers JSON consommes par le site ;
  4. evalue les regles d'alerte de config/alerts.yaml et envoie l'email.

    python scripts/build_site.py
    python scripts/build_site.py --universes cac40 --output web/data
    python scripts/build_site.py --no-alerts --tickers MSFT,AIR.PA
"""
from __future__ import annotations

import argparse
import logging
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from investassist import export  # noqa: E402
from investassist.alerts import Notifier, evaluate_rules  # noqa: E402
from investassist.alerts.rules import attach_earnings_dates  # noqa: E402
from investassist.config import load_scoring, load_settings, load_universes  # noqa: E402
from investassist.disclaimers import MAIN  # noqa: E402
from investassist.screener import Screener  # noqa: E402
from investassist.storage import Database  # noqa: E402

log = logging.getLogger("build_site")

SORTIE_DEFAUT = ROOT / "web" / "data"
FICHIER_ALERTES = ROOT / "config" / "alerts.yaml"


def charger_regles() -> dict[str, Any]:
    if not FICHIER_ALERTES.exists():
        return {}
    with FICHIER_ALERTES.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def regles_effectives(configuration: dict[str, Any]) -> list[dict[str, Any]]:
    """Developpe la watchlist et les regles explicites en regles unitaires."""
    general = configuration.get("general") or {}
    regles: list[dict[str, Any]] = []
    for ticker in configuration.get("watchlist") or []:
        for kind in general.get("kinds") or []:
            parametres: dict[str, Any] = {}
            if kind == "score_change":
                parametres["threshold"] = general.get("score_change_threshold", 5.0)
            elif kind in ("top_n_entry", "top_n_exit"):
                parametres["n"] = general.get("top_n", 20)
            regles.append({"ticker": str(ticker).upper(), "kind": kind, "params": parametres})

    for regle in configuration.get("rules") or []:
        parametres = {k: v for k, v in regle.items() if k not in ("ticker", "kind")}
        regles.append(
            {
                "ticker": str(regle["ticker"]).upper(),
                "kind": regle["kind"],
                "params": parametres,
            }
        )
    return regles


def preparer_base(
    regles: list[dict[str, Any]], etat: dict[str, Any], chemin: Path
) -> tuple[Database, dict[tuple[str, str], int]]:
    """Base temporaire alimentee par les regles du depot et l'etat publie.

    L'execution planifiee ne conserve aucun disque entre deux passages : la
    memoire des alertes (seuil deja franchi, derniere publication vue) voyage
    dans un fichier JSON publie avec le site, puis est rechargee ici. Le moteur
    d'alertes reste ainsi strictement le meme qu'en local.
    """
    base = Database(chemin)
    identifiants: dict[tuple[str, str], int] = {}
    etats_regles = etat.get("rules") or {}
    for regle in regles:
        rule_id = base.add_alert_rule(regle["ticker"], regle["kind"], regle["params"])
        cle = f"{regle['ticker']}:{regle['kind']}"
        identifiants[(regle["ticker"], regle["kind"])] = rule_id
        if etats_regles.get(cle):
            base.set_rule_state(rule_id, etats_regles[cle])
    for ticker, derniere in (etat.get("earnings") or {}).items():
        base.set_last_earnings_seen(ticker, derniere)
    return base, identifiants


def etat_a_publier(base: Database) -> dict[str, Any]:
    etats = {
        f"{regle['ticker']}:{regle['kind']}": regle.get("last_state")
        for regle in base.alert_rules(enabled_only=False)
        if regle.get("last_state")
    }
    with base.connect() as conn:
        publications = {
            ligne["ticker"]: ligne["last_report"]
            for ligne in conn.execute("SELECT ticker, last_report FROM earnings_seen").fetchall()
        }
    return {"rules": etats, "earnings": publications}


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--universes", default="", help="Univers separes par des virgules")
    parser.add_argument("--tickers", default="", help="Liste de tickers, ignore --universes")
    parser.add_argument("--output", default=str(SORTIE_DEFAUT), help="Repertoire des JSON")
    parser.add_argument("--no-alerts", action="store_true", help="N'evalue pas les alertes")
    parser.add_argument("--cache", action="store_true", help="Autorise le cache disque")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S"
    )

    settings, cfg = load_settings(), load_scoring()
    sortie = Path(args.output)
    if not sortie.is_absolute():
        sortie = ROOT / sortie

    ancien_classement = export.read_json(sortie / "ranking.json")
    ancien_historique = export.read_json(sortie / "history.json")
    etat_alertes = export.read_json(sortie / "alert_state.json") or {}
    # L'historique fait foi : il est publie a chaque execution et reste
    # compact, la ou le classement complet n'est pas conserve d'un passage a
    # l'autre en integration continue.
    precedent, rangs_precedents = export.previous_state_from_history(ancien_historique)
    if not precedent:
        precedent, rangs_precedents = export.previous_state(ancien_classement)
    if ancien_classement:
        log.info(
            "Analyse precedente du %s : %s titres classes.",
            ancien_classement.get("generated_at"),
            (ancien_classement.get("counts") or {}).get("ranked"),
        )

    tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    universes = [u.strip() for u in args.universes.split(",") if u.strip()] or (
        load_universes().get("default_selection") or []
    )

    depart = time.time()
    screener = Screener(settings, cfg)
    resultat = screener.run(
        universes,
        tickers=tickers or None,
        use_cache=args.cache,
        persist=False,
        progress=lambda fait, total, ticker: log.info("[%d/%d] %s", fait, total, ticker),
    )
    duree = time.time() - depart
    genere_le = datetime.now()

    # ------------------------------------------------------------ export
    export.write_json(
        sortie / "ranking.json",
        export.ranking_payload(
            resultat.ranked, resultat.excluded, resultat.failures, cfg,
            universes=universes or ["personnalise"],
            generated_at=genere_le, duration_seconds=duree,
        ),
    )
    export.write_json(
        sortie / "history.json",
        export.append_history(
            ancien_historique, resultat.ranked, resultat.excluded, generated_at=genere_le
        ),
    )
    log.info(
        "Ecrit : %s titres classes, %s exclus, %s echecs en %.0f s.",
        len(resultat.ranked), len(resultat.excluded), len(resultat.failures), duree,
    )

    # ----------------------------------------------------------- alertes
    if args.no_alerts:
        return 0

    configuration = charger_regles()
    regles = regles_effectives(configuration)
    if not regles:
        log.info("Aucune regle d'alerte definie dans config/alerts.yaml.")
        return 0

    destinataires = ((configuration.get("email") or {}).get("recipients")) or []
    if destinataires and not settings.email.get("recipients"):
        settings.email["recipients"] = destinataires

    with tempfile.TemporaryDirectory() as repertoire:
        base, _ = preparer_base(regles, etat_alertes, Path(repertoire) / "alertes.sqlite")
        attach_earnings_dates(resultat.scores, resultat.last_earnings)
        evenements = evaluate_rules(
            base, resultat.scores, cfg,
            previous=precedent, ranks=resultat.ranks, previous_ranks=rangs_precedents,
        )
        export.write_json(sortie / "alert_state.json", etat_a_publier(base))

    if not rangs_precedents and not precedent:
        # Premiere analyse : sans point de comparaison, tout titre paraitrait
        # « entrer » dans le classement. On initialise l'etat sans notifier.
        # (Le fichier d'historique vient d'etre ecrit : le prochain passage
        # disposera donc d'une reference.)
        log.info(
            "Premiere analyse : %s alerte(s) ignoree(s), etat initialise pour "
            "les prochaines executions.",
            len(evenements),
        )
        return 0

    if not evenements:
        log.info("Aucune alerte declenchee.")
        return 0

    log.info("%s alerte(s) declenchee(s) :", len(evenements))
    for evenement in evenements:
        log.info("  • %s", evenement.message)

    if settings.alerts_email_enabled:
        statut = Notifier(settings).dispatch(evenements)
        log.info("Envoi des alertes : %s", statut)
    else:
        log.info("Envoi email desactive : alertes journalisees uniquement.")

    print(f"\n⚠️  {MAIN}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
