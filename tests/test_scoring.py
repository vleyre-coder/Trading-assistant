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
        price=100.0, trailing_pe=20.0, price_to_book=2.0,
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
                    "equity": revenue * 0.8,
                    "total_debt": revenue * 0.3,
                    "cash": revenue * 0.2,
                    "current_assets": revenue * 0.6,
                    "current_liabilities": revenue * 0.35,
                    "eps_diluted": 2.0 * (1.12 ** index),
                    "dividend_per_share": (1.0 * (1.05 ** index)) if dividends else None,
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
    assert "incompletes" in score.exclusion_reason


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
    for record in fund.annual:
        record.values["ebitda"] = None
        record.values["total_debt"] = None
        record.values["current_assets"] = None
        record.values["current_liabilities"] = None
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
