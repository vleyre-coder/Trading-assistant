#!/usr/bin/env python3
"""Preparation du premier lancement.

Cree config/settings.yaml a partir du modele si necessaire, en demandant
l'adresse email exigee par la SEC pour ses appels d'API. Concu pour etre
appele par start.bat / start.sh : aucune saisie n'est requise aux
lancements suivants.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SETTINGS = ROOT / "config" / "settings.yaml"
EXAMPLE = ROOT / "config" / "settings.example.yaml"

PLACEHOLDER = "votre.email@exemple.fr"


def demander_email() -> str:
    """L'API de la SEC exige un contact identifiable sur chaque requete.

    Sans adresse valide, la SEC bloque les appels : l'outil fonctionnerait
    alors sur les seules donnees Yahoo, avec 4 ans d'historique au lieu de 5.
    """
    print()
    print("  Première configuration")
    print("  " + "-" * 58)
    print("  L'API publique de la SEC (données financières officielles des")
    print("  sociétés américaines) exige une adresse email d'identification")
    print("  sur chaque requête. Elle n'est envoyée qu'à la SEC, jamais")
    print("  ailleurs, et reste sur votre ordinateur.")
    print()
    try:
        saisie = input("  Votre adresse email : ").strip()
    except (EOFError, KeyboardInterrupt):
        saisie = ""
    if "@" not in saisie:
        print("  Adresse non renseignée — vous pourrez la corriger plus tard")
        print(f"  dans {SETTINGS.relative_to(ROOT)} (champ sec.user_agent).")
        return PLACEHOLDER
    return saisie


def main() -> int:
    if SETTINGS.exists():
        return 0
    if not EXAMPLE.exists():
        print(f"  ERREUR : modèle introuvable ({EXAMPLE})", file=sys.stderr)
        return 1

    email = demander_email()
    contenu = EXAMPLE.read_text(encoding="utf-8").replace(PLACEHOLDER, email)
    SETTINGS.write_text(contenu, encoding="utf-8")
    print()
    print(f"  Configuration créée : {SETTINGS.relative_to(ROOT)}")
    print("  (ce fichier reste local et n'est jamais partagé)")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
