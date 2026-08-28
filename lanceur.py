"""Point d'entree de l'application de bureau Investassist.

Demarre le serveur local, ouvre le navigateur sur l'interface, puis attend.
Fermer la fenetre (ou Ctrl+C) arrete tout.

    python lanceur.py
    python lanceur.py --port 8765 --sans-navigateur

C'est ce fichier qui est transforme en executable par PyInstaller.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import threading
import time
import webbrowser
from pathlib import Path

if __package__ in (None, ""):  # execution directe depuis les sources
    racine = Path(__file__).resolve().parent
    if str(racine / "src") not in sys.path:
        sys.path.insert(0, str(racine / "src"))

from investassist import __version__  # noqa: E402
from investassist.chemins import (  # noqa: E402
    dossier_configuration,
    dossier_donnees,
    est_empaquete,
    racine_installation,
    resume,
)
from investassist.config import load_scoring, load_settings  # noqa: E402
from investassist.disclaimers import MAIN  # noqa: E402
from investassist.serveur import demarrer  # noqa: E402

LARGEUR = 74


def bandeau(lignes: list[str]) -> None:
    print("┌" + "─" * (LARGEUR - 2) + "┐")
    for ligne in lignes:
        print("│ " + ligne.ljust(LARGEUR - 4) + " │")
    print("└" + "─" * (LARGEUR - 2) + "┘")


def preparer_configuration() -> None:
    """Cree config/settings.yaml a cote de l'executable au premier lancement.

    Sans identification, la SEC refuse ses donnees : l'application resterait
    limitee a Yahoo Finance, donc a quatre exercices au lieu de cinq.
    """
    dossier = dossier_configuration()

    if est_empaquete() and not (racine_installation() / "config").exists():
        # Dans un executable, la configuration embarquee vit dans un dossier
        # temporaire efface a la fermeture. On la recopie donc a cote de
        # l'executable : les reglages deviennent persistants et modifiables
        # sans reconstruire quoi que ce soit.
        externe = racine_installation() / "config"
        try:
            externe.mkdir(parents=True, exist_ok=True)
            for fichier in sorted(dossier.glob("*.yaml")):
                (externe / fichier.name).write_text(
                    fichier.read_text(encoding="utf-8"), encoding="utf-8"
                )
            print(f"  Configuration installée : {externe}")
            dossier = externe
        except OSError:
            pass  # emplacement en lecture seule : valeurs par defaut

    reglages = dossier / "settings.yaml"
    modele = dossier / "settings.example.yaml"
    if reglages.exists() or not modele.exists():
        return
    try:
        reglages.write_text(modele.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"  Réglages créés : {reglages}")
    except OSError:
        pass


def main() -> int:
    # Sortie ligne par ligne : sans cela, la console d'un executable Windows
    # reste vide jusqu'a la fermeture, et l'adresse a coller n'apparait jamais.
    for flux in (sys.stdout, sys.stderr):
        try:
            flux.reconfigure(line_buffering=True, errors="replace")
        except (AttributeError, ValueError):
            pass

    analyseur = argparse.ArgumentParser(description=__doc__)
    analyseur.add_argument("--port", type=int, default=0, help="Port (0 = automatique)")
    analyseur.add_argument("--sans-navigateur", action="store_true",
                           help="Ne pas ouvrir le navigateur")
    analyseur.add_argument("--verbeux", action="store_true", help="Journal détaillé")
    arguments = analyseur.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if arguments.verbeux else logging.INFO,
        format="%(asctime)s  %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("peewee").setLevel(logging.WARNING)

    preparer_configuration()

    # Base, cache et exports vivent dans le dossier portable, jamais dans le
    # paquet : copier le dossier de l'application emporte donc l'historique,
    # la watchlist et les regles d'alerte avec lui.
    donnees = dossier_donnees()
    os.environ.setdefault("INVESTASSIST_DB", str(donnees / "investassist.sqlite"))
    os.environ.setdefault("INVESTASSIST_CACHE_DIR", str(donnees / "cache"))

    settings = load_settings(dossier_configuration() / "settings.yaml")
    cfg = load_scoring(dossier_configuration() / "scoring.yaml")

    try:
        serveur, app, _ = demarrer(settings, cfg, port=arguments.port)
    except OSError as exc:
        print(f"\n  Impossible de démarrer le serveur local : {exc}")
        input("\n  Appuyez sur Entrée pour fermer.")
        return 1

    port = serveur.server_address[1]
    adresse = f"http://127.0.0.1:{port}/?jeton={app.jeton}"
    chemins = resume()

    print()
    bandeau([
        f"Investassist {__version__} — analyse fondamentale",
        "",
        "L'application tourne sur VOTRE ordinateur. Aucune donnée",
        "personnelle ne quitte cette machine.",
        "",
        f"Adresse    : http://127.0.0.1:{port}",
        f"Données    : {chemins['donnees']}",
        f"Réglages   : {chemins['configuration']}",
        "",
        "Fermez cette fenêtre pour arrêter l'application.",
    ])
    print()
    print(f"  ⚠️  {MAIN}")
    print()

    # L'adresse porte le jeton d'acces : elle doit etre affichee dans tous les
    # cas, sans quoi l'interface serait inaccessible lorsque le navigateur ne
    # s'ouvre pas tout seul.
    print("  Adresse complète, à coller dans le navigateur si besoin :")
    print(f"  {adresse}")
    print()
    if not arguments.sans_navigateur:
        threading.Timer(0.6, lambda: webbrowser.open(adresse)).start()

    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n  Arrêt demandé.")
    finally:
        serveur.shutdown()
        serveur.server_close()

    if est_empaquete() and os.name == "nt":
        # Sans cette pause, la fenetre se referme avant que l'utilisateur ait
        # pu lire un eventuel message d'erreur.
        input("  Appuyez sur Entrée pour fermer.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
