from __future__ import annotations
from dataclasses import dataclass, asdict
import re
import unicodedata
from datetime import date, datetime
from pathlib import Path

RFC_RE = re.compile(r"\b([A-ZÑ&]{3,4}\d{6}[A-Z0-9]{3})\b", re.I)
EMAIL_RE = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")

# Descripciones tal como se muestran en Polaris / catálogo SAT.
USO_CFDI = {
    "G01": "ADQUISICIÓN DE MERCANCÍAS.",
    "G02": "DEVOLUCIONES, DESCUENTOS O BONIFICACIONES.",
    "G03": "GASTOS EN GENERAL.",
    "I01": "CONSTRUCCIONES.",
    "I02": "MOBILIARIO Y EQUIPO DE OFICINA POR INVERSIONES.",
    "I03": "EQUIPO DE TRANSPORTE.",
    "I04": "EQUIPO DE CÓMPUTO Y ACCESORIOS.",
    "I05": "DADOS, TROQUELES, MOLDES, MATRICES Y HERRAMENTAL.",
    "I06": "COMUNICACIONES TELEFÓNICAS.",
    "I07": "COMUNICACIONES SATELITALES.",
    "I08": "OTRA MAQUINARIA Y EQUIPO.",
    "D01": "HONORARIOS MÉDICOS, DENTALES Y GASTOS HOSPITALARIOS.",
    "D02": "GASTOS MÉDICOS POR INCAPACIDAD O DISCAPACIDAD.",
    "D03": "GASTOS FUNERALES.",
    "D04": "DONATIVOS.",
    "D05": "INTERESES REALES EFECTIVAMENTE PAGADOS POR CRÉDITOS HIPOTECARIOS.",
    "D06": "APORTACIONES VOLUNTARIAS AL SAR.",
    "D07": "PRIMAS POR SEGUROS DE GASTOS MÉDICOS.",
    "D08": "GASTOS DE TRANSPORTACIÓN ESCOLAR OBLIGATORIA.",
    "D09": "DEPÓSITOS EN CUENTAS PARA EL AHORRO Y PLANES DE PENSIONES.",
    "D10": "PAGOS POR SERVICIOS EDUCATIVOS (COLEGIATURAS).",
    "S01": "SIN EFECTOS FISCALES.",
    "CP01": "PAGOS.",
    "CN01": "NÓMINA.",
    # Polaris del video todavía muestra esta opción.
    "P01": "POR DEFINIR",
}


def _plain(value: str) -> str:
    s = unicodedata.normalize("NFKD", value or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^A-Z0-9]+", " ", s.upper()).strip()
    return re.sub(r"\s+", " ", s)


def normalize_cfdi_usage(value: str) -> str:
    """Normaliza Uso CFDI. Si viene vacío, ARY usa GASTOS EN GENERAL (G03)."""
    raw=(value or "").strip()
    if not raw:
        return USO_CFDI["G03"]
    code=raw.upper().replace(" ", "")
    if code in USO_CFDI:
        return USO_CFDI[code]
    p=_plain(raw)
    # Acepta descripciones sin acentos/puntos y las lleva al texto canónico.
    for desc in USO_CFDI.values():
        if p == _plain(desc):
            return desc
    # Alias comunes.
    aliases={
        "GASTOS": USO_CFDI["G03"],
        "GASTOS GENERALES": USO_CFDI["G03"],
        "GASTOS EN GENERAL": USO_CFDI["G03"],
        "POR DEFINIR": USO_CFDI["P01"],
        "SIN EFECTOS FISCALES": USO_CFDI["S01"],
        "PAGOS": USO_CFDI["CP01"],
    }
    return aliases.get(p, raw.upper())


# Estaciones observadas en Polaris (video 25/09/2026).
# El número es el identificador usado en Utilerías -> Cambio de estación.
STATIONS = {
    "ARY I": {"numero":"6953", "polaris":"ARY SUPER SERVICIOS I, S.A. DE C.V.", "aliases":["ARY I","ARY 1","ARY1","ARY UNO","ARY SUPER SERVICIOS I"]},
    "ARY II": {"numero":"4376", "polaris":"ARY SUPER SERVICIOS II, S.A. DE C.V.", "aliases":["ARY II","ARY 2","ARY2","ARY DOS","ARY SUPER SERVICIOS II"]},
    "ARY III": {"numero":"4837", "polaris":"ARY SUPER SERVICIOS III, S.A. DE C.V.", "aliases":["ARY III","ARY 3","ARY3","ARY TRES","ARY SUPER SERVICIOS III"]},
    "ARY IV": {"numero":"5522", "polaris":"ARY SUPER SERVICIOS IV, S.A. DE C.V.", "aliases":["ARY IV","ARY 4","ARY4","ARY CUATRO","ARY SUPER SERVICIOS IV"]},
    "SERVICIO APACHE": {"numero":"6086", "polaris":"SERVICIO APACHE, SA DE CV", "aliases":["SERVICIO APACHE","APACHE","ESTACION APACHE"]},
    "ARY V": {"numero":"9427", "polaris":"ARY SUPER EXPRESS V, S.A. DE C.V.", "aliases":["ARY V","ARY 5","ARY5","ARY CINCO","ARY SUPER EXPRESS V"]},
    "ARY VI": {"numero":"11507", "polaris":"ARY SUPER SERVICIOS VI, SA DE CV", "aliases":["ARY VI","ARY 6","ARY6","ARY SEIS","ARY SUPER SERVICIOS VI"]},
}

STATION_CHOICES = list(STATIONS.keys())

def normalize_station(value: str) -> str:
    raw=(value or "").strip()
    if not raw:
        return ""
    p=_plain(raw)
    digits=re.sub(r"\D", "", raw)
    for key,info in STATIONS.items():
        if digits and digits == info["numero"]:
            return key
        for candidate in [key, info["polaris"], *info.get("aliases",[])]:
            if p == _plain(candidate):
                return key
    for key,info in STATIONS.items():
        for candidate in [info["polaris"], *info.get("aliases",[])]:
            pc=_plain(candidate)
            if len(pc)>=5 and re.search(rf"(?:^| )"+re.escape(pc)+r"(?: |$)", p):
                return key
    return ""

def detect_station(text: str) -> str:
    t=_plain(text or "")
    if not t:
        return ""
    matches=[]
    for key,info in STATIONS.items():
        for candidate in [info["polaris"], *info.get("aliases",[])]:
            pc=_plain(candidate)
            if len(pc)>=5 and re.search(rf"(?:^| )"+re.escape(pc)+r"(?: |$)", t):
                matches.append((len(pc),key))
    if matches:
        matches.sort(reverse=True)
        return matches[0][1]
    return ""


def normalize_ticket_date(value: str) -> str:
    """Normaliza fecha de ticket a YYYY-MM-DD. Acepta YYYY-MM-DD o DD/MM/YYYY."""
    raw=str(value or '').strip()
    if not raw:
        return ''
    parsed=None
    for fmt in ('%Y-%m-%d','%d/%m/%Y','%d-%m-%Y'):
        try:
            parsed=datetime.strptime(raw,fmt).date(); break
        except ValueError:
            pass
    if parsed is None:
        raise ValueError('La fecha del ticket debe tener formato DD/MM/AAAA.')
    if parsed > date.today():
        raise ValueError('La fecha del ticket no puede ser futura.')
    return parsed.isoformat()


def ticket_date_display(value: str) -> str:
    iso=normalize_ticket_date(value)
    if not iso:
        return ''
    return datetime.strptime(iso,'%Y-%m-%d').strftime('%d/%m/%Y')


@dataclass
class Solicitud:
    estacion: str = ""
    razon_social: str = ""
    # RFC se conserva si viene en correo/constancia, pero NO es obligatorio y
    # NO se captura en Polaris: al elegir el cliente Polaris lo llena solo.
    rfc: str = ""
    ticket: str = ""
    fecha_ticket: str = ""
    forma_pago: str = "EFECTIVO"
    # Regla ARY: si el cliente no manda Uso CFDI, usar GASTOS EN GENERAL.
    uso_cfdi: str = USO_CFDI["G03"]
    codigo_postal: str = ""
    regimen_fiscal: str = ""
    correo_destino: str = ""
    foto_ticket: Path | None = None
    constancia_pdf: Path | None = None
    remitente: str = ""

    def missing(self):
        out = []
        if not self.estacion.strip(): out.append("Estación/Sucursal")
        # v2.6: Polaris busca al cliente por RFC, no por nombre.
        # Si adjuntan Constancia, processor.py intenta obtener el RFC de ahí.
        if not self.rfc.strip(): out.append("RFC o Constancia de Situación Fiscal")
        if not self.ticket.strip(): out.append("Ticket/Folio o foto legible del ticket")
        if not self.fecha_ticket.strip(): out.append("Fecha del ticket")
        if not self.forma_pago.strip(): out.append("Forma de pago elegida por el cliente")
        # En Gmail el remitente cuenta como correo destino; no se obliga a
        # escribir el correo dentro del mensaje si ya llegó desde esa dirección.
        if not (self.correo_destino or self.remitente).strip(): out.append("Correo para recibir la factura")
        return out

    def to_dict(self): return asdict(self)


def _field(text: str, labels: list[str]):
    for label in labels:
        m = re.search(rf"(?im)^\s*{label}\s*[:\-]\s*(.+?)\s*$", text)
        if m: return m.group(1).strip()
    return ""


def parse_body(text: str, sender_email="") -> Solicitud:
    text = text or ""
    estacion_raw = _field(text, [r"estaci[oó]n", r"sucursal", r"gasolinera"])
    estacion = normalize_station(estacion_raw) or detect_station(text)
    rs = _field(text, [r"raz[oó]n\s+social", r"nombre(?:\s+fiscal)?", r"cliente"])
    rfc = _field(text, [r"r\.?f\.?c\.?"])
    rfc = (rfc or "").upper().replace(" ", "")
    if not rfc:
        m = RFC_RE.search(text.upper()); rfc = m.group(1).upper() if m else ""
    ticket = _field(text, [r"folio\s+del\s+ticket", r"ticket", r"folio"])
    if ticket:
        ticket = re.sub(r"[^A-Za-z0-9_-]", "", ticket)
    fecha_raw = _field(text, [r"fecha\s+(?:del\s+)?ticket", r"fecha\s+ticket"])
    try:
        fecha_ticket = normalize_ticket_date(fecha_raw) if fecha_raw else ""
    except ValueError:
        fecha_ticket = fecha_raw.strip()
    fp = _field(text, [r"forma\s+de\s+pago", r"pago"])
    uso = normalize_cfdi_usage(_field(text, [r"uso\s+(?:de\s+)?cfdi", r"uso\s+cfdi", r"cfdi"]))
    cp = _field(text, [r"c[oó]digo\s+postal(?:\s+fiscal)?", r"c\.?p\.?"])
    reg = _field(text, [r"r[eé]gimen\s+fiscal", r"regimen\s+fiscal"])
    em = _field(text, [r"correo(?:\s+para\s+recibir\s+la\s+factura)?", r"email", r"e-mail"])
    if not em:
        found = EMAIL_RE.findall(text)
        em = found[0] if found else sender_email
    return Solicitud(
        estacion=estacion,
        razon_social=rs,
        rfc=rfc,
        ticket=ticket,
        fecha_ticket=fecha_ticket,
        forma_pago=normalize_payment(fp) if fp else "",
        uso_cfdi=uso,
        codigo_postal=cp,
        regimen_fiscal=reg,
        correo_destino=em,
        remitente=sender_email,
    )


def normalize_payment(value: str) -> str:
    s = re.sub(r"\s+", " ", (value or "").strip()).upper()
    aliases = {
        "CASH": "EFECTIVO",
        "TARJETA": "TARJETA DE CREDITO",
        "TARJETA CREDITO": "TARJETA DE CREDITO",
        "TARJETA DE CRÉDITO": "TARJETA DE CREDITO",
        "CREDITO": "TARJETA DE CREDITO",
        "CRÉDITO": "TARJETA DE CREDITO",
        "DEBITO": "TARJETA DE DEBITO",
        "DÉBITO": "TARJETA DE DEBITO",
        "TARJETA DE DÉBITO": "TARJETA DE DEBITO",
        "TRANSFERENCIA ELECTRÓNICA": "TRANSFERENCIA ELECTRONICA DE FONDOS",
        "TRANSFERENCIA ELECTRÓNICA DE FONDOS": "TRANSFERENCIA ELECTRONICA DE FONDOS",
    }
    return aliases.get(s, s or "EFECTIVO")


def classify_attachments(paths):
    imgs, pdfs = [], []
    for p in paths:
        ext = p.suffix.lower()
        if ext in {".jpg",".jpeg",".png",".webp",".bmp",".tif",".tiff",".heic"}: imgs.append(p)
        elif ext == ".pdf": pdfs.append(p)
    return imgs, pdfs
