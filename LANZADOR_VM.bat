@echo off
setlocal
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
cd /d "%~dp0"
set "PYTHONHOME="
set "PYTHONPATH="
set "BOOT="
set "BOOTARG="
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "BOOT=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not defined BOOT if exist "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" set "BOOT=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
if not defined BOOT if exist ".venv\Scripts\python.exe" set "BOOT=%CD%\.venv\Scripts\python.exe"
if not defined BOOT for /f "delims=" %%P in ('where py.exe 2^>nul') do if not defined BOOT set "BOOT=%%P"
if defined BOOT goto arrancar
for /f "delims=" %%P in ('where python.exe 2^>nul') do if not defined BOOT set "BOOT=%%P"
if not defined BOOT (
  echo No se encontro Python. No se borro nada.
  pause
  exit /b 1
)
:arrancar
for %%P in ("%BOOT%") do if /I "%%~nxP"=="py.exe" set "BOOTARG=-3"
echo Python del lanzador: %BOOT%
"%BOOT%" %BOOTARG% -u "%~dp0ejecutar_vm.py" %1
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" echo Fallo detectado. Lee esta consola o logs\arranque_vm.txt.
pause
exit /b %RC%
