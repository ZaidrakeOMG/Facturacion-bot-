"""Inicializa solo archivos ausentes; nunca sustituye los ajustes del operador."""
from __future__ import annotations
import json
from pathlib import Path

CONFIG_INICIAL = {'app': {'nombre': 'ARY Facturacion Bot',
         'correo': 'emisioncfdi@grupoary.com',
         'intervalo_segundos': 20,
         'modo_prueba': True,
         'auto_responder_faltantes': False,
         'auto_responder_completada': False,
         'asunto_palabras': ['FACTURA', 'CFDI']},
 'gmail': {'credentials_file': 'credentials.json',
           'token_file': 'token.json',
           'labels': {'procesando': 'ARYBOT_PROCESANDO',
                      'esperando': 'ARYBOT_ESPERANDO_DATOS',
                      'prueba_ok': 'ARYBOT_PRUEBA_OK',
                      'procesado': 'ARYBOT_PROCESADO',
                      'error': 'ARYBOT_ERROR'},
           'cuenta_esperada': 'emisioncfdi@grupoary.com'},
 'polaris': {'executable': 'C:/Polaris/PolarisFacturacion.exe',
             'usuario': 'SUPERVISOR',
             'servicio_credencial_windows': 'ARY_FACTURACION_BOT_POLARIS',
             'ventana_principal_regex': '.*Polaris Facturacion.*',
             'ventana_login_regex': '.*Entrada al Sistema.*Polaris.*',
             'espera_corta': 0.5,
             'espera_media': 1.5,
             'espera_larga': 6.0,
             'espera_timbrado': 45.0},
 'ocr': {'tesseract_cmd': '', 'idiomas': 'spa+eng'}}

PORTAL_INICIAL = {'puerto': 8765,
 'escuchar_en': '10.20.20.182',
 'origenes_permitidos': ['https://www.grupoary.com.mx'],
 'https_publico': True,
 'proxy_confiable': '10.20.20.154',
 'permitir_facturacion_real': False,
 'max_solicitudes_pendientes': 100,
 'limite_envios_10_minutos': 8}


def asegurar_archivos(base):
    base = Path(base)
    for folder in ('data', 'logs', 'descargas', 'capturas_error'):
        (base / folder).mkdir(parents=True, exist_ok=True)
    for name, value in (('config.json', CONFIG_INICIAL), ('portal_config.json', PORTAL_INICIAL)):
        path = base / name
        # Modo exclusivo: jamás sobrescribir configuración o credenciales existentes.
        try:
            with path.open('x', encoding='utf-8') as stream:
                json.dump(value, stream, indent=2, ensure_ascii=False)
                stream.write('\n')
        except FileExistsError:
            pass
