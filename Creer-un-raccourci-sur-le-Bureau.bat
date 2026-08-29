@echo off
REM =====================================================================
REM  Cree un raccourci Investassist sur le Bureau, avec l'icone de
REM  l'application. Double-cliquez sur ce fichier une seule fois.
REM =====================================================================
setlocal
chcp 65001 >nul 2>&1
cd /d "%~dp0"

set "CIBLE=%~dp0Investassist.exe"
set "ICONE=%~dp0assets\investassist.ico"

if not exist "%CIBLE%" (
    echo.
    echo   Investassist.exe est introuvable dans ce dossier.
    echo   Placez ce fichier a cote de l'executable, puis relancez-le.
    echo.
    pause
    exit /b 1
)

REM L'icone embarquee dans l'executable sert de repli si assets\ est absent.
if not exist "%ICONE%" set "ICONE=%CIBLE%"

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$bureau = [Environment]::GetFolderPath('Desktop');" ^
  "$lien = (New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $bureau 'Investassist.lnk'));" ^
  "$lien.TargetPath = '%CIBLE%';" ^
  "$lien.WorkingDirectory = '%~dp0';" ^
  "$lien.IconLocation = '%ICONE%';" ^
  "$lien.Description = 'Investassist - analyse fondamentale (usage personnel)';" ^
  "$lien.Save();" ^
  "Write-Host ('  Raccourci cree : ' + (Join-Path $bureau 'Investassist.lnk'))"

if errorlevel 1 (
    echo.
    echo   La creation du raccourci a echoue.
    echo   Vous pouvez le faire a la main : clic droit sur Investassist.exe,
    echo   puis « Envoyer vers » ^> « Bureau (creer un raccourci) ».
) else (
    echo.
    echo   Vous pouvez maintenant lancer Investassist depuis votre Bureau.
)
echo.
pause
