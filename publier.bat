@echo off
REM Envoie le contenu de ce dossier vers votre depot GitHub.
REM Double-cliquez sur ce fichier.
setlocal
cd /d "%~dp0"

REM Console en UTF-8 : sans cela, un simple caractere accentue interrompt
REM le script et la fenetre se referme avant tout message lisible.
chcp 65001 >nul 2>&1
set "PYTHONIOENCODING=utf-8"

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
set "CODE=%ERRORLEVEL%"

REM Pause systematique : une erreur doit rester lisible, meme lorsque le
REM script est lance par double-clic.
echo.
if not "%CODE%"=="0" echo   Le script s'est termine avec le code %CODE%.
pause
