# ARY Gasolineras | Centro de Control Magenta (solo lectura)

Un panel web independiente para revisar la cola del bot en la VM sin abrir Escritorio Remoto ni tocar Polaris. **No reemplaza al bot**: solo lee el archivo existente `data/cola_operaciones.sqlite3`. No inicia el proceso GUI, no escribe SQLite, no puede reintentar facturas y no envía correos.

## Funciones
- Actualización automática cada 10 segundos, sin modificar solicitudes.
- Conteos por estado, trabajos en cola, ejecutándose, terminados y revisiones.
- Detalle limitado a estación, modo seguro/real, tipo de trámite, referencia corta, estado y fecha; oculta RFC, nombres, correos y el número completo de ticket.
- Estado persistente de pausa/bloqueo y conteos de avisos.
- Funciona como proceso independiente incluso si el bot está cerrado; **la última actividad SQLite no acredita que Polaris esté abierto ni que se ejecuten trabajos**.

## Dashboard magenta (nueva versión)

El panel funciona en móvil y escritorio, con menú lateral plegable, transiciones sutiles y actualización automática cada 10 segundos.

- Dashboard: facturas reales con resultado SÍ, NO, pendientes y SIN CONFIRMAR; tarjetas numéricas animadas.
- Gráfica dona animada: distribución de las facturas reales sin incluir altas ni pruebas.
- Tendencias: líneas animadas con el resultado de solicitudes creadas en los últimos siete días (no fecha de timbrado).
- Motivos de NO y de revisión: barras horizontales con causas resumidas sin mostrar RFC, correo, nombres ni motivos libres con datos personales.
- Resultados por estación: barras apiladas SÍ, NO, pendiente y revisión.
- Solicitudes: hasta 150 filas recientes, con búsqueda, resultado y motivo seguro.
- Operación: estado persistente de cola/bloqueos y avisos; el panel NO confirma que Polaris esté abierto.
- Periodos: siete días, treinta días o todo el historial visible no archivado. Las gráficas suman todos los registros del periodo, sin el límite de 150 de la tabla.

**Criterios de la gráfica:** SÍ incluye ENVIO_SOLICITADO (el bot solicitó envío, NO garantiza timbrado o recepción) y YA_FACTURADO (el ticket ya tenía CFDI; no se emitió uno nuevo). NO incluye CANCELADA, DUPLICADA y ESPERANDO_DATOS reales. Los resultados inciertos, como REVISION_REQUERIDA, REVISION_CERRADA o con riesgo_cfdi=1, se clasifican SIN CONFIRMAR: revisar Polaris antes de cualquier intento nuevo. Los casos en modo seguro son pruebas aparte y las ALTAS no se cuentan como facturas.

### Para actualizar una instalación ya existente

Ejecuta desde CMD como administrador, dentro de la carpeta del bot, **cada comando en una línea separada**. No necesitas reinstalar el monitor, cambiar la contraseña ni detener Polaris.

    git pull --ff-only origin main
    powershell -NoProfile -Command "Stop-ScheduledTask -TaskName 'ARY - Monitor Web Solo Lectura'; Start-Sleep -Seconds 2; Start-ScheduledTask -TaskName 'ARY - Monitor Web Solo Lectura'"

Entra a http://10.20.20.182:8787/ con usuario Ary y la contraseña local ya configurada.

Para validar el código sin tocar Polaris:

    python -m py_compile MONITOR_ARY_WEB.py monitor_reportes.py
    python -m unittest discover -s tests -p test_monitor_ary_web.py -v

No publiques el puerto 8787 en Internet; HTTP no cifra las contraseñas. Para acceso externo usa VPN y HTTPS.


## Primera y única instalación en la VM

1. Mantén intacto el bot que ya factura. Actualiza una sola vez su copia local desde el repositorio, por ejemplo con `git pull --ff-only origin main`; sin esta instalación inicial la URL no existe.
2. En la misma carpeta de `MONITOR_ARY_WEB.py`, abre **PowerShell como administrador** y ejecuta:
   
   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File .\INSTALAR_MONITOR_ARY.ps1
   ```

3. Se registra una tarea de Windows **ARY - Monitor Web Solo Lectura** al inicio del sistema. No depende de tener RDP abierto.
4. Desde un dispositivo de la **red privada 10.20.0.0/16** o VPN que termine en esa red, abre `http://10.20.20.182:8787`. El usuario es `Ary`; el instalador crea una clave aleatoria una sola vez en `data\monitor_web_clave.txt`. Esa clave no se sube a GitHub.
5. Si Windows tiene otra regla de firewall más permisiva para Python/ese puerto, revísala y desactívala. El instalador solo crea su propia regla limitada.

## Seguridad

**No publiques el puerto 8787 en Internet ni lo redirijas con NAT.** HTTP envía la autenticación sin cifrar, incluso en la LAN. Para operar desde redes no confiables, configura HTTPS con un proxy autenticado (IIS/ARR, por ejemplo), VPN y ACLs de red. La IP `10.20.20.182` es privada y no funciona directamente desde fuera.

La tarea tiene permisos mínimos a nivel de aplicación: los únicos métodos HTTP admitidos son GET y solo lee SQLite en modo `mode=ro` y `PRAGMA query_only=ON`. La cuenta de Windows de la tarea es SYSTEM para funcionar sin una sesión iniciada; proteger su servicio y el equipo es esencial.

### Diagnóstico

```powershell
Get-ScheduledTaskInfo -TaskName 'ARY - Monitor Web Solo Lectura'
Test-NetConnection 10.20.20.182 -Port 8787
```

Si la tarea no inicia, revisa historial de Programador de tareas, que Python 3 esté instalado y que `10.20.20.182` exista en la interfaz de la VM. Para iniciar manualmente solo en localhost:

```cmd
python MONITOR_ARY_WEB.py --crear-clave
python MONITOR_ARY_WEB.py
```

En esa modalidad abre `http://127.0.0.1:8787` desde la misma máquina. No uses el monitor como sustituto de la verificación del estado real de Polaris.
