#!/usr/bin/env python3
"""Audit de bout en bout de l'application empaquetée.

Rejoue le parcours réel d'un utilisateur : dossier vierge, lancement de
l'exécutable, ouverture du navigateur sur l'adresse annoncée, usage complet de
l'interface, arrêt, relance, puis copie du dossier sur une autre machine.

Chaque affirmation est vérifiée par exécution — jamais par lecture de code.

    python scripts/audit_application.py
    python scripts/audit_application.py --executable dist/Investassist.exe

Prérequis : l'exécutable doit avoir été construit (pyinstaller
investassist.spec) et Playwright installé (pip install playwright).
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

RACINE = Path(__file__).resolve().parents[1]

analyseur = argparse.ArgumentParser(
    description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
)
analyseur.add_argument("--executable", default="", help="Exécutable à auditer")
analyseur.add_argument("--navigateur", default="", help="Chemin d'un navigateur Chromium")
analyseur.add_argument("--captures", default="", help="Dossier de dépôt des captures")
analyseur.add_argument("--univers", default="cac40",
                       help="Univers analysé pendant l'audit (le plus petit possible)")
arguments = analyseur.parse_args()

EXECUTABLE = Path(arguments.executable) if arguments.executable else (
    RACINE / "dist" / ("Investassist.exe" if sys.platform == "win32" else "Investassist")
)
NAVIGATEUR = arguments.navigateur or None
CAPTURES = Path(arguments.captures) if arguments.captures else None
BASE = Path(tempfile.mkdtemp(prefix="audit-investassist-"))
BUREAU = BASE / "Bureau" / "Investassist"
AILLEURS = BASE / "AutrePC" / "Investassist"

resultats: list[tuple[bool, str, str]] = []


def verifier(intitule: str, condition: object, detail: str = "") -> bool:
    """Enregistre et affiche le résultat d'une vérification."""
    reussi = bool(condition)
    resultats.append((reussi, intitule, detail))
    print(f"  [{'OK  ' if reussi else 'ECHEC'}] {intitule}" + (f" — {detail}" if detail else ""))
    return reussi


def lancer(dossier: Path, port: int) -> tuple[subprocess.Popen, str | None]:
    """Démarre l'exécutable comme le ferait un double-clic, et lit l'adresse."""
    journal = (dossier / "console.log").open("w", encoding="utf-8")
    processus = subprocess.Popen(
        [str(dossier / EXECUTABLE.name), "--port", str(port)],
        cwd=dossier, stdout=journal, stderr=subprocess.STDOUT,
    )
    for _ in range(60):
        time.sleep(1)
        texte = (dossier / "console.log").read_text(encoding="utf-8", errors="replace")
        trouve = re.search(r"(http://127\.0\.0\.1:\d+/\?jeton=[\w-]+)", texte)
        if trouve:
            return processus, trouve.group(1)
    return processus, None


def api(socle: str, chemin: str, jeton: str) -> dict:
    requete = urllib.request.Request(socle + chemin, headers={"X-Jeton": jeton})
    with urllib.request.urlopen(requete, timeout=20) as reponse:
        return json.loads(reponse.read().decode("utf-8"))


def capturer(page, nom: str) -> None:
    if CAPTURES:
        CAPTURES.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(CAPTURES / nom))


def main() -> int:
    if not EXECUTABLE.exists():
        print(f"Exécutable introuvable : {EXECUTABLE}")
        print("Construisez-le d'abord :  pyinstaller investassist.spec")
        return 2

    print("\n=== 1. PREMIER LANCEMENT, DOSSIER VIERGE (comme un téléchargement) ===")
    BUREAU.mkdir(parents=True, exist_ok=True)
    cible = BUREAU / EXECUTABLE.name
    shutil.copy2(EXECUTABLE, cible)
    cible.chmod(0o755)

    depart = time.time()
    processus, adresse = lancer(BUREAU, 8811)
    verifier("L'application démarre et annonce son adresse", adresse,
             f"{time.time() - depart:.0f} s")
    if not adresse:
        print((BUREAU / "console.log").read_text(errors="replace")[:2000])
        return 1

    console = (BUREAU / "console.log").read_text(encoding="utf-8", errors="replace")
    verifier("Aucune trace d'erreur dans la console",
             "Traceback" not in console and "Error" not in console)
    verifier("Avertissement de non-conseil affiché au démarrage",
             "conseil en investissement" in console)
    verifier("Réglages créés à côté de l'exécutable",
             (BUREAU / "config" / "settings.yaml").exists())
    verifier("Données créées à côté de l'exécutable",
             (BUREAU / "donnees" / "ranking.json").exists())
    presents = sorted(p.name for p in BUREAU.iterdir())
    verifier("Rien n'est écrit ailleurs sur la machine",
             presents == sorted([EXECUTABLE.name, "config", "console.log", "donnees"]),
             ", ".join(presents))

    socle, jeton = adresse.split("/?")[0], adresse.split("jeton=")[1]

    print("\n=== 2. PARCOURS DANS LE NAVIGATEUR ===")
    from playwright.sync_api import sync_playwright

    erreurs: list[str] = []
    with sync_playwright() as pilote:
        options = {"executable_path": NAVIGATEUR} if NAVIGATEUR else {}
        navigateur = pilote.chromium.launch(**options)
        page = navigateur.new_page(viewport={"width": 1320, "height": 950})
        page.on("pageerror", lambda e: erreurs.append(f"pageerror: {e}"))
        page.on("console", lambda m: erreurs.append(m.text) if m.type == "error" else None)

        page.goto(adresse)
        page.wait_for_selector("table tbody tr", timeout=30000)
        lignes = page.locator("table tbody tr").count()
        verifier("Le classement s'affiche", lignes > 20, f"{lignes} titres")
        verifier("Aucun mot de passe demandé",
                 page.locator('input[type="password"]').count() == 0)
        verifier("Le jeton disparaît de la barre d'adresse", "jeton" not in page.url)
        verifier("Fonctions interactives disponibles",
                 page.locator("text=Lancer l'analyse maintenant").count() == 1)
        capturer(page, "audit-classement.png")

        page.locator("table tbody tr a.lien-titre").first.click()
        page.wait_for_selector("#detail-titre", timeout=10000)
        criteres = page.locator("#detail-titre .critere").count()
        verifier("La fiche d'un titre montre le détail par critère", criteres >= 10,
                 f"{criteres} critères")

        page.locator("#detail-titre button.bouton").first.click()
        page.wait_for_timeout(900)
        suivis = api(socle, "/api/watchlist", jeton)["titres"]
        verifier("Ajout à la watchlist enregistré", len(suivis) == 1,
                 suivis[0]["ticker"] if suivis else "aucun")

        # Rechargement sans le jeton : le cookie de session doit prendre le relais.
        page.goto(socle + "/")
        page.wait_for_selector("table tbody tr", timeout=30000)
        verifier("Rechargement sans jeton : toujours pleinement fonctionnel",
                 page.locator("text=Lancer l'analyse maintenant").count() == 1)

        print("\n=== 3. ANALYSE LANCÉE DEPUIS L'INTERFACE ===")
        page.locator('nav.onglets button[data-vue="classement"]').click()
        page.wait_for_timeout(400)
        for case in page.locator(".panneau .filtres label input").all():
            libelle = case.evaluate("n => n.parentElement.textContent")
            if arguments.univers not in libelle.lower().replace(" ", "").replace("-", ""):
                if case.is_checked() and "cache" not in libelle.lower():
                    case.uncheck()
                    page.wait_for_timeout(250)
        page.locator("text=Lancer l'analyse maintenant").click()
        page.wait_for_timeout(2500)
        verifier("L'avancement s'affiche pendant l'analyse",
                 page.locator("text=Analyse en cours").count() == 1)
        capturer(page, "audit-analyse.png")

        debut = time.time()
        analyse = {}
        while time.time() - debut < 600:
            analyse = api(socle, "/api/etat", jeton)["analyse"]
            if not analyse["en_cours"]:
                break
            time.sleep(4)
        verifier("L'analyse se termine sans erreur", not analyse.get("erreur"),
                 analyse.get("erreur") or str(analyse.get("resume")))
        resume = analyse.get("resume") or {}
        verifier("Le classement est recalculé", resume.get("classes", 0) > 10,
                 f"{resume.get('classes')} titres en {resume.get('duree_secondes', 0):.0f} s")
        navigateur.close()

    verifier("Aucune erreur JavaScript sur tout le parcours", not erreurs,
             "; ".join(erreurs[:2]))

    print("\n=== 4. FERMETURE ET RELANCE ===")
    processus.terminate()
    processus.wait(timeout=20)
    verifier("Arrêt propre", processus.returncode in (0, -15, 143, 1),
             f"code {processus.returncode}")

    processus2, adresse2 = lancer(BUREAU, 8812)
    verifier("Deuxième lancement", adresse2)
    if adresse2:
        console2 = (BUREAU / "console.log").read_text(encoding="utf-8", errors="replace")
        verifier("Les réglages existants ne sont pas réécrits",
                 "Réglages créés" not in console2)
        with urllib.request.urlopen(
            adresse2.split("/?")[0] + "/data/ranking.json", timeout=20
        ) as reponse:
            donnees = json.loads(reponse.read().decode("utf-8"))
        verifier("L'analyse précédente est retrouvée",
                 donnees["counts"]["ranked"] > 10,
                 f"{donnees['counts']['ranked']} titres, univers {donnees['universes']}")
    processus2.terminate()
    processus2.wait(timeout=20)

    print("\n=== 5. TRANSPORT SUR UN AUTRE ORDINATEUR (copie du dossier) ===")
    shutil.copytree(BUREAU, AILLEURS)
    processus3, adresse3 = lancer(AILLEURS, 8813)
    verifier("Démarrage depuis le dossier copié", adresse3)
    if adresse3:
        jeton3 = adresse3.split("jeton=")[1]
        suivis3 = api(adresse3.split("/?")[0], "/api/watchlist", jeton3)["titres"]
        verifier("La watchlist a suivi le dossier", len(suivis3) == 1,
                 suivis3[0]["ticker"] if suivis3 else "perdue")
        verifier("Un jeton différent à chaque lancement", jeton3 != jeton)
    processus3.terminate()
    processus3.wait(timeout=20)

    print("\n" + "=" * 62)
    echecs = [ligne for ligne in resultats if not ligne[0]]
    print(f"  {len(resultats) - len(echecs)} / {len(resultats)} vérifications passées")
    for _, intitule, detail in echecs:
        print(f"  ECHEC : {intitule} — {detail}")
    return 1 if echecs else 0


if __name__ == "__main__":
    try:
        code = main()
    finally:
        shutil.rmtree(BASE, ignore_errors=True)
    raise SystemExit(code)
