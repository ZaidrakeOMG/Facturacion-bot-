@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"
title ARY - Recuperacion segura de folio SIN CFDI

echo ==========================================================
echo  ARY FACTURACION - RECUPERAR FOLIO BLOQUEADO
echo ==========================================================
echo.
echo IMPORTANTE: cierra la aplicacion ARY Facturacion Bot
echo y verifica en Polaris que el ticket NO tiene CFDI.
echo Esta herramienta NO crea una segunda solicitud: recupera
echo la solicitud original y conserva el historial de auditoria.
echo.

if not exist "data\cola_operaciones.sqlite3" (
  echo ERROR: No se encontro data\cola_operaciones.sqlite3
  echo Coloca este BAT en la carpeta raiz ARY_FACTURACION_BOT.
  goto :error
)

set "PY=python"
if exist "venv\Scripts\python.exe" set "PY=venv\Scripts\python.exe"
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
"%PY%" -c "import sys; print('Python:',sys.executable)" >nul 2>&1
if errorlevel 1 (
  echo ERROR: Python no esta disponible en esta VM.
  goto :error
)

set /p "ARY_ESTACION=Estacion (ejemplo ARY IV): "
set /p "ARY_TICKET=Ticket o folio (ejemplo 0006043150): "
"%PY%" -c "import os,re,sys; from arybot.parser import STATIONS; e=os.environ.get('ARY_ESTACION','').strip().upper(); t=os.environ.get('ARY_TICKET','').strip(); sys.exit(0 if e in STATIONS and re.fullmatch('[0-9]{1,20}',t) and int(t)>0 else 1)"
if errorlevel 1 (
  echo ERROR: Estacion invalida o ticket no numerico. Usa ARY IV y 0006043150.
  goto :error
)
echo.
echo SOLO si ya se verifico en Polaris que no hay CFDI.
set /p "ARY_CONFIRMACION=Para autorizar la recuperacion escribe SIN CFDI: "
"%PY%" -c "import os,sys; sys.exit(0 if os.environ.get('ARY_CONFIRMACION','').strip().upper()=='SIN CFDI' else 1)"
if errorlevel 1 (
  echo Cancelado: no se confirmo la verificacion en Polaris.
  goto :error
)

echo.
echo [1/2] Creando respaldo consistente de SQLite...
"%PY%" -c "import sqlite3,datetime; from pathlib import Path; p=Path('data/cola_operaciones.sqlite3'); b=p.with_name('cola_operaciones_RESPALDO_'+datetime.datetime.now().strftime('%%Y%%m%%d_%%H%%M%%S_%%f')+'.sqlite3'); src=sqlite3.connect(str(p),timeout=20); dst=sqlite3.connect(str(b)); src.backup(dst); dst.close(); src.close(); print('RESPALDO:',b)"
if errorlevel 1 (
  echo ERROR: No se creo el respaldo. No se hara ninguna recuperacion.
  goto :error
)

echo.
echo [2/2] Recuperando el trabajo original y reanudando la cola...
"%PY%" -c "import os; from pathlib import Path; from arybot.cola_local import ColaLocal; s=ColaLocal(Path.cwd()); j=s.retomar_folio_sin_cfdi(os.environ['ARY_ESTACION'],os.environ['ARY_TICKET'],confirmado=True); print('TRABAJO RECUPERADO:',j['id'],'ESTADO:',j['estado']); s.resume(polaris_comprobado=True); print('COLA REANUDADA: SI')"
if errorlevel 1 (
  echo ERROR: La recuperacion no se completo o la cola no pudo reanudarse.
  echo No ejecutes el BAT repetidamente; revisa el mensaje de Python.
  goto :error
)

echo.
echo LISTO: abre de nuevo ARY Facturacion Bot y consulta el trabajo.
echo No reenvies el mismo ticket desde el portal mientras se procesa.
pause
exit /b 0

:error
echo.
echo No se ha confirmado que el problema quedara resuelto.
pause
exit /b 1
