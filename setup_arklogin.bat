@echo off
setlocal
cd /d "%~dp0"

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
echo Installation failed. Review the messages above and try again.
pause
exit /b 1
