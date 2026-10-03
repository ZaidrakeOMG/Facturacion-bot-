@echo off
cd /d "%~dp0"
python ARY_Facturacion_Portal.py
if errorlevel 1 pause
