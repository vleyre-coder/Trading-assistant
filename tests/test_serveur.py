"""Tests du serveur local de l'application de bureau.

Le serveur est demarre pour de vrai sur un port libre et interroge par HTTP :
ces tests eprouvent donc le comportement reel (routage, protection par jeton,
traversee de repertoire), pas une simulation. Aucune analyse n'est declenchee,
donc aucun appel reseau vers les sources financieres.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

import pytest

from investassist.config import load_scoring, load_settings
from investassist.serveur import demarrer


@pytest.fixture()
def serveur_local(tmp_path, monkeypatch):
    monkeypatch.setenv("INVESTASSIST_DONNEES", str(tmp_path / "donnees"))
    monkeypatch.setenv("INVESTASSIST_DB", str(tmp_path / "donnees" / "app.sqlite"))
    monkeypatch.setenv("INVESTASSIST_CACHE_DIR", str(tmp_path / "cache"))
    serveur, app, _ = demarrer(load_settings(), load_scoring(), port=0)
    socle = f"http://127.0.0.1:{serveur.server_address[1]}"
    try:
        yield socle, app
    finally:
        serveur.shutdown()
        serveur.server_close()


def appeler(socle, chemin, *, jeton=None, methode="GET", corps=None):
    donnees = json.dumps(corps).encode() if corps is not None else None
    entetes = {"Content-Type": "application/json"}
    if jeton:
        entetes["X-Jeton"] = jeton
    requete = urllib.request.Request(socle + chemin, data=donnees, method=methode, headers=entetes)
    with urllib.request.urlopen(requete, timeout=20) as reponse:
        return reponse.status, json.loads(reponse.read().decode("utf-8"))


def statut(socle, chemin, **kwargs):
    try:
        return appeler(socle, chemin, **kwargs)[0]
    except urllib.error.HTTPError as erreur:
        return erreur.code


# ------------------------------------------------------------- protection
def test_api_refusee_sans_jeton(serveur_local):
    """Un serveur sur 127.0.0.1 est joignable par tout programme de la
    machine : sans jeton, un site ouvert dans un onglet pourrait piloter
    l'application."""
    socle, _ = serveur_local
    assert statut(socle, "/api/etat") == 403
    assert statut(socle, "/api/watchlist", methode="POST", corps={"ticker": "MSFT"}) == 403


def test_api_refusee_avec_mauvais_jeton(serveur_local):
    socle, _ = serveur_local
    assert statut(socle, "/api/etat", jeton="mauvais-jeton") == 403


def test_interface_accessible_sans_jeton(serveur_local):
    """La page elle-meme reste servie : c'est elle qui porte le jeton."""
    socle, _ = serveur_local
    requete = urllib.request.Request(socle + "/")
    with urllib.request.urlopen(requete, timeout=20) as reponse:
        page = reponse.read().decode("utf-8")
    assert reponse.status == 200
    assert "conseil en investissement" in page.lower()


def test_traversee_de_repertoire_bloquee(serveur_local):
    socle, _ = serveur_local
    for chemin in ("/data/..%2f..%2fconfig%2fsettings.yaml", "/..%2f..%2fetc%2fpasswd"):
        assert statut(socle, chemin) == 404


# ------------------------------------------------------------------- etat
def test_etat_decrit_l_application(serveur_local):
    socle, app = serveur_local
    code, charge = appeler(socle, "/api/etat", jeton=app.jeton)
    assert code == 200
    assert charge["mode"] == "application locale"
    assert charge["analyse"]["en_cours"] is False
    assert {u["cle"] for u in charge["univers"]} >= {"cac40", "nasdaq100"}
    assert all(u["nombre"] > 0 for u in charge["univers"])
    assert "donnees" in charge["chemins"]


def test_instantane_livre_avec_l_application(serveur_local):
    """Un classement doit s'afficher des l'ouverture, sans attendre huit
    minutes d'analyse."""
    socle, _ = serveur_local
    with urllib.request.urlopen(socle + "/data/ranking.json", timeout=20) as reponse:
        classement = json.loads(reponse.read().decode("utf-8"))
    assert classement["counts"]["ranked"] > 0
    assert classement["disclaimer"]["main"]


# -------------------------------------------------------------- watchlist
def test_cycle_watchlist(serveur_local):
    socle, app = serveur_local
    _, vide = appeler(socle, "/api/watchlist", jeton=app.jeton)
    assert vide["titres"] == []

    _, apres = appeler(socle, "/api/watchlist", jeton=app.jeton, methode="POST",
                       corps={"ticker": "msft", "note": "à suivre"})
    assert [t["ticker"] for t in apres["titres"]] == ["MSFT"]

    _, retire = appeler(socle, "/api/watchlist/MSFT", jeton=app.jeton, methode="DELETE")
    assert retire["titres"] == []


def test_watchlist_refuse_un_ticker_vide(serveur_local):
    socle, app = serveur_local
    assert statut(socle, "/api/watchlist", jeton=app.jeton, methode="POST", corps={}) == 400


# ---------------------------------------------------------------- alertes
def test_creation_et_suppression_d_alerte(serveur_local):
    socle, app = serveur_local
    _, creee = appeler(socle, "/api/alertes", jeton=app.jeton, methode="POST",
                       corps={"ticker": "air.pa", "type": "price_below",
                              "parametres": {"threshold": 150}})
    regles = creee["regles"]
    assert len(regles) == 1
    assert regles[0]["ticker"] == "AIR.PA" and regles[0]["params"]["threshold"] == 150

    _, apres = appeler(socle, f"/api/alertes/{regles[0]['id']}", jeton=app.jeton, methode="DELETE")
    assert apres["regles"] == []


def test_type_d_alerte_inconnu_refuse(serveur_local):
    socle, app = serveur_local
    assert statut(socle, "/api/alertes", jeton=app.jeton, methode="POST",
                  corps={"ticker": "MSFT", "type": "achat_immediat"}) == 400


def test_seuil_de_cours_nul_refuse(serveur_local):
    """Un seuil a zero rendrait la regle inoperante ou toujours vraie."""
    socle, app = serveur_local
    assert statut(socle, "/api/alertes", jeton=app.jeton, methode="POST",
                  corps={"ticker": "MSFT", "type": "price_below",
                         "parametres": {"threshold": 0}}) == 400


# ----------------------------------------------------------------- divers
def test_route_inconnue(serveur_local):
    socle, app = serveur_local
    assert statut(socle, "/api/inexistant", jeton=app.jeton) == 404


def test_analyse_sans_univers_refusee(serveur_local, monkeypatch):
    socle, app = serveur_local
    monkeypatch.setattr("investassist.serveur.load_universes", lambda: {"default_selection": []})
    assert statut(socle, "/api/analyse", jeton=app.jeton, methode="POST",
                  corps={"univers": []}) == 400
