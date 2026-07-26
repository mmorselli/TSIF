@echo off
setlocal EnableExtensions
cd /d "%~dp0"

if not exist "app\setup_tsif.bat" goto :broken_distribution
if not exist "app\tsif.py" goto :broken_distribution
if not exist "app\requirements.txt" goto :broken_distribution

call :environment_ready
if errorlevel 1 (
    echo TSIF is preparing the application for first use...
    echo.
    call "app\setup_tsif.bat"
    if errorlevel 1 goto :setup_failed
    cd /d "%~dp0"
)

call :environment_ready
if errorlevel 1 goto :setup_incomplete

"app\venv\Scripts\python.exe" "app\tsif.py" %*
exit /b %ERRORLEVEL%

:environment_ready
if not exist "app\venv\Scripts\python.exe" exit /b 1
if not exist "app\config\config.json" exit /b 1
if not exist "app\config\lang.json" exit /b 1
"app\venv\Scripts\python.exe" -c "import struct, sys; from importlib.metadata import version; from pathlib import Path; import cv2, mss, numpy, onnxruntime, psutil, rapidocr, win32api; requirements = [line.strip().split('==', 1) for line in Path(r'app\requirements.txt').read_text(encoding='utf-8').splitlines() if '==' in line]; raise SystemExit(0 if sys.version_info[:2] == (3, 14) and struct.calcsize('P') * 8 == 64 and all(version(name) == expected for name, expected in requirements) else 1)" >nul 2>&1
exit /b %ERRORLEVEL%

:broken_distribution
echo ERROR: the TSIF application files are incomplete.
echo Extract the complete distribution and try again.
pause
exit /b 1

:setup_failed
echo.
echo ERROR: TSIF setup did not complete successfully.
pause
exit /b 1

:setup_incomplete
echo.
echo ERROR: the TSIF environment is still incomplete after setup.
pause
exit /b 1
