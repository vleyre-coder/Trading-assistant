#!/usr/bin/env bash
# Lance Investassist depuis les sources (macOS et Linux).
# Pour une utilisation courante, préférez l'exécutable : il n'a besoin
# de rien d'autre. Ce script sert au développement et aux essais.

set -euo pipefail
cd "$(dirname "$0")"

echo ""
echo "  Investassist — analyse fondamentale (usage personnel)"
echo "  ====================================================="
echo ""

PYTHON=""
for candidat in python3.13 python3.12 python3.11 python3 python; do
    if command -v "$candidat" >/dev/null 2>&1 &&
       "$candidat" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
        PYTHON="$candidat"; break
    fi
done

if [ -z "$PYTHON" ]; then
    echo "  Python 3.11 ou plus récent est nécessaire et n'a pas été trouvé."
    echo "  macOS : brew install python@3.12   —   Linux : sudo apt install python3 python3-venv"
    read -r -p "  Appuyez sur Entrée pour fermer."
    exit 1
fi
echo "  Python détecté : $($PYTHON --version)"

if [ ! -d ".venv" ]; then
    echo "  Création de l'environnement Python (une seule fois)…"
    "$PYTHON" -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate

EMPREINTE=$(cksum requirements.txt | awk '{print $1}')
MARQUEUR=".venv/.deps-$EMPREINTE"
if [ ! -f "$MARQUEUR" ]; then
    echo "  Installation des bibliothèques (quelques minutes la première fois)…"
    python -m pip install --quiet --upgrade pip
    python -m pip install --quiet -r requirements.txt
    rm -f .venv/.deps-* 2>/dev/null || true
    touch "$MARQUEUR"
fi

if [ "${INVESTASSIST_NO_LAUNCH:-}" = "1" ]; then
    echo "  (mode vérification : démarrage non effectué)"
    exit 0
fi

exec python lanceur.py "$@"
