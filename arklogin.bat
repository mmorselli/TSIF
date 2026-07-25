@echo off
setlocal
cd /d "%~dp0"

if not exist "venv\Scripts\python.exe" (
    echo ERRORE: l'ambiente Python venv non e' disponibile.
    echo Esegui prima avvia_arklogin.bat per crearlo e installare le librerie.
    exit /b 1
)

"venv\Scripts\python.exe" arklogin.py %*
exit /b %ERRORLEVEL%
