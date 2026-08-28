"""Verifications du cablage de l'interface et de l'empaquetage.

Ces tests protegent des erreurs muettes : un chemin de fichier renomme, une
recette d'empaquetage qui oublie l'interface, ou une page servie sans son
avertissement de non-conseil.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"


def test_fichiers_du_site_presents():
    for chemin in ["index.html", "robots.txt", "assets/styles.css", "assets/app.js"]:
        assert (WEB / chemin).exists(), f"fichier manquant : web/{chemin}"


def test_page_reference_ses_ressources():
    page = (WEB / "index.html").read_text(encoding="utf-8")
    assert 'href="assets/styles.css"' in page
    assert 'src="assets/app.js"' in page
    assert 'name="robots" content="noindex' in page


def test_page_porte_l_avertissement_sans_javascript():
    """L'avertissement doit etre dans le HTML servi, pas seulement injecte
    par le script : une erreur de chargement ne doit jamais faire disparaitre
    la mention de non-conseil."""
    page = (WEB / "index.html").read_text(encoding="utf-8")
    sans_balises = re.sub(r"<[^>]+>", " ", page)
    assert "ne constitue pas un conseil en investissement" in sans_balises.lower()
    assert "prédiction de performance future" in sans_balises.lower()


def test_script_lit_les_bons_fichiers():
    script = (WEB / "assets/app.js").read_text(encoding="utf-8")
    assert 'fetch("data/ranking.json"' in script
    assert 'fetch("data/history.json"' in script


def test_aucune_ressource_externe():
    """Le site doit rester autonome : aucune requete vers un tiers, qui
    revelerait la consultation de cet outil personnel."""
    for chemin in ["index.html", "assets/app.js", "assets/styles.css"]:
        contenu = (WEB / chemin).read_text(encoding="utf-8")
        externes = re.findall(r"https?://[^\s\"')]+", contenu)
        autorises = ("http://www.w3.org/2000/svg", "http://127.0.0.1")
        for url in externes:
            assert url.startswith(autorises), f"ressource externe dans {chemin} : {url}"


def test_recette_d_empaquetage_embarque_l_essentiel():
    """Une recette qui oublie l'interface ou la configuration produit un
    executable qui demarre puis affiche une page blanche."""
    recette = (ROOT / "investassist.spec").read_text(encoding="utf-8")
    for ressource in ("web/index.html", "web/assets", "config/scoring.yaml",
                      "config/universes.yaml", "config/settings.example.yaml"):
        assert ressource in recette, f"ressource absente de l'empaquetage : {ressource}"
    assert '"lanceur.py"' in recette
    # Les bibliotheques de l'ancienne interface alourdiraient l'executable.
    assert '"streamlit"' in recette and '"plotly"' in recette


def test_workflow_de_construction():
    workflow = yaml.safe_load(
        (ROOT / ".github/workflows/executable.yml").read_text(encoding="utf-8")
    )
    travail = workflow["jobs"]["windows"]
    assert travail["runs-on"] == "windows-latest"
    etapes = json.dumps(travail["steps"], ensure_ascii=False)
    assert "investassist.spec" in etapes
    # L'executable produit doit etre eprouve, pas seulement construit.
    assert "127.0.0.1:8797" in etapes
    assert "pytest" in etapes


def test_lanceur_present_et_autonome():
    lanceur = (ROOT / "lanceur.py").read_text(encoding="utf-8")
    assert "demarrer(" in lanceur
    assert "127.0.0.1" in lanceur
    # Le jeton doit etre affiche : sans lui, l'interface est inaccessible.
    assert "jeton" in lanceur


def test_script_de_publication():
    script = (ROOT / "scripts" / "publier.py").read_text(encoding="utf-8")
    # Seuls le dépôt et la branche sont mémorisés : jamais d'identifiant.
    assert 'enregistrer_memoire({"depot": depot, "branche": branche})' in script
    # Les données locales et les réglages ne doivent jamais être publiés.
    assert "donnees/" in script and "config/settings.yaml" in script
    assert (ROOT / "publier.bat").exists() and (ROOT / "publier.sh").exists()


@pytest.mark.skipif(not (WEB / "data/ranking.json").exists(), reason="aucune analyse publiee")
def test_donnees_publiees_coherentes():
    donnees = json.loads((WEB / "data/ranking.json").read_text(encoding="utf-8"))
    assert donnees["disclaimer"]["main"]
    assert donnees["counts"]["ranked"] == len(donnees["ranked"])
    assert donnees["counts"]["excluded"] == len(donnees["excluded"])
    rangs = [t["rank"] for t in donnees["ranked"]]
    assert rangs == sorted(rangs), "le classement publié n'est pas ordonné"
    scores = [t["composite"] for t in donnees["ranked"]]
    assert scores == sorted(scores, reverse=True), "scores non décroissants"
