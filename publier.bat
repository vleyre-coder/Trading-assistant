@echo off
REM Envoie le contenu de ce dossier vers votre depot GitHub.
REM Double-cliquez sur ce fichier.
setlocal
cd /d "%~dp0"

set "PYEXE="
py -3 -c "import sys; sys.exit(0)" >nul 2>&1 && set "PYEXE=py -3"
if not defined PYEXE (
    python -c "import sys; sys.exit(0)" >nul 2>&1 && set "PYEXE=python"
)
if not defined PYEXE (
    echo   Python est necessaire pour ce script.
    echo   Installez-le depuis le Microsoft Store ^(rechercher "Python 3.12"^).
    pause
    exit /b 1
)

%PYEXE% scripts\publier.py %*
