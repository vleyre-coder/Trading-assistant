@echo off
REM ====================================================================
REM  Investassist - lancement depuis les sources (Windows).
REM  Pour une utilisation courante, preferez Investassist.exe : il n'a
REM  besoin de rien d'autre. Ce script sert au developpement.
REM  Le premier lancement installe les dependances (quelques minutes).
REM ====================================================================
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo.
echo   Investassist - analyse fondamentale (usage personnel)
echo   =====================================================
echo.

REM --- 1. Python 3.11 ou plus ----------------------------------------
set "PYEXE="
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)" >nul 2>&1
if not errorlevel 1 set "PYEXE=py -3"

if not defined PYEXE (
    python -c "import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)" >nul 2>&1
    if not errorlevel 1 set "PYEXE=python"
)

if not defined PYEXE (
    echo   Python 3.11 ou plus recent est necessaire et n'a pas ete trouve.
    echo.
    echo   Installez-le depuis le Microsoft Store ^(rechercher "Python 3.12"^)
    echo   ou depuis https://www.python.org/downloads/
    echo.
    echo   IMPORTANT : sur python.org, cochez "Add python.exe to PATH"
    echo   pendant l'installation.
    echo.
    pause
    exit /b 1
)

for /f "delims=" %%v in ('%PYEXE% --version 2^>^&1') do echo   Python detecte : %%v

REM --- 2. Environnement isole -----------------------------------------
if not exist ".venv\Scripts\python.exe" (
    echo   Creation de l'environnement Python ^(une seule fois^)...
    %PYEXE% -m venv .venv
    if errorlevel 1 (
        echo   ERREUR : creation de l'environnement impossible.
        pause
        exit /b 1
    )
)
set "VPY=.venv\Scripts\python.exe"

REM --- 3. Dependances --------------------------------------------------
REM Le marqueur evite de reinstaller a chaque lancement. Il est invalide
REM des que requirements.txt est modifie (comparaison de date).
set "MARQUEUR=.venv\deps-installees.txt"
set "AINSTALLER=1"
if exist "%MARQUEUR%" (
    set "AINSTALLER=0"
    for /f %%d in ('%VPY% -c "import os,sys; sys.stdout.write('1' if os.path.getmtime('requirements.txt') > os.path.getmtime(r'%MARQUEUR%') else '0')"') do set "AINSTALLER=%%d"
)

if "!AINSTALLER!"=="1" (
    echo   Installation des bibliotheques ^(quelques minutes la premiere fois^)...
    %VPY% -m pip install --quiet --upgrade pip
    %VPY% -m pip install --quiet -r requirements.txt
    if errorlevel 1 (
        echo   ERREUR : installation des bibliotheques impossible.
        echo   Verifiez votre connexion Internet, puis relancez ce fichier.
        pause
        exit /b 1
    )
    echo   Installation terminee.> "%MARQUEUR%"
    echo   Installation terminee.
)

REM --- 4. Demarrage ----------------------------------------------------
echo.
echo   Demarrage de l'application...
echo   Votre navigateur va s'ouvrir automatiquement.
echo   Pour quitter : fermez cette fenetre.
echo.

%VPY% lanceur.py %*

pause
