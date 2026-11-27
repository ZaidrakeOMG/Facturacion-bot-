# Cambia SOLO la clave y el usuario del monitor WEB ARY.
# No reinicia ARY Bot, no toca Polaris y no modifica la base de datos.
$ErrorActionPreference = 'Stop'
$raiz = Split-Path -Parent $MyInvocation.MyCommand.Path
$script = Join-Path $raiz 'MONITOR_ARY_WEB.py'
$tarea = 'ARY - Monitor Web Solo Lectura'
$identidad = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identidad)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Abre CMD como administrador antes de ejecutar este archivo.'
}
if (-not (Test-Path $script)) { throw 'Falta el monitor. Ejecuta git pull en la carpeta del bot.' }
if (-not (Get-ScheduledTask -TaskName $tarea -ErrorAction SilentlyContinue)) {
    throw 'El monitor no esta instalado como tarea. Ejecuta primero INSTALAR_MONITOR_ARY.ps1.'
}
$python = $null
$launcher = Get-Command py.exe -ErrorAction SilentlyContinue
if ($launcher) {
    $candidate = (& $launcher.Source -3 -c 'import sys; print(sys.executable)' 2>$null | Select-Object -Last 1)
    if ($LASTEXITCODE -eq 0 -and $candidate -and (Test-Path $candidate)) {
        $python = $candidate.Trim()
    }
}
if (-not $python) {
    $candidate = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($candidate -and (Test-Path $candidate.Source)) {
        $version = & $candidate.Source -V 2>&1 | Out-String
        if ($version -match 'Python 3\.') { $python = $candidate.Source }
    }
}
if (-not $python) { throw 'No se encontro Python 3.' }
Write-Host 'Usuario nuevo del monitor: Ary'
Write-Host 'La contrasena no aparecera en pantalla y solo se guarda su hash.'
& $python $script '--base' $raiz '--cambiar-clave'
if ($LASTEXITCODE -ne 0) { throw 'No se cambio la clave; no se reinicia el monitor.' }
Write-Host 'Reiniciando exclusivamente la tarea del monitor web...'
Stop-ScheduledTask -TaskName $tarea -ErrorAction Stop
Start-Sleep -Seconds 2
Start-ScheduledTask -TaskName $tarea -ErrorAction Stop
Start-Sleep -Seconds 2
$estado = (Get-ScheduledTask -TaskName $tarea).State
Write-Host ("Estado de la tarea: " + $estado)
Write-Host 'Panel LAN: http://10.20.20.182:8787'
Write-Host 'Usuario: Ary'
Write-Host 'Cierra el navegador antiguo y prueba en una ventana privada.'
