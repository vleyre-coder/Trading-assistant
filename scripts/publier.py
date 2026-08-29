#!/usr/bin/env python3
"""Publie le contenu de ce dossier vers un depot GitHub.

Concu pour le cas suivant : vous travaillez dans un dossier sur votre
ordinateur (telecharge en ZIP ou clone), vous y faites des modifications, et
vous voulez les envoyer vers VOTRE depot GitHub — y compris si ce dossier
n'a jamais ete relie a Git.

    python scripts/publier.py
    python scripts/publier.py --message "Nouveaux barèmes de notation"
    python scripts/publier.py --depot https://github.com/Llegender/Trading-assistant.git

Aucun mot de passe ni jeton n'est enregistre par ce script : l'authentification
est confiee au gestionnaire d'identifiants de Git (celui installe avec Git pour
Windows, ou GitHub Desktop).
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import shutil
import subprocess
import sys
import traceback
from datetime import datetime
from pathlib import Path

# Les consoles Windows héritées (cp850, cp437) ne savent pas afficher les
# caractères typographiques. Sans ce réglage, un simple tiret de séparation
# provoque une UnicodeEncodeError : le script s'arrête et, lancé par
# double-clic, la fenêtre se referme avant qu'on ait pu lire quoi que ce soit.
for _flux in (sys.stdout, sys.stderr):
    try:
        _flux.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

RACINE = Path(__file__).resolve().parents[1]
MEMOIRE = RACINE / "donnees" / "publication.json"
DEPOT_PROPOSE = "https://github.com/Llegender/Trading-assistant.git"
BRANCHE_PROPOSEE = "main"

# Ce qui ne doit jamais partir sur GitHub : donnees locales, secrets,
# artefacts de construction.
IGNORES = [
    "donnees/",
    "dist/",
    "build/",
    "*.spec.bak",
    "config/settings.yaml",
]


def trouver_git() -> str | None:
    """Localise git, y compris celui fourni par GitHub Desktop.

    GitHub Desktop installe son propre Git mais ne l'ajoute PAS au PATH du
    système : un utilisateur qui n'a installé que GitHub Desktop verrait donc
    « Git n'est pas installé » alors qu'il l'a bel et bien.
    """
    chemin = shutil.which("git")
    if chemin:
        return chemin
    if sys.platform != "win32":
        return None
    candidats: list[str] = []
    local = os.environ.get("LOCALAPPDATA", "")
    if local:
        candidats += sorted(
            glob.glob(os.path.join(local, "GitHubDesktop", "app-*", "resources", "app",
                                   "git", "cmd", "git.exe")),
            reverse=True,  # la version la plus récente d'abord
        )
    candidats += [
        r"C:\Program Files\Git\cmd\git.exe",
        r"C:\Program Files (x86)\Git\cmd\git.exe",
    ]
    for candidat in candidats:
        if os.path.isfile(candidat):
            return candidat
    return None


GIT = trouver_git()


def executer(*arguments: str, silencieux: bool = False) -> subprocess.CompletedProcess:
    commande = [GIT or "git", *arguments]
    return subprocess.run(
        commande, cwd=RACINE, capture_output=True, text=True,
        check=False, encoding="utf-8", errors="replace",
    ) if silencieux else subprocess.run(
        commande, cwd=RACINE, text=True, check=False,
    )


def sortie(*arguments: str) -> str:
    resultat = executer(*arguments, silencieux=True)
    return resultat.stdout.strip()


def pause(interactif: bool = True) -> None:
    """Laisse le temps de lire le message avant fermeture de la fenetre.

    Lance par double-clic, le script referme sa fenetre des qu'il se termine :
    sans cette pause, aucun message n'est lisible. Elle est evitee quand
    l'entree n'est pas un terminal (execution planifiee, tube), ou l'attente
    provoquerait une erreur.
    """
    if not interactif or not sys.stdin or not sys.stdin.isatty():
        return
    try:
        input("\n  Appuyez sur Entrée pour fermer.")
    except (EOFError, KeyboardInterrupt):
        pass


def demander(question: str, defaut: str) -> str:
    try:
        reponse = input(f"  {question} [{defaut}] : ").strip()
    except (EOFError, KeyboardInterrupt):
        return defaut
    return reponse or defaut


def charger_memoire() -> dict[str, str]:
    if MEMOIRE.exists():
        try:
            return json.loads(MEMOIRE.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return {}
    return {}


def enregistrer_memoire(donnees: dict[str, str]) -> None:
    MEMOIRE.parent.mkdir(parents=True, exist_ok=True)
    MEMOIRE.write_text(json.dumps(donnees, ensure_ascii=False, indent=2), encoding="utf-8")


def garantir_ignores() -> None:
    """Complete .gitignore : les donnees locales ne doivent jamais partir."""
    fichier = RACINE / ".gitignore"
    contenu = fichier.read_text(encoding="utf-8") if fichier.exists() else ""
    manquants = [motif for motif in IGNORES if motif not in contenu]
    if not manquants:
        return
    ajout = "\n# Ajouté par scripts/publier.py — jamais publié\n" + "\n".join(manquants) + "\n"
    fichier.write_text(contenu + ajout, encoding="utf-8")
    print(f"  .gitignore complété : {', '.join(manquants)}")


def preparer_depot(url: str, branche: str) -> bool:
    """Met le dossier sous suivi Git et le rattache au depot distant.

    Le cas interessant est celui d'un dossier issu d'un ZIP : il n'a pas
    d'historique. On recupere alors l'historique distant et on positionne
    HEAD dessus SANS toucher aux fichiers, de sorte que le contenu actuel du
    dossier devienne le prochain commit.
    """
    if not (RACINE / ".git").exists():
        print("  Ce dossier n'est pas encore suivi par Git : initialisation.")
        executer("init")
        executer("branch", "-M", branche)

    distant = sortie("remote", "get-url", "origin")
    if not distant:
        executer("remote", "add", "origin", url)
    elif distant != url:
        print(f"  Dépôt distant actuel : {distant}")
        if demander("Le remplacer par le dépôt visé ? (o/n)", "o").lower().startswith("o"):
            executer("remote", "set-url", "origin", url)

    print(f"  Récupération de {url} …")
    recuperation = executer("fetch", "origin", branche, silencieux=True)
    if recuperation.returncode != 0:
        message = (recuperation.stderr or "").strip()
        if "couldn't find remote ref" in message.lower() or "not found" in message.lower():
            print(f"  La branche « {branche} » n'existe pas encore sur le dépôt : "
                  "elle sera créée par la première publication.")
            return True
        print("\n  Impossible de joindre le dépôt distant :")
        print("  " + message.replace("\n", "\n  "))
        print("\n  Causes fréquentes : dépôt inexistant, nom mal orthographié, "
              "ou compte GitHub sans accès en écriture.")
        return False

    if sortie("rev-parse", "--verify", "HEAD"):
        # Le dossier a deja un historique : on rejoue par-dessus le distant.
        fusion = executer("merge", "--no-edit", f"origin/{branche}", silencieux=True)
        if fusion.returncode != 0:
            print("\n  Conflit avec la version distante. Résolvez-le puis relancez :")
            print("  " + (fusion.stdout or fusion.stderr).replace("\n", "\n  "))
            return False
    else:
        # Dossier issu d'un ZIP : on adopte l'historique distant sans modifier
        # le moindre fichier du dossier.
        executer("reset", "--soft", f"origin/{branche}", silencieux=True)
        print("  Historique distant adopté ; vos fichiers actuels sont conservés.")
    return True


def main() -> int:
    analyseur = argparse.ArgumentParser(description=__doc__,
                                        formatter_class=argparse.RawDescriptionHelpFormatter)
    analyseur.add_argument("--depot", default="", help="URL du dépôt GitHub")
    analyseur.add_argument("--branche", default="", help="Branche visée")
    analyseur.add_argument("--message", default="", help="Message du commit")
    analyseur.add_argument("--sans-question", action="store_true",
                           help="N'interroge pas ; utilise les valeurs mémorisées")
    analyseur.add_argument("--autoriser-suppressions", action="store_true",
                           help="Autorise la suppression de fichiers présents "
                                "seulement sur le dépôt distant")
    arguments = analyseur.parse_args()

    interactif = not arguments.sans_question
    print()
    print("  Publication vers GitHub")
    print("  " + "-" * 58)

    if GIT is None:
        print("\n  Git est introuvable sur cet ordinateur.")
        print("  Windows : https://git-scm.com/download/win  (laisser les options par defaut)")
        print("  macOS   : xcode-select --install")
        print("\n  Si vous avez installe GitHub Desktop, son Git n'est pas ajoute au")
        print("  PATH du systeme : installez Git separement avec le lien ci-dessus.")
        print("\n  Installez-le puis relancez ce script.")
        pause(interactif)
        return 1
    print(f"  Git utilisé : {GIT}")

    memoire = charger_memoire()
    depot = arguments.depot or memoire.get("depot") or DEPOT_PROPOSE
    branche = arguments.branche or memoire.get("branche") or BRANCHE_PROPOSEE
    if not arguments.sans_question:
        depot = demander("Dépôt GitHub", depot)
        branche = demander("Branche", branche)
    enregistrer_memoire({"depot": depot, "branche": branche})

    garantir_ignores()
    if not preparer_depot(depot, branche):
        pause(interactif)
        return 1

    executer("add", "-A")
    etat = sortie("status", "--porcelain")
    if not etat:
        print("\n  Aucune modification à publier : le dépôt est déjà à jour.")
        pause(interactif)
        return 0

    lignes = etat.splitlines()
    print(f"\n  {len(lignes)} fichier(s) modifié(s) :")
    for ligne in lignes[:15]:
        print(f"    {ligne}")
    if len(lignes) > 15:
        print(f"    … et {len(lignes) - 15} autre(s)")

    # Garde-fou : un dossier issu d'un ZIP ne contient pas forcement tout ce
    # que porte le depot. Publier tel quel effacerait ces fichiers. On ne le
    # fait donc jamais sans accord explicite.
    suppressions = [ligne[3:] for ligne in lignes if ligne.startswith("D ")]
    if suppressions and not arguments.autoriser_suppressions:
        print(f"\n  ATTENTION — {len(suppressions)} fichier(s) présent(s) sur le dépôt")
        print("  seraient SUPPRIMÉS, car absents de ce dossier :")
        for chemin in suppressions[:12]:
            print(f"    [x] {chemin}")
        if len(suppressions) > 12:
            print(f"    … et {len(suppressions) - 12} autre(s)")
        print("\n  Cela arrive quand ce dossier vient d'une archive ZIP qui ne")
        print("  contient pas tout le dépôt. Les fichiers resteraient récupérables")
        print("  dans l'historique, mais disparaîtraient de la version visible.")

        accord = "n"
        if interactif:
            accord = demander("Confirmer la suppression de ces fichiers ? (o/n)", "n")
        if not accord.lower().startswith("o"):
            print("\n  Publication annulée : rien n'a été envoyé.")
            print("  Deux solutions :")
            print("   - recuperer d'abord le dépôt complet (git clone) puis y refaire")
            print("     vos modifications - c'est la voie recommandee ;")
            print("   - ou relancer avec --autoriser-suppressions si la suppression")
            print("     est bien ce que vous voulez.")
            executer("reset", "-q", silencieux=True)
            pause(interactif)
            return 1

    message = arguments.message
    if not message and not arguments.sans_question:
        message = demander("Message du commit",
                           f"Mise à jour du {datetime.now():%d/%m/%Y %H:%M}")
    message = message or f"Mise à jour du {datetime.now():%d/%m/%Y %H:%M}"

    if executer("commit", "-m", message).returncode != 0:
        print("\n  Le commit a échoué.")
        print("  Si Git demande votre identité, exécutez une fois :")
        print('    git config --global user.name "Votre Nom"')
        print('    git config --global user.email "vous@exemple.fr"')
        pause(interactif)
        return 1

    print(f"\n  Envoi vers {depot} (branche {branche}) …")
    envoi = executer("push", "-u", "origin", f"HEAD:{branche}")
    if envoi.returncode != 0:
        print("\n  L'envoi a échoué.")
        print("  Si GitHub a refusé l'authentification : votre mot de passe de compte")
        print("  n'est pas accepté. Utilisez un jeton d'accès personnel")
        print("  (github.com > Settings > Developer settings > Personal access tokens),")
        print("  à coller à la place du mot de passe. GitHub Desktop gère cela pour vous.")
        pause(interactif)
        return 1

    print("\n  Publication terminée.")
    print(f"  {depot.removesuffix('.git')}/tree/{branche}")
    pause(interactif)
    return 0


def journaliser(texte: str) -> None:
    """Consigne le déroulement dans donnees/publication.log.

    Filet de sécurité : si la fenêtre se referme malgré tout, le message
    d'erreur reste consultable dans ce fichier.
    """
    try:
        journal = RACINE / "donnees" / "publication.log"
        journal.parent.mkdir(parents=True, exist_ok=True)
        with journal.open("a", encoding="utf-8") as fichier:
            fichier.write(f"\n===== {datetime.now():%d/%m/%Y %H:%M:%S} =====\n{texte}\n")
    except OSError:
        pass


if __name__ == "__main__":
    try:
        code = main()
    except Exception:  # noqa: BLE001 - toute erreur doit rester lisible
        trace = traceback.format_exc()
        print("\n  Une erreur inattendue s'est produite :\n")
        print(trace)
        journaliser(trace)
        print(f"  Détail également enregistré dans donnees/publication.log")
        try:
            if sys.stdin and sys.stdin.isatty():
                input("\n  Appuyez sur Entrée pour fermer.")
        except (EOFError, KeyboardInterrupt):
            pass
        code = 1
    raise SystemExit(code)
