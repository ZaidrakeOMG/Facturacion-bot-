"""Monitor local de solo lectura para la cola de ARY.
No inicia Polaris, no crea otro consumidor, no escribe SQLite y no factura.
Usar solo en una red/VPN privada. Para trafico no confiable, poner HTTPS delante.
"""
from __future__ import annotations

import argparse
import base64
from contextlib import closing
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
from pathlib import Path
import secrets
import sqlite3
import time


ROOT = Path(__file__).resolve().parent
ASSETS = ROOT / "monitor_web"
PASSWORD_FILE = ROOT / "data" / "monitor_web_clave.txt"
DB = ROOT / "data" / "cola_operaciones.sqlite3"
USER = "Ary"


def preparar_clave(path=PASSWORD_FILE):
    """El archivo se crea solo la primera vez; nunca se sobreescribe."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="ascii") as out:
            out.write(secrets.token_urlsafe(32) + "\n")
        try:
            path.chmod(0o600)
        except OSError:
            pass
        return True
    except FileExistsError:
        return False


def obtener_clave(path=PASSWORD_FILE):
    path = Path(path)
    if not path.is_file():
        raise RuntimeError("Falta la clave. Ejecuta primero: python MONITOR_ARY_WEB.py --crear-clave")
    key = path.read_text(encoding="ascii").strip()
    if len(key) < 32:
        raise RuntimeError("La clave del monitor es demasiado corta.")
    return key


def verificar_clave(password, almacenada):
    """Acepta hashes locales PBKDF2 y claves aleatorias antiguas."""
    if almacenada.startswith("pbkdf2_sha256$"):
        try:
            algoritmo, iteraciones, salt, esperado = almacenada.split("$", 3)
            rondas = int(iteraciones)
            if not 100_000 <= rondas <= 500_000:
                return False
            salt_bytes = base64.urlsafe_b64decode(salt)
            esperado_bytes = base64.urlsafe_b64decode(esperado)
            if len(salt_bytes) != 16 or len(esperado_bytes) != 32:
                return False
            calculado = hashlib.pbkdf2_hmac(
                "sha256", password.encode("utf-8"), salt_bytes, rondas
            )
            return hmac.compare_digest(calculado, esperado_bytes)
        except (ValueError, UnicodeError):
            return False
    return hmac.compare_digest(password, almacenada)


def guardar_clave_interactiva(path):
    """Nunca acepta la clave como argumento ni la muestra en pantalla."""
    from getpass import getpass
    nueva = getpass("Nueva contrasena del monitor (no se mostrara): ")
    verificacion = getpass("Repite la contrasena: ")
    if not hmac.compare_digest(nueva, verificacion):
        raise ValueError("Las contrasenas no coinciden.")
    if len(nueva) < 6 or len(nueva) > 128:
        raise ValueError("La contrasena debe tener entre 6 y 128 caracteres.")
    if any(ord(c) < 33 or ord(c) == 127 for c in nueva):
        raise ValueError("La contrasena no debe contener espacios ni caracteres de control.")
    salt = secrets.token_bytes(16)
    rondas = 240_000
    digest = hashlib.pbkdf2_hmac("sha256", nueva.encode("utf-8"), salt, rondas)
    registro = "$".join((
        "pbkdf2_sha256", str(rondas),
        base64.urlsafe_b64encode(salt).decode("ascii"),
        base64.urlsafe_b64encode(digest).decode("ascii")
    ))
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Mantener las ACL del archivo preexistente instalado por Windows.
    with path.open("w", encoding="ascii") as output:
        output.write(registro + "\n")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    print("Contrasena local actualizada para usuario Ary. Reinicia SOLO la tarea del monitor.")


def instantanea(db_path=DB):
    """Leer con SQLite mode=ro; no se importa ColaLocal (su constructor escribe)."""
    p = Path(db_path)
    salida = {
        "disponible": False,
        "actualizado": int(time.time()),
        "pausada": None,
        "bloqueada": None,
        "activa": "",
        "conteos": {},
        "avisos": {},
        "ultima_actividad": None,
        "trabajos": [],
        "mensaje": "",
    }
    if not p.is_file():
        salida["mensaje"] = "La base de datos de la cola aun no existe."
        return salida
    try:
        with closing(sqlite3.connect(p.resolve().as_uri() + "?mode=ro", timeout=4, uri=True)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA query_only=ON")
            meta = dict(conn.execute("SELECT clave, valor FROM ajustes"))
            counts = conn.execute(
                "SELECT estado, COUNT(*) AS cantidad FROM trabajos "
                "WHERE archivado=0 GROUP BY estado"
            ).fetchall()
            items = conn.execute(
                "SELECT id, tipo, origen, seguro, estado, datos, creado, actualizado, "
                "iniciado, riesgo_cfdi FROM trabajos WHERE archivado=0 "
                "ORDER BY CASE WHEN estado='EJECUTANDO' THEN 0 "
                "WHEN estado IN ('PREPARADA_ALTA','PREPARADA_FACTURA',"
                "'REVISION_REQUERIDA','ACEPTAR_ALTA') THEN 1 "
                "WHEN estado='EN_COLA' THEN 2 ELSE 3 END, "
                "CASE WHEN estado='EN_COLA' THEN creado ELSE -creado END, id LIMIT 100"
            ).fetchall()
            notices = conn.execute(
                "SELECT a.estado, COUNT(*) AS cantidad FROM avisos a "
                "JOIN trabajos t ON t.id=a.trabajo AND t.archivado=0 "
                "GROUP BY a.estado"
            ).fetchall()
            last = conn.execute(
                "SELECT MAX(actualizado) FROM trabajos WHERE archivado=0"
            ).fetchone()[0]
            salida["pausada"] = meta.get("pausada", "1") == "1"
            salida["bloqueada"] = bool(meta.get("bloqueo", ""))
            salida["conteos"] = {r["estado"]: r["cantidad"] for r in counts}
            salida["avisos"] = {r["estado"]: r["cantidad"] for r in notices}
            salida["ultima_actividad"] = last
            salida["activa"] = next((r["id"][:12] for r in items if r["estado"] == "EJECUTANDO"), "")
            for r in items:
                try:
                    fields = json.loads(r["datos"])
                    if not isinstance(fields, dict):
                        fields = {}
                except (TypeError, ValueError):
                    fields = {}
                ticket = str(fields.get("ticket") or "")
                salida["trabajos"].append({
                    "id": r["id"][:12].upper(),
                    "tipo": r["tipo"],
                    "estacion": str(fields.get("estacion") or "—")[:48],
                    "origen": r["origen"][:32],
                    "modo": "Prueba" if r["seguro"] else "Real",
                    "estado": r["estado"],
                    "folio": ("••••" + ticket[-4:]) if ticket else "",
                    "riesgo_cfdi": bool(r["riesgo_cfdi"]),
                    "creado": r["creado"],
                    "actualizado": r["actualizado"],
                })
            salida["disponible"] = True
    except (sqlite3.Error, OSError, ValueError):
        salida["mensaje"] = "No fue posible leer la cola en este momento."
    return salida


class Handler(BaseHTTPRequestHandler):
    server_version = "ARY-Monitor"
    sys_version = ""

    def _responder(self, status, body, content_type):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; script-src 'self'; style-src 'self'; "
            "connect-src 'self'; form-action 'none'; frame-ancestors 'none'; base-uri 'none'"
        )
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status, message):
        self._responder(status, json.dumps({"error": message}).encode("utf-8"),
                        "application/json; charset=utf-8")

    def do_GET(self):
        try:
            ip = ipaddress.ip_address(self.client_address[0])
        except ValueError:
            return self._error(403, "Acceso no permitido.")
        if not any(ip in net for net in self.server.redes_permitidas):
            return self._error(403, "Acceso fuera de la red autorizada.")
        if not self._autorizado():
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="ARY Monitor Privado", charset="UTF-8"')
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.path == "/api/estado":
            body = json.dumps(instantanea(self.server.base / "data" / "cola_operaciones.sqlite3"),
                              ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            return self._responder(200, body, "application/json; charset=utf-8")
        assets = {
            "/": ("index.html", "text/html; charset=utf-8"),
            "/monitor.css": ("monitor.css", "text/css; charset=utf-8"),
            "/monitor.js": ("monitor.js", "text/javascript; charset=utf-8"),
        }
        if self.path not in assets:
            return self._error(404, "Ruta no disponible.")
        filename, mime = assets[self.path]
        try:
            body = (self.server.base / "monitor_web" / filename).read_bytes()
        except OSError:
            return self._error(503, "Falta un archivo del monitor.")
        self._responder(200, body, mime)

    def _autorizado(self):
        auth = self.headers.get("Authorization", "")
        if not auth.startswith("Basic ") or len(auth) > 256:
            return False
        try:
            raw = base64.b64decode(auth[6:], validate=True).decode("utf-8")
            user, separator, pwd = raw.partition(":")
            return bool(separator) and hmac.compare_digest(user, USER) and verificar_clave(pwd, self.server.clave)
        except (ValueError, UnicodeError, TypeError):
            return False

    def do_POST(self):
        self._error(405, "Monitor de solo lectura.")

    def do_PUT(self):
        self._error(405, "Monitor de solo lectura.")

    def do_DELETE(self):
        self._error(405, "Monitor de solo lectura.")

    def log_message(self, fmt, *args):
        # No registrar tokens, query strings ni informacion del cliente.
        pass


class Server(ThreadingHTTPServer):
    daemon_threads = True


def main():
    parser = argparse.ArgumentParser(description="Monitor ARY: lectura de cola sin controlar Polaris.")
    parser.add_argument("--base", default=str(ROOT), help="Carpeta raiz del bot ARY.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--red", action="append", default=None,
                        help="Red de origen permitida, por ejemplo 10.20.0.0/16.")
    parser.add_argument("--crear-clave", action="store_true")
    parser.add_argument("--cambiar-clave", action="store_true", help="Pedir nueva contrasena sin mostrarla ni guardarla en GitHub.")
    args = parser.parse_args()
    base = Path(args.base).resolve()
    clave_path = base / "data" / "monitor_web_clave.txt"
    if args.crear_clave:
        nuevo = preparar_clave(clave_path)
        print(("Clave creada" if nuevo else "Clave existente") + ": " + str(clave_path))
        return
    if args.cambiar_clave:
        guardar_clave_interactiva(clave_path)
        return
    if not 1024 <= args.port <= 65535:
        parser.error("El puerto debe estar entre 1024 y 65535.")
    networks = [ipaddress.ip_network(n, strict=False)
                for n in (args.red or ["10.20.0.0/16", "127.0.0.0/8"])]
    if not (base / "monitor_web" / "index.html").exists():
        parser.error("Falta monitor_web/index.html.")
    key = obtener_clave(clave_path)
    with Server((args.host, args.port), Handler) as server:
        server.base = base
        server.clave = key
        server.redes_permitidas = networks
        print("ARY Monitor solo lectura: " + args.host + ":" + str(args.port), flush=True)
        server.serve_forever(poll_interval=0.5)


if __name__ == "__main__":
    main()
