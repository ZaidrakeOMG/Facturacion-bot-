"""Catálogo SAT c_RegimenFiscal para altas; nunca asignar un régimen por defecto.

Una clave debe coincidir con el RFC y la Constancia de Situación Fiscal.
El texto de Polaris se compara sin acentos y sin distinguir mayúsculas.
"""
from __future__ import annotations
import re
import unicodedata

# (Descripción SAT, tipos de RFC admitidos: F=física, M=moral).
REGIMENES = {
    '601': ('General de Ley Personas Morales', 'M'),
    '603': ('Personas Morales con Fines no Lucrativos', 'M'),
    '605': ('Sueldos y Salarios e Ingresos Asimilados a Salarios', 'F'),
    '606': ('Arrendamiento', 'F'),
    '607': ('Régimen de Enajenación o Adquisición de Bienes', 'F'),
    '608': ('Demás ingresos', 'F'),
    '610': ('Residentes en el Extranjero sin Establecimiento Permanente en México', 'FM'),
    '611': ('Ingresos por Dividendos (socios y accionistas)', 'F'),
    '612': ('Personas Físicas con Actividades Empresariales y Profesionales', 'F'),
    '614': ('Ingresos por intereses', 'F'),
    '615': ('Régimen de los ingresos por obtención de premios', 'F'),
    '616': ('Sin obligaciones fiscales', 'F'),
    '620': ('Sociedades Cooperativas de Producción que optan por diferir sus ingresos', 'M'),
    '621': ('Incorporación Fiscal', 'F'),
    '622': ('Actividades Agrícolas, Ganaderas, Silvícolas y Pesqueras', 'M'),
    '623': ('Opcional para Grupos de Sociedades', 'M'),
    '624': ('Coordinados', 'M'),
    '625': ('Régimen de las Actividades Empresariales con ingresos a través de Plataformas Tecnológicas', 'F'),
    '626': ('Régimen Simplificado de Confianza', 'FM'),
}


def normalizar_regimen(valor):
    s = unicodedata.normalize('NFKD', str(valor or ''))
    return re.sub(r'[^A-Z0-9]', '', ''.join(c for c in s if not unicodedata.combining(c)).upper())


def texto_regimen(clave):
    return REGIMENES[str(clave)][0]


def validar_regimen(rfc, clave):
    """Devuelve la clave cuando el régimen elegido coincide con el tipo de RFC.

    No se infiere el régimen a partir de nombre, RFC ni actividad económica.
    """
    clave = str(clave or '').strip()
    if clave not in REGIMENES:
        raise ValueError('Selecciona el régimen fiscal que aparece en tu constancia del SAT.')
    rf = str(rfc or '').strip().upper()
    tipo = 'M' if len(rf) == 12 else 'F' if len(rf) == 13 else ''
    if tipo not in REGIMENES[clave][1]:
        raise ValueError('El régimen fiscal '+clave+' no corresponde al tipo de RFC del cliente. Revisa tu constancia del SAT.')
    return clave


def opciones_regimen():
    return [{'valor': codigo, 'texto': codigo+' — '+desc, 'tipo': tipos}
            for codigo, (desc, tipos) in REGIMENES.items()]
