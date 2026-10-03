"""Transporte Gmail dedicado al hilo de alertas, sin reutilizar el transporte del monitor.
No crea credenciales, no responde al hilo del cliente, no envía a dominios externos.
"""
from __future__ import annotations
import base64
from email.message import EmailMessage
from email import policy
from pathlib import Path
from .alertas_modelo import destinatarios_internos, ETIQUETA_HEADER, PREFIJO_ASUNTO
from .gmail_limites import GmailEnEspera, limite_cuenta


class ErrorEnvioAlerta(RuntimeError):
    def __init__(self, message, *, incierto=False, reintentable=False, espera=60):
        super().__init__(message)
        self.incierto = bool(incierto)
        self.reintentable = bool(reintentable) and not self.incierto
        self.espera = max(1, int(espera))


def construir_mensaje(sender, recipients, subject, body, ident, png=None):
    recipients = destinatarios_internos(recipients)
    if not recipients: raise ValueError('Sin destinatarios internos.')
    if not subject.startswith(PREFIJO_ASUNTO): raise ValueError('Falta el prefijo de alerta interna.')
    if not ident or any(c not in '0123456789abcdef' for c in ident): raise ValueError('ID interno inválido.')
    if any(c in sender+subject for c in '\r\n'): raise ValueError('Cabecera inválida.')
    em = EmailMessage(policy=policy.SMTP)
    em['From'] = sender; em['To'] = ', '.join(recipients); em['Subject'] = subject
    em[ETIQUETA_HEADER] = '1'; em['Auto-Submitted'] = 'auto-generated'
    em['X-Auto-Response-Suppress'] = 'All'
    em['Message-ID'] = f'<ary-alerta-{ident}@grupoary.com>'
    em.set_content(body)
    if png:
        if len(png)>3_000_000 or not png.startswith(b'\x89PNG\r\n\x1a\n'):
            raise ValueError('Solo se permite un recorte PNG acotado de la ventana.')
        em.add_attachment(png, maintype='image', subtype='png', filename='ventana_polaris.png')
    return em


class CorreoAlertasGmail:
    def __init__(self, base, cfg): self.base = Path(base); self.cfg = cfg

    def enviar(self, recipients, subject, body, ident, png=None):
        # Importación tardía: ver el historial no exige una sesión Gmail activa.
        send_started = False
        service = None
        try:
            from google.oauth2.credentials import Credentials
            from google.auth.transport.requests import Request
            from google_auth_httplib2 import AuthorizedHttp
            import httplib2
            from googleapiclient.discovery import build
            from googleapiclient.errors import HttpError
            g = self.cfg.get('gmail', {})
            token = self.base/g.get('token_file', 'token.json')
            if not token.exists():
                raise ErrorEnvioAlerta('Gmail no conectado. Usa Conectar Gmail en Panel y después reenvía esta alerta.')
            creds = Credentials.from_authorized_user_file(str(token), ['https://www.googleapis.com/auth/gmail.modify'])
            if creds.expired and creds.refresh_token:
                request = Request()
                def bounded(*args, **kwargs):
                    kwargs['timeout'] = 20
                    return request(*args, **kwargs)
                creds.refresh(bounded)
            if not creds.valid: raise ErrorEnvioAlerta('La autorización de Gmail no es válida. Vuelve a conectar Gmail.')
            http = AuthorizedHttp(creds, http=httplib2.Http(timeout=25))
            service = build('gmail', 'v1', http=http, cache_discovery=False)
            limite = limite_cuenta(self.base, self.cfg)
            profile = limite.ejecutar(service.users().getProfile(userId='me'), coste=1)
            actual = (profile.get('emailAddress') or '').strip().lower()
            expected = (g.get('cuenta_esperada') or self.cfg.get('app',{}).get('correo') or 'emisioncfdi@grupoary.com').strip().lower()
            if not actual or actual != expected:
                raise ErrorEnvioAlerta('La cuenta Gmail autorizada no coincide con la cuenta configurada del bot.')
            em = construir_mensaje(actual, recipients, subject, body, ident, png)
            raw = base64.urlsafe_b64encode(em.as_bytes()).decode('ascii')
            send_started = True
            # Sin threadId: NO es una respuesta al correo del cliente.
            return limite.ejecutar(service.users().messages().send(userId='me', body={'raw':raw}), coste=100, lectura=False)
        except GmailEnEspera as exc:
            raise ErrorEnvioAlerta(str(exc), reintentable=True, espera=exc.segundos) from exc
        except ErrorEnvioAlerta:
            raise
        except Exception as exc:
            code = getattr(getattr(exc, 'resp', None), 'status', None)
            if not send_started:
                raise ErrorEnvioAlerta('No fue posible conectar/autenticar Gmail antes del envío. La alerta queda local para reenviar.') from exc
            if isinstance(code, int) and 400 <= code < 500 and code != 408:
                raise ErrorEnvioAlerta(f'Gmail rechazó el correo (HTTP {code}). Revisa conexión, permisos o cuota y reenvía manualmente.') from exc
            raise ErrorEnvioAlerta('El envío no quedó confirmado. Puede haber llegado a Gmail; revisa Enviados antes de reenviar.', incierto=True) from exc
        finally:
            if service:
                try: service.close()
                except Exception: pass
