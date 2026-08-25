"""Integrite des univers configures.

Ces tests protegent contre un piege reel rencontre en production : le ticker
ON (ON Semiconductor, Nasdaq-100) non quote est lu par YAML comme le booleen
True. Le titre disparaissait de l'analyse et l'affichage final plantait.
"""
from __future__ import annotations

import logging

import pytest
import yaml

from investassist.config import CONFIG_DIR, load_universes
from investassist.screener import tickers_for

CATALOGUE = load_universes()
UNIVERS = CATALOGUE.get("universes") or {}

# Valeurs que YAML 1.1 transforme en booleens si elles ne sont pas quotees.
PIEGES_YAML = {"ON", "OFF", "YES", "NO", "Y", "N", "TRUE", "FALSE"}


def test_tous_les_tickers_sont_du_texte():
    fautifs = [
        (nom, valeur, type(valeur).__name__)
        for nom, bloc in UNIVERS.items()
        for valeur in bloc.get("tickers") or []
        if not isinstance(valeur, str)
    ]
    assert fautifs == [], (
        "Tickers non textuels dans config/universes.yaml (guillemets manquants) : "
        f"{fautifs}"
    )


def test_le_ticker_on_est_preserve():
    """Regression : ON Semiconductor doit survivre au chargement YAML."""
    assert "ON" in tickers_for(["nasdaq100"])


def test_les_tickers_pieges_sont_quotes_dans_le_fichier():
    brut = (CONFIG_DIR / "universes.yaml").read_text(encoding="utf-8")
    for ligne in brut.splitlines():
        depouillee = ligne.strip()
        if depouillee.startswith("- "):
            valeur = depouillee[2:].strip()
            if valeur.strip('"').upper() in PIEGES_YAML:
                assert valeur.startswith('"'), f"ticker à risque non quoté : {ligne}"


def test_aucun_doublon_dans_un_univers():
    for nom, bloc in UNIVERS.items():
        tickers = bloc.get("tickers") or []
        doublons = {t for t in tickers if tickers.count(t) > 1}
        assert not doublons, f"doublons dans l'univers {nom} : {doublons}"


def test_univers_par_defaut_existent():
    for nom in CATALOGUE.get("default_selection") or []:
        assert nom in UNIVERS, f"univers par défaut inconnu : {nom}"


def test_valeur_non_textuelle_ecartee_avec_message(caplog):
    """Une erreur de saisie ne doit jamais produire un ticker booleen."""
    catalogue = {"test": {"tickers": ["MSFT", True, "", "AIR.PA"]}}
    with caplog.at_level(logging.ERROR):
        obtenus = tickers_for(["test"], catalogue=catalogue)
    assert obtenus == ["MSFT", "AIR.PA"]
    assert "guillemets" in caplog.text


def test_univers_inconnu_ignore(caplog):
    with caplog.at_level(logging.WARNING):
        assert tickers_for(["univers_inexistant"], catalogue={}) == []
    assert "Univers inconnu" in caplog.text


@pytest.mark.parametrize("nom", sorted(UNIVERS))
def test_chaque_univers_est_documente_et_non_vide(nom):
    bloc = UNIVERS[nom]
    assert bloc.get("label"), f"univers {nom} sans libellé"
    assert bloc.get("region") in ("EU", "US"), f"univers {nom} sans région valide"
    assert len(bloc.get("tickers") or []) > 0
