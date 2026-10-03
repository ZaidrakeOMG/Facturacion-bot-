"""Clasificación de ventanas durante el tramo final de factura.

No llama a Windows, no escribe campos y no pulsa botones. Sólo distingue una
espera conocida de un aviso que necesita revisión. Una clase #32770 identifica
un diálogo, no necesariamente un error. Nunca se infiere éxito de una barra.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class ControlDialogo:
    clase: str
    texto: str = ''
    habilitado: bool = True


PROGRESOS = frozenset(('PROGRESO_CFDI', 'PROGRESO_ARCHIVOS', 'PROGRESO_CORREO'))
ETAPAS = {
    'GENERANDOELCFDI': 'PROGRESO_CFDI',
    'GENERANDOCFDI': 'PROGRESO_CFDI',
    'TIMBRANDOELCFDI': 'PROGRESO_CFDI',
    'TIMBRANDOCFDI': 'PROGRESO_CFDI',
    'CERTIFICANDOELCFDI': 'PROGRESO_CFDI',
    'CERTIFICANDOCFDI': 'PROGRESO_CFDI',
    'GENERANDOELPDF': 'PROGRESO_ARCHIVOS',
    'GENERANDOPDF': 'PROGRESO_ARCHIVOS',
    'GENERANDOELXML': 'PROGRESO_ARCHIVOS',
    'GENERANDOXML': 'PROGRESO_ARCHIVOS',
    'ENVIANDOCORREO': 'PROGRESO_CORREO',
    'ENVIANDOELCORREO': 'PROGRESO_CORREO',
    'ENVIANDOCORREOELECTRONICO': 'PROGRESO_CORREO',
    'ENVIANDOELCORREOELECTRONICO': 'PROGRESO_CORREO',
}
# Se descartan controles, paneles y campos: no son ventanas de aviso.
CONTROL_CLASES = ('BUTTON', 'BITBTN', 'SPEEDBUTTON', 'CHECKBOX', 'RADIO',
                  'EDIT', 'COMBO', 'MEMO', 'STATIC', 'LABEL', 'PANEL',
                  'GROUPBOX', 'GROUP', 'GRID', 'LIST', 'PROGRESS', 'STATUS',
                  'TOOLBAR', 'SCROLL', 'TABCONTROL', 'PAGECONTROL', 'BEVEL')
PREFIJOS_AVISO = ('ERROR', 'AVISO', 'ADVERTENCIA', 'CONFIRMACION',
                  'PREGUNTA', 'MENSAJE', 'INFORMACION')
NEGATIVOS = ('ERROR', 'FALLO', 'NOSEPUDO', 'NOSELOGRO', 'RECHAZ',
             'INVALID', 'INCORRECT', 'EXCEPTION', 'EXCEPCION',
             'PARAMETROVACIO', 'PARAMETROSVACIOS', 'PARAMETEREMPTY', 'PARAMETERISEMPTY')


def normalizar(texto: str) -> str:
    value = unicodedata.normalize('NFKD', texto or '')
    value = ''.join(c for c in value if not unicodedata.combining(c))
    return re.sub(r'[^A-Z0-9]', '', value.upper())


def es_campo(clase: str) -> bool:
    return any(k in clase.upper() for k in ('EDIT', 'COMBO', 'MEMO', 'GRID', 'LIST'))


def es_boton(clase: str) -> bool:
    return any(k in clase.upper() for k in ('BUTTON', 'BITBTN', 'SPEEDBUTTON', 'CHECKBOX', 'RADIO'))


def es_candidato(clase: str) -> bool:
    # #32770 no contiene ninguno de los tokens de controles excluidos.
    return not any(k in (clase or '').upper() for k in CONTROL_CLASES)


def clasificar_dialogo(clase: str, titulo: str,
                       controles: Iterable[ControlDialogo]) -> str:
    """Devuelve OTRA, AVISO o una etapa PROGRESO_*, nunca éxito.

    Una espera exige una etiqueta exacta reconocida y ausencia de acciones
    afirmativas, campos o errores. Un botón Cancelar sólo se admite junto con
    una barra real; no se pulsa nunca. Los errores prevalecen sobre el progreso.
    """
    if not es_candidato(clase):
        return 'OTRA'
    items = tuple(controles)
    title = normalizar(titulo)
    captions = [title]
    for c in items:
        # No usamos el RFC, correo ni otros valores de entrada como etiquetas.
        if not es_campo(c.clase):
            captions.append(normalizar(c.texto))
    etapa = next((ETAPAS[c] for c in captions if c in ETAPAS), None)
    aviso = clase == '#32770' or title.startswith(PREFIJOS_AVISO)
    negativo = (title.startswith(('ERROR', 'ADVERTENCIA', 'CONFIRMACION', 'PREGUNTA'))
                or any(n in c for c in captions for n in NEGATIVOS))
    if negativo:
        return 'AVISO'
    if etapa:
        barra = any('PROGRESS' in c.clase.upper() for c in items)
        botones = [c for c in items if es_boton(c.clase)]
        campos = any(es_campo(c.clase) for c in items)
        acciones = any(normalizar(c.texto) not in ('CANCELAR', 'CANCEL') for c in botones)
        if campos or acciones or (botones and not barra):
            return 'AVISO'
        # Sin barra accesible también se admite una etiqueta exacta SIN acciones:
        # algunos formularios dibujan el progreso sin crear un control Windows.
        return etapa
    return 'AVISO' if aviso else 'OTRA'
