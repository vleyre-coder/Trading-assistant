"""Garde-fous sur la recette d'empaquetage.

Un fichier de configuration oublie dans la recette ne provoque aucune
erreur : l'application demarre, mais la fonction qui en depend disparait
sans message. C'est arrive avec profils.yaml et esef.yaml — le selecteur de
profils ne proposait plus qu'une entree et l'historique europeen cessait
d'etre complete. Ces tests rendent l'oubli impossible.
"""
from __future__ import annotations

from pathlib import Path

RACINE = Path(__file__).resolve().parents[1]
SPEC = (RACINE / "investassist.spec").read_text(encoding="utf-8")


def test_la_recette_enumere_la_configuration_au_lieu_de_la_lister():
    """La liste doit etre construite par balayage du dossier, pas ecrite a la
    main : c'est le seul moyen qu'un fichier ajoute plus tard soit embarque."""
    assert 'Path("config").glob("*.yaml")' in SPEC
    # Aucun fichier de configuration ne doit etre nomme un a un.
    for nom in ("scoring.yaml", "universes.yaml", "alerts.yaml", "profils.yaml"):
        assert f'("config/{nom}", "config")' not in SPEC, (
            f"{nom} est listé à la main dans investassist.spec : "
            "l'énumération automatique suffit et évite les oublis."
        )


def test_les_reglages_personnels_ne_sont_pas_embarques():
    """settings.yaml contient l'adresse email transmise a la SEC : il ne doit
    pas voyager dans l'executable publie."""
    assert 'chemin.name != "settings.yaml"' in SPEC


def test_chaque_fichier_de_configuration_est_lisible():
    """Un YAML invalide casserait la construction ET l'application."""
    import yaml

    fichiers = sorted((RACINE / "config").glob("*.yaml"))
    assert len(fichiers) >= 5, f"configuration trop maigre : {fichiers}"
    for chemin in fichiers:
        with chemin.open(encoding="utf-8") as fh:
            assert yaml.safe_load(fh) is not None, f"{chemin.name} est vide"


def test_les_fichiers_dont_le_code_depend_existent():
    """Liste explicite des fichiers sans lesquels une fonction disparait."""
    for nom in ("scoring.yaml", "universes.yaml", "profils.yaml", "esef.yaml",
                "alerts.yaml", "settings.example.yaml"):
        assert (RACINE / "config" / nom).is_file(), f"config/{nom} manquant"
