#!/usr/bin/env bash
# Envoie le contenu de ce dossier vers votre dépôt GitHub.
set -euo pipefail
cd "$(dirname "$0")"
for candidat in python3 python; do
    if command -v "$candidat" >/dev/null 2>&1; then
        exec "$candidat" scripts/publier.py "$@"
    fi
done
echo "  Python est nécessaire pour ce script."
exit 1
