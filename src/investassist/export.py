"""Export des resultats vers des fichiers JSON destines au site statique.

Le site publie sur Netlify ne calcule rien : il lit ces fichiers, produits en
amont par l'analyse (localement ou via GitHub Actions). Le format est donc la
frontiere entre le moteur Python et l'interface web.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import ScoringConfig
from .disclaimers import DATA_LIMITS, MAIN_HTML, WHAT_THIS_IS, WHAT_THIS_IS_NOT
from .models import StockScore

# Nombre d'analyses conservees dans l'historique publie : borne la taille du
# fichier tout en couvrant plusieurs mois d'executions quotidiennes.
MAX_HISTORIQUE = 90

PILLAR_LABELS = {
    "growth": "Croissance",
    "valuation": "Valorisation",
    "profitability": "Rentabilité",
    "balance_sheet": "Qualité du bilan",
    "dividend": "Dividende",
    "qualitative": "Signal qualitatif",
}


def _criterion_payload(criterion) -> dict[str, Any]:
    return {
        "key": criterion.key,
        "label": criterion.label,
        "unit": criterion.unit,
        "value": criterion.value,
        "score": None if criterion.score is None else round(criterion.score, 1),
        "weight": criterion.weight,
        "detail": criterion.detail,
        "reason_missing": criterion.reason_missing,
        # Distingue « ce critere n'a pas de sens ici » de « la donnee manque » :
        # l'interface ne doit pas presenter une banque comme mal renseignee
        # parce qu'elle n'a pas de ratio de liquidite generale.
        "not_applicable": criterion.not_applicable,
    }


def score_payload(score: StockScore, rank: int | None = None) -> dict[str, Any]:
    return {
        "ticker": score.ticker,
        "name": score.name,
        "sector": score.sector,
        "region": score.region,
        "country": score.country,
        "sector_rank": score.sector_rank,
        "sector_count": score.sector_count,
        "currency": score.currency,
        "price": score.price,
        "composite": None if score.composite is None else round(score.composite, 1),
        "rank": rank,
        "window_years": score.window_years,
        "coverage": round(score.coverage, 3),
        "ranked": score.ranked,
        "exclusion_reason": score.exclusion_reason,
        "warnings": score.warnings,
        "pillars": {
            key: {
                "label": PILLAR_LABELS.get(key, key),
                "score": None if pillar.score is None else round(pillar.score, 1),
                "weight": pillar.weight,
                "coverage": round(pillar.coverage, 3),
                "neutralized": pillar.neutralized,
                "criteria": [_criterion_payload(c) for c in pillar.criteria],
            }
            for key, pillar in score.pillars.items()
        },
    }


def ranking_payload(
    ranked: list[StockScore],
    excluded: list[StockScore],
    failures: dict[str, str],
    cfg: ScoringConfig,
    *,
    universes: list[str],
    generated_at: datetime | None = None,
    duration_seconds: float | None = None,
) -> dict[str, Any]:
    """Charge utile complete d'une analyse, telle que consommee par le site."""
    generated_at = generated_at or datetime.now()
    return {
        "generated_at": generated_at.isoformat(timespec="seconds"),
        "universes": universes,
        "duration_seconds": None if duration_seconds is None else round(duration_seconds, 1),
        "counts": {
            "ranked": len(ranked),
            "excluded": len(excluded),
            "failed": len(failures),
        },
        # L'avertissement voyage AVEC les donnees : une interface qui les
        # affiche sans lui serait immediatement incoherente.
        "disclaimer": {
            "main": MAIN_HTML,
            "what_this_is": WHAT_THIS_IS,
            "what_this_is_not": WHAT_THIS_IS_NOT,
            "data_limits": DATA_LIMITS,
        },
        "methodology": methodology_payload(cfg),
        "ranked": [score_payload(s, i) for i, s in enumerate(ranked, start=1)],
        "excluded": [score_payload(s) for s in excluded],
        "failures": [{"ticker": t, "reason": r} for t, r in sorted(failures.items())],
    }


def methodology_payload(cfg: ScoringConfig) -> dict[str, Any]:
    return {
        "target_years": cfg.target_years,
        "min_years": cfg.min_years,
        "min_weight_coverage": cfg.min_weight_coverage,
        "min_pillar_coverage": cfg.min_pillar_coverage,
        "no_dividend_score": cfg.no_dividend_score,
        "pillars": [
            {"key": key, "label": PILLAR_LABELS.get(key, key), "weight": weight}
            for key, weight in cfg.pillar_weights.items()
            if weight > 0
        ],
        "criteria": [
            {
                "key": c.key,
                "label": c.label,
                "pillar": c.pillar,
                "pillar_label": PILLAR_LABELS.get(c.pillar, c.pillar),
                "weight": c.weight,
                "higher_is_better": c.higher_is_better,
                "unit": c.unit,
                "points": c.points,
            }
            for c in cfg.criteria.values()
            if c.enabled
        ],
    }


def append_history(
    previous: dict[str, Any] | None,
    ranked: list[StockScore],
    excluded: list[StockScore],
    *,
    generated_at: datetime | None = None,
    max_runs: int = MAX_HISTORIQUE,
) -> dict[str, Any]:
    """Ajoute l'analyse courante a l'historique publie.

    L'historique vit dans le fichier publie lui-meme : c'est ce qui permet de
    suivre l'evolution d'un score sans base de donnees cote serveur, le site
    etant purement statique.
    """
    generated_at = generated_at or datetime.now()
    historique = dict(previous or {})
    runs: list[dict[str, Any]] = list(historique.get("runs") or [])
    horodatage = generated_at.isoformat(timespec="seconds")

    entrees = {
        s.ticker: {
            "score": None if s.composite is None else round(s.composite, 1),
            "rank": index,
        }
        for index, s in enumerate(ranked, start=1)
    }
    for s in excluded:
        entrees[s.ticker] = {"score": None, "rank": None}

    runs = [r for r in runs if r.get("generated_at") != horodatage]
    runs.append({"generated_at": horodatage, "scores": entrees})
    runs = runs[-max_runs:]

    historique["runs"] = runs
    historique["updated_at"] = horodatage
    return historique


def previous_state(ranking: dict[str, Any] | None) -> tuple[dict[str, dict], dict[str, int]]:
    """Reconstitue l'etat precedent (scores et rangs) a partir d'un export.

    Sur un site statique, le fichier publie lors de l'execution precedente
    tient lieu de memoire : c'est lui qui permet de detecter une variation de
    score ou une entree dans le top N, sans base de donnees.
    """
    if not ranking:
        return {}, {}
    precedent: dict[str, dict] = {}
    rangs: dict[str, int] = {}
    for entree in list(ranking.get("ranked") or []) + list(ranking.get("excluded") or []):
        ticker = entree.get("ticker")
        if not ticker:
            continue
        precedent[ticker] = {
            "composite": entree.get("composite"),
            "rank": entree.get("rank"),
            "ranked": entree.get("ranked", False),
        }
        if entree.get("rank"):
            rangs[ticker] = int(entree["rank"])
    return precedent, rangs


def previous_state_from_history(
    history: dict[str, Any] | None,
) -> tuple[dict[str, dict], dict[str, int]]:
    """Etat precedent lu dans l'historique publie.

    L'historique est un fichier compact, versionne avec le depot : il constitue
    la memoire des alertes d'une execution a l'autre, la ou le classement
    complet serait trop volumineux a conserver a chaque passage.
    """
    runs = list((history or {}).get("runs") or [])
    if not runs:
        return {}, {}
    dernier = runs[-1].get("scores") or {}
    precedent: dict[str, dict] = {}
    rangs: dict[str, int] = {}
    for ticker, entree in dernier.items():
        precedent[ticker] = {
            "composite": entree.get("score"),
            "rank": entree.get("rank"),
            "ranked": entree.get("rank") is not None,
        }
        if entree.get("rank"):
            rangs[ticker] = int(entree["rank"])
    return precedent, rangs


def write_json(path: Path, payload: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Ecriture atomique : un build interrompu ne doit pas laisser un fichier
    # tronque que le site tenterait d'afficher.
    temporaire = path.with_suffix(path.suffix + ".tmp")
    with temporaire.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"), default=str)
    temporaire.replace(path)
    return path


def read_json(path: Path) -> Any | None:
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError):
        return None
