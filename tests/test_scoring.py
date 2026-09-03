"""Tests de la notation : bornes, couverture, exclusions, neutralite."""
from __future__ import annotations

from datetime import date

import pytest

from investassist import scoring
from investassist.config import load_scoring
from investassist.models import AnnualRecord, Fundamentals, Snapshot

CONFIG = load_scoring()


def make_fundamentals(*, years: int = 5, dividends: bool = True, **snapshot_kwargs) -> Fundamentals:
    snapshot = Snapshot(
        ticker="TEST", name="Testco", sector="Technology", currency="EUR",
        price=100.0, trailing_pe=20.0, price_to_book=2.0, market_cap=10_000.0,
        dividend_yield=0.025 if dividends else None,
        **snapshot_kwargs,
    )
    annual = []
    for index in range(years):
        year = 2026 - years + index
        revenue = 1000.0 * (1.12 ** index)
        annual.append(
            AnnualRecord(
                fiscal_year=year,
                period_end=date(year, 12, 31),
                values={
                    "revenue": revenue,
                    "net_income": revenue * 0.15,
                    "operating_income": revenue * 0.20,
                    "ebitda": revenue * 0.25,
                    "gross_profit": revenue * 0.45,
                    "equity": revenue * 0.8,
                    "total_assets": revenue * 1.6,
                    "total_debt": revenue * 0.3,
                    "cash": revenue * 0.2,
                    "current_assets": revenue * 0.6,
                    "current_liabilities": revenue * 0.35,
                    "eps_diluted": 2.0 * (1.12 ** index),
                    "dividend_per_share": (1.0 * (1.05 ** index)) if dividends else None,
                    "operating_cash_flow": revenue * 0.22,
                    "capex": revenue * 0.05,
                    "free_cash_flow": revenue * 0.17,
                    "depreciation_amortisation": revenue * 0.05,
                    "interest_expense": revenue * 0.01,
                    "shares_diluted": 100e6 * (0.99 ** index),
                },
            )
        )
    return Fundamentals(ticker="TEST", snapshot=snapshot, annual=annual, region="EU")


def test_scores_bornes_0_100():
    score = scoring.score_stock(make_fundamentals(), CONFIG)
    assert score.composite is not None and 0.0 <= score.composite <= 100.0
    for pillar in score.pillars.values():
        if pillar.score is not None:
            assert 0.0 <= pillar.score <= 100.0
    for criterion in score.criteria_flat():
        if criterion.score is not None:
            assert 0.0 <= criterion.score <= 100.0


def test_absence_de_dividende_non_penalisante():
    """Une valeur de croissance sans dividende ne doit pas etre desavantagee."""
    with_dividend = scoring.score_stock(make_fundamentals(dividends=True), CONFIG)
    without = scoring.score_stock(make_fundamentals(dividends=False), CONFIG)

    pillar = without.pillars["dividend"]
    assert pillar.neutralized is True
    assert pillar.score == pytest.approx(CONFIG.no_dividend_score)
    assert without.ranked is True
    # L'ecart de score composite reste marginal (poids du pilier : 5 %).
    assert abs(with_dividend.composite - without.composite) < 3.0


def test_historique_trop_court_exclut_du_classement():
    fund = make_fundamentals(years=2)
    score = scoring.score_stock(fund, CONFIG)
    assert score.ranked is False
    assert "historique fondamental insuffisant" in score.exclusion_reason
    assert "incomplètes" in score.exclusion_reason


def test_couverture_insuffisante_exclut_du_classement():
    """Un titre sans aucune donnee de marche ne doit pas etre classe sur un
    score partiel : il doit apparaitre comme incomplet."""
    fund = make_fundamentals()
    fund.snapshot.trailing_pe = None
    fund.snapshot.price_to_book = None
    fund.snapshot.dividend_yield = None
    for record in fund.annual:
        record.values["eps_diluted"] = None
        record.values["dividend_per_share"] = None
        record.values["ebitda"] = None
        record.values["total_debt"] = None
        record.values["current_assets"] = None
        record.values["current_liabilities"] = None
    score = scoring.score_stock(fund, CONFIG)
    assert score.ranked is False
    assert score.coverage < CONFIG.min_weight_coverage


def test_pilier_neutralise_redistribue_son_poids():
    fund = make_fundamentals()
    # Priver le pilier bilan de TOUTES ses entrees, y compris celles des
    # criteres ajoutes (couverture des interets, fonds propres sur actif,
    # evolution du nombre d'actions) : sans cela le pilier reste calculable et
    # le test ne verifie plus la redistribution qu'il vise.
    for record in fund.annual:
        for champ in (
            "ebitda", "total_debt", "current_assets", "current_liabilities",
            "free_cash_flow", "operating_income", "interest_expense",
            "total_assets", "shares_diluted",
        ):
            record.values[champ] = None
    score = scoring.score_stock(fund, CONFIG)
    assert score.pillars["balance_sheet"].score is None
    usable = [p for p in score.pillars.values() if p.score is not None]
    attendu = sum(p.score * p.weight for p in usable) / sum(p.weight for p in usable)
    assert score.composite == pytest.approx(attendu)


def test_mediane_sectorielle_exige_un_minimum_de_pairs():
    funds = [make_fundamentals(), make_fundamentals()]
    assert scoring.sector_pe_medians(funds, min_peers=3) == {}
    medians = scoring.sector_pe_medians(funds, min_peers=2)
    assert medians["Technology"] == pytest.approx(20.0)


def test_pe_vs_secteur_na_sans_pairs():
    fund = make_fundamentals()
    value, _, missing = scoring.pe_vs_sector(fund, {})
    assert value is None and "pairs" in missing


def test_classement_decroissant_et_exclusions_separees():
    bon = scoring.score_stock(make_fundamentals(), CONFIG)
    court = scoring.score_stock(make_fundamentals(years=2), CONFIG)
    court.ticker = "COURT"
    ranked = scoring.rank([court, bon])
    assert [s.ticker for s in ranked] == ["TEST"]
    assert [s.ticker for s in scoring.excluded([court, bon])] == ["COURT"]


def test_interpolation_bornee_du_bareme():
    criterion = CONFIG.criteria["revenue_cagr"]
    assert criterion.score(-10.0) == criterion.points[0][1]   # borne basse
    assert criterion.score(10.0) == criterion.points[-1][1]   # borne haute
    assert criterion.score(None) is None
    assert criterion.score(float("nan")) is None


def test_echec_de_recuperation_distingue_des_donnees_incompletes():
    """Un titre illisible ne doit pas etre presente comme ayant de mauvais
    fondamentaux : c'est un echec technique, pas un diagnostic."""
    from investassist.models import Fundamentals, Snapshot

    vide = Fundamentals(
        ticker="XXX", snapshot=Snapshot(ticker="XXX"), annual=[], region="EU",
        fetch_failed=True,
    )
    assert vide.fetch_failed is True
    assert vide.years_available == 0

    partiel = make_fundamentals(years=2)
    assert partiel.fetch_failed is False
    score = scoring.score_stock(partiel, CONFIG)
    assert score.ranked is False and "historique" in score.exclusion_reason


# --------------------------------------------- pertinence sectorielle
def test_banque_criteres_industriels_sans_objet_et_non_penalisants():
    """« Dette nette / EBITDA » pour une banque n'est pas une donnee
    manquante : la dette EST sa matiere premiere. Le critere doit etre marque
    sans objet et son poids redistribue, sans peser sur la couverture."""
    banque = make_fundamentals()
    banque.snapshot.sector = "Financial Services"
    for record in banque.annual:
        record.values["ebitda"] = None          # une banque n'en publie pas
        record.values["current_assets"] = None
        record.values["current_liabilities"] = None
        record.values["gross_profit"] = None

    score = scoring.score_stock(banque, CONFIG)
    bilan = score.pillars["balance_sheet"]
    sans_objet = {c.key for c in bilan.criteria if c.not_applicable}
    assert {"net_debt_to_ebitda", "current_ratio", "interest_coverage"} <= sans_objet
    # Le pilier reste note, sur les criteres qui gardent un sens.
    assert bilan.score is not None
    assert bilan.coverage == pytest.approx(1.0)
    assert score.ranked is True


def test_bareme_sectoriel_de_levier_pour_les_foncieres():
    """Sept fois l'EBITDA est alarmant dans l'industrie et banal pour une
    foncière : le meme ratio ne doit pas donner le meme score."""
    critere = CONFIG.criteria["net_debt_to_ebitda"]
    fonciere = critere.score(7.0, "Real Estate")
    industrie = critere.score(7.0, "Industrials")
    assert fonciere is not None and industrie is not None
    assert fonciere > industrie
    assert industrie == pytest.approx(0.0)


def test_societe_en_perte_les_criteres_de_pe_sont_sans_objet():
    """Sans benefice, le P/E n'existe pas : ses trois criteres derives doivent
    etre sans objet, sinon ils comptent comme des lacunes et suffisent a
    exclure du classement une societe en forte croissance."""
    perte = make_fundamentals()
    for record in perte.annual:
        record.values["net_income"] = -100.0
    perte.snapshot.trailing_pe = None

    score = scoring.score_stock(perte, CONFIG)
    valorisation = score.pillars["valuation"]
    sans_objet = {c.key for c in valorisation.criteria if c.not_applicable}
    assert {"peg_ratio", "pe_vs_own_history", "pe_vs_sector"} == sans_objet
    # Le pilier reste calculable grace au rendement du FCF et a la VE/CA.
    assert valorisation.score is not None
    assert score.ranked is True


def test_condition_prealable_inconnue_n_ecarte_pas_le_critere(caplog):
    """Une condition mal orthographiee dans la configuration doit alerter, pas
    faire disparaitre silencieusement un critere du calcul."""
    from dataclasses import replace

    critere = replace(CONFIG.criteria["peg_ratio"], requires=("condition_inexistante",))
    fund = make_fundamentals()
    with caplog.at_level("WARNING"):
        raison = scoring.raisons_sans_objet(fund, critere)
    assert raison == ""
    assert "condition_inexistante" in caplog.text


def test_secteur_inconnu_reste_evalue():
    """Source muette sur le secteur : mieux vaut noter sur la base generale
    que de vider le titre de ses criteres."""
    fund = make_fundamentals()
    fund.snapshot.sector = None
    score = scoring.score_stock(fund, CONFIG)
    assert score.ranked is True
    assert not any(c.not_applicable for c in score.criteria_flat())


def test_rang_sectoriel_independant_du_rang_general():
    """Un titre peut etre 40e au general et premier de son secteur : c'est la
    reponse a « le meilleur de sa categorie », que les seuils absolus du
    classement general ne donnent pas."""
    def titre(ticker, secteur, composite):
        s = scoring.score_stock(make_fundamentals(), CONFIG)
        s.ticker, s.sector, s.composite = ticker, secteur, composite
        return s

    scores = [
        titre("LOG1", "Technology", 90.0),
        titre("LOG2", "Technology", 80.0),
        titre("DIS1", "Consumer Defensive", 55.0),
        titre("DIS2", "Consumer Defensive", 50.0),
    ]
    classement = scoring.rank(scores)
    scoring.assign_sector_ranks(classement)
    par_ticker = {s.ticker: s for s in classement}

    assert par_ticker["DIS1"].sector_rank == 1        # 3e au general
    assert par_ticker["DIS1"].sector_count == 2
    assert par_ticker["LOG2"].sector_rank == 2
    assert [s.ticker for s in classement] == ["LOG1", "LOG2", "DIS1", "DIS2"]


def test_pays_de_l_emetteur_distinct_de_la_place_de_cotation():
    """PDD Holdings est un groupe chinois cote au Nasdaq : la region sert a
    choisir la source de donnees, le pays informe sur l'exposition reelle."""
    fund = make_fundamentals()
    fund.snapshot.country = "China"
    fund.region = "US"
    score = scoring.score_stock(fund, CONFIG)
    assert score.region == "US"
    assert score.country == "China"
