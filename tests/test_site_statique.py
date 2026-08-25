"""Verifications du cablage du site statique et de son deploiement.

Ces tests protegent des erreurs muettes : un chemin de fichier renomme, une
configuration Netlify qui ne publie pas le bon repertoire, ou un site publie
sans son avertissement de non-conseil.
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


def test_configuration_netlify():
    configuration = (ROOT / "netlify.toml").read_text(encoding="utf-8")
    assert 'publish = "web"' in configuration
    assert 'function = "auth"' in configuration
    assert "noindex" in configuration
    assert (ROOT / "netlify" / "edge-functions" / "auth.ts").exists()


def test_fonction_de_protection():
    fonction = (ROOT / "netlify" / "edge-functions" / "auth.ts").read_text(encoding="utf-8")
    # Le mot de passe vient d'une variable d'environnement, jamais du depot.
    assert 'Deno.env.get("SITE_PASSWORD")' in fonction
    assert "WWW-Authenticate" in fonction
    assert "comparaisonConstante" in fonction
    assert not re.search(r'attendu\s*=\s*"[^"]+"', fonction), "mot de passe en dur"


def test_workflow_planifie():
    workflow = yaml.safe_load((ROOT / ".github/workflows/analyse.yml").read_text(encoding="utf-8"))
    declencheurs = workflow[True] if True in workflow else workflow["on"]
    assert "schedule" in declencheurs and "workflow_dispatch" in declencheurs
    assert workflow["jobs"]["analyser"]["permissions"]["contents"] == "write"
    etapes = json.dumps(workflow["jobs"]["analyser"]["steps"], ensure_ascii=False)
    assert "scripts/build_site.py" in etapes
    # Les identifiants passent par des secrets, jamais en clair.
    assert "${{ secrets.INVESTASSIST_SMTP_PASSWORD }}" in etapes
    assert "password" not in etapes.lower().replace("smtp_password", "")


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
