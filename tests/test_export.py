"""Tests du format d'export consomme par le site statique.

Ce format est le contrat entre le moteur Python et l'interface web : une
modification silencieuse casserait le site publie sans qu'aucun autre test
ne s'en apercoive.
"""
from __future__ import annotations

import importlib.util
import json
from datetime import datetime
from pathlib import Path

import pytest

from investassist import export
from investassist.config import load_scoring
from investassist.models import CriterionResult, PillarResult, StockScore

CONFIG = load_scoring()
ROOT = Path(__file__).resolve().parents[1]


def score(ticker="AAA", composite=82.0, ranked=True) -> StockScore:
    critere = CriterionResult(
        key="revenue_cagr", label="CAGR chiffre d'affaires", unit="percent",
        value=0.14, score=78.0, weight=0.4, pillar="growth",
        detail="2021 : 1,0 Md → 2025 : 1,7 Md",
    )
    manquant = CriterionResult(
        key="peg_ratio", label="PEG", unit="ratio", value=None, score=None,
        weight=0.4, pillar="valuation", reason_missing="croissance négative",
    )
    return StockScore(
        ticker=ticker, name=f"{ticker} SA", sector="Technology", region="EU",
        currency="EUR", price=101.5, composite=composite, ranked=ranked,
        window_years=5, coverage=0.98,
        exclusion_reason="" if ranked else "Données fondamentales incomplètes — test",
        warnings=["Fenêtre réduite à 4 exercices"],
        pillars={
            "growth": PillarResult(key="growth", weight=0.35, score=78.0, coverage=1.0,
                                   criteria=[critere]),
            "valuation": PillarResult(key="valuation", weight=0.25, score=None, coverage=0.2,
                                      criteria=[manquant], neutralized=True),
        },
    )


# --------------------------------------------------------------- classement
def test_structure_du_classement():
    charge = export.ranking_payload(
        [score("AAA", 82.0), score("BBB", 61.0)], [score("CCC", None, ranked=False)],
        {"DDD": "aucune donnée récupérée"}, CONFIG,
        universes=["cac40"], generated_at=datetime(2026, 8, 25, 21, 0), duration_seconds=487.3,
    )
    assert charge["counts"] == {"ranked": 2, "excluded": 1, "failed": 1}
    assert charge["generated_at"] == "2026-08-25T21:00:00"
    assert charge["duration_seconds"] == 487.3
    assert [t["rank"] for t in charge["ranked"]] == [1, 2]
    assert charge["excluded"][0]["rank"] is None
    assert charge["failures"] == [{"ticker": "DDD", "reason": "aucune donnée récupérée"}]


def test_avertissement_toujours_embarque():
    """Exigence fonctionnelle : les donnees ne voyagent jamais sans
    l'avertissement de non-conseil, sans quoi une interface pourrait les
    afficher sans lui."""
    charge = export.ranking_payload([score()], [], {}, CONFIG, universes=["cac40"])
    avertissement = charge["disclaimer"]
    assert "conseil en investissement" in avertissement["main"]
    assert avertissement["what_this_is"] and avertissement["what_this_is_not"]
    assert avertissement["data_limits"]


def test_detail_des_criteres_conserve():
    charge = export.ranking_payload([score()], [], {}, CONFIG, universes=["cac40"])
    piliers = charge["ranked"][0]["pillars"]
    croissance = piliers["growth"]["criteria"][0]
    assert croissance["detail"].startswith("2021")
    assert croissance["score"] == 78.0
    valorisation = piliers["valuation"]["criteria"][0]
    assert valorisation["score"] is None
    assert "négative" in valorisation["reason_missing"]
    assert piliers["valuation"]["neutralized"] is True


def test_methodologie_exportee():
    methode = export.methodology_payload(CONFIG)
    assert methode["target_years"] == CONFIG.target_years
    assert sum(p["weight"] for p in methode["pillars"]) == pytest.approx(1.0)
    cles = {c["key"] for c in methode["criteria"]}
    assert {"revenue_cagr", "peg_ratio", "roe_avg"} <= cles
    for critere in methode["criteria"]:
        assert critere["points"], f"barème manquant pour {critere['key']}"


# --------------------------------------------------------------- historique
def test_historique_accumule_les_analyses():
    premier = export.append_history(None, [score("AAA", 80.0)], [], generated_at=datetime(2026, 8, 1))
    second = export.append_history(premier, [score("AAA", 84.0)], [], generated_at=datetime(2026, 8, 2))
    assert len(second["runs"]) == 2
    assert second["runs"][-1]["scores"]["AAA"] == {"score": 84.0, "rank": 1}
    assert second["updated_at"] == "2026-08-02T00:00:00"


def test_historique_borne_sa_taille():
    historique = None
    for jour in range(1, 15):
        historique = export.append_history(
            historique, [score("AAA", 70.0 + jour)], [],
            generated_at=datetime(2026, 8, jour), max_runs=5,
        )
    assert len(historique["runs"]) == 5
    assert historique["runs"][0]["generated_at"].startswith("2026-08-10")


def test_historique_ne_duplique_pas_une_meme_analyse():
    quand = datetime(2026, 8, 20)
    premier = export.append_history(None, [score("AAA", 80.0)], [], generated_at=quand)
    second = export.append_history(premier, [score("AAA", 91.0)], [], generated_at=quand)
    assert len(second["runs"]) == 1
    assert second["runs"][0]["scores"]["AAA"]["score"] == 91.0


def test_titres_exclus_dans_l_historique():
    historique = export.append_history(
        None, [score("AAA", 80.0)], [score("BBB", None, ranked=False)],
        generated_at=datetime(2026, 8, 1),
    )
    assert historique["runs"][0]["scores"]["BBB"] == {"score": None, "rank": None}


# ------------------------------------------------------- etat precedent
def test_etat_precedent_depuis_l_historique():
    historique = export.append_history(None, [score("AAA", 80.0), score("BBB", 60.0)], [])
    precedent, rangs = export.previous_state_from_history(historique)
    assert precedent["AAA"]["composite"] == 80.0
    assert rangs == {"AAA": 1, "BBB": 2}


def test_etat_precedent_absent():
    assert export.previous_state_from_history(None) == ({}, {})
    assert export.previous_state_from_history({"runs": []}) == ({}, {})
    assert export.previous_state(None) == ({}, {})


def test_etat_precedent_depuis_un_classement():
    charge = export.ranking_payload([score("AAA", 82.0)], [], {}, CONFIG, universes=["cac40"])
    precedent, rangs = export.previous_state(charge)
    assert precedent["AAA"]["composite"] == 82.0 and rangs["AAA"] == 1


# ------------------------------------------------------------- fichiers
def test_ecriture_atomique(tmp_path):
    cible = tmp_path / "sous" / "ranking.json"
    export.write_json(cible, {"a": 1})
    assert json.loads(cible.read_text(encoding="utf-8")) == {"a": 1}
    assert not list(tmp_path.rglob("*.tmp")), "fichier temporaire laissé derrière"
    assert export.read_json(cible) == {"a": 1}


def test_lecture_tolerante(tmp_path):
    assert export.read_json(tmp_path / "absent.json") is None
    casse = tmp_path / "casse.json"
    casse.write_text("{ ceci n'est pas du JSON", encoding="utf-8")
    assert export.read_json(casse) is None


# ------------------------------------------- regles d'alerte du depot
def charger_build_site():
    chemin = ROOT / "scripts" / "build_site.py"
    spec = importlib.util.spec_from_file_location("build_site", chemin)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_developpement_des_regles_d_alerte():
    build_site = charger_build_site()
    regles = build_site.regles_effectives(
        {
            "watchlist": ["msft", "AIR.PA"],
            "general": {"kinds": ["score_change", "top_n_entry"],
                        "score_change_threshold": 4.0, "top_n": 15},
            "rules": [{"ticker": "mc.pa", "kind": "price_below", "threshold": 500}],
        }
    )
    assert len(regles) == 5  # 2 titres x 2 types + 1 regle explicite
    assert all(r["ticker"] == r["ticker"].upper() for r in regles)
    variation = next(r for r in regles if r["ticker"] == "MSFT" and r["kind"] == "score_change")
    assert variation["params"] == {"threshold": 4.0}
    entree = next(r for r in regles if r["kind"] == "top_n_entry")
    assert entree["params"] == {"n": 15}
    explicite = next(r for r in regles if r["kind"] == "price_below")
    assert explicite["params"] == {"threshold": 500}


def test_configuration_d_alertes_du_depot_valide():
    """Le fichier livre doit rester exploitable par le script planifie."""
    build_site = charger_build_site()
    configuration = build_site.charger_regles()
    regles = build_site.regles_effectives(configuration)
    from investassist.storage import ALERT_KINDS

    for regle in regles:
        assert regle["kind"] in ALERT_KINDS, f"type d'alerte inconnu : {regle['kind']}"
