@echo off
setlocal
cd /d "%~dp0"
if not exist "..\arybot\cola_local.py" (
  echo ERROR: coloca esta carpeta PARCHE dentro de la carpeta principal del bot.
  pause
  exit /b 1
)
copy /Y "arybot\cola_local.py" "..\arybot\cola_local.py" >nul
echo Parche aplicado. Inicia de nuevo el bot.
pause
