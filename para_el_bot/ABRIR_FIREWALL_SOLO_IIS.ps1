# Ejecutar en 10.20.20.182, Windows PowerShell COMO ADMINISTRADOR.
# Permiso limitado: TCP 8765, IP local .182, origen .154. No modifica el router.
$ErrorActionPreference = 'Stop'
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Abre Windows PowerShell como administrador antes de ejecutar este archivo.'
}
if (-not (Get-NetIPAddress -AddressFamily IPv4 | Where-Object IPAddress -eq '10.20.20.182')) {
    throw 'Este equipo no tiene la IP 10.20.20.182. No se cambió el firewall.'
}
$name = 'ARY-Portal-LAN-8765-IIS154'
if (Get-NetFirewallRule -Name $name -ErrorAction SilentlyContinue) {
    Remove-NetFirewallRule -Name $name
}
New-NetFirewallRule -Name $name -DisplayName 'ARY Portal 8765 solo IIS 10.20.20.154' `
    -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8765 `
    -LocalAddress 10.20.20.182 -RemoteAddress 10.20.20.154 -Profile Any `
    -EdgeTraversalPolicy Block | Out-Null
Write-Host 'Regla creada: IIS 10.20.20.154 -> bot 10.20.20.182 TCP 8765.'
Write-Host 'No se eliminaron reglas ajenas. Una regla anterior amplia de Python también debe revisarse.'
Write-Host 'Para revertir: Remove-NetFirewallRule -Name ARY-Portal-LAN-8765-IIS154'
