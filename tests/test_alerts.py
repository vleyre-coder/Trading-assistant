"""Tests des regles d'alerte : declenchement au franchissement, pas a l'etat."""
from __future__ import annotations

import pytest

from investassist.alerts.rules import attach_earnings_dates, evaluate_rules
from investassist.config import load_scoring
from investassist.models import StockScore
from investassist.storage import Database

CONFIG = load_scoring()


@pytest.fixture()
def db(tmp_path):
    return Database(tmp_path / "alertes.sqlite")


def score(ticker: str = "MSFT", prix: float = 480.0, composite: float = 75.0) -> StockScore:
    return StockScore(
        ticker=ticker, name=f"{ticker} Inc.", sector="Technology", region="US",
        currency="USD", price=prix, composite=composite, ranked=True,
        window_years=5, coverage=1.0,
    )


# ------------------------------------------------------- seuils de cours
def test_alerte_de_prix_ne_se_repete_pas(db):
    """Le defaut a eviter : la meme alerte a chaque execution tant que le
    cours reste sous le seuil."""
    db.add_alert_rule("MSFT", "price_below", {"threshold": 500})

    premier = evaluate_rules(db, [score(prix=480.0)], CONFIG)
    assert [e.kind for e in premier] == ["price_below"]

    second = evaluate_rules(db, [score(prix=470.0)], CONFIG)
    assert second == [], "l'alerte s'est redeclenchee alors que rien n'a change"


def test_alerte_de_prix_se_redeclenche_apres_retour_au_dessus(db):
    db.add_alert_rule("MSFT", "price_below", {"threshold": 500})

    assert len(evaluate_rules(db, [score(prix=480.0)], CONFIG)) == 1
    assert evaluate_rules(db, [score(prix=520.0)], CONFIG) == []   # repasse au-dessus
    assert len(evaluate_rules(db, [score(prix=490.0)], CONFIG)) == 1  # nouveau franchissement


def test_alerte_de_prix_haute(db):
    db.add_alert_rule("MSFT", "price_above", {"threshold": 500})
    assert evaluate_rules(db, [score(prix=480.0)], CONFIG) == []
    evenements = evaluate_rules(db, [score(prix=510.0)], CONFIG)
    assert len(evenements) == 1 and "au-dessus" in evenements[0].message


def test_regle_desactivee_ne_declenche_rien(db):
    rule_id = db.add_alert_rule("MSFT", "price_below", {"threshold": 500})
    db.set_rule_enabled(rule_id, False)
    assert evaluate_rules(db, [score(prix=100.0)], CONFIG) == []


# ------------------------------------------------------ variation de score
def test_variation_de_score(db):
    db.add_alert_rule("MSFT", "score_change", {"threshold": 5})
    precedent = {"MSFT": {"composite": 75.0, "rank": 2}}

    assert evaluate_rules(db, [score(composite=78.0)], CONFIG, previous=precedent) == []
    evenements = evaluate_rules(db, [score(composite=82.0)], CONFIG, previous=precedent)
    assert len(evenements) == 1
    assert "+7.0 pts" in evenements[0].message
    assert "sans portee predictive" in evenements[0].message


def test_variation_de_score_sans_historique(db):
    db.add_alert_rule("MSFT", "score_change", {"threshold": 1})
    assert evaluate_rules(db, [score()], CONFIG, previous={}) == []


# ------------------------------------------------------------ classement
def test_entree_dans_le_top_n(db):
    db.add_alert_rule("MSFT", "top_n_entry", {"n": 10})
    evenements = evaluate_rules(
        db, [score()], CONFIG, ranks={"MSFT": 4}, previous_ranks={"MSFT": 25}
    )
    assert len(evenements) == 1 and "entre dans le top 10" in evenements[0].message
    # Deja dans le top au passage suivant : aucune nouvelle alerte
    assert evaluate_rules(
        db, [score()], CONFIG, ranks={"MSFT": 3}, previous_ranks={"MSFT": 4}
    ) == []


def test_sortie_du_top_n(db):
    db.add_alert_rule("MSFT", "top_n_exit", {"n": 10})
    evenements = evaluate_rules(
        db, [score()], CONFIG, ranks={"MSFT": 18}, previous_ranks={"MSFT": 6}
    )
    assert len(evenements) == 1 and "sort du top 10" in evenements[0].message


def test_sortie_du_classement_complete(db):
    """Un titre devenu non classable doit compter comme une sortie."""
    db.add_alert_rule("MSFT", "top_n_exit", {"n": 10})
    evenements = evaluate_rules(db, [score()], CONFIG, ranks={}, previous_ranks={"MSFT": 3})
    assert len(evenements) == 1 and "hors classement" in evenements[0].message


# -------------------------------------------------------- publications
def test_premiere_observation_de_publication_silencieuse(db):
    """La premiere observation initialise la reference sans alerter : sinon
    toute nouvelle regle declencherait immediatement une fausse alerte."""
    db.add_alert_rule("MSFT", "earnings_published", {})
    scores = [score()]
    attach_earnings_dates(scores, {"MSFT": "2026-06-30"})
    assert evaluate_rules(db, scores, CONFIG) == []
    assert db.last_earnings_seen("MSFT") == "2026-06-30"

    suivants = [score()]
    attach_earnings_dates(suivants, {"MSFT": "2026-09-30"})
    evenements = evaluate_rules(db, suivants, CONFIG)
    assert len(evenements) == 1 and "nouvelle publication" in evenements[0].message


# ------------------------------------------------------------ journal
def test_evenements_journalises(db):
    db.add_alert_rule("MSFT", "price_below", {"threshold": 500})
    evaluate_rules(db, [score(prix=480.0)], CONFIG)
    journal = db.events()
    assert len(journal) == 1 and journal[0]["ticker"] == "MSFT"
    assert db.alert_rules()[0]["last_fired"] is not None


def test_titre_absent_de_l_analyse_ignore(db):
    db.add_alert_rule("INCONNU", "price_below", {"threshold": 500})
    assert evaluate_rules(db, [score()], CONFIG) == []
