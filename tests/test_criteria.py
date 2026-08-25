"""Tests des calculs de criteres, sans aucun appel reseau."""
from __future__ import annotations

from datetime import date

import pytest

from investassist import criteria
from investassist.models import AnnualRecord, Fundamentals, Snapshot


def build(values_by_year: dict[int, dict], **snapshot_kwargs) -> Fundamentals:
    snapshot = Snapshot(ticker="TEST", currency="EUR", **snapshot_kwargs)
    annual = [
        AnnualRecord(fiscal_year=year, period_end=date(year, 12, 31), values=values)
        for year, values in sorted(values_by_year.items())
    ]
    return Fundamentals(ticker="TEST", snapshot=snapshot, annual=annual, region="EU")


# ------------------------------------------------------------------ CAGR
def test_cagr_nominal():
    value, reason = criteria.cagr([(2020, 100.0), (2025, 200.0)])
    assert reason == ""
    assert value == pytest.approx(2 ** (1 / 5) - 1, rel=1e-9)


def test_cagr_refuse_base_negative():
    """Un TCAM sur base negative n'a pas de sens : il doit etre refuse."""
    value, reason = criteria.cagr([(2020, -50.0), (2025, 200.0)])
    assert value is None and "négative" in reason


def test_cagr_refuse_arrivee_negative():
    value, reason = criteria.cagr([(2020, 100.0), (2025, -20.0)])
    assert value is None and "arrivée" in reason


def test_cagr_un_seul_point():
    value, reason = criteria.cagr([(2025, 100.0)])
    assert value is None and "deux exercices" in reason


# ------------------------------------------------------------ croissance
def test_revenue_cagr_et_detail():
    fund = build({y: {"revenue": v} for y, v in
                  [(2021, 1000.0), (2022, 1200.0), (2023, 1500.0), (2024, 1900.0), (2025, 2400.0)]})
    value, detail, missing = criteria.revenue_cagr(fund)
    assert missing == ""
    assert value == pytest.approx((2400 / 1000) ** (1 / 4) - 1, rel=1e-9)
    assert "2021" in detail and "2025" in detail  # le detail doit etre explicite


def test_net_margin_trend_lisse_sur_quatre_exercices():
    fund = build({
        2022: {"revenue": 1000.0, "net_income": 100.0},   # 10 %
        2023: {"revenue": 1000.0, "net_income": 120.0},   # 12 %
        2024: {"revenue": 1000.0, "net_income": 150.0},   # 15 %
        2025: {"revenue": 1000.0, "net_income": 170.0},   # 17 %
    })
    value, detail, _ = criteria.net_margin_trend(fund)
    # moyenne(10, 12) = 11 % -> moyenne(15, 17) = 16 % : +5 points
    assert value == pytest.approx(0.05, abs=1e-9)
    assert "moyenne" in detail


# ----------------------------------------------------------- rentabilite
def test_roe_ecarte_les_fonds_propres_negatifs():
    fund = build({
        2023: {"net_income": 100.0, "equity": -500.0},   # doit etre ecarte
        2024: {"net_income": 100.0, "equity": 1000.0},   # 10 %
        2025: {"net_income": 200.0, "equity": 1000.0},   # 20 %
    })
    value, detail, _ = criteria.roe_avg(fund)
    assert value == pytest.approx(0.15)
    assert "écartés" in detail and "2023" in detail


# ------------------------------------------------------------ bilan
def test_net_debt_to_ebitda_tresorerie_nette():
    fund = build({2025: {"total_debt": 100.0, "cash": 400.0, "ebitda": 200.0}})
    value, detail, _ = criteria.net_debt_to_ebitda(fund)
    assert value == pytest.approx(-1.5)
    assert "trésorerie nette positive" in detail


def test_net_debt_to_ebitda_refuse_ebitda_negatif():
    fund = build({2025: {"total_debt": 100.0, "cash": 0.0, "ebitda": -50.0}})
    value, _, missing = criteria.net_debt_to_ebitda(fund)
    assert value is None and "EBITDA négatif" in missing


# -------------------------------------------------------------- PEG
def test_peg_refuse_croissance_negative():
    """Piege classique : un PEG incalculable ne doit pas paraitre excellent."""
    fund = build(
        {2021: {"eps_diluted": 10.0, "net_income": 100.0},
         2025: {"eps_diluted": 5.0, "net_income": 50.0}},
        trailing_pe=15.0,
    )
    value, _, missing = criteria.peg_ratio(fund)
    assert value is None and "négative ou nulle" in missing


def test_peg_refuse_pe_negatif():
    fund = build({2021: {"eps_diluted": 1.0}, 2025: {"eps_diluted": 2.0}}, trailing_pe=-12.0)
    value, _, missing = criteria.peg_ratio(fund)
    assert value is None and "perte" in missing


def test_peg_nominal():
    fund = build(
        {2021: {"eps_diluted": 1.0}, 2025: {"eps_diluted": 1.0 * (1.2 ** 4)}},
        trailing_pe=24.0,
    )
    value, detail, missing = criteria.peg_ratio(fund)
    assert missing == ""
    assert value == pytest.approx(24.0 / 20.0, rel=1e-6)  # croissance 20 %/an
    assert "BPA" in detail


# --------------------------------------------------------- dividendes
def test_dividende_absent_est_signale_comme_tel():
    fund = build({2024: {"revenue": 1.0}, 2025: {"revenue": 1.0}})
    value, _, missing = criteria.dividend_growth_streak(fund)
    assert value is None and "ne versant pas de dividende" in missing


def test_dividende_baisse_penalisee():
    fund = build({
        2021: {"dividend_per_share": 2.0},
        2022: {"dividend_per_share": 2.2},
        2023: {"dividend_per_share": 1.0},
        2024: {"dividend_per_share": 1.1},
    })
    value, detail, _ = criteria.dividend_growth_streak(fund)
    assert "baisse constatée" in detail
    assert 0.0 <= value <= 1.0


def test_annee_civile_en_cours_exclue_du_dividende():
    """Le versement de l'annee en cours est partiel : il ne doit pas etre lu
    comme une baisse du dividende."""
    current = date.today().year
    fund = build({
        current - 3: {"dividend_per_share": 1.0},
        current - 2: {"dividend_per_share": 1.1},
        current - 1: {"dividend_per_share": 1.2},
        current: {"dividend_per_share": 0.3},  # trimestre unique deja verse
    })
    value, detail, _ = criteria.dividend_growth_streak(fund)
    assert "baisse constatée" not in detail
    assert f"année {current} en cours" in detail
    assert value > 0.5


# ------------------------------------------------------- P/E historique
def test_pe_historique_exige_trois_exercices():
    fund = build({2024: {"eps_diluted": 2.0}, 2025: {"eps_diluted": 2.5}}, trailing_pe=20.0)
    value, _, missing = criteria.pe_vs_own_history(fund, None)
    assert value is None and "insuffisant" in missing
