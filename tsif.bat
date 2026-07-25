@echo off
setlocal
cd /d "%~dp0"

if not exist "venv\Scripts\python.exe" (
    echo ERROR: the Python virtual environment is not available.
    echo Run setup_tsif.bat first to create it and install the dependencies.
    exit /b 1
)

"venv\Scripts\python.exe" tsif.py %*
exit /b %ERRORLEVEL%
