"""Persistencia de trabajos y avisos en SQLite LOCAL. No abre SQL Server/Polaris."""
from __future__ import annotations
from contextlib import contextmanager
from pathlib import Path
import json
import sqlite3
import time
import uuid
from .cola_modelo import BLOQUEAN_CLAVE, ESTADOS_REVISION, SolicitudDuplicada, ColaError

SCHEMA = '''
CREATE TABLE IF NOT EXISTS trabajos (
 id TEXT PRIMARY KEY, fuente_id TEXT UNIQUE, clave TEXT, tipo TEXT NOT NULL,
 origen TEXT NOT NULL, seguro INTEGER NOT NULL, estado TEXT NOT NULL,
 datos TEXT NOT NULL, correo TEXT NOT NULL DEFAULT '', motivo TEXT NOT NULL DEFAULT '',
 creado REAL NOT NULL, actualizado REAL NOT NULL, iniciado REAL, terminado REAL,
 resultado TEXT NOT NULL DEFAULT '{}', minutos INTEGER NOT NULL DEFAULT 5);
CREATE INDEX IF NOT EXISTS ix_cola_estado ON trabajos(estado,creado);
CREATE INDEX IF NOT EXISTS ix_cola_clave ON trabajos(clave);
CREATE TABLE IF NOT EXISTS ajustes (clave TEXT PRIMARY KEY, valor TEXT);
CREATE TABLE IF NOT EXISTS avisos (
 id TEXT PRIMARY KEY, trabajo TEXT NOT NULL, evento TEXT NOT NULL, correo TEXT NOT NULL,
 asunto TEXT NOT NULL, cuerpo TEXT NOT NULL, estado TEXT NOT NULL, creado REAL NOT NULL,
 actualizado REAL NOT NULL, gmail_id TEXT NOT NULL DEFAULT '', detalle TEXT NOT NULL DEFAULT '',
 UNIQUE(trabajo,evento));
'''

class ColaLocal:
    def __init__(self, base):
        self.path = Path(base)/'data'/'cola_operaciones.sqlite3'
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.connect(True) as conn:
            conn.executescript(SCHEMA)
            cols = {r[1] for r in conn.execute('PRAGMA table_info(avisos)')}
            if 'proximo_intento' not in cols:
                conn.execute('ALTER TABLE avisos ADD COLUMN proximo_intento REAL NOT NULL DEFAULT 0')
            if 'intentos' not in cols:
                conn.execute('ALTER TABLE avisos ADD COLUMN intentos INTEGER NOT NULL DEFAULT 0')

    @contextmanager
    def connect(self, write=False):
        c=sqlite3.connect(self.path,timeout=15)
        c.row_factory=sqlite3.Row
        try:
            if write: c.execute('BEGIN IMMEDIATE')
            yield c
            c.commit()
        except Exception:
            c.rollback(); raise
        finally: c.close()

    @staticmethod
    def unpack(row):
        if row is None:return None
        d=dict(row); d['datos']=json.loads(d['datos']);d['resultado']=json.loads(d['resultado'])
        d['seguro']=bool(d['seguro']);return d

    def get(self, ident):
        with self.connect() as c:return self.unpack(c.execute('SELECT * FROM trabajos WHERE id=?',(ident,)).fetchone())

    def source(self, source_id):
        with self.connect() as c:return self.unpack(c.execute('SELECT * FROM trabajos WHERE fuente_id=?',(source_id,)).fetchone())

    def list(self, limit=200):
        with self.connect() as c:return [self.unpack(r) for r in c.execute("""SELECT * FROM trabajos
            ORDER BY CASE WHEN estado='EJECUTANDO' THEN 0
                          WHEN estado IN ('PREPARADA_ALTA','PREPARADA_FACTURA','REVISION_REQUERIDA','PRUEBA_PENDIENTE','ACEPTAR_ALTA') THEN 1
                          WHEN estado='EN_COLA' THEN 2 ELSE 3 END,
                     CASE WHEN estado='EN_COLA' THEN creado ELSE -creado END, id
            LIMIT ?""",(limit,))]

    def meta(self,key,default=''):
        with self.connect() as c:
            r=c.execute('SELECT valor FROM ajustes WHERE clave=?',(key,)).fetchone()
            return r[0] if r else default

    @staticmethod
    def _set(c,key,val):
        c.execute('INSERT INTO ajustes(clave,valor) VALUES(?,?) ON CONFLICT(clave) DO UPDATE SET valor=excluded.valor',(key,str(val)))

    def pause(self, reason='Pausada por el operador'):
        with self.connect(True) as c:self._set(c,'pausada','1');self._set(c,'motivo_pausa',reason)

    def pause_if_idle(self):
        """Reserva la pantalla para un diálogo local sin carrera contra claim()."""
        with self.connect(True) as c:
            if c.execute("SELECT 1 FROM trabajos WHERE estado='EJECUTANDO' LIMIT 1").fetchone():return False
            self._set(c,'pausada','1')
            self._set(c,'motivo_pausa','Pausada para revisión/confirmación del operador. Continúe la cola al terminar.')
            return True

    def status(self):
        with self.connect() as c:
            meta=dict(c.execute('SELECT clave,valor FROM ajustes'))
            counts={r['estado']:r['n'] for r in c.execute('SELECT estado,COUNT(*) AS n FROM trabajos GROUP BY estado')}
            active=c.execute("SELECT id FROM trabajos WHERE estado='EJECUTANDO' ORDER BY creado LIMIT 1").fetchone()
        return {'pausada':meta.get('pausada','1')=='1','motivo':meta.get('motivo_pausa','Cola detenida al iniciar'),
                'bloqueo':meta.get('bloqueo',''),'activa':active[0] if active else '', 'conteos':counts}

    def recover(self):
        """Nunca reintenta una operación que pudo haber guardado/timbrado antes del cierre."""
        now=time.time()
        with self.connect(True) as c:
            rows=c.execute("SELECT id FROM trabajos WHERE estado IN ('EJECUTANDO','ACEPTAR_ALTA','PREPARADA_ALTA','PREPARADA_FACTURA','PRUEBA_PENDIENTE')").fetchall()
            for r in rows:
                c.execute("UPDATE trabajos SET estado='REVISION_REQUERIDA',motivo=?,actualizado=? WHERE id=?",
                          ('La aplicación se cerró con una operación o captura pendiente. Verifique Polaris; no se reintenta.',now,r['id']))
            c.execute("UPDATE avisos SET estado='ENVIO_INCIERTO',detalle=?,actualizado=? WHERE estado='ENVIANDO'",
                      ('El programa se cerró durante un envío. Revisar Enviados antes de reenviar.',now))
            # Una revisión incierta conserva el mismo folio bloqueado contra duplicados,
            # pero NO congela solicitudes distintas. La cola arranca sola.
            self._set(c,'bloqueo','')
            self._set(c,'pausada','0')
            self._set(c,'motivo_pausa','')
        return [self.get(r['id']) for r in rows]

    def enqueue(self,kind,data,*,source,source_id=None,safe=True,correo='',clave='',minutes=5,state='EN_COLA',reason=''):
        now=time.time();ident=uuid.uuid4().hex
        with self.connect(True) as c:
            if source_id:
                old=c.execute('SELECT * FROM trabajos WHERE fuente_id=?',(source_id,)).fetchone()
                if old:return self.unpack(old),False
            if clave:
                marks=','.join('?' for _ in BLOQUEAN_CLAVE)
                old=c.execute(f'SELECT id FROM trabajos WHERE clave=? AND estado IN ({marks}) LIMIT 1',
                              (clave,*sorted(BLOQUEAN_CLAVE))).fetchone()
                if old:raise SolicitudDuplicada('Ya hay una solicitud activa para ese folio/RFC y estación. Espere a que termine o se cierre la captura de prueba. Esto no significa que ya esté facturado.')
            c.execute('INSERT INTO trabajos(id,fuente_id,clave,tipo,origen,seguro,estado,datos,correo,motivo,creado,actualizado,minutos) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                      (ident,source_id,clave,kind,source,int(safe),state,json.dumps(data,ensure_ascii=False),correo,reason,now,now,int(minutes)))
        return self.get(ident),True

    def resume(self):
        with self.connect(True) as c:
            # Sólo una preparación interactiva que todavía ocupa Polaris bloquea la cola.
            # REVISION_REQUERIDA es una incidencia individual y no bloquea otros trabajos.
            if c.execute("SELECT 1 FROM trabajos WHERE estado IN ('PREPARADA_ALTA','PREPARADA_FACTURA','PRUEBA_PENDIENTE','ACEPTAR_ALTA') LIMIT 1").fetchone():
                raise ColaError('Hay una captura interactiva pendiente en Polaris. Resuélvala antes de continuar.')
            self._set(c,'pausada','0');self._set(c,'motivo_pausa','');self._set(c,'bloqueo','')

    def claim(self, production_allowed=True):
        with self.connect(True) as c:
            # Exclusión adicional incluso si accidentalmente se llama dos veces al consumidor.
            if c.execute("SELECT 1 FROM trabajos WHERE estado='EJECUTANDO' LIMIT 1").fetchone():return None
            meta=dict(c.execute('SELECT clave,valor FROM ajustes'))
            block=meta.get('bloqueo','')
            if block:
                r=c.execute("SELECT * FROM trabajos WHERE id=? AND estado='ACEPTAR_ALTA'",(block,)).fetchone()
                if not r:return None
            else:
                if meta.get('pausada','1')=='1':return None
                r=c.execute("SELECT * FROM trabajos WHERE estado='EN_COLA' ORDER BY creado,id LIMIT 1").fetchone()
                if not r:return None
            if not r['seguro'] and not production_allowed:
                self._set(c,'pausada','1');self._set(c,'motivo_pausa','Hay una solicitud REAL pendiente y MODO SEGURO está activo. No se convirtió su modo.')
                return None
            previous=r['estado']
            c.execute("UPDATE trabajos SET estado='EJECUTANDO',iniciado=?,actualizado=? WHERE id=?",(time.time(),time.time(),r['id']))
            job=self.unpack(r);job['accion']='aceptar' if previous=='ACEPTAR_ALTA' else 'preparar'
            job['estado']='EJECUTANDO'; return job

    def finish(self,ident,state,*,result=None,reason='',hold=False):
        now=time.time()
        with self.connect(True) as c:
            c.execute('UPDATE trabajos SET estado=?,resultado=?,motivo=?,actualizado=?,terminado=? WHERE id=?',
                      (state,json.dumps(result or {},ensure_ascii=False),reason,now,now,ident))
            if hold:
                if state == 'REVISION_REQUERIDA':
                    # Error/revisión individual: no congela toda la cola.
                    self._set(c,'bloqueo','')
                    self._set(c,'pausada','0')
                    self._set(c,'motivo_pausa','')
                else:
                    # En modo seguro, una captura preparada sí ocupa la pantalla y espera revisión.
                    self._set(c,'bloqueo',ident)
                    self._set(c,'pausada','1')
                    self._set(c,'motivo_pausa',reason or 'Captura pendiente de revisión en Polaris.')
            else:
                old=c.execute("SELECT valor FROM ajustes WHERE clave='bloqueo'").fetchone()
                if old and old[0]==ident:
                    self._set(c,'bloqueo','')
                    # Al resolver el bloqueo, continuar automáticamente si no queda otra revisión.
                    pending=c.execute("SELECT 1 FROM trabajos WHERE estado IN ('PREPARADA_ALTA','PREPARADA_FACTURA','REVISION_REQUERIDA','PRUEBA_PENDIENTE','ACEPTAR_ALTA') LIMIT 1").fetchone()
                    if not pending:
                        self._set(c,'pausada','0')
                        self._set(c,'motivo_pausa','')

    def authorize_alta(self,ident):
        with self.connect(True) as c:
            r=c.execute('SELECT * FROM trabajos WHERE id=?',(ident,)).fetchone()
            block=c.execute("SELECT valor FROM ajustes WHERE clave='bloqueo'").fetchone()
            if not r or r['tipo']!='ALTA' or r['estado']!='PREPARADA_ALTA' or not block or block[0]!=ident:
                raise ColaError('No hay una preparación de alta válida reservada para esta solicitud.')
            c.execute("UPDATE trabajos SET estado='ACEPTAR_ALTA',seguro=0,actualizado=? WHERE id=?",(time.time(),ident))

    def release(self,ident):
        with self.connect(True) as c:
            r=c.execute('SELECT estado FROM trabajos WHERE id=?',(ident,)).fetchone()
            if not r or r[0] not in ESTADOS_REVISION:raise ColaError('La solicitud no está en revisión o todavía se está ejecutando.')
            state='PRUEBA_CERRADA' if r[0] in {'PREPARADA_ALTA','PREPARADA_FACTURA','PRUEBA_PENDIENTE'} else 'REVISION_CERRADA'
            c.execute('UPDATE trabajos SET estado=?,actualizado=? WHERE id=?',(state,time.time(),ident))
            # Al cerrar una revisión, reanuda automáticamente si no queda otra
            # preparación interactiva ocupando Polaris. REVISION_REQUERIDA no bloquea.
            nextrow=c.execute("SELECT id FROM trabajos WHERE estado IN ('PREPARADA_ALTA','PREPARADA_FACTURA','PRUEBA_PENDIENTE','ACEPTAR_ALTA') ORDER BY creado LIMIT 1").fetchone()
            self._set(c,'bloqueo',nextrow[0] if nextrow else '')
            if nextrow:
                self._set(c,'pausada','1')
                self._set(c,'motivo_pausa','Hay una captura interactiva pendiente en Polaris.')
            else:
                self._set(c,'pausada','0')
                self._set(c,'motivo_pausa','')

    def cancel(self,ident):
        with self.connect(True) as c:
            row=c.execute('SELECT estado FROM trabajos WHERE id=?',(ident,)).fetchone()
            if not row or row[0]!='EN_COLA':raise ColaError('Solo se puede retirar una solicitud que no ha empezado.')
            c.execute("UPDATE trabajos SET estado='CANCELADA',actualizado=?,terminado=? WHERE id=?",(time.time(),time.time(),ident))

    def eta(self,ident,margin):
        state=self.status()
        if state['pausada'] or state['bloqueo']:return None
        with self.connect() as c:
            target=c.execute('SELECT * FROM trabajos WHERE id=?',(ident,)).fetchone()
            if not target or target['estado'] not in {'EN_COLA','EJECUTANDO'}:return None
            rows=c.execute("SELECT * FROM trabajos WHERE estado IN ('EN_COLA','EJECUTANDO') AND creado<=? ORDER BY creado",(target['creado'],)).fetchall()
        # Un alta anterior requiere aprobación humana, sin ETA fiable para las demás.
        if any(r['tipo']=='ALTA' and r['id']!=ident for r in rows):return None
        low=0
        for r in rows:
            remaining=r['minutos']
            if r['estado']=='EJECUTANDO' and r['iniciado']:
                remaining=max(1, remaining-int(max(0,time.time()-r['iniciado'])//60))
            low+=remaining
        return max(1,low),max(1,low)+margin

    def notice(self,job,event,subject,body,*,send):
        now=time.time();ident=uuid.uuid4().hex
        with self.connect(True) as c:
            # Un estado terminal hace obsoletos los avisos de avance aún sin enviar.
            if event in {'completada','ya_facturado','revision','cancelada'}:
                c.execute("UPDATE avisos SET estado='SUPERADA',actualizado=? WHERE trabajo=? AND evento IN ('recibida','procesando') AND estado='PENDIENTE'",(now,job['id']))
            c.execute('INSERT OR IGNORE INTO avisos(id,trabajo,evento,correo,asunto,cuerpo,estado,creado,actualizado) VALUES(?,?,?,?,?,?,?,?,?)',
                      (ident,job['id'],event,job['correo'],subject,body,'PENDIENTE' if send else 'SIMULADA',now,now))

    def notices(self,limit=200):
        with self.connect() as c:return [dict(r) for r in c.execute('SELECT * FROM avisos ORDER BY creado DESC LIMIT ?',(limit,))]

    def claim_notice(self):
        with self.connect(True) as c:
            # Conserva el orden por solicitud si hay más de un consumidor por accidente.
            if c.execute("SELECT 1 FROM avisos WHERE estado='ENVIANDO' LIMIT 1").fetchone():return None
            r=c.execute("SELECT * FROM avisos WHERE estado='PENDIENTE' AND proximo_intento<=? ORDER BY creado,id LIMIT 1",(time.time(),)).fetchone()
            if not r:return None
            c.execute("UPDATE avisos SET estado='ENVIANDO',actualizado=?,intentos=intentos+1 WHERE id=?",(time.time(),r['id']))
            return dict(r, intentos=int(r['intentos'])+1)

    def notice_state(self,ident,state,detail='',gmail_id=''):
        with self.connect(True) as c:
            c.execute('UPDATE avisos SET estado=?,detalle=?,gmail_id=?,actualizado=? WHERE id=?',(state,detail,gmail_id,time.time(),ident))

    def retry_notice(self,ident):
        with self.connect(True) as c:
            r=c.execute('SELECT * FROM avisos WHERE id=?',(ident,)).fetchone()
            if not r or r['estado'] not in {'ERROR_ENVIO','ENVIO_INCIERTO'}:raise ColaError('Solo se reenvían avisos fallidos o inciertos tras revisar Enviados.')
            c.execute("UPDATE avisos SET estado='PENDIENTE',detalle='',proximo_intento=0,intentos=0,actualizado=? WHERE id=?",(time.time(),ident))

    def posponer_aviso(self, ident, segundos, detalle):
        """Solo para rechazo explícito o petición aún no enviada; nunca un timeout de envío."""
        now = time.time()
        with self.connect(True) as c:
            c.execute("UPDATE avisos SET estado='PENDIENTE',detalle=?,proximo_intento=?,actualizado=? WHERE id=? AND estado='ENVIANDO'",
                      (str(detalle)[:900], now + max(1, segundos), now, ident))
