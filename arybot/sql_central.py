"""SQL Server mirror for the independent PolarisBot database.

No access to commercial Polaris DBs. SQLite remains operational authority.
Only fixed SQL statements; never expose credentials or customer data to Groq.
"""
from __future__ import annotations

import json
import re
import sqlite3
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .parser import STATIONS, USO_CFDI, _plain, normalize_ticket_date

DATABASE = "PolarisBot"
APP_USER = "POLARISBOT_APP"
SERVICE = "ARY-PolarisBot-SQL"
CODES = {name: ("APACHE" if name == "SERVICIO APACHE" else name.replace(" ", "_")) for name in STATIONS}
STATES = frozenset(("EN_COLA","EJECUTANDO","PREPARADA_FACTURA","PREPARADA_ALTA",
    "PRUEBA_PENDIENTE","ACEPTAR_ALTA","ALTA_CONFIRMADA","ENVIO_SOLICITADO",
    "YA_FACTURADO","REVISION_REQUERIDA","REVISION_ENTRADA","ESPERANDO_DATOS",
    "DUPLICADA","CANCELADA","PRUEBA_CERRADA","REVISION_CERRADA","FINALIZADA"))
PAYMENTS = frozenset(("EFECTIVO","CHEQUE NOMINATIVO","TRANSFERENCIA ELECTRONICA DE FONDOS",
    "TARJETA DE CREDITO","TARJETA DE DEBITO","MONEDERO ELECTRONICO","DINERO ELECTRONICO",
    "VALES DE DESPENSA","DACION EN PAGO","PAGO POR SUBROGACION","PAGO POR CONSIGNACION",
    "CONDONACION","COMPENSACION","NOVACION","CONFUSION","REMISION DE DEUDA",
    "PRESCRIPCION O CADUCIDAD","A SATISFACCION DEL ACREEDOR","TARJETA DE SERVICIOS",
    "POR DEFINIR","APLICACION DE ANTICIPOS","INTERMEDIARIO PAGOS"))
REQUIRED_TABLES = frozenset(("BotEsquema","Estaciones","ConexionesPolaris","Solicitudes",
    "Facturas","EventosSolicitud","ConsultasIA","AlertasBot","ConfiguracionBot"))


class SqlError(RuntimeError):
    pass


@dataclass(frozen=True)
class Settings:
    server: str
    port: int = 1433
    user: str = APP_USER

    def validate(self):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]{0,252}", self.server):
            raise SqlError("Introduce la IP o el nombre DNS del servidor, sin instancia ni prefijos.")
        if type(self.port) is not int or not 1 <= self.port <= 65535:
            raise SqlError("Puerto TCP inválido.")
        if not re.fullmatch(r"[A-Za-z0-9_.@-]{1,128}", self.user):
            raise SqlError("Usuario SQL inválido.")
        return self

    @property
    def credential(self):
        return self.server.lower() + ":" + str(self.port) + "/" + self.user.lower()


def _vault():
    from keyring.backends.Windows import WinVaultKeyring
    return WinVaultKeyring()


def get_password(settings):
    try:
        return _vault().get_password(SERVICE, settings.credential) or ""
    except Exception:
        return ""


def config_path(base):
    return Path(base) / "data" / "sql_central.json"


def load_settings(base):
    try:
        raw = json.loads(config_path(base).read_text(encoding="utf-8"))
        return Settings(raw["server"], raw["port"], raw["user"]).validate()
    except (OSError, ValueError, KeyError, TypeError, SqlError):
        return Settings("")


def save_settings(base, settings, password):
    settings.validate()
    if settings.user.casefold() != APP_USER.casefold():
        raise SqlError("Guarda únicamente la cuenta limitada POLARISBOT_APP; sa solo sirve para una prueba puntual.")
    if not password:
        raise SqlError("Introduce la contraseña de la cuenta limitada.")
    try:
        _vault().set_password(SERVICE, settings.credential, password)
        path = config_path(base)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(dict(server=settings.server, port=settings.port, user=settings.user), indent=2), encoding="utf-8")
        tmp.replace(path)
    except Exception:
        raise SqlError("No se pudo guardar la configuración en Windows.") from None


def _driver():
    try:
        import pyodbc
    except ImportError:
        raise SqlError("Instala pyodbc en el entorno Python del bot.") from None
    if "ODBC Driver 18 for SQL Server" not in pyodbc.drivers():
        raise SqlError("Instala Microsoft ODBC Driver 18 for SQL Server.")
    return pyodbc


def connection_string(settings, password):
    settings.validate()
    if not password or "\x00" in password:
        raise SqlError("Introduce una contraseña SQL.")
    # Brace escape prevents passwords from injecting additional ODBC attributes.
    pw = "{" + password.replace("}", "}}") + "}"
    return ("DRIVER={ODBC Driver 18 for SQL Server};"
            f"SERVER=tcp:{settings.server},{settings.port};DATABASE={DATABASE};"
            f"UID={settings.user};PWD={pw};Encrypt=yes;TrustServerCertificate=no;")


def friendly_error(exc):
    # Do not display driver strings, connection strings, queries or row values.
    if isinstance(exc, SqlError):
        return str(exc)
    code = str(exc.args[0]) if getattr(exc, "args", ()) else ""
    if code == "28000":
        return "SQL rechazó la autenticación. Revisa el usuario y la contraseña."
    if code in ("08001", "08004", "08S01", "HYT00", "HYT01"):
        return "No se pudo conectar: comprueba TCP, puerto y certificado TLS confiable."
    if code == "42000":
        return "SQL rechazó el acceso. Revisa permisos y esquema."
    return "La operación SQL no se completó. SQLite conserva el historial local."


@contextmanager
def connect(settings, password):
    driver = _driver()
    try:
        with closing(driver.connect(connection_string(settings, password), timeout=5, autocommit=False)) as conn:
            conn.timeout = 15
            yield conn
    except SqlError:
        raise
    except driver.Error as exc:
        raise SqlError(friendly_error(exc)) from None


def verify(cursor, *, write=False):
    row = cursor.execute("""SELECT DB_NAME(), CONVERT(nvarchar(128),SERVERPROPERTY('ProductVersion')),
        IS_SRVROLEMEMBER('sysadmin'), IS_MEMBER('db_owner'), IS_MEMBER('PolarisBot_RolAplicacion'),
        ORIGINAL_LOGIN()""").fetchone()
    if not row or row[0] != DATABASE:
        raise SqlError("La conexión no corresponde a PolarisBot.")
    if write and (row[2] != 0 or row[3] != 0 or row[4] != 1 or str(row[5]).casefold() != APP_USER.casefold()):
        raise SqlError("Sincronización bloqueada: utiliza POLARISBOT_APP con su rol limitado, no sa.")
    tables = {r[0] for r in cursor.execute("SELECT name FROM sys.tables WHERE schema_id=SCHEMA_ID('dbo')")}
    if not REQUIRED_TABLES <= tables:
        raise SqlError("Faltan tablas del esquema PolarisBot o permisos para verlas.")
    if not cursor.execute("SELECT 1 FROM dbo.BotEsquema WHERE Version=1").fetchone():
        raise SqlError("Versión del esquema PolarisBot incompatible.")
    return str(row[1]), bool(row[2] or row[3])


def check(settings, password):
    with connect(settings, password) as conn:
        cur = conn.cursor()
        version, admin = verify(cur)
        stations = cur.execute("SELECT COUNT(*) FROM dbo.Estaciones").fetchone()[0]
        jobs = cur.execute("SELECT COUNT_BIG(*) FROM dbo.Solicitudes").fetchone()[0]
        verified = cur.execute("SELECT COUNT_BIG(*) FROM dbo.v_FacturasVerificadas").fetchone()[0]
        conn.rollback()
        return dict(version=version, admin=admin, stations=stations, jobs=jobs, verified=verified)


def utc(value):
    return datetime.fromtimestamp(float(value), timezone.utc).replace(tzinfo=None) if value is not None else None


def to_record(row):
    """Mirror allowlisted operational fields only, never RFC, name, email or raw errors."""
    try:
        data = json.loads(row["datos"])
        if not isinstance(data, dict):
            return None
        station = CODES.get(data.get("estacion"))
        kind, state = row["tipo"], row["estado"]
        ident = str(row["id"])
        if not station or kind not in ("FACTURA", "ALTA") or state not in STATES:
            return None
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", ident):
            return None
        origin = str(row["origen"]).casefold()
        source = ("PORTAL" if "portal" in origin or origin == "web" else
                  "GMAIL_HISTORICO" if "gmail" in origin else
                  "MANUAL" if origin in ("manual", "formulario") else "OTRO")
        folio = str(data.get("ticket") or "") if kind == "FACTURA" else ""
        folio = folio if re.fullmatch(r"[0-9]{1,20}", folio) else None
        date = normalize_ticket_date(data.get("fecha_ticket")) or None if kind == "FACTURA" else None
        pay = _plain(str(data.get("forma_pago") or "")) if kind == "FACTURA" else ""
        pay = pay if pay in PAYMENTS else None
        usage = _plain(str(data.get("uso_cfdi") or ""))
        cfdi = next((code for code, desc in USO_CFDI.items() if usage in (code, _plain(desc))), None) if kind == "FACTURA" and usage else None
        started, ended = utc(row["iniciado"]), utc(row["terminado"])
        if started and ended and ended < started:
            return None
        return (ident, source, kind, station, folio, date, pay, cfdi, bool(row["seguro"]), state,
                utc(row["creado"]), utc(row["actualizado"]), started, ended)
    except (KeyError, TypeError, ValueError, OverflowError, OSError):
        return None


def read_local(path):
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=2)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        return [dict(r) for r in conn.execute("""SELECT id,tipo,origen,seguro,estado,datos,creado,actualizado,iniciado,terminado
                    FROM trabajos WHERE tipo IN ('FACTURA','ALTA') ORDER BY creado,id""")]


# Fixed statements and bound parameters; the filtered unique index on IdLocal
# plus UPDLOCK/HOLDLOCK serializes inserts of the same local work ID.
UPSERT = """
SET NOCOUNT ON;
DECLARE @IdLocal varchar(64)=?, @Origen varchar(30)=?, @Tipo varchar(12)=?,
 @Codigo varchar(30)=?, @Folio varchar(20)=?, @Fecha date=?, @Pago varchar(60)=?,
 @CFDI varchar(10)=?, @Seguro bit=?, @Estado varchar(35)=?, @Creado datetime2(3)=?,
 @Actualizado datetime2(3)=?, @Iniciado datetime2(3)=?, @Terminado datetime2(3)=?;
DECLARE @Estacion int=(SELECT EstacionID FROM dbo.Estaciones WHERE Codigo=@Codigo);
IF @Estacion IS NULL THROW 50021,'Estacion no registrada',1;
IF EXISTS(SELECT 1 FROM dbo.Solicitudes WITH (UPDLOCK,HOLDLOCK) WHERE IdLocal=@IdLocal)
BEGIN
 IF EXISTS(SELECT 1 FROM dbo.Solicitudes WHERE IdLocal=@IdLocal AND (EstacionID<>@Estacion OR Tipo<>@Tipo))
  THROW 50022,'Conflicto de identidad de solicitud',1;
 UPDATE dbo.Solicitudes SET Estado=@Estado,ActualizadoUTC=@Actualizado,IniciadoUTC=@Iniciado,TerminadoUTC=@Terminado
 WHERE IdLocal=@IdLocal AND ActualizadoUTC<@Actualizado AND EstacionID=@Estacion AND Tipo=@Tipo;
END
ELSE
 INSERT dbo.Solicitudes(IdLocal,Origen,Tipo,EstacionID,Folio,FechaTicket,FormaPago,UsoCFDI,ModoSeguro,
 Estado,CreadoUTC,ActualizadoUTC,IniciadoUTC,TerminadoUTC)
 VALUES(@IdLocal,@Origen,@Tipo,@Estacion,@Folio,@Fecha,@Pago,@CFDI,@Seguro,@Estado,@Creado,@Actualizado,@Iniciado,@Terminado);
"""


def synchronize(settings, password, local_path):
    if settings.user.casefold() != APP_USER.casefold():
        raise SqlError("La sincronización requiere la cuenta POLARISBOT_APP.")
    try:
        rows = read_local(local_path)
    except (sqlite3.Error, OSError):
        raise SqlError("No se pudo leer el historial SQLite.") from None
    records = [r for row in rows if (r := to_record(row)) is not None]
    with connect(settings, password) as conn:
        cur = conn.cursor()
        verify(cur, write=True)
        try:
            cur.execute("SET XACT_ABORT ON")
            for record in records:
                cur.execute(UPSERT, *record)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return dict(processed=len(records), skipped=len(rows)-len(records))
