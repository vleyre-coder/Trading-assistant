"""Agregation des criteres en sous-scores, piliers et score composite.

Le score composite n'est jamais un chiffre isole : chaque titre porte le
detail de ses criteres, la fenetre reellement utilisee et son taux de
couverture de donnees. Un titre dont les donnees sont trop incompletes est
EXCLU du classement plutot que classe sur une base partielle et trompeuse.
"""
from __future__ import annotations

import statistics
from datetime import datetime

import pandas as pd

from . import criteria as crit
from .config import ScoringConfig
from .models import CriterionResult, Fundamentals, PillarResult, StockScore

def pays_no_dividend(fund: Fundamentals) -> bool:
    """Le titre ne verse-t-il PAS de dividende (par opposition a : on l'ignore) ?

    Distinction essentielle : « ne verse pas de dividende » est une
    caracteristique du titre, qui ne doit pas etre penalisee ; « donnee
    manquante » est une lacune, qui doit neutraliser le pilier.

    Le constat repose sur trois elements structurels et non sur le libelle
    des messages d'erreur : aucun dividende par action dans la fenetre,
    aucun rendement courant, et des donnees de marche par ailleurs presentes
    (sans quoi on ne peut rien conclure).
    """
    paid = any(value > 0 for _, value in fund.series("dividend_per_share"))
    current_yield = fund.snapshot.dividend_yield or 0.0
    market_data_available = fund.snapshot.price is not None
    return not paid and current_yield <= 0 and market_data_available


def sector_pe_medians(
    funds: list[Fundamentals], *, min_peers: int = 3
) -> dict[str, float]:
    """Mediane du P/E par secteur, calculee sur l'univers analyse.

    Un secteur represente par moins de min_peers titres valorisables ne
    produit pas de mediane exploitable : le critere sera marque N/A pour ces
    titres plutot que compare a un echantillon non significatif.
    """
    by_sector: dict[str, list[float]] = {}
    for f in funds:
        pe = f.snapshot.trailing_pe
        sector = f.snapshot.sector
        if sector and pe and pe > 0:
            by_sector.setdefault(sector, []).append(float(pe))
    return {
        sector: statistics.median(values)
        for sector, values in by_sector.items()
        if len(values) >= min_peers
    }


def pe_vs_sector(fund: Fundamentals, medians: dict[str, float]) -> crit.Result:
    pe = fund.snapshot.trailing_pe
    sector = fund.snapshot.sector
    if pe is None or pe <= 0:
        return None, "", "P/E courant indisponible ou negatif"
    if not sector:
        return None, "", "secteur non renseigne par la source de donnees"
    median = medians.get(sector)
    if median is None:
        return None, "", f"pas assez de pairs valorisables dans le secteur « {sector} » de l'univers analyse"
    if median <= 0:
        return None, "", "mediane sectorielle non exploitable"
    detail = f"P/E {pe:.1f} vs mediane du secteur « {sector} » {median:.1f}"
    return pe / median, detail, ""


def score_stock(
    fund: Fundamentals,
    cfg: ScoringConfig,
    *,
    prices: pd.DataFrame | None = None,
    sector_medians: dict[str, float] | None = None,
    raw_values: dict[str, crit.Result] | None = None,
) -> StockScore:
    values = dict(raw_values) if raw_values is not None else crit.compute_all(fund, prices)
    values["pe_vs_sector"] = pe_vs_sector(fund, sector_medians or {})

    snapshot = fund.snapshot
    score = StockScore(
        ticker=fund.ticker,
        name=snapshot.name,
        sector=snapshot.sector,
        region=fund.region,
        currency=snapshot.currency,
        price=snapshot.price,
        composite=None,
        window_years=fund.years_available,
        warnings=list(fund.warnings),
        computed_at=datetime.now(),
    )

    # --- Sous-scores par pilier ---------------------------------------
    for pillar, pillar_weight in cfg.pillar_weights.items():
        members = cfg.criteria_for(pillar)
        if pillar_weight <= 0 or not members:
            continue

        results: list[CriterionResult] = []
        for criterion in members:
            value, detail, reason = values.get(criterion.key, (None, "", "critere non calcule"))
            results.append(
                CriterionResult(
                    key=criterion.key,
                    label=criterion.label,
                    unit=criterion.unit,
                    value=value,
                    score=criterion.score(value),
                    weight=criterion.weight,
                    pillar=pillar,
                    detail=detail,
                    reason_missing=reason,
                )
            )

        total_weight = sum(r.weight for r in results) or 1.0
        available_weight = sum(r.weight for r in results if r.available)
        coverage = available_weight / total_weight

        # Cas particulier du dividende : aucun versement n'est pas une lacune
        # de donnees. Le pilier recoit le score neutre configure, sans penalite
        # pour les valeurs de croissance qui ne distribuent pas.
        no_dividend = (
            pillar == "dividend"
            and coverage < 1.0
            and pays_no_dividend(fund)
        )
        if no_dividend:
            pillar_result = PillarResult(
                key=pillar,
                weight=pillar_weight,
                score=cfg.no_dividend_score,
                coverage=1.0,
                criteria=results,
                neutralized=True,
            )
            score.warnings.append(
                "Titre sans dividende : pilier dividende neutralise "
                f"(score {cfg.no_dividend_score:.0f}/100), sans penalite."
            )
        elif coverage < cfg.min_pillar_coverage:
            pillar_result = PillarResult(
                key=pillar, weight=pillar_weight, score=None, coverage=coverage,
                criteria=results, neutralized=True,
            )
        else:
            weighted = sum(r.score * r.weight for r in results if r.available)
            pillar_result = PillarResult(
                key=pillar,
                weight=pillar_weight,
                score=weighted / available_weight,
                coverage=coverage,
                criteria=results,
            )
        score.pillars[pillar] = pillar_result

    # --- Score composite ----------------------------------------------
    total_pillar_weight = sum(p.weight for p in score.pillars.values()) or 1.0
    score.coverage = (
        sum(p.weight * (1.0 if p.score is not None else 0.0) * max(p.coverage, 0.0)
            for p in score.pillars.values())
        / total_pillar_weight
    )

    usable = [p for p in score.pillars.values() if p.score is not None]
    if usable:
        weight_sum = sum(p.weight for p in usable)
        score.composite = sum(p.score * p.weight for p in usable) / weight_sum

    # --- Regles d'exclusion du classement -----------------------------
    reasons: list[str] = []
    if fund.years_available < cfg.min_years:
        reasons.append(
            f"historique fondamental insuffisant ({fund.years_available} exercice(s), "
            f"{cfg.min_years} requis)"
        )
    if score.composite is None:
        reasons.append("aucun pilier calculable")
    if score.coverage < cfg.min_weight_coverage:
        reasons.append(
            f"couverture des criteres trop faible ({score.coverage * 100:.0f} %, "
            f"minimum {cfg.min_weight_coverage * 100:.0f} %)"
        )
    neutralized = [p.key for p in score.pillars.values() if p.score is None]
    if neutralized:
        score.warnings.append(
            "Piliers neutralises faute de donnees : " + ", ".join(neutralized)
        )

    if reasons:
        score.ranked = False
        score.exclusion_reason = "Donnees fondamentales incompletes — " + " ; ".join(reasons)
    else:
        score.ranked = True

    return score


def rank(scores: list[StockScore]) -> list[StockScore]:
    """Tri par score composite decroissant.

    Le rang exprime l'adequation aux criteres fondamentaux au moment du
    calcul. Il ne prejuge d'aucune evolution de cours.
    """
    ranked = [s for s in scores if s.ranked and s.composite is not None]
    ranked.sort(key=lambda s: (-s.composite, s.ticker))
    return ranked


def excluded(scores: list[StockScore]) -> list[StockScore]:
    return [s for s in scores if not s.ranked]


def to_dataframe(scores: list[StockScore]) -> pd.DataFrame:
    if not scores:
        return pd.DataFrame()
    frame = pd.DataFrame([s.to_row() for s in scores])
    if "score" in frame.columns:
        frame = frame.sort_values("score", ascending=False, na_position="last")
        frame.insert(0, "rang", range(1, len(frame) + 1))
    return frame.reset_index(drop=True)
