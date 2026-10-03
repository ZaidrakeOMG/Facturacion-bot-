@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" "instalar.py"
  goto resultado
)
python -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
if not errorlevel 1 (
  python "instalar.py"
  goto resultado
)
py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
if not errorlevel 1 (
  py -3 "instalar.py"
  goto resultado
)
echo No se encontro Python 3.10 o posterior. Instala Python y habilita Add Python to PATH.
pause
exit /b 1
:resultado
set "RC=%ERRORLEVEL%"
pause
exit /b %RC%
