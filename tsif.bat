@echo off
setlocal
cd /d "%~dp0"

if not exist "venv\Scripts\python.exe" (
    echo ERROR: the Python virtual environment is not available.
    echo Run setup_tsif.bat first to create it and install the dependencies.
    exit /b 1
)

"venv\Scripts\python.exe" -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 14) else 1)"
if errorlevel 1 (
    echo ERROR: the virtual environment does not use Python 3.14.
    echo Run setup_tsif.bat to recreate it.
    exit /b 1
)

"venv\Scripts\python.exe" tsif.py %*
exit /b %ERRORLEVEL%
