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


def test_net_debt_to_ebitda_refuse_ebitda_et_fcf_negatifs():
    """Sans EBITDA ni free cash flow positifs, aucune capacite de
    remboursement ne peut etre rapportee a la dette : le critere est N/A."""
    fund = build({2025: {"total_debt": 100.0, "cash": 0.0, "ebitda": -50.0,
                         "free_cash_flow": -20.0}})
    value, _, missing = criteria.net_debt_to_ebitda(fund)
    assert value is None
    assert "aucune capacité de remboursement" in missing


def test_net_debt_to_ebitda_se_replie_sur_le_free_cash_flow():
    """Cas des editeurs de logiciels : la remuneration en actions creuse le
    resultat comptable alors que la tresorerie rentre. Le levier reste
    mesurable sur le free cash flow, et l'origine du chiffre est annoncee."""
    fund = build({2025: {"total_debt": 100.0, "cash": 20.0, "ebitda": -50.0,
                         "free_cash_flow": 40.0}})
    value, detail, missing = criteria.net_debt_to_ebitda(fund)
    assert missing == ""
    assert value == pytest.approx(2.0)          # (100 - 20) / 40
    assert "free cash flow" in detail and "EBITDA négatif" in detail


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


# ------------------------------------------- criteres de tresorerie et qualite
def test_conversion_en_tresorerie_ignore_les_exercices_deficitaires():
    """Sur un resultat net negatif le rapport change de signe et perd tout
    sens : l'exercice doit etre ecarte, pas produire un score flatteur."""
    fund = build({
        2023: {"net_income": -50.0, "free_cash_flow": -10.0},
        2024: {"net_income": 100.0, "free_cash_flow": 90.0},
        2025: {"net_income": 200.0, "free_cash_flow": 220.0},
    })
    value, detail, missing = criteria.cash_conversion(fund)
    assert missing == ""
    assert value == pytest.approx((0.9 + 1.1) / 2)
    assert "2023" not in detail


def test_conversion_en_tresorerie_absente_si_aucun_exercice_beneficiaire():
    fund = build({2025: {"net_income": -50.0, "free_cash_flow": 10.0}})
    value, _, missing = criteria.cash_conversion(fund)
    assert value is None and "aucun exercice bénéficiaire" in missing


def test_rendement_du_free_cash_flow_reste_calculable_en_perte():
    """Interet du critere : une societe sans benefice comptable peut degager
    de la tresorerie, la ou le P/E n'existe pas."""
    fund = build({2025: {"net_income": -10.0, "free_cash_flow": 500.0}},
                 market_cap=10_000.0)
    value, _, missing = criteria.fcf_yield(fund)
    assert missing == "" and value == pytest.approx(0.05)


def test_rendement_du_free_cash_flow_negatif_est_une_information():
    """Un FCF negatif doit produire une valeur (donc un score plancher), pas
    une absence de donnee."""
    fund = build({2025: {"free_cash_flow": -200.0}}, market_cap=10_000.0)
    value, detail, missing = criteria.fcf_yield(fund)
    assert missing == "" and value == pytest.approx(-0.02)
    assert "trésorerie consommée" in detail


def test_roce_calculable_malgre_des_fonds_propres_negatifs():
    """Cas Starbucks : rachats d'actions superieurs aux benefices accumules.
    Le ROE n'a plus de sens, le ROCE si."""
    fund = build({2025: {"operating_income": 100.0, "total_assets": 1000.0,
                         "current_liabilities": 500.0, "equity": -200.0,
                         "net_income": 80.0}})
    roce, _, missing = criteria.roce_avg(fund)
    assert missing == "" and roce == pytest.approx(0.2)
    roe, _, roe_missing = criteria.roe_avg(fund)
    assert roe is None
    # Le message doit nommer la vraie cause, pas pretendre a une donnee absente.
    assert "négatifs" in roe_missing and "absent" not in roe_missing


def test_marge_brute_moyenne():
    fund = build({
        2024: {"revenue": 1000.0, "gross_profit": 400.0},
        2025: {"revenue": 2000.0, "gross_profit": 1000.0},
    })
    value, _, missing = criteria.gross_margin_avg(fund)
    assert missing == "" and value == pytest.approx(0.45)


def test_evolution_du_nombre_d_actions_distingue_rachat_et_dilution():
    rachat = build({2023: {"shares_diluted": 110.0}, 2025: {"shares_diluted": 100.0}})
    value, detail, missing = criteria.share_count_trend(rachat)
    assert missing == "" and value > 0 and "réduction" in detail

    dilution = build({2023: {"shares_diluted": 100.0}, 2025: {"shares_diluted": 121.0}})
    value, detail, _ = criteria.share_count_trend(dilution)
    assert value == pytest.approx(-0.10) and "dilution" in detail


def test_couverture_des_interets_sans_dette_ne_penalise_pas():
    """Aucune charge d'interets et aucune dette : c'est une force, pas une
    donnee manquante."""
    fund = build({2025: {"operating_income": 500.0, "total_debt": 0.0}})
    value, detail, missing = criteria.interest_coverage(fund)
    assert missing == "" and value == 100.0
    assert "aucune charge d'intérêts" in detail


def test_couverture_des_interets_ne_conclut_pas_si_dette_sans_charge_publiee():
    fund = build({2025: {"operating_income": 500.0, "total_debt": 900.0}})
    value, _, missing = criteria.interest_coverage(fund)
    assert value is None and missing != ""


def test_valeur_entreprise_sur_chiffre_d_affaires():
    fund = build({2025: {"revenue": 1000.0, "total_debt": 300.0, "cash": 100.0}},
                 market_cap=5000.0)
    value, _, missing = criteria.ev_to_sales(fund)
    assert missing == "" and value == pytest.approx(5.2)   # (5000 + 200) / 1000


def test_fonds_propres_sur_actif_signale_les_capitaux_negatifs():
    fund = build({2025: {"equity": -100.0, "total_assets": 1000.0}})
    value, detail, missing = criteria.equity_to_assets(fund)
    assert missing == "" and value == pytest.approx(-0.1)
    assert "négatifs" in detail


def test_pe_historique_utilise_la_mediane_et_signale_l_exercice_atypique():
    """Un exercice a benefice quasi nul produit un P/E de plusieurs centaines.
    La moyenne le laisserait passer pour une decote ; la mediane non."""
    import pandas as pd

    fund = build(
        {
            2021: {"eps_diluted": 0.10},   # exercice atypique -> P/E ~1000
            2022: {"eps_diluted": 5.0},
            2023: {"eps_diluted": 5.0},
            2024: {"eps_diluted": 5.0},
        },
        trailing_pe=20.0,
    )
    index = pd.to_datetime([f"{y}-12-31" for y in (2021, 2022, 2023, 2024)])
    prices = pd.DataFrame({"Close": [100.0] * 4}, index=index)

    value, detail, missing = criteria.pe_vs_own_history(fund, prices)
    assert missing == ""
    # Serie de P/E : 1000, 20, 20, 20 -> mediane 20, moyenne 265.
    assert value == pytest.approx(1.0)      # avec la moyenne : 0,075, soit 100/100
    assert "médiane" in detail and "atypique" in detail
