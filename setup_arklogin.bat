@echo off
setlocal
cd /d "%~dp0"

if not exist "venv\Scripts\python.exe" (
    echo Creating the Python virtual environment...
    py -3 -m venv venv
    if errorlevel 1 goto :error
)

echo Checking required dependencies...
"venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 goto :error

echo.
"venv\Scripts\python.exe" arklogin.py %*
set "ARKLOGIN_EXIT=%ERRORLEVEL%"
echo.
if not "%ARKLOGIN_EXIT%"=="0" (
    echo ARK Login exited with error %ARKLOGIN_EXIT%.
)
pause
exit /b %ARKLOGIN_EXIT%

:error
echo.
echo Installation failed. Check your Internet connection and try again.
pause
exit /b 1
