"""Cuota compartida por cuenta: monitor y notificadores, sin tocar Polaris.

Los 403 de cuota / 429 son rechazos explícitos. Se espera antes del próximo
intento. Un timeout/5xx al ENVIAR NO se reintenta: pudo entregar el mensaje.
No se comparten transportes httplib2 entre hilos, solo este limitador.
"""
from __future__ import annotations
import json
import math
import random
import threading
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime


class GmailEnEspera(RuntimeError):
    def __init__(self, segundos=60, motivo='cuota', *, rechazo_explicito=False):
        self.segundos = max(1, int(math.ceil(segundos)))
        self.motivo = motivo
        self.rechazo_explicito = rechazo_explicito
        super().__init__(f'Gmail en espera por {motivo}: reintento en {self.segundos} s. '
                         'Se conservan los datos locales; no se repite Polaris.')


def es_cuota(error):
    status = getattr(getattr(error, 'resp', None), 'status', None)
    if status == 429:
        return True
    if status != 403:
        return False
    try:
        body = json.loads(error.content)
        reasons = {x.get('reason', '') for x in body.get('error', {}).get('errors', [])}
    except (AttributeError, ValueError, TypeError):
        return False
    return bool(reasons & {'rateLimitExceeded', 'userRateLimitExceeded', 'quotaExceeded'})


def retry_after(error):
    try:
        value = error.resp.get('retry-after') or error.resp.get('Retry-After')
        if not value:
            return 0
        try:
            return max(0, float(value))
        except (ValueError, TypeError):
            date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                date = date.replace(tzinfo=timezone.utc)
            return max(0, (date - datetime.now(timezone.utc)).total_seconds())
    except (AttributeError, TypeError, ValueError, OverflowError):
        return 0


class LimiteGmail:
    # Presupuesto conservador de ESTE bot; no representa la cuota del proyecto.
    def __init__(self, *, reloj=None, dormir=None, azar=None, unidades_minuto=2400):
        self.reloj = reloj or time.monotonic
        self.dormir = dormir or time.sleep
        self.azar = azar or random.random
        self.unidades_minuto = max(120, min(int(unidades_minuto), 5000))
        self.lock = threading.RLock()
        self.proxima = 0.0
        self.hasta = 0.0
        self.fallos = 0
        self.ultimo_fallo = None

    def ejecutar(self, request, *, coste=20, lectura=True):
        # Una petición de red por cuenta. Cada hilo conserva su propio Http.
        with self.lock:
            now = self.reloj()
            if now < self.hasta:
                raise GmailEnEspera(self.hasta - now)
            wait = max(0, self.proxima - now)
            if wait:
                self.dormir(wait)
            self.proxima = self.reloj() + max(1, coste) * 60 / self.unidades_minuto
            try:
                result = request.execute(num_retries=0)
            except Exception as exc:
                status = getattr(getattr(exc, 'resp', None), 'status', None)
                quota = es_cuota(exc)
                transient_read = lectura and (status in {500, 502, 503, 504} or
                                               isinstance(exc, (TimeoutError, ConnectionError)))
                if not quota and not transient_read:
                    raise
                self.fallos = min(self.fallos + 1, 6)
                delay = max(retry_after(exc), min(900, 60 * (2 ** (self.fallos - 1))) + self.azar())
                self.ultimo_fallo = self.reloj()
                self.hasta = self.ultimo_fallo + delay
                raise GmailEnEspera(delay, 'cuota' if quota else 'fallo temporal de lectura',
                                    rechazo_explicito=quota) from exc
            # No reinicia el backoff por una sola lectura correcta entre rechazos.
            if self.ultimo_fallo is not None and self.reloj() - self.ultimo_fallo >= 300:
                self.fallos = 0
                self.ultimo_fallo = None
            return result


_REGISTRY = {}
_REGISTRY_LOCK = threading.Lock()


def limite_cuenta(base, cfg):
    account = (cfg.get('gmail', {}).get('cuenta_esperada') or
               cfg.get('app', {}).get('correo') or 'emisioncfdi@grupoary.com').strip().lower()
    with _REGISTRY_LOCK:
        if account not in _REGISTRY:
            _REGISTRY[account] = LimiteGmail()
        return _REGISTRY[account]
