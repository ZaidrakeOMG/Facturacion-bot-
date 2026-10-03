# Ejecutar en 10.20.20.154. Solo prueba TCP y GET /salud: no crea solicitudes.
$ErrorActionPreference = 'Stop'
Write-Host 'Destino del bot: 10.20.20.182:8765'
$result = Test-NetConnection -ComputerName 10.20.20.182 -Port 8765 -InformationLevel Detailed
$result | Format-List ComputerName, RemoteAddress, RemotePort, SourceAddress, TcpTestSucceeded
if (-not $result.TcpTestSucceeded) {
    Write-Host 'No hay conexión TCP. Revisa el arranque del portal, puerto, IP y firewall.'
    exit 1
}
try {
    Invoke-RestMethod -Uri 'http://10.20.20.182:8765/api/facturacion/salud' -Method Get -TimeoutSec 10 | Format-List
} catch {
    Write-Host 'TCP responde, pero la prueba HTTP no terminó. Conserva el mensaje de error:'
    Write-Host $_.Exception.Message
    exit 2
}
