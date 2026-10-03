@echo off
setlocal
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "PYTHONHOME="
set "PYTHONPATH="
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo No existe la .venv en esta carpeta.
  echo Ejecuta primero 1_PREPARAR_VM.bat en esta misma carpeta.
  echo Cuando muestre ENTORNO OK, vuelve a abrir INICIAR_RAPIDO.bat.
  pause
  exit /b 1
)
echo Cierra primero el bot normal. No abras los dos al mismo tiempo.
echo MODO RAPIDO OPCIONAL R1 - mismos datos y mismos controles fiscales.
"%~dp0.venv\Scripts\python.exe" -u "%~dp0iniciar_rapido.py"
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
  echo Hubo un error. Revisa logs\arranque_rapido.txt.
  pause
)
exit /b %RC%
