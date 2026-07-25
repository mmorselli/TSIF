@echo off
setlocal
cd /d "%~dp0"
call setup_arklogin.bat --check-images
