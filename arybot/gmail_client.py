from __future__ import annotations

import base64
import os
import socket
import threading
import time
from pathlib import Path
from email.message import EmailMessage
from email.utils import parseaddr
from urllib.parse import urlsplit, parse_qs

from bs4 import BeautifulSoup
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]


class NeedGmailAuth(RuntimeError):
    pass


class HistoryExpired(RuntimeError):
    pass


class WrongGmailAccount(RuntimeError):
    pass


class GmailClient:
    def __init__(self, base: Path, cfg: dict, log):
        self.base = base
        self.cfg = cfg
        self.log = log
        self._transport = threading.local()
        self._generation = 0
        self.service = None
        self.email = None
        self._label_cache = {}

        # OAuth manual robusto: no depende de que Windows/Chrome logren
        # conectarse a un servidor localhost. Google redirige a 127.0.0.1
        # y el usuario copia esa URL completa de retorno al bot.
        self._auth_lock = threading.Lock()
        self._manual_flow = None
        self._manual_redirect_uri = None
        self._manual_started_at = None
        self._manual_timeout_seconds = 600

    @property
    def service(self):
        # Cada hilo usa su propio servicio/httplib2.Http; OAuth sigue en la UI.
        if getattr(self._transport, 'generation', -1) != self._generation:
            self._transport.service = None
            self._transport.generation = self._generation
        return getattr(self._transport, 'service', None)

    @service.setter
    def service(self, value):
        self._transport.service = value
        self._transport.generation = self._generation

    @property
    def _label_cache(self):
        if not hasattr(self._transport, 'labels'): self._transport.labels = {}
        return self._transport.labels

    @_label_cache.setter
    def _label_cache(self, value): self._transport.labels = value

    @property
    def token_path(self):
        return self.base / self.cfg["gmail"]["token_file"]

    @property
    def credentials_path(self):
        return self.base / self.cfg["gmail"]["credentials_file"]

    @property
    def expected_account(self):
        return (
            self.cfg.get("gmail", {}).get("cuenta_esperada")
            or self.cfg.get("app", {}).get("correo")
            or "emisioncfdi@grupoary.com"
        ).strip()

    def _finish_connect(self, creds, save_token=False):
        import httplib2
        from google_auth_httplib2 import AuthorizedHttp
        service = build("gmail", "v1", http=AuthorizedHttp(creds, http=httplib2.Http(timeout=25)), cache_discovery=False)
        prof = service.users().getProfile(userId="me").execute()
        email = (prof.get("emailAddress") or "").strip()

        expected = self.expected_account.lower()
        if expected and email.lower() != expected:
            raise WrongGmailAccount(
                f"Se autorizó {email}, pero este bot debe usar {self.expected_account}. "
                "Vuelve a conectar Gmail y elige la cuenta correcta."
            )

        if save_token:
            self.token_path.write_text(creds.to_json(), encoding="utf-8")
            self._generation += 1  # Los demás hilos cargarán la nueva autorización.

        self.service = service
        self.email = email
        self.log(f"Gmail conectado como {self.email}")
        return self.email

    def connect(self, interactive=False):
        creds = None
        if self.token_path.exists():
            try:
                creds = Credentials.from_authorized_user_file(str(self.token_path), SCOPES)
            except Exception:
                creds = None

        if creds and creds.expired and creds.refresh_token:
            try:
                request = Request()
                def bounded(*args, **kwargs):
                    kwargs['timeout'] = 20
                    return request(*args, **kwargs)
                creds.refresh(bounded)
            except Exception:
                creds = None

        if not creds or not creds.valid:
            if not interactive:
                raise NeedGmailAuth(
                    "Gmail todavía no está autorizado. Usa 'Conectar Gmail'."
                )
            raise NeedGmailAuth(
                "Esta versión usa autorización manual por vínculo. Usa el botón Conectar Gmail."
            )

        try:
            return self._finish_connect(creds, save_token=False)
        except WrongGmailAccount:
            try:
                self.token_path.unlink(missing_ok=True)
            except Exception:
                pass
            self.service = None
            self.email = None
            raise

    def _release_manual_auth(self):
        self._manual_flow = None
        self._manual_redirect_uri = None
        self._manual_started_at = None
        try:
            if self._auth_lock.locked():
                self._auth_lock.release()
        except Exception:
            pass

    def start_manual_auth(self, timeout_seconds=600):
        """
        Genera el vínculo OAuth SIN abrir navegador y SIN depender de un
        servidor HTTP local.

        Tras autorizar, Google redirige a http://127.0.0.1:PUERTO/?code=...
        Es normal que el navegador muestre ERR_CONNECTION_REFUSED. El usuario
        copia la URL completa de la barra y la pega de vuelta en el bot.

        Devuelve (auth_url, redirect_uri).
        """
        if not self.credentials_path.exists():
            raise FileNotFoundError(f"No existe {self.credentials_path.name}")

        if not self._auth_lock.acquire(blocking=False):
            raise RuntimeError("Ya hay una autorización de Gmail en curso.")

        try:
            self._manual_timeout_seconds = int(timeout_seconds)
            self._manual_started_at = time.time()

            flow = InstalledAppFlow.from_client_secrets_file(
                str(self.credentials_path), SCOPES
            )

            # Elegimos un puerto libre solo para construir el redirect URI.
            # No abrimos un servidor ahí: si Chrome no puede conectar, eso es
            # esperado y justamente por eso ofrecemos el pegado manual.
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]

            redirect_uri = f"http://127.0.0.1:{port}/"
            flow.redirect_uri = redirect_uri

            auth_url, _state = flow.authorization_url(
                access_type="offline",
                prompt="select_account consent",
                include_granted_scopes="true",
                login_hint=self.expected_account,
            )

            self._manual_flow = flow
            self._manual_redirect_uri = redirect_uri
            return auth_url, redirect_uri
        except Exception:
            self._release_manual_auth()
            raise

    def finish_manual_auth(self, callback_url: str):
        """Completa OAuth usando la URL de retorno copiada del navegador."""
        flow = self._manual_flow
        redirect_uri = self._manual_redirect_uri
        started = self._manual_started_at

        if flow is None or not redirect_uri or started is None:
            raise RuntimeError(
                "No hay una autorización de Gmail activa. Pulsa 'Conectar Gmail' y genera un vínculo nuevo."
            )

        if time.time() - started > self._manual_timeout_seconds:
            self._release_manual_auth()
            raise TimeoutError(
                "El vínculo de autorización expiró. Pulsa 'Conectar Gmail' y genera uno nuevo."
            )

        callback_url = (callback_url or "").strip()
        if not callback_url:
            raise ValueError("Pega la URL completa de retorno que aparece en la barra del navegador.")

        try:
            got = urlsplit(callback_url)
            expected = urlsplit(redirect_uri)
        except Exception as exc:
            raise ValueError(f"La URL de retorno no es válida: {exc}") from exc

        if got.scheme.lower() != "http" or got.hostname not in {"127.0.0.1", "localhost"}:
            raise ValueError(
                "La URL pegada no parece ser el retorno de Google. Debe empezar con http://127.0.0.1:..."
            )

        if got.port != expected.port:
            raise ValueError(
                "La URL pegada pertenece a otra autorización o a otro puerto. Genera un vínculo nuevo e inténtalo otra vez."
            )

        params = parse_qs(got.query)
        if "error" in params:
            err = params.get("error", ["desconocido"])[0]
            self._release_manual_auth()
            raise RuntimeError(f"Google devolvió un error de autorización: {err}")
        if not params.get("code"):
            raise ValueError(
                "La URL no contiene el código de autorización. Copia toda la dirección de la barra DESPUÉS de pulsar Permitir."
            )

        old_insecure = os.environ.get("OAUTHLIB_INSECURE_TRANSPORT")
        os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"
        try:
            flow.fetch_token(authorization_response=callback_url)
            email = self._finish_connect(flow.credentials, save_token=True)
            self._release_manual_auth()
            return email
        except Exception:
            # Si el código ya fue usado o Google lo rechazó, es mejor empezar
            # una autorización nueva en vez de reutilizar una sesión dudosa.
            self._release_manual_auth()
            raise
        finally:
            if old_insecure is None:
                os.environ.pop("OAUTHLIB_INSECURE_TRANSPORT", None)
            else:
                os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = old_insecure

    def cancel_manual_auth(self):
        self._release_manual_auth()

    def disconnect(self):
        self._generation += 1
        self.service = None
        self.email = None
        self.cancel_manual_auth()
        try:
            self.token_path.unlink(missing_ok=True)
        except Exception:
            pass

    def require(self):
        if self.service is None:
            self.connect(interactive=False)
        return self.service

    def current_history_id(self) -> str:
        prof = self.require().users().getProfile(userId="me").execute()
        return str(prof["historyId"])

    def new_messages_from_history(self, start_history_id: str):
        svc = self.require()
        ids = []
        page = None
        latest = str(start_history_id)
        try:
            while True:
                kwargs = dict(
                    userId="me",
                    startHistoryId=str(start_history_id),
                    historyTypes=["messageAdded"],
                )
                if page:
                    kwargs["pageToken"] = page
                r = svc.users().history().list(**kwargs).execute()
                latest = str(r.get("historyId", latest))
                for h in r.get("history", []) or []:
                    for item in h.get("messagesAdded", []) or []:
                        m = item.get("message", {})
                        mid = m.get("id")
                        if mid and mid not in ids:
                            ids.append(mid)
                page = r.get("nextPageToken")
                if not page:
                    break
        except HttpError as e:
            if getattr(e, "resp", None) is not None and e.resp.status == 404:
                raise HistoryExpired("El historial de Gmail expiró y debe reiniciarse.")
            raise

        messages = []
        for mid in ids:
            msg = self.get(mid)
            if "INBOX" in (msg.get("labelIds") or []):
                messages.append(msg)
        return messages, latest

    def latest_matching(self, max_results=10):
        words = list(self.cfg["app"].get("asunto_palabras", ["FACTURA"]))
        words.extend(["ALTA", "REGISTRO"])
        subject_query = " OR ".join(f"subject:{w}" for w in words if w)
        q = (
            f"in:inbox newer_than:7d ({subject_query})"
            if subject_query
            else "in:inbox newer_than:7d"
        )
        r = (
            self.require()
            .users()
            .messages()
            .list(userId="me", q=q, maxResults=max_results)
            .execute()
        )
        return [self.get(x["id"]) for x in r.get("messages", [])]

    def get(self, mid: str):
        return (
            self.require()
            .users()
            .messages()
            .get(userId="me", id=mid, format="full")
            .execute()
        )

    @staticmethod
    def _headers(msg):
        return {
            h["name"].lower(): h["value"]
            for h in msg.get("payload", {}).get("headers", [])
        }

    def basics(self, msg):
        h = self._headers(msg)
        n, e = parseaddr(h.get("from", ""))
        return {
            "from_name": n,
            "from_email": e,
            "subject": h.get("subject", ""),
            "message_id_header": h.get("message-id", ""),
            "references": h.get("references", ""),
        }

    @staticmethod
    def _decode(s):
        if not s:
            return ""
        return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4)).decode(
            "utf-8", errors="replace"
        )

    def body_text(self, msg):
        plain, html = [], []

        def walk(p):
            mime = p.get("mimeType", "")
            body = p.get("body", {}) or {}
            if body.get("data"):
                if mime == "text/plain":
                    plain.append(self._decode(body["data"]))
                elif mime == "text/html":
                    html.append(self._decode(body["data"]))
            for ch in p.get("parts", []) or []:
                walk(ch)

        walk(msg.get("payload", {}))
        if plain:
            return "\n".join(plain)
        if html:
            return "\n".join(
                BeautifulSoup(x, "html.parser").get_text("\n") for x in html
            )
        return self._decode((msg.get("payload", {}).get("body", {}) or {}).get("data", ""))

    def download_attachments(self, msg, folder: Path):
        folder.mkdir(parents=True, exist_ok=True)
        out = []

        def walk(p):
            fn = p.get("filename") or ""
            body = p.get("body", {}) or {}
            if fn and body.get("attachmentId"):
                a = (
                    self.require()
                    .users()
                    .messages()
                    .attachments()
                    .get(
                        userId="me",
                        messageId=msg["id"],
                        id=body["attachmentId"],
                    )
                    .execute()
                )
                data = base64.urlsafe_b64decode(
                    a["data"] + "=" * (-len(a["data"]) % 4)
                )
                safe = "".join(
                    c for c in fn if c.isalnum() or c in "._- ()"
                ).strip() or "adjunto.bin"
                pth = folder / safe
                if pth.exists():
                    pth = folder / f"{pth.stem}_{len(out)+1}{pth.suffix}"
                pth.write_bytes(data)
                out.append(pth)
            for ch in p.get("parts", []) or []:
                walk(ch)

        walk(msg.get("payload", {}))
        return out

    def _label_id(self, name):
        if name in self._label_cache:
            return self._label_cache[name]
        for l in self.require().users().labels().list(userId="me").execute().get("labels", []):
            if l.get("name") == name:
                self._label_cache[name] = l["id"]
                return l["id"]
        l = (
            self.require()
            .users()
            .labels()
            .create(
                userId="me",
                body={
                    "name": name,
                    "labelListVisibility": "labelShow",
                    "messageListVisibility": "show",
                },
            )
            .execute()
        )
        self._label_cache[name] = l["id"]
        return l["id"]

    def add_label(self, mid, name):
        lid = self._label_id(name)
        self.require().users().messages().modify(
            userId="me", id=mid, body={"addLabelIds": [lid]}
        ).execute()

    def remove_label(self, mid, name):
        try:
            lid = self._label_id(name)
            self.require().users().messages().modify(
                userId="me", id=mid, body={"removeLabelIds": [lid]}
            ).execute()
        except Exception:
            pass

    def reply(self, original, text):
        b = self.basics(original)
        em = EmailMessage()
        em["To"] = b["from_email"]
        subj = b["subject"] or "Solicitud de factura"
        em["Subject"] = subj if subj.lower().startswith("re:") else "Re: " + subj
        if b.get("message_id_header"):
            em["In-Reply-To"] = b["message_id_header"]
            em["References"] = (
                b.get("references", "") + " " + b["message_id_header"]
            ).strip()
        em.set_content(text)
        raw = base64.urlsafe_b64encode(em.as_bytes()).decode()
        return (
            self.require()
            .users()
            .messages()
            .send(
                userId="me",
                body={"raw": raw, "threadId": original.get("threadId")},
            )
            .execute()
        )
