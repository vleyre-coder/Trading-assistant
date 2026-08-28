"""Localisation des fichiers selon le mode d'execution.

Trois modes doivent fonctionner sans configuration :
  - depuis les sources (developpement) ;
  - depuis un executable PyInstaller « un seul fichier » ;
  - depuis un dossier portable copie sur une cle USB.

Principe : les donnees et la configuration modifiables vivent A COTE de
l'executable, jamais dans le paquet lui-meme. Copier le dossier suffit donc a
emporter l'outil, son historique et ses reglages sur une autre machine.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path


def est_empaquete() -> bool:
    """Vrai lorsque le programme tourne depuis un executable PyInstaller."""
    return bool(getattr(sys, "frozen", False))


def racine_paquet() -> Path:
    """Racine des ressources embarquees (configuration par defaut, site web)."""
    if est_empaquete():
        # PyInstaller extrait les ressources dans un dossier temporaire dont
        # le chemin est expose par _MEIPASS.
        base = getattr(sys, "_MEIPASS", None)
        if base:
            return Path(base)
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def racine_installation() -> Path:
    """Dossier ou vit l'executable (ou la racine du depot en developpement)."""
    if est_empaquete():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def _inscriptible(dossier: Path) -> bool:
    try:
        dossier.mkdir(parents=True, exist_ok=True)
        temoin = dossier / ".test-ecriture"
        temoin.write_text("ok", encoding="utf-8")
        temoin.unlink()
        return True
    except OSError:
        return False


def dossier_donnees() -> Path:
    """Emplacement de la base, du cache et des exports.

    Ordre de priorite :
      1. la variable INVESTASSIST_DONNEES, si elle est definie ;
      2. « donnees/ » a cote de l'executable — c'est le mode portable ;
      3. le dossier utilisateur, si l'emplacement precedent est en lecture
         seule (executable lance depuis un CD, un partage reseau protege,
         ou le dossier Telechargements verrouille par une politique).
    """
    force = os.environ.get("INVESTASSIST_DONNEES")
    if force:
        chemin = Path(force).expanduser()
        if _inscriptible(chemin):
            return chemin

    portable = racine_installation() / "donnees"
    if _inscriptible(portable):
        return portable

    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home()))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    utilisateur = base / "Investassist"
    if _inscriptible(utilisateur):
        return utilisateur

    # Dernier recours : l'outil reste utilisable, mais sans persistance.
    return Path(tempfile.gettempdir()) / "Investassist"


def dossier_configuration() -> Path:
    """Configuration active : celle posee a cote de l'executable si elle existe.

    L'utilisateur peut ainsi ajuster les ponderations sans reconstruire
    l'executable : il copie le dossier config/ a cote et l'edite.
    """
    externe = racine_installation() / "config"
    if externe.exists() and any(externe.glob("*.yaml")):
        return externe
    return racine_paquet() / "config"


def dossier_site() -> Path:
    """Fichiers de l'interface web servis par le serveur local."""
    return racine_paquet() / "web"


def resume() -> dict[str, str]:
    """Chemins effectifs, affiches au demarrage pour lever toute ambiguite."""
    return {
        "mode": "executable" if est_empaquete() else "sources",
        "installation": str(racine_installation()),
        "configuration": str(dossier_configuration()),
        "donnees": str(dossier_donnees()),
        "site": str(dossier_site()),
    }
