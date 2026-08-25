#!/usr/bin/env bash
# Lancement d'Investassist sur macOS et Linux.
# Double-cliquer sur ce fichier, ou l'exécuter : ./start.sh
# Le premier lancement installe les dépendances (quelques minutes) ;
# les suivants sont immédiats.

set -euo pipefail
cd "$(dirname "$0")"

echo ""
echo "  Investassist — analyse fondamentale (usage personnel)"
echo "  ====================================================="
echo ""

# --- 1. Python 3.11 ou plus -------------------------------------------
PYTHON=""
for candidat in python3.13 python3.12 python3.11 python3 python; do
    if command -v "$candidat" >/dev/null 2>&1; then
        if "$candidat" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
            PYTHON="$candidat"
            break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    echo "  Python 3.11 ou plus récent est nécessaire et n'a pas été trouvé."
    echo ""
    echo "  macOS  : brew install python@3.12"
    echo "           (ou téléchargez-le sur https://www.python.org/downloads/)"
    echo "  Linux  : sudo apt install python3 python3-venv   (Debian/Ubuntu)"
    echo ""
    read -r -p "  Appuyez sur Entrée pour fermer."
    exit 1
fi
echo "  Python détecté : $($PYTHON --version)"

# --- 2. Environnement isolé -------------------------------------------
if [ ! -d ".venv" ]; then
    echo "  Création de l'environnement Python (une seule fois)…"
    "$PYTHON" -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate

# --- 3. Dépendances ----------------------------------------------------
# Le marqueur évite de réinstaller à chaque lancement ; il est invalidé
# dès que requirements.txt change.
EMPREINTE=$(cksum requirements.txt | awk '{print $1}')
MARQUEUR=".venv/.deps-$EMPREINTE"
if [ ! -f "$MARQUEUR" ]; then
    echo "  Installation des bibliothèques (quelques minutes la première fois)…"
    python -m pip install --quiet --upgrade pip
    python -m pip install --quiet -r requirements.txt
    rm -f .venv/.deps-* 2>/dev/null || true
    touch "$MARQUEUR"
    echo "  Installation terminée."
fi

# --- 4. Configuration du premier lancement -----------------------------
python scripts/bootstrap.py

# --- 5. Démarrage ------------------------------------------------------
if [ "${INVESTASSIST_NO_LAUNCH:-}" = "1" ]; then
    echo "  (mode vérification : démarrage non effectué)"
    exit 0
fi

echo ""
echo "  Démarrage du tableau de bord…"
echo "  Votre navigateur va s'ouvrir sur http://localhost:8501"
echo "  Pour quitter : fermez cette fenêtre, ou Ctrl+C ici."
echo ""
echo "  Rappel : ce tableau de bord classe des titres selon des critères"
echo "  fondamentaux. Il ne constitue pas un conseil en investissement"
echo "  ni une prédiction de performance future."
echo ""

python -m streamlit run app.py
