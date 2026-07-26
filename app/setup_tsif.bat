@echo off
setlocal EnableExtensions
cd /d "%~dp0"
set "TSIF_PYTHON_VERSION=3.14"
set "TSIF_FALLBACK_PYTHON=%LocalAppData%\Programs\Python\Python314\python.exe"
set "TSIF_SYSTEM_PYTHON=%ProgramFiles%\Python314\python.exe"
set "PYTHON_MANAGER_DEFAULT_PLATFORM=-64"
set "TSIF_PYTHON_EXE="
set "TSIF_MANAGER_EXE="

if not exist "config" mkdir "config"
if errorlevel 1 goto :error
if not exist "logs" mkdir "logs"
if errorlevel 1 goto :error

if not exist "config\config.json" (
    if not exist "config\config.json.example" (
        echo ERROR: config\config.json.example is missing.
        goto :error
    )
    echo Creating config\config.json from its default...
    copy /Y "config\config.json.example" "config\config.json" >nul
    if errorlevel 1 goto :error
)

if not exist "config\lang.json" (
    if not exist "config\lang.json.example" (
        echo ERROR: config\lang.json.example is missing.
        goto :error
    )
    echo Creating config\lang.json from its default...
    copy /Y "config\lang.json.example" "config\lang.json" >nul
    if errorlevel 1 goto :error
)

if exist "venv\Scripts\python.exe" (
    "venv\Scripts\python.exe" -c "import struct, sys; raise SystemExit(0 if sys.version_info[:2] == (3, 14) and struct.calcsize('P') * 8 == 64 else 1)" >nul 2>&1
    if errorlevel 1 (
        echo The existing virtual environment does not use 64-bit Python %TSIF_PYTHON_VERSION%.
        echo Recreating it with the required Python version...
        rmdir /S /Q "venv"
        if exist "venv" (
            echo ERROR: the old venv is in use. Close TSIF and Python tools, then retry.
            goto :error
        )
    )
)

if not exist "venv\Scripts\python.exe" call :prepare_venv
if errorlevel 1 goto :error

"venv\Scripts\python.exe" -c "import struct, sys; raise SystemExit(0 if sys.version_info[:2] == (3, 14) and struct.calcsize('P') * 8 == 64 else 1)" >nul 2>&1
if errorlevel 1 (
    echo ERROR: the virtual environment does not use 64-bit Python %TSIF_PYTHON_VERSION%.
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
echo TSIF environment is ready.
exit /b 0

:error
echo.
echo Installation failed. Review the messages above and try again.
exit /b 1

:prepare_venv
call :find_python
if not errorlevel 1 goto :create_venv

echo.
echo TSIF requires 64-bit Python %TSIF_PYTHON_VERSION%, but it is not installed.
echo Other Python versions already on this computer will not be removed.
%SystemRoot%\System32\choice.exe /C YN /N /M "Download and install it automatically for this user? [Y/N] "
if errorlevel 2 goto :python_install_declined

call :install_python
if errorlevel 1 exit /b 1
call :find_python
if errorlevel 1 (
    echo ERROR: Python installation completed, but a usable 64-bit Python %TSIF_PYTHON_VERSION% was not found.
    exit /b 1
)

:create_venv
echo Creating the Python %TSIF_PYTHON_VERSION% virtual environment...
"%TSIF_PYTHON_EXE%" -m venv venv
if errorlevel 1 (
    echo ERROR: Python could not create the virtual environment.
    exit /b 1
)
exit /b 0

:python_install_declined
echo Automatic Python installation was declined.
exit /b 1

:find_python
set "TSIF_PYTHON_EXE="

call :try_python "%TSIF_FALLBACK_PYTHON%"
if defined TSIF_PYTHON_EXE exit /b 0

call :try_python "%TSIF_SYSTEM_PYTHON%"
if defined TSIF_PYTHON_EXE exit /b 0

call :find_python_manager
if not errorlevel 1 call :find_managed_python
if defined TSIF_PYTHON_EXE exit /b 0

where py.exe >nul 2>&1
if errorlevel 1 exit /b 1
py -0p 2>nul | %SystemRoot%\System32\findstr.exe /C:"%TSIF_PYTHON_VERSION%" >nul
if errorlevel 1 exit /b 1
set "TSIF_LAUNCHER_RESULT=%TEMP%\tsif-launcher-%RANDOM%-%RANDOM%.txt"
py -%TSIF_PYTHON_VERSION% -c "import sys; print(sys.executable)" >"%TSIF_LAUNCHER_RESULT%" 2>nul
if errorlevel 1 goto :find_python_launcher_cleanup
set "TSIF_CANDIDATE="
for /f "usebackq delims=" %%P in ("%TSIF_LAUNCHER_RESULT%") do set "TSIF_CANDIDATE=%%P"
call :try_python "%TSIF_CANDIDATE%"

:find_python_launcher_cleanup
del /Q "%TSIF_LAUNCHER_RESULT%" >nul 2>&1
if defined TSIF_PYTHON_EXE exit /b 0
exit /b 1

:try_python
if "%~1"=="" exit /b 1
if not exist "%~1" exit /b 1
"%~1" -c "import struct, sys; raise SystemExit(0 if sys.version_info[:2] == (3, 14) and struct.calcsize('P') * 8 == 64 else 1)" >nul 2>&1
if errorlevel 1 exit /b 1
set "TSIF_PYTHON_EXE=%~1"
exit /b 0

:find_python_manager
set "TSIF_MANAGER_EXE="

call :validate_manager "%LocalAppData%\Microsoft\WindowsApps\pymanager.exe"
if defined TSIF_MANAGER_EXE exit /b 0

call :validate_manager "%LocalAppData%\Microsoft\WindowsApps\PythonSoftwareFoundation.PythonManager_qbz5n2kfra8p0\pymanager.exe"
if defined TSIF_MANAGER_EXE exit /b 0

call :validate_manager "%LocalAppData%\Microsoft\WindowsApps\PythonSoftwareFoundation.PythonManager_3847v3x7pw1km\pymanager.exe"
if defined TSIF_MANAGER_EXE exit /b 0
exit /b 1

:validate_manager
if "%~1"=="" exit /b 1
if not exist "%~1" exit /b 1
"%~1" help >nul 2>&1
if errorlevel 1 exit /b 1
set "TSIF_MANAGER_EXE=%~1"
exit /b 0

:find_managed_python
set "TSIF_MANAGER_RESULT=%TEMP%\tsif-python-%RANDOM%-%RANDOM%.txt"
"%TSIF_MANAGER_EXE%" list --one --format=exe %TSIF_PYTHON_VERSION% >"%TSIF_MANAGER_RESULT%" 2>nul
if errorlevel 1 goto :find_managed_python_cleanup
set "TSIF_CANDIDATE="
for /f "usebackq delims=" %%P in ("%TSIF_MANAGER_RESULT%") do set "TSIF_CANDIDATE=%%P"
call :try_python "%TSIF_CANDIDATE%"

:find_managed_python_cleanup
del /Q "%TSIF_MANAGER_RESULT%" >nul 2>&1
if defined TSIF_PYTHON_EXE exit /b 0
exit /b 1

:install_python
call :find_python_manager
if not errorlevel 1 goto :install_python_runtime

where winget.exe >nul 2>&1
if errorlevel 1 goto :install_python_manager_fallback

echo.
echo Installing the official Python Install Manager with WinGet...
winget install 9NQ7512CXL7T -e --source msstore --silent --accept-package-agreements --accept-source-agreements --disable-interactivity
if not errorlevel 1 (
    call :find_python_manager
    if not errorlevel 1 goto :install_python_runtime
)
echo WinGet could not install the Python Install Manager.

:install_python_manager_fallback
echo Trying the official python.org installer...
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference = 'Stop'; Add-AppxPackage -AppInstallerFile 'https://www.python.org/ftp/python/pymanager/pymanager.appinstaller'"
if errorlevel 1 (
    echo ERROR: the Python Install Manager could not be installed.
    echo Check the internet connection and that App Installer is enabled in Windows.
    exit /b 1
)
call :find_python_manager
if errorlevel 1 (
    echo ERROR: Windows installed Python Install Manager, but its command is unavailable.
    exit /b 1
)

:install_python_runtime
echo.
echo Downloading and installing 64-bit Python %TSIF_PYTHON_VERSION%...
"%TSIF_MANAGER_EXE%" install --yes %TSIF_PYTHON_VERSION%
if errorlevel 1 (
    echo ERROR: Python %TSIF_PYTHON_VERSION% installation failed.
    exit /b 1
)
exit /b 0
