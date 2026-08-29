# -*- mode: python ; coding: utf-8 -*-
"""Recette d'empaquetage PyInstaller de l'application Investassist.

    pyinstaller investassist.spec

Produit un executable unique. Les reglages et les donnees sont ecrits A COTE
de cet executable au premier lancement : copier le dossier suffit a emporter
l'outil, son historique et sa watchlist sur une autre machine.
"""
from PyInstaller.utils.hooks import collect_all, collect_submodules

# L'ecran de demarrage de PyInstaller repose sur tkinter. Il est fourni avec
# Python sous Windows, mais absent de certaines installations Linux. La
# construction ne doit pas echouer pour autant : on s'en passe alors, sans
# rien changer au fonctionnement de l'application.
try:
    import tkinter  # noqa: F401

    ECRAN_DISPONIBLE = True
except ImportError:
    ECRAN_DISPONIBLE = False
    print("PyInstaller : tkinter absent, construction sans ecran de demarrage.")

donnees = [
    # Configuration par defaut : recopiee a cote de l'executable au premier
    # lancement, pour rester modifiable sans reconstruction.
    ("config/scoring.yaml", "config"),
    ("config/universes.yaml", "config"),
    ("config/settings.example.yaml", "config"),
    ("config/alerts.yaml", "config"),
    # Interface web servie par le serveur local.
    ("web/index.html", "web"),
    ("web/assets", "web/assets"),
    # Instantane livre avec l'application : un classement s'affiche des
    # l'ouverture, clairement date, sans attendre une premiere analyse.
    ("web/data/ranking.json", "web/data"),
    ("web/data/history.json", "web/data"),
    # Icone : reutilisee par le raccourci du Bureau et par l'interface.
    ("assets/investassist.ico", "assets"),
    ("assets/investassist.png", "assets"),
]

binaires = []
imports_caches = [
    "investassist",
    *collect_submodules("investassist"),
]

# yfinance et curl_cffi chargent des modules et des fichiers binaires par des
# chemins que l'analyse statique ne voit pas.
for paquet in ("yfinance", "curl_cffi", "peewee", "frozendict", "multitasking"):
    try:
        modules, fichiers, cachés = collect_all(paquet)
        binaires += modules
        donnees += fichiers
        imports_caches += cachés
    except Exception:  # noqa: BLE001 - paquet absent : on continue
        pass

analyse = Analysis(
    ["lanceur.py"],
    pathex=["src"],
    binaries=binaires,
    datas=donnees,
    hiddenimports=imports_caches,
    hookspath=[],
    runtime_hooks=[],
    # Rien de tout cela n'est utilise par l'application de bureau : les
    # exclure divise la taille de l'executable par plus de deux.
    excludes=[
        "streamlit", "plotly", "matplotlib", "pytest", "playwright",
        "PyQt5", "PyQt6", "PySide2", "PySide6", "IPython",
        "notebook", "jupyter", "sphinx", "scipy",
        # tkinter n'est exclu que lorsqu'il ne sert pas : l'ecran de
        # demarrage en depend.
        *([] if ECRAN_DISPONIBLE else ["tkinter"]),
    ],
    noarchive=False,
)

pyz = PYZ(analyse.pure)

# Écran de démarrage : un exécutable « un seul fichier » met une dizaine de
# secondes à se déballer avant d'afficher quoi que ce soit. Sans cet écran,
# rien ne se passe à l'écran et l'utilisateur double-clique une seconde fois.
# Le texte est mis à jour par le programme au fil des étapes.
demarrage = (
    Splash(
        "assets/demarrage.png",
        binaries=analyse.binaries,
        datas=analyse.datas,
        text_pos=(36, 170),
        text_size=9,
        text_color="#52514e",
        text_default="Demarrage...",
        minify_script=True,
        always_on_top=True,
    )
    if ECRAN_DISPONIBLE
    else None
)

# Les elements de l'ecran de demarrage ne sont ajoutes que s'il existe.
elements_ecran = [demarrage, demarrage.binaries] if demarrage else []

exe = EXE(
    pyz,
    analyse.scripts,
    *elements_ecran,
    analyse.binaries,
    analyse.datas,
    [],
    name="Investassist",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    # Console visible : elle affiche l'adresse, l'avancement de l'analyse et
    # les erreurs eventuelles. La masquer rendrait tout diagnostic impossible.
    console=True,
    disable_windowed_traceback=False,
    icon="assets/investassist.ico",
)
