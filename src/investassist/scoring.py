"""Agregation des criteres en sous-scores, piliers et score composite.

Le score composite n'est jamais un chiffre isole : chaque titre porte le
detail de ses criteres, la fenetre reellement utilisee et son taux de
couverture de donnees. Un titre dont les donnees sont trop incompletes est
EXCLU du classement plutot que classe sur une base partielle et trompeuse.
"""
from __future__ import annotations

import logging
import statistics
from dataclasses import replace
from datetime import datetime

import pandas as pd

from . import criteria as crit
from .config import ScoringConfig
from .models import CriterionResult, Fundamentals, PillarResult, StockScore

log = logging.getLogger(__name__)



def _benefice_positif(fund: Fundamentals) -> bool:
    """Le dernier exercice connu est-il benificiaire ?

    Sert a distinguer « ce ratio n'existe pas pour cette societe » de « la
    donnee manque ». Une societe en perte n'a pas de P/E : les trois criteres
    qui en decoulent sont sans objet, exactement comme la dette nette sur
    EBITDA pour une banque. Les compter comme des lacunes revenait a
    neutraliser le pilier valorisation et a faire disparaitre du classement
    les societes en forte croissance pas encore rentables.
    """
    for rec in reversed(fund.sorted_annual()):
        net = rec.get("net_income")
        if net is not None:
            return net > 0
    # Aucun resultat net connu : c'est une vraie lacune, pas une perte.
    return True


# Conditions prealables reconnues dans le champ « requires » de scoring.yaml.
PRECONDITIONS = {
    "benefice_positif": _benefice_positif,
}


def raisons_sans_objet(fund: Fundamentals, criterion) -> str:
    """Explique pourquoi un critere ne s'applique pas a ce titre, ou "" sinon."""
    if not criterion.applies_to(fund.snapshot.sector):
        return f"sans objet pour le secteur « {fund.snapshot.sector} »"
    for nom in criterion.requires:
        predicat = PRECONDITIONS.get(nom)
        if predicat is None:
            log.warning(
                "config/scoring.yaml : condition « %s » inconnue sur le critère %s.",
                nom, criterion.key,
            )
            continue
        if not predicat(fund):
            if nom == "benefice_positif":
                return (
                    "sans objet : société en perte sur le dernier exercice, "
                    "le P/E n'existe pas"
                )
            return f"sans objet : condition « {nom} » non remplie"
    return ""


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
        return None, "", "P/E courant indisponible ou négatif"
    if not sector:
        return None, "", "secteur non renseigne par la source de données"
    median = medians.get(sector)
    if median is None:
        return None, "", f"pas assez de pairs valorisables dans le secteur « {sector} » de l'univers analyse"
    if median <= 0:
        return None, "", "médiane sectorielle non exploitable"
    detail = f"P/E {pe:.1f} vs médiane du secteur « {sector} » {median:.1f}"
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
        country=snapshot.country,
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
        if not members:
            continue
        # Un pilier a poids nul est tout de meme CALCULE : un profil peut lui
        # donner un poids reel sans qu'aucune donnee soit rechargee. Il
        # n'entre pas dans le score composite pour autant, puisque sa
        # contribution y est multipliee par zero — la ponderation d'origine
        # reste donc strictement inchangee.

        results: list[CriterionResult] = []
        for criterion in members:
            # Un critere sans pertinence pour le secteur est ecarte AVANT tout
            # calcul : ni note, ni compte comme lacune. « Dette nette /
            # EBITDA » pour une banque n'est pas une donnee manquante, c'est
            # une question qui ne se pose pas.
            sans_objet = raisons_sans_objet(fund, criterion)
            if sans_objet:
                results.append(
                    CriterionResult(
                        key=criterion.key,
                        label=criterion.label,
                        unit=criterion.unit,
                        value=None,
                        score=None,
                        weight=criterion.weight,
                        pillar=pillar,
                        detail="",
                        reason_missing=sans_objet,
                        not_applicable=True,
                    )
                )
                continue
            value, detail, reason = values.get(criterion.key, (None, "", "critère non calculé"))
            results.append(
                CriterionResult(
                    key=criterion.key,
                    label=criterion.label,
                    unit=criterion.unit,
                    value=value,
                    score=criterion.score(value, snapshot.sector),
                    weight=criterion.weight,
                    pillar=pillar,
                    detail=detail,
                    reason_missing=reason,
                )
            )

        # La couverture se mesure sur les seuls criteres applicables : le poids
        # d'un critere sans objet est redistribue sur les autres.
        applicables = [r for r in results if not r.not_applicable]
        total_weight = sum(r.weight for r in applicables) or 1.0
        available_weight = sum(r.weight for r in applicables if r.available)
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
                "Titre sans dividende : pilier dividende neutralisé "
                f"(score {cfg.no_dividend_score:.0f}/100), sans pénalité."
            )
        elif not applicables:
            # Aucun critere du pilier n'a de sens pour ce secteur. Le pilier
            # est neutralise, mais il faut le dire autrement qu'une lacune :
            # rien ne manque, la question ne se pose pas.
            pillar_result = PillarResult(
                key=pillar, weight=pillar_weight, score=None, coverage=1.0,
                criteria=results, neutralized=True,
            )
            score.warnings.append(
                f"Pilier {pillar} sans objet pour le secteur "
                f"« {snapshot.sector} » : poids redistribué."
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
            f"couverture des critères trop faible ({score.coverage * 100:.0f} %, "
            f"minimum {cfg.min_weight_coverage * 100:.0f} %)"
        )
    neutralized = [p.key for p in score.pillars.values() if p.score is None]
    if neutralized:
        score.warnings.append(
            "Piliers neutralisés faute de données : " + ", ".join(neutralized)
        )

    if reasons:
        score.ranked = False
        score.exclusion_reason = "Données fondamentales incomplètes — " + " ; ".join(reasons)
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


def assign_sector_ranks(ranked: list[StockScore]) -> None:
    """Renseigne le rang de chaque titre au sein de son secteur.

    Le classement general repose sur des seuils ABSOLUS, choisis pour rester
    comparables d'une execution a l'autre. La contrepartie est structurelle :
    un distributeur ou un service public ne peut pas atteindre la marge d'un
    editeur de logiciels, et les premieres places reviennent donc toujours aux
    memes secteurs. Le rang sectoriel repond a l'autre question — « le
    meilleur de sa categorie » — sans toucher au score composite.
    """
    par_secteur: dict[str, list[StockScore]] = {}
    for score in ranked:
        par_secteur.setdefault(score.sector or "Non renseigné", []).append(score)
    for membres in par_secteur.values():
        membres.sort(key=lambda s: (-(s.composite or 0.0), s.ticker))
        for position, score in enumerate(membres, start=1):
            score.sector_rank = position
            score.sector_count = len(membres)


def to_dataframe(scores: list[StockScore]) -> pd.DataFrame:
    if not scores:
        return pd.DataFrame()
    frame = pd.DataFrame([s.to_row() for s in scores])
    if "score" in frame.columns:
        frame = frame.sort_values("score", ascending=False, na_position="last")
        frame.insert(0, "rang", range(1, len(frame) + 1))
    return frame.reset_index(drop=True)


# =====================================================================
# Profils : la meme donnee, une autre question
# =====================================================================
def _exigences_non_tenues(score: StockScore, profil) -> list[str]:
    """Seuils propres au profil qui ecartent un titre du classement.

    Distincts des regles de qualite de donnees : ici le titre est
    parfaitement mesure, il ne correspond simplement pas a ce qui est
    recherche. Le motif doit donc le dire dans ces termes.
    """
    motifs: list[str] = []
    exigences = profil.exigences

    plancher = exigences.get("couverture_minimale")
    if plancher is not None and score.coverage < plancher:
        motifs.append(
            f"couverture des critères de {score.coverage * 100:.0f} %, "
            f"minimum {plancher * 100:.0f} % pour ce profil"
        )

    plafond = exigences.get("dette_nette_ebitda_max")
    if plafond is not None:
        critere = score.criterion("net_debt_to_ebitda")
        # Un critere sans objet (une banque) ne peut pas manquer a
        # l'exigence : la question ne se pose pas pour elle.
        if critere and critere.value is not None and not critere.not_applicable:
            if critere.value > plafond:
                motifs.append(
                    f"endettement de {critere.value:.1f} fois l'EBITDA, "
                    f"maximum {plafond:.0f} pour ce profil"
                )

    volatilite_max = exigences.get("volatilite_max")
    if volatilite_max is not None:
        critere = score.criterion("volatility")
        if critere and critere.value is not None and critere.value > volatilite_max:
            motifs.append(
                f"volatilité annualisée de {critere.value * 100:.0f} %, "
                f"maximum {volatilite_max * 100:.0f} % pour ce profil"
            )

    plancher_marge = exigences.get("marge_nette_moyenne_min")
    if plancher_marge is not None:
        critere = score.criterion("net_margin_avg")
        if critere and critere.value is not None and critere.value < plancher_marge:
            motifs.append(
                f"marge nette moyenne de {critere.value * 100:.1f} %, "
                "profil réservé aux sociétés bénéficiaires"
            )
    return motifs


def appliquer_profil(score: StockScore, cfg: ScoringConfig, profil) -> StockScore:
    """Renote un titre selon un profil, sans recalculer aucun critere.

    Les valeurs et sous-scores par critere ne dependent que des comptes de
    la societe : ils sont conserves tels quels. Seule leur ponderation
    change, donc les scores de pilier, le score composite et l'admission au
    classement. Le recalcul est instantane et hors ligne.

    L'objet d'origine n'est jamais modifie : l'interface doit pouvoir
    passer d'un profil a l'autre et revenir.
    """
    if profil is None or profil.par_defaut:
        return score

    copie = replace(
        score,
        pillars={},
        warnings=list(score.warnings),
        ranked=score.ranked,
        exclusion_reason=score.exclusion_reason,
    )

    poids_piliers = dict(cfg.pillar_weights)
    poids_piliers.update(profil.pillar_weights)
    total = sum(v for v in poids_piliers.values() if v > 0)
    if total <= 0:
        return score

    for cle, pilier in score.pillars.items():
        poids = poids_piliers.get(cle, pilier.weight)
        criteres = []
        for c in pilier.criteria:
            poids_critere = profil.criteria_weights.get(c.key, c.weight)
            sous_score = c.score
            detail = c.detail
            if c.key in profil.criteria_inverted and sous_score is not None:
                # Sens retourne pour ce profil : la valeur brute reste
                # affichee telle quelle, seul le jugement porte sur elle
                # change. Le detail le dit, sans quoi un lecteur verrait un
                # sous-score incoherent avec le chiffre a cote.
                sous_score = 100.0 - sous_score
                detail = (
                    f"{detail} — critère inversé pour ce profil : une valeur "
                    "plus faible est ici recherchée"
                ).lstrip(" —")
            criteres.append(replace(c, weight=poids_critere, score=sous_score, detail=detail))
        applicables = [c for c in criteres if not c.not_applicable]
        total_criteres = sum(c.weight for c in applicables) or 1.0
        disponible = sum(c.weight for c in applicables if c.available)
        couverture = disponible / total_criteres

        if pilier.neutralized and pilier.score is not None:
            # Pilier dividende neutralise (titre qui ne distribue pas) :
            # le score neutre est conserve, mais son poids peut tomber a
            # zero selon le profil.
            recalcule = PillarResult(
                key=cle, weight=poids, score=pilier.score, coverage=1.0,
                criteria=criteres, neutralized=True,
            )
        elif not applicables or couverture < cfg.min_pillar_coverage:
            recalcule = PillarResult(
                key=cle, weight=poids, score=None, coverage=couverture,
                criteria=criteres, neutralized=True,
            )
        else:
            pondere = sum(c.score * c.weight for c in applicables if c.available)
            recalcule = PillarResult(
                key=cle, weight=poids, score=pondere / disponible,
                coverage=couverture, criteria=criteres,
            )
        copie.pillars[cle] = recalcule

    retenus = [p for p in copie.pillars.values() if p.score is not None and p.weight > 0]
    somme = sum(p.weight for p in retenus)
    copie.composite = (
        sum(p.score * p.weight for p in retenus) / somme if retenus and somme > 0 else None
    )
    copie.coverage = (
        sum(p.weight * (1.0 if p.score is not None else 0.0) * max(p.coverage, 0.0)
            for p in copie.pillars.values())
        / total
    )

    # Un titre deja ecarte pour donnees insuffisantes le reste : un profil
    # ne repare pas une lacune de mesure.
    if not score.ranked:
        copie.ranked = False
        return copie

    motifs = _exigences_non_tenues(copie, profil)
    if copie.composite is None:
        motifs.append("aucun pilier calculable avec cette pondération")
    if motifs:
        copie.ranked = False
        copie.exclusion_reason = (
            f"Ne correspond pas au profil « {profil.label} » — " + " ; ".join(motifs)
        )
    else:
        copie.ranked = True
        copie.exclusion_reason = ""
    return copie


def classer_selon_profil(
    scores: list[StockScore], cfg: ScoringConfig, profil
) -> tuple[list[StockScore], list[StockScore]]:
    """Applique un profil a une liste de titres, puis les trie."""
    renotes = [appliquer_profil(s, cfg, profil) for s in scores]
    retenus = rank(renotes)
    assign_sector_ranks(retenus)
    return retenus, excluded(renotes)
