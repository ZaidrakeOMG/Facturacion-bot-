from __future__ import annotations
from pathlib import Path
import sqlite3
import threading
from datetime import datetime

SCHEMA = """
CREATE TABLE IF NOT EXISTS solicitudes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  gmail_id TEXT UNIQUE,
  thread_id TEXT,
  remitente TEXT,
  asunto TEXT,
  razon_social TEXT,
  estacion TEXT,
  rfc TEXT,
  ticket TEXT,
  forma_pago TEXT,
  estado TEXT NOT NULL,
  detalle TEXT,
  creado_en TEXT NOT NULL,
  actualizado_en TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (
  clave TEXT PRIMARY KEY,
  valor TEXT
);
"""


class DB:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with self._conn() as c:
            c.executescript(SCHEMA)
            # Migración compatible con bases creadas por v2.5 o anteriores.
            cols={row[1] for row in c.execute("PRAGMA table_info(solicitudes)")}
            if "estacion" not in cols:
                c.execute("ALTER TABLE solicitudes ADD COLUMN estacion TEXT")

    def _conn(self):
        return sqlite3.connect(self.path, timeout=30)

    def existe(self, gmail_id: str) -> bool:
        with self._lock, self._conn() as c:
            return c.execute("SELECT 1 FROM solicitudes WHERE gmail_id=?", (gmail_id,)).fetchone() is not None

    def crear(self, gmail_id: str, thread_id: str, remitente: str, asunto: str):
        now = datetime.now().isoformat(timespec="seconds")
        with self._lock, self._conn() as c:
            c.execute("""INSERT OR IGNORE INTO solicitudes
              (gmail_id,thread_id,remitente,asunto,estado,creado_en,actualizado_en)
              VALUES(?,?,?,?,?,?,?)""", (gmail_id, thread_id, remitente, asunto, "RECIBIDO", now, now))

    def actualizar(self, gmail_id: str, **fields):
        if not fields: return
        fields["actualizado_en"] = datetime.now().isoformat(timespec="seconds")
        cols = ",".join(f"{k}=?" for k in fields)
        vals = list(fields.values()) + [gmail_id]
        with self._lock, self._conn() as c:
            c.execute(f"UPDATE solicitudes SET {cols} WHERE gmail_id=?", vals)

    def meta_get(self, key: str, default=None):
        with self._lock, self._conn() as c:
            row = c.execute("SELECT valor FROM meta WHERE clave=?", (key,)).fetchone()
        return row[0] if row else default

    def meta_set(self, key: str, value):
        with self._lock, self._conn() as c:
            c.execute("INSERT INTO meta(clave,valor) VALUES(?,?) ON CONFLICT(clave) DO UPDATE SET valor=excluded.valor", (key, str(value)))

    def ultimas(self, limit=30):
        with self._lock, self._conn() as c:
            return c.execute("SELECT creado_en,remitente,asunto,ticket,estado,detalle FROM solicitudes ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
