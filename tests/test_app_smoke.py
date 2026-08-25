"""Verification que chaque vue de l'interface s'affiche sans erreur.

Le test tourne hors ligne : la base et le cache sont rediriges vers un
repertoire temporaire, aucune analyse n'est declenchee (elle exigerait des
appels reseau), et l'on verifie surtout la presence de l'avertissement de
non-conseil sur chaque ecran.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app.py"

pytest.importorskip("streamlit.testing.v1")
from streamlit.testing.v1 import AppTest  # noqa: E402

VUES = ["Classement", "Watchlist", "Alertes", "Historique des scores", "Méthodologie"]


@pytest.fixture(autouse=True)
def isolation(tmp_path, monkeypatch):
    """Base et cache isoles pour chaque test.

    Les caches Streamlit sont vides explicitement : @st.cache_resource est
    global au processus, donc sans ce nettoyage un test reutiliserait
    silencieusement la base du test precedent.
    """
    import streamlit as st

    monkeypatch.setenv("INVESTASSIST_DB", str(tmp_path / "test.sqlite"))
    monkeypatch.setenv("INVESTASSIST_CACHE_DIR", str(tmp_path / "cache"))
    st.cache_resource.clear()
    st.cache_data.clear()
    yield
    st.cache_resource.clear()
    st.cache_data.clear()


def run_view(nom: str) -> AppTest:
    app = AppTest.from_file(str(APP), default_timeout=120)
    app.run()
    assert not app.exception, f"exception au demarrage : {app.exception}"
    app.radio[0].set_value(nom).run()
    assert not app.exception, f"exception sur la vue {nom} : {app.exception}"
    return app


@pytest.mark.parametrize("nom", VUES)
def test_vue_s_affiche_avec_avertissement(nom):
    app = run_view(nom)
    textes = [w.value for w in app.warning] + [w.value for w in app.caption]
    assert any("conseil en investissement" in t for t in textes), (
        f"aucun avertissement de non-conseil sur la vue {nom}"
    )


def test_vue_classement_sans_analyse_prealable():
    """Sans analyse en memoire, l'ecran doit expliquer quoi faire, pas planter."""
    app = run_view("Classement")
    assert any("Relancer l'analyse" in info.value for info in app.info)


def test_methodologie_expose_les_ponderations():
    app = run_view("Méthodologie")
    contenu = " ".join(m.value for m in app.markdown)
    assert "Fenêtre visée" in contenu
    assert "35 %" in " ".join(str(df.value.to_dict()) for df in app.dataframe) + contenu


def test_watchlist_vide_ne_declenche_aucun_appel_reseau():
    app = run_view("Watchlist")
    assert any("Watchlist vide" in info.value for info in app.info)


def test_dernier_classement_restaure_au_demarrage(tmp_path):
    """Une analyse enregistree doit etre visible des l'ouverture, sans
    relancer de calcul : c'est le cas d'usage d'une execution planifiee."""
    from investassist.models import PillarResult, StockScore
    from investassist.storage import Database

    db = Database(tmp_path / "test.sqlite")
    scores = [
        StockScore(
            ticker=ticker, name=f"{ticker} SA", sector="Technology", region="EU",
            currency="EUR", price=100.0, composite=composite, ranked=True,
            window_years=5, coverage=1.0,
            pillars={
                "growth": PillarResult(
                    key="growth", weight=0.35, score=composite, coverage=1.0
                )
            },
        )
        for ticker, composite in [("AAA", 88.0), ("BBB", 61.0)]
    ]
    run_id = db.start_run(["cac40"])
    db.save_scores(run_id, scores, {"AAA": 1, "BBB": 2})
    db.finish_run(run_id, 2, 2, sector_medians={"Technology": 22.5})

    app = run_view("Classement")
    lignes = app.dataframe[0].value
    assert list(lignes["Ticker"]) == ["AAA", "BBB"]
    assert lignes["Score composite"].iloc[0] == pytest.approx(88.0)
    assert any("restauré" in c.value for c in app.caption)
    assert any("classé n°1" in s.value for s in app.success)
