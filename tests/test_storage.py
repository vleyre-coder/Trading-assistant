"""Tests du stockage local SQLite."""
from __future__ import annotations

import pytest

from investassist.config import load_scoring
from investassist.models import PillarResult, StockScore
from investassist.storage import Database, score_from_row

CONFIG = load_scoring()


@pytest.fixture()
def db(tmp_path):
    return Database(tmp_path / "test.sqlite")


def make_score(ticker: str, composite: float, ranked: bool = True) -> StockScore:
    return StockScore(
        ticker=ticker, name=f"{ticker} SA", sector="Technology", region="EU",
        currency="EUR", price=100.0, composite=composite, ranked=ranked,
        window_years=5, coverage=1.0,
        pillars={"growth": PillarResult(key="growth", weight=0.35, score=composite, coverage=1.0)},
    )


def test_cycle_de_vie_execution(db):
    run_id = db.start_run(["cac40"])
    scores = [make_score("AAA", 80.0), make_score("BBB", 60.0)]
    db.save_scores(run_id, scores, {"AAA": 1, "BBB": 2})
    db.finish_run(run_id, n_analyzed=2, n_ranked=2)

    assert db.last_run()["n_ranked"] == 2
    history = db.score_history("AAA")
    assert len(history) == 1 and history[0]["composite"] == pytest.approx(80.0)


def test_snapshot_precedent_pour_les_alertes(db):
    first = db.start_run(["cac40"])
    db.save_scores(first, [make_score("AAA", 70.0)], {"AAA": 3})
    db.finish_run(first, 1, 1)

    second = db.start_run(["cac40"])
    db.save_scores(second, [make_score("AAA", 82.0)], {"AAA": 1})
    db.finish_run(second, 1, 1)

    previous = db.previous_snapshot(second)
    assert previous["AAA"]["composite"] == pytest.approx(70.0)
    assert previous["AAA"]["rank"] == 3
    # Aucune execution avant la premiere
    assert db.previous_snapshot(first) == {}


def test_watchlist(db):
    db.add_to_watchlist("msft", "a suivre")
    assert db.watchlist()[0]["ticker"] == "MSFT"
    db.add_to_watchlist("MSFT", "note mise a jour")   # idempotent
    assert len(db.watchlist()) == 1
    assert db.watchlist()[0]["note"] == "note mise a jour"
    db.remove_from_watchlist("MSFT")
    assert db.watchlist() == []


def test_regles_alertes(db):
    rule_id = db.add_alert_rule("MSFT", "price_below", {"threshold": 300})
    assert db.alert_rules()[0]["params"]["threshold"] == 300
    db.set_rule_enabled(rule_id, False)
    assert db.alert_rules(enabled_only=True) == []
    assert len(db.alert_rules(enabled_only=False)) == 1
    db.delete_alert_rule(rule_id)
    assert db.alert_rules(enabled_only=False) == []


def test_type_alerte_inconnu_refuse(db):
    with pytest.raises(ValueError, match="inconnu"):
        db.add_alert_rule("MSFT", "acheter_maintenant", {})


def test_publications_deja_vues(db):
    assert db.last_earnings_seen("MSFT") is None
    db.set_last_earnings_seen("MSFT", "2026-06-30")
    assert db.last_earnings_seen("MSFT") == "2026-06-30"
    db.set_last_earnings_seen("MSFT", "2026-09-30")
    assert db.last_earnings_seen("MSFT") == "2026-09-30"


def test_reconstruction_complete_d_un_score(db):
    """Aller-retour base -> objet : le detail par critere doit survivre."""
    from investassist.models import CriterionResult, PillarResult

    score = make_score("AAA", 77.5)
    score.pillars["growth"].criteria = [
        CriterionResult(
            key="revenue_cagr", label="CAGR chiffre d'affaires", unit="percent",
            value=0.124, score=71.0, weight=0.4, pillar="growth",
            detail="2021 : 1,0 Md → 2025 : 1,6 Md",
        ),
        CriterionResult(
            key="net_income_cagr", label="CAGR resultat net", unit="percent",
            value=None, score=None, weight=0.35, pillar="growth",
            reason_missing="base de depart negative",
        ),
    ]
    score.warnings = ["Fenetre reduite a 4 exercices"]

    run_id = db.start_run(["cac40"])
    db.save_scores(run_id, [score], {"AAA": 1})
    db.finish_run(run_id, 1, 1, sector_medians={"Technology": 21.0})

    restaure = score_from_row(db.scores_for_run(run_id)[0])
    assert restaure.ticker == "AAA"
    assert restaure.composite == pytest.approx(77.5)
    assert restaure.window_years == 5 and restaure.ranked is True
    assert restaure.warnings == ["Fenetre reduite a 4 exercices"]

    criteres = {c.key: c for c in restaure.criteria_flat()}
    assert criteres["revenue_cagr"].value == pytest.approx(0.124)
    assert criteres["revenue_cagr"].detail.startswith("2021")
    assert criteres["net_income_cagr"].score is None
    assert "negative" in criteres["net_income_cagr"].reason_missing


def test_medianes_sectorielles_persistees(db):
    import json

    run_id = db.start_run(["cac40"])
    db.finish_run(run_id, 1, 1, sector_medians={"Technology": 21.0, "Utilities": 14.2})
    stocke = json.loads(db.last_run()["sector_medians_json"])
    assert stocke["Technology"] == pytest.approx(21.0)


def test_etat_des_regles_memorise(db):
    rule_id = db.add_alert_rule("MSFT", "price_below", {"threshold": 300})
    assert db.alert_rules()[0]["last_state"] is None
    db.set_rule_state(rule_id, "crossed")
    assert db.alert_rules()[0]["last_state"] == "crossed"
