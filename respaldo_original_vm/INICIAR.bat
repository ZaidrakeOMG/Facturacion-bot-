@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" "ARY_Facturacion_Bot.py"
  goto resultado
)
python -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
if not errorlevel 1 (
  python "ARY_Facturacion_Bot.py"
  goto resultado
)
py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
if not errorlevel 1 (
  py -3 "ARY_Facturacion_Bot.py"
  goto resultado
)
echo No se encontro Python 3.10 o posterior. Instala Python y habilita Add Python to PATH.
pause
exit /b 1
:resultado
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" pause
exit /b %RC%
