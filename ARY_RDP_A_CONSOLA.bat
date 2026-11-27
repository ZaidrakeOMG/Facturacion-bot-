@echo off
setlocal EnableExtensions DisableDelayedExpansion
title ARY - Transferir MI sesion RDP a consola
echo ======================================================
echo ARY - SESION DE POLARIS / ESCRITORIO REMOTO
echo ======================================================
echo.
echo IMPORTANTE: Esta accion transfiere la sesion Windows
echo actual a consola. No factura ni libera solicitudes.
echo Debe ejecutarse desde la MISMA sesion del bot.
echo.

if not defined SESSIONNAME (
  echo ERROR: No se detecto SESSIONNAME.
  goto :error
)
echo Sesion detectada: %SESSIONNAME%
echo.
query session
echo.
if /I not "%SESSIONNAME:~0,7%"=="RDP-Tcp" (
  echo ERROR: No se detecto una sesion RDP actual.
  echo No se ejecutara tscon sobre otro usuario o sesion.
  goto :error
)
echo Si funciona, tu conexion de Escritorio Remoto
echo debe desconectarse INMEDIATAMENTE.
echo Espera a que Polaris y ARY Bot esten abiertos.
echo.
pause
"%SystemRoot%\System32\tscon.exe" "%SESSIONNAME%" /dest:console
if errorlevel 1 (
  echo ERROR: tscon no pudo transferir la sesion.
  echo Ejecutalo como administrador de la misma sesion.
  goto :error
)
echo.
echo Si sigues viendo este CMD a traves de RDP, revisa
echo si otra sesion de Windows esta ejecutando el bot.
pause
exit /b 0

:error
echo.
echo No se transfirio ninguna sesion.
pause
exit /b 1
