<#
MONITOR ARY - Instalacion unica de solo lectura.
Ejecutar en la VM con PowerShell como Administrador.
No inicia/cierra Polaris, no modifica la cola y no altera el bot.
#>
param(
    [string]$IP = '10.20.20.182',
    [int]$Puerto = 8787
)
$ErrorActionPreference = 'Stop'
$raiz = Split-Path -Parent $MyInvocation.MyCommand.Path
$script = Join-Path $raiz 'MONITOR_ARY_WEB.py'
$clave = Join-Path $raiz 'data\monitor_web_clave.txt'
$nombreTarea = 'ARY - Monitor Web Solo Lectura'
$nombreFirewall = 'ARY-Monitor-Privado-8787'

$identidad = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identidad)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Abre PowerShell como administrador en la VM para la instalacion inicial.'
}
if (-not (Test-Path $script)) { throw 'Falta MONITOR_ARY_WEB.py en la carpeta del bot.' }
if (-not (Test-Path (Join-Path $raiz 'monitor_web\index.html'))) {
    throw 'Faltan los archivos de la interfaz web.'
}
if ($Puerto -lt 1024 -or $Puerto -gt 65535) { throw 'Puerto invalido.' }
if ($IP -ne '10.20.20.182') {
    Write-Warning 'Se modifico la IP de enlace. Comprueba que pertenece a la VM.'
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
if (-not $python) {
    throw 'No se encontro Python 3. Instala Python 3 y vuelve a ejecutar este instalador.'
}
Write-Host ('Python: ' + $python)
& $python $script '--base' $raiz '--crear-clave'
if ($LASTEXITCODE -ne 0) { throw 'No se pudo preparar la clave de acceso.' }

# La clave no se guarda en GitHub. Solo SYSTEM y administradores de Windows.
& icacls.exe $clave '/inheritance:r' '/grant:r' '*S-1-5-18:F' '*S-1-5-32-544:F' | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'No se pudo restringir el archivo de clave mediante ACL.' }

$arguments = ('-u "{0}" --base "{1}" --host {2} --port {3} --red 10.20.0.0/16 --red 127.0.0.0/8' -f $script, $raiz, $IP, $Puerto)
$accion = New-ScheduledTaskAction -Execute $python -Argument $arguments -WorkingDirectory $raiz
$disparador = New-ScheduledTaskTrigger -AtStartup
$config = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $nombreTarea -Action $accion -Trigger $disparador -Settings $config -User 'SYSTEM' -RunLevel Highest -Force | Out-Null

# Firewall por regla privada, sin publicar el servicio a Internet.
$old = Get-NetFirewallRule -Name $nombreFirewall -ErrorAction SilentlyContinue
if ($old) { $old | Remove-NetFirewallRule }
New-NetFirewallRule -Name $nombreFirewall -DisplayName 'ARY Monitor web - LAN 10.20 solamente' -Direction Inbound -Protocol TCP -LocalPort $Puerto -LocalAddress $IP -RemoteAddress '10.20.0.0/16' -Profile Domain,Private -Action Allow | Out-Null
Start-ScheduledTask -TaskName $nombreTarea

Write-Host ''
Write-Host 'MONITOR INSTALADO COMO TAREA DE ARRANQUE DE WINDOWS'
Write-Host ('URL para la red 10.20: http://' + $IP + ':' + $Puerto)
Write-Host 'Usuario: ary'
Write-Host ('Clave: consulta este archivo LOCALMENTE en la VM: ' + $clave)
Write-Host 'ADVERTENCIA: HTTP no cifra contrasenas. Usar solo en la VLAN/VPN de confianza.'
Write-Host 'Para Internet o redes sin confianza, se requiere HTTPS con proxy y acceso privado.'
Write-Host 'Esta tarea monitorea la cola: no inicia Polaris ni puede facturar por si sola.'
Write-Host ''
Get-ScheduledTaskInfo -TaskName $nombreTarea | Select-Object TaskName,LastRunTime,LastTaskResult
