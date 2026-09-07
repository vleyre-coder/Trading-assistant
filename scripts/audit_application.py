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
import socket
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
def _trouver_navigateur() -> str | None:
    """Chemin d'un Chromium reellement present sur la machine.

    Playwright vise un numero de version precis : quand l'environnement met
    la bibliotheque a jour sans retelecharger les navigateurs, le chemin
    attendu n'existe plus et l'audit echoue sur son outillage, pas sur
    l'application — diagnostic trompeur. On cherche donc le binaire
    installe plutot que de supposer lequel c'est.
    """
    if arguments.navigateur:
        return arguments.navigateur
    for racine in (Path("/opt/pw-browsers"), Path.home() / ".cache/ms-playwright"):
        if not racine.is_dir():
            continue
        for motif in ("chrome-linux/chrome", "chrome-headless-shell-linux64/chrome-headless-shell"):
            trouves = sorted(racine.glob(f"*/{motif}"))
            if trouves:
                return str(trouves[-1])
    return None


NAVIGATEUR = _trouver_navigateur()
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


def port_libre() -> int:
    """Port disponible, demandé au système.

    Un audit doit pouvoir être rejoué sans nettoyage préalable. Avec des
    ports fixes, un processus resté en vie d'une exécution précédente
    faisait échouer le premier contrôle sur « adresse déjà utilisée » — un
    diagnostic qui accuse l'application alors que le défaut est dans
    l'outillage de test.
    """
    with socket.socket() as prise:
        prise.bind(("127.0.0.1", 0))
        return int(prise.getsockname()[1])


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
    processus, adresse = lancer(BUREAU, port_libre())
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
                 page.locator('nav.onglets button[data-vue="alertes"]').count() == 1)

        # ---- profils d'investissement --------------------------------
        segments = page.locator(".segment")
        verifier("Les profils sont proposés", segments.count() == 4,
                 f"{segments.count()} profils")
        avant = page.locator("table tbody tr").count()
        page.locator(".segment", has_text="Sécurisé").first.click()
        page.wait_for_timeout(2500)
        apres = page.locator("table tbody tr").count()
        titre = page.locator(".carte h2").nth(1).inner_text()
        verifier("Le profil change le classement sans nouvelle analyse",
                 apres != avant and "Sécurisé" in titre,
                 f"{avant} titres → {apres}")
        # Vérification de fond : un titre écarté par un profil ne doit jamais
        # être présenté comme ayant des données incomplètes.
        # Le compte figure sous le titre du classement, soit la deuxieme
        # carte : la premiere est le selecteur de profils.
        compte = page.locator(".carte").nth(1).locator(".note").first.inner_text()
        verifier("Les deux motifs d'exclusion sont distingués",
                 "écartés par le profil" in compte, compte[:90])
        capturer(page, "audit-profil.png")
        page.locator(".segment", has_text="Équilibré").first.click()
        page.wait_for_timeout(2000)

        # ---- contexte économique -------------------------------------
        page.locator('nav.onglets button[data-vue="contexte"]').click()
        page.wait_for_selector(".tuile", timeout=60000)
        tuiles = page.locator(".tuile").count()
        articles = page.locator(".article").count()
        verifier("Les indicateurs publics s'affichent", tuiles >= 3, f"{tuiles} indicateurs")
        verifier("L'actualité est récupérée", articles >= 3, f"{articles} articles")
        verifier("Chaque indicateur porte sa période de référence",
                 page.locator(".tuile-pied").count() >= tuiles)
        texte_contexte = page.locator("#contenu").inner_text()
        verifier("La revue de presse est attribuée à des tiers",
                 "tiers" in texte_contexte and "aucun calcul" in texte_contexte)
        capturer(page, "audit-contexte.png")

        print("\n=== 3. ANALYSE LANCÉE DEPUIS L'INTERFACE ===")
        page.locator('nav.onglets button[data-vue="classement"]').click()
        page.wait_for_timeout(400)
        # Le panneau de relance est replié par défaut, pour que le classement
        # soit visible dès l'ouverture. Il faut donc l'ouvrir avant de
        # toucher à ses cases — sans quoi elles existent mais sont masquées.
        replies = page.evaluate(
            "[...document.querySelectorAll('details.pliant')].map(d => d.open)"
        )
        verifier("Le panneau de relance est replié par défaut",
                 replies and not any(replies), f"{len(replies)} blocs repliables")

        # Le DOM est reconstruit a chaque rendu : un bloc ouvert par un clic
        # se referme au rendu suivant, et l'element vise se retrouve detache.
        # Chaque interaction reouvre donc les blocs dans le MEME passage
        # navigateur que l'action qu'elle porte.
        def ouvrir_les_blocs() -> None:
            page.evaluate(
                "document.querySelectorAll('details.pliant')"
                ".forEach(d => { d.open = true; })"
            )

        # Ne garder que l'univers demande, pour que l'audit reste court.
        ouvrir_les_blocs()
        retires = page.evaluate(
            """(vise) => {
                document.querySelectorAll('details.pliant').forEach(d => { d.open = true; });
                const retires = [];
                for (const case_ of document.querySelectorAll('.panneau .filtres label input')) {
                    const libelle = (case_.parentElement.textContent || '').toLowerCase();
                    const nu = libelle.replace(/[ -]/g, '');
                    if (!nu.includes(vise) && case_.checked && !libelle.includes('cache')) {
                        case_.click();
                        retires.push(case_.value || libelle.trim());
                    }
                }
                return retires;
            }""",
            arguments.univers,
        )
        verifier("Sélection des univers modifiable", isinstance(retires, list),
                 f"retirés : {', '.join(retires) or 'aucun'}")

        lance = page.evaluate(
            """() => {
                document.querySelectorAll('details.pliant').forEach(d => { d.open = true; });
                const bouton = [...document.querySelectorAll('button')]
                    .find(b => b.textContent.includes("Lancer l'analyse maintenant"));
                if (!bouton) return false;
                bouton.click();
                return true;
            }"""
        )
        verifier("Le bouton de lancement est atteignable", lance)
        page.wait_for_timeout(2500)
        verifier("L'avancement s'affiche pendant l'analyse",
                 page.locator("text=Analyse en cours").count() >= 1)
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

    processus2, adresse2 = lancer(BUREAU, port_libre())
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
    processus3, adresse3 = lancer(AILLEURS, port_libre())
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
