"""Tests du stockage local SQLite."""
from __future__ import annotations

import pytest

from investassist.config import load_scoring
from investassist.models import PillarResult, StockScore
from investassist.storage import Database

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
