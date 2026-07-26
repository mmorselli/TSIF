@echo off
setlocal
cd /d "%~dp0"
set "TSIF_PYTHON_VERSION=3.14"
set "TSIF_FALLBACK_PYTHON=%LocalAppData%\Programs\Python\Python314\python.exe"

if not exist "config.json" (
    if not exist "config.json.example" (
        echo ERROR: config.json.example is missing.
        goto :error
    )
    echo Creating config.json from config.json.example...
    copy /Y "config.json.example" "config.json" >nul
    if errorlevel 1 goto :error
)

if not exist "lang.json" (
    if not exist "lang.json.example" (
        echo ERROR: lang.json.example is missing.
        goto :error
    )
    echo Creating lang.json from lang.json.example...
    copy /Y "lang.json.example" "lang.json" >nul
    if errorlevel 1 goto :error
)

if exist "venv\Scripts\python.exe" (
    "venv\Scripts\python.exe" -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 14) else 1)" >nul 2>&1
    if errorlevel 1 (
        echo Recreating the virtual environment with Python %TSIF_PYTHON_VERSION%...
        rmdir /S /Q "venv"
        if exist "venv" (
            echo ERROR: the old venv is in use. Close TSIF and Python tools, then retry.
            goto :error
        )
    )
)

if not exist "venv\Scripts\python.exe" (
    echo Creating the Python %TSIF_PYTHON_VERSION% virtual environment...
    py -%TSIF_PYTHON_VERSION% -c "import sys" >nul 2>&1
    if not errorlevel 1 (
        py -%TSIF_PYTHON_VERSION% -m venv venv
    ) else if exist "%TSIF_FALLBACK_PYTHON%" (
        "%TSIF_FALLBACK_PYTHON%" -m venv venv
    ) else (
        echo ERROR: Python %TSIF_PYTHON_VERSION% was not found.
        echo Install the 64-bit release from https://www.python.org/downloads/
        goto :error
    )
    if errorlevel 1 goto :error
)

"venv\Scripts\python.exe" -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 14) else 1)" >nul 2>&1
if errorlevel 1 (
    echo ERROR: the virtual environment does not use Python %TSIF_PYTHON_VERSION%.
    goto :error
)

echo Checking required dependencies...
"venv\Scripts\python.exe" -m pip install --disable-pip-version-check --upgrade pip
if errorlevel 1 goto :error
"venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 goto :error
"venv\Scripts\python.exe" -m pip check
if errorlevel 1 goto :error

echo.
"venv\Scripts\python.exe" tsif.py %*
set "TSIF_EXIT=%ERRORLEVEL%"
echo.
if not "%TSIF_EXIT%"=="0" (
    echo TSIF exited with error %TSIF_EXIT%.
)
pause
exit /b %TSIF_EXIT%

:error
echo.
echo Installation failed. Review the messages above and try again.
pause
exit /b 1
