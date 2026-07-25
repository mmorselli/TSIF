@echo off
setlocal
cd /d "%~dp0"

if not exist "venv\Scripts\python.exe" (
    echo Creazione dell'ambiente Python...
    py -3 -m venv venv
    if errorlevel 1 goto :error
)

echo Controllo delle librerie necessarie...
"venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 goto :error

echo.
"venv\Scripts\python.exe" arklogin.py %*
set "ARKLOGIN_EXIT=%ERRORLEVEL%"
echo.
if not "%ARKLOGIN_EXIT%"=="0" (
    echo ARK Login si e' chiuso con errore %ARKLOGIN_EXIT%.
)
pause
exit /b %ARKLOGIN_EXIT%

:error
echo.
echo Installazione non riuscita. Controlla la connessione Internet e riprova.
pause
exit /b 1
