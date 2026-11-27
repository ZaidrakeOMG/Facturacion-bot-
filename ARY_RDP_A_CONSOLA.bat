@echo off
setlocal EnableExtensions DisableDelayedExpansion
title ARY - Mantener Windows y Polaris sin RDP
echo ======================================================
echo ARY - PASAR LA SESION RDP A LA CONSOLA DE LA VM
echo ======================================================
echo.
echo Esta herramienta NO factura, no crea CFDI y no cierra Polaris.
echo Abre ARY Bot y Polaris ANTES de transferir la sesion.
echo La sesion debe seguir siendo interactiva y desbloqueada.
echo.

REM Algunas ejecuciones como administrador NO reciben SESSIONNAME.
REM La sesion del proceso PowerShell es la misma de este BAT.
set "ARY_SID="
for /f "usebackq delims=" %%I in (`powershell.exe -NoProfile -NonInteractive -Command "[System.Diagnostics.Process]::GetCurrentProcess().SessionId" 2^>nul`) do set "ARY_SID=%%I"
if not defined ARY_SID (
  echo ERROR: No se pudo identificar la sesion Windows de este proceso.
  echo Revisa el comando query user y el acceso a PowerShell.
  goto :error
)
echo %ARY_SID% | findstr /R /C:"^[1-9][0-9]* *$" >nul
if errorlevel 1 (
  echo ERROR: ID de sesion no valido. No se ejecutara tscon.
  goto :error
)
echo ID real de ESTA sesion: %ARY_SID%
echo.
echo SESIONES DE WINDOWS:
query user
if errorlevel 1 (
  echo ERROR: Windows no pudo enumerar las sesiones.
  goto :error
)
echo.
echo CONFIRMA en la tabla que el ID %ARY_SID% corresponde a
echo la sesion RDP ACTIVA de admin y NO a console u otra persona.
echo.
set "ARY_CONFIRMACION="
set /p "ARY_CONFIRMACION=Si corresponde a RDP, escribe SI para desconectarte: "
if /I not "%ARY_CONFIRMACION%"=="SI" (
  echo Cancelado: no se realizo ninguna transferencia.
  goto :error
)
echo.
echo Intentando transferir a consola la sesion %ARY_SID%...
"%SystemRoot%\System32\tscon.exe" %ARY_SID% /dest:console
if errorlevel 1 (
  echo ERROR: tscon fallo. Prueba CMD como administrador.
  goto :error
)
echo.
echo Transferencia solicitada. Si sigues conectado por RDP,
echo verifica la salida de query user y el escritorio de la VM.
pause
exit /b 0

:error
echo.
echo No se confirmo la transferencia de la sesion a consola.
pause
exit /b 1
