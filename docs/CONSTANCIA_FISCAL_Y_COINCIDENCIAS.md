# Constancia fiscal obligatoria y selección de cliente

## Cambios
- Portal web/ARY Astro: factura y alta exigen PDF digital de la Constancia de Situación Fiscal, máximo 2 MB, 1 a 3 páginas.
- Se eliminan RFC e idCIF manuales de esos formularios. El teléfono sigue siendo opcional; estación, ticket y correo permanecen.
- Se lee en memoria el PDF enviado por API y se conserva en la cola **solo RFC, razón social, calle e idCIF (alta)**. No se guarda el PDF desde la API ni aparece en el seguimiento.
- Para Gmail se exige igualmente un PDF: el flujo existente descarga los adjuntos a `descargas/`; esos ficheros no se borran por este parche y requieren una política local de retención.
- En facturación se busca por RFC, pero solo se acepta una fila del catálogo cuando el lector UI Automation puede leer **todas** las filas y confirmar una única coincidencia de RFC, razón social y calle.
- Si hay 0 o varias coincidencias verificadas, o Polaris no expone filas completas por UI Automation, se marca la solicitud `REVISION_REQUERIDA`, se avisa al cliente y se continúa con la siguiente solicitud **antes de timbrar**.
- No se cambia la protección frente a timbrado/envío o alta inciertos. Nunca se repite automáticamente un intento que pudo haber guardado o timbrado.

## Requisitos antes de activar en producción
1. Actualizar juntos el bot y Astro. Durante el despliegue de uno sin el otro, los formularios antiguos/nuevos serán rechazados por el otro sistema.
2. Empezar en **MODO SEGURO** y comprobar en Polaris real una prueba con dos clientes que compartan RFC pero tengan nombre y domicilio distintos.
3. Si el catálogo VCL/DevExpress no expone `GridPattern` y `SelectionItem` por UI Automation, el bot **no elegirá ninguno**. Será necesario calibrar el lector sobre ese Windows, conservando las barreras de seguridad.
4. Proteger mediante TLS/HTTPS o IPsec/VPN el tramo IIS → VM antes de aceptar PDFs reales. La regla IIS existente usa HTTP privado, que **no cifra los datos del PDF**.
5. La lectura del PDF **no demuestra autenticidad del SAT ni propiedad del RFC**: verifica texto y estructura; mantener revisión de identidad/propiedad del ticket.
6. El PDF debe ser digital, no una captura escaneada, no estar cifrado y contener los campos fiscales legibles, incluido idCIF para altas.

## Lo que NO hace este cambio
No modifica la base de datos de Polaris por SQL, no extrae de la web registros de terceros, no envía constancias por correo al cliente y no asegura que el nuevo selector funcione en todas las instalaciones de Polaris hasta probar UI Automation en tu VM.
