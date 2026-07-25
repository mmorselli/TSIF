@echo off
setlocal
cd /d "%~dp0"
call setup_tsif.bat --check-images
