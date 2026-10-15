# Ejecutar en la VM del bot como Administrador.
# Crea una regla exacta IIS -> Bot según portal_config.json y audita aperturas amplias del mismo puerto.
$ErrorActionPreference = 'Stop'
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Abre Windows PowerShell como administrador antes de ejecutar este archivo.'
}

$base = Split-Path -Parent $MyInvocation.MyCommand.Path
$configPath = Join-Path $base 'portal_config.json'
if (-not (Test-Path -LiteralPath $configPath)) { throw 'No se encontro portal_config.json.' }
$cfg = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
$bind = [string]$cfg.escuchar_en
$proxy = [string]$cfg.proxy_confiable
$port = [int]$cfg.puerto
if (-not $bind -or -not $proxy -or $port -lt 1024 -or $port -gt 65535) {
    throw 'portal_config.json no contiene IP local, proxy IIS y puerto validos.'
}
if (-not (Get-NetIPAddress -AddressFamily IPv4 | Where-Object IPAddress -eq $bind)) {
    throw "Este equipo no tiene la IP $bind. No se cambio el firewall."
}

$name = "ARY-Portal-LAN-$port-IIS"
if (Get-NetFirewallRule -Name $name -ErrorAction SilentlyContinue) {
    Remove-NetFirewallRule -Name $name
}
New-NetFirewallRule -Name $name -DisplayName "ARY Portal $port solo IIS $proxy" -Direction Inbound -Action Allow -Protocol TCP -LocalPort $port -LocalAddress $bind -RemoteAddress $proxy -Profile Any -EdgeTraversalPolicy Block | Out-Null

Write-Host "Regla exacta creada: $proxy -> $bind TCP $port." -ForegroundColor Green
Write-Host 'La aplicacion ademas valida la IP TCP del IIS antes de aceptar el portal.' -ForegroundColor Green

$amplias = @()
foreach ($rule in @(Get-NetFirewallRule -Enabled True -Direction Inbound -Action Allow -ErrorAction SilentlyContinue)) {
    if ($rule.Name -eq $name) { continue }
    try {
        $portFilter = $rule | Get-NetFirewallPortFilter -ErrorAction Stop
        if ([string]$portFilter.Protocol -ne 'TCP' -or [string]$portFilter.LocalPort -ne [string]$port) { continue }
        $addressFilter = $rule | Get-NetFirewallAddressFilter -ErrorAction Stop
        $remote = @($addressFilter.RemoteAddress | ForEach-Object { [string]$_ })
        if ($remote -contains 'Any' -or $remote -contains '*') { $amplias += $rule }
    } catch { }
}
if ($amplias.Count) {
    Write-Warning "Hay $($amplias.Count) regla(s) adicional(es) que permiten TCP $port desde cualquier origen:"
    $amplias | Select-Object Name,DisplayName,Profile | Format-Table -AutoSize
    Write-Warning 'Revisalas antes de considerar cerrado el firewall. No se deshabilitaron automaticamente para no afectar otros servicios.'
    exit 2
}

Write-Host "No se detectaron otras reglas Allow amplias para TCP $port." -ForegroundColor Green
Write-Host "Para revertir solo esta regla: Remove-NetFirewallRule -Name $name"
