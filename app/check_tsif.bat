@echo off
setlocal
cd /d "%~dp0\.."
call tsif.bat --check-images
exit /b %ERRORLEVEL%
