"""Resumen estadistico del monitor ARY (solo lectura, sin dependencias del bot).

Las cifras de facturas excluyen altas y modo seguro. "SI" representa
envio solicitado o folio que Polaris marco como ya facturado: NO acredita
timbre SAT ni entrega del correo. Cualquier desenlace incierto es revision.
Nunca se devuelve el motivo bruto: puede contener RFC o datos personales.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta
import json
import re
import time
import unicodedata


PENDIENTES = {"EN_COLA", "EJECUTANDO"}
REVISION = {
    "REVISION_REQUERIDA", "REVISION_CERRADA",
    "PREPARADA_FACTURA", "PREPARADA_ALTA", "ACEPTAR_ALTA",
    "PRUEBA_PENDIENTE", "REVISION_ENTRADA",
}
NO_FACTURA = {"CANCELADA", "ESPERANDO_DATOS", "DUPLICADA"}
RESULTADOS = {"si", "no", "pendiente", "revision", "prueba"}


def _normalizar(valor):
    sin_acentos = unicodedata.normalize("NFKD", str(valor or ""))
    return "".join(c for c in sin_acentos if not unicodedata.combining(c)).casefold()


def motivo_categorizado(estado, motivo):
    """Diagnostico general sin copiar motivos que puedan contener datos fiscales."""
    e = str(estado or "").upper()
    raw = _normalizar(motivo)
    if e == "YA_FACTURADO":
        return "Ticket ya facturado"
    if e == "CANCELADA":
        return "Solicitud cancelada"
    if e == "DUPLICADA":
        return "Solicitud duplicada"
    if e == "ESPERANDO_DATOS":
        return "Datos incompletos"
    if e == "REVISION_ENTRADA":
        return "Validación de entrada pendiente"
    if "primer plano" in raw or "foco" in raw or "ventana" in raw or "foreground" in raw:
        return "Pantalla / primer plano de Polaris"
    if "cliente no registrado" in raw or "no existe el cliente" in raw:
        return "Cliente no registrado"
    if "regimen" in raw or "fiscal" in raw or "constancia" in raw or "idcif" in raw:
        return "Validación fiscal"
    if "ticket" in raw or "folio" in raw or "codigo de barras" in raw:
        return "Validación del ticket / folio"
    if "correo" in raw or "email" in raw or "gmail" in raw:
        return "Correo / envío"
    if "polaris" in raw or "captura" in raw or "pantalla" in raw:
        return "Operación en Polaris"
    if "timbr" in raw or "cfdi" in raw or "emit" in raw:
        return "Timbrado / CFDI no confirmado"
    if "tiempo" in raw or "espera" in raw or "timeout" in raw:
        return "Tiempo de espera agotado"
    if e in REVISION:
        return "Revisión manual pendiente"
    if e in NO_FACTURA:
        return "Trámite no completado"
    return "Sin motivo detallado registrado"


def clasificar(tipo, estado, seguro, motivo="", riesgo_cfdi=False):
    """Devuelve (grupo, etiqueta, motivo_seguro), nunca afirmaciones especulativas."""
    t = str(tipo or "").upper()
    e = str(estado or "").upper()
    razon = motivo_categorizado(e, motivo)
    if bool(seguro):
        return ("prueba", "Prueba sin emisión", "Modo seguro")
    if t == "ALTA":
        if e == "ALTA_CONFIRMADA":
            return ("alta_si", "Alta confirmada", "—")
        if e in PENDIENTES or e == "ACEPTAR_ALTA":
            return ("alta_pendiente", "Alta en trámite", "—")
        if e in REVISION:
            return ("alta_revision", "Alta en revisión", razon)
        if e in NO_FACTURA:
            return ("alta_no", "Alta sin completar", razon)
        return ("alta_revision", "Alta por verificar", razon)
    if t != "FACTURA":
        return ("otro", "Otro trámite", "—")
    # La marca persistida de posible CFDI siempre tiene prioridad sobre
    # un estado nominalmente terminado: evitar certificar un SI incierto.
    if bool(riesgo_cfdi):
        return ("revision", "Resultado sin confirmar", "Timbrado / CFDI no confirmado")
    if e == "ENVIO_SOLICITADO":
        return ("si", "Envío solicitado", "—")
    if e == "YA_FACTURADO":
        return ("si", "Ya estaba facturado", razon)
    if e in PENDIENTES:
        return ("pendiente", "En atención", "—")
    if bool(riesgo_cfdi) or e in REVISION or e in ("FINALIZADA", "PRUEBA_CERRADA"):
        return ("revision", "Resultado sin confirmar", razon)
    if e in NO_FACTURA:
        return ("no", "No se completó", razon)
    return ("revision", "Resultado por verificar", razon)


def estacion_segura(value):
    txt = str(value or "").strip()
    if not txt or len(txt) > 64 or not re.fullmatch(
        r"[a-zA-ZáéíóúÁÉÍÓÚüÜñÑ0-9 _./(),&-]+", txt
    ):
        return "No identificada"
    return txt


def _campos(datos):
    try:
        resultado = json.loads(datos) if isinstance(datos, str) else datos
        return resultado if isinstance(resultado, dict) else {}
    except (ValueError, TypeError):
        return {}


def generar_reporte(filas, *, ahora=None, limite_tabla=150):
    """Recorre todos los registros visibles del periodo; limita solo tabla."""
    ahora = float(time.time() if ahora is None else ahora)
    resultados = Counter()
    altas = Counter()
    dispositivos = defaultdict(Counter)
    motivos_no = Counter()
    motivos_revision = Counter()
    estaciones = defaultdict(Counter)
    recientes = []
    total = 0
    dias = {}
    for offset in range(6, -1, -1):
        fecha = (datetime.fromtimestamp(ahora) - timedelta(days=offset)).strftime("%Y-%m-%d")
        dias[fecha] = {"fecha": fecha, "si": 0, "no": 0, "revision": 0}
    for item in filas:
        total += 1
        campos = _campos(item["datos"])
        estacion = estacion_segura(campos.get("estacion"))
        tipo = str(item["tipo"] or "").upper()
        estado = str(item["estado"] or "").upper()
        seguro = bool(item["seguro"])
        riesgo = bool(item["riesgo_cfdi"])
        dispositivo = campos.get("dispositivo_origen", "no_identificado")
        if item["origen"] != "Portal web" or dispositivo not in ("telefono", "computadora", "tableta"):
            dispositivo = "no_identificado"
        grupo, etiqueta, razon = clasificar(tipo, estado, seguro, item["motivo"], riesgo)
        if tipo == "FACTURA":
            resultados[grupo] += 1
            if grupo != "prueba":
                dispositivos[dispositivo]["total"] += 1
                dispositivos[dispositivo][grupo] += 1
            if grupo != "prueba":
                estaciones[estacion][grupo] += 1
            if grupo == "no":
                motivos_no[razon] += 1
            elif grupo == "revision":
                motivos_revision[razon] += 1
            if grupo in ("si", "no", "revision"):
                fecha = datetime.fromtimestamp(float(item["creado"] or 0)).strftime("%Y-%m-%d")
                if fecha in dias:
                    dias[fecha][grupo] += 1
            if estado == "YA_FACTURADO" and not seguro and grupo == "si":
                resultados["ya_facturado"] += 1
            if estado == "ENVIO_SOLICITADO" and not seguro and grupo == "si":
                resultados["envio_solicitado"] += 1
        elif tipo == "ALTA":
            altas[grupo] += 1
        if len(recientes) < limite_tabla:
            recientes.append({
                "id": str(item["id"] or "")[:12].upper(),
                "tipo": tipo,
                "estado": estado,
                "estacion": estacion,
                "origen": str(item["origen"] or "")[:28] if str(item["origen"] or "") in (
                    "Portal web", "Formulario", "Gmail", "Interno", "Manual"
                ) else "Otro",
                "modo": "Prueba" if seguro else "Real",
                "grupo": grupo,
                "dispositivo": dispositivo if item["origen"] == "Portal web" else "no_identificado",
                "resultado": etiqueta,
                "motivo": razon,
                "riesgo_cfdi": riesgo,
                "creado": item["creado"],
                "actualizado": item["actualizado"],
            })
    return {
        "total_registros": total,
        "facturas": {k: int(resultados[k]) for k in (
            "si", "no", "pendiente", "revision", "prueba",
            "ya_facturado", "envio_solicitado"
        )},
        "dispositivos": {
            categoria: {clave: int(dispositivos[categoria][clave]) for clave in (
                "total", "si", "no", "pendiente", "revision"
            )}
            for categoria in ("telefono", "computadora", "tableta", "no_identificado")
        },
        "altas": {k: int(altas[k]) for k in (
            "alta_si", "alta_no", "alta_pendiente", "alta_revision", "prueba"
        )},
        "motivos_no": [{"motivo": k, "cantidad": n}
                       for k, n in motivos_no.most_common(8)],
        "motivos_revision": [{"motivo": k, "cantidad": n}
                             for k, n in motivos_revision.most_common(8)],
        "estaciones": [
            {"nombre": k, "si": int(c["si"]), "no": int(c["no"]),
             "pendiente": int(c["pendiente"]), "revision": int(c["revision"])}
            for k, c in sorted(estaciones.items(), key=lambda kv: (-sum(kv[1].values()), kv[0]))
        ],
        "tendencia": list(dias.values()),
        "trabajos": recientes,
        "tabla_limitada": total > limite_tabla,
    }
