"""Centro SQL: conexión y espejo manual de historial; no afecta la cola."""
from __future__ import annotations
import queue
import threading
import tkinter as tk
from tkinter import ttk, messagebox

from .dashboard import BLUE, MUTED, GREEN, RED
from . import sql_central as sql


class SqlTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, padding=14)
        self.app = app
        self.base = app.cola.base
        self.events = queue.Queue()
        self.busy = False
        settings = sql.load_settings(self.base)
        self.server = tk.StringVar(value=settings.server)
        self.port = tk.StringVar(value=str(settings.port))
        self.user = tk.StringVar(value=settings.user)
        self.password = tk.StringVar()
        self.test_untrusted = tk.BooleanVar(value=settings.diagnostic_untrusted)
        self.auto_tested = False
        self.status = tk.StringVar(value="Cuenta guardada; comprobación pendiente." if settings.server and sql.get_password(settings) else "Sin conexión comprobada.")
        self.stats = tk.StringVar(value="Estaciones: —    |    Solicitudes SQL: —    |    CFDI verificados: —")

        ttk.Label(self, text="Centro de datos SQL", font=("Segoe UI", 19, "bold")).pack(anchor="w")
        ttk.Label(self, text="PolarisBot • Base central independiente de Polaris", foreground=BLUE).pack(anchor="w", pady=(3, 12))
        form = ttk.LabelFrame(self, text="Conexión SQL Server", padding=12)
        form.pack(fill="x")
        form.columnconfigure(1, weight=1)
        for i, (label, var) in enumerate((("Servidor / IP",self.server),("Puerto TCP",self.port),
                                            ("Usuario",self.user),("Contraseña",self.password))):
            ttk.Label(form, text=label).grid(row=i,column=0,sticky="w",padx=(0,10),pady=5)
            ttk.Entry(form,textvariable=var,show="*" if i==3 else "").grid(row=i,column=1,sticky="ew",pady=5)
        ttk.Label(form,text="Base de datos: PolarisBot • TLS cifrado; excepción solo para diagnóstico").grid(row=4,column=0,columnspan=2,sticky="w",pady=8)
        actions = ttk.Frame(form);actions.grid(row=5,column=0,columnspan=2,sticky="w")
        self.test = ttk.Button(actions,text="Probar conexión",command=lambda:self._run(False))
        self.test.pack(side="left")
        self.save = ttk.Button(actions,text="Guardar conexión en Windows",command=self._save)
        self.save.pack(side="left",padx=8)
        self.connection_result = ttk.Label(form,textvariable=self.status,foreground=BLUE,
                                           font=("Segoe UI",11,"bold"),wraplength=620)
        self.connection_result.grid(row=6,column=0,columnspan=2,sticky="ew",pady=(10,4))
        ttk.Label(form,text="La contraseña guardada se reutiliza automáticamente; el campo permanece vacío.",
                  foreground=MUTED,wraplength=620).grid(row=7,column=0,columnspan=2,sticky="w",pady=4)
        ttk.Checkbutton(form,text="Diagnóstico en MODO SEGURO: permitir certificado TLS sin validar",
                        variable=self.test_untrusted).grid(row=8,column=0,columnspan=2,sticky="w",pady=4)

        mirror = ttk.LabelFrame(self,text="Historial central",padding=12)
        mirror.pack(fill="x")
        ttk.Label(mirror,textvariable=self.stats,wraplength=710,font=("Segoe UI",11,"bold")).pack(anchor="w")
        ttk.Label(mirror,text="Copia estados y metadatos operativos desde SQLite. No emite CFDI ni modifica las bases de venta. "
                            "La cola local sigue funcionando aunque SQL no responda.",foreground=MUTED,wraplength=710).pack(anchor="w",pady=8)
        self.sync = ttk.Button(mirror,text="Sincronizar historial local",command=lambda:self._run(True))
        self.sync.pack(anchor="w")
        ttk.Label(self,text="Se puede guardar sa para diagnóstico de solo lectura. Para sincronizar, utiliza POLARISBOT_APP con TLS verificado. "
                            "No se guardan RFC, nombres, correos, contraseñas ni constancias en SQL.",foreground=MUTED,wraplength=720).pack(anchor="w",pady=12)
        self.after(180,self._poll)
        self.bind("<Map>", self._auto_test, add="+")

    def _auto_test(self, event=None):
        if self.auto_tested or self.busy:
            return
        saved = sql.load_settings(self.base)
        if not saved.server or not sql.get_password(saved):
            return
        if saved.diagnostic_untrusted and not self.app.cfg.get("app",{}).get("modo_prueba",True):
            self._report("Conexión guardada. Activa MODO SEGURO para diagnóstico TLS.")
            return
        self.auto_tested = True
        self._run(False)

    def _report(self, message, *, ok=None):
        prefix = "CONECTADO: " if ok is True else "ERROR: " if ok is False else ""
        self.status.set(prefix + message)
        self.connection_result.configure(foreground=GREEN if ok is True else RED if ok is False else BLUE)
        self.app.log("SQL | " + prefix + message)

    def _settings(self):
        try:
            return sql.Settings(self.server.get().strip(),int(self.port.get()),self.user.get().strip(),
                                bool(self.test_untrusted.get())).validate()
        except ValueError:
            raise sql.SqlError("Introduce un puerto numérico.") from None

    def _credentials(self):
        settings = self._settings()
        password = self.password.get() or sql.get_password(settings)
        if not password:
            raise sql.SqlError("Introduce la contraseña de SQL.")
        return settings,password

    def _save(self):
        if self.busy:
            return
        try:
            settings,password=self._credentials()
            diagnostic = settings.user.casefold()!=sql.APP_USER.casefold() or settings.diagnostic_untrusted
            if diagnostic:
                if not self.app.cfg.get("app",{}).get("modo_prueba",True):
                    raise sql.SqlError("Activa MODO SEGURO para guardar una conexión de diagnóstico.")
                if not messagebox.askyesno("Guardar diagnóstico SQL",
                        "La contraseña se guarda solo en el Administrador de credenciales de Windows. "
                        "La cuenta administrativa no podrá sincronizar. Si elegiste omitir la validación TLS, "
                        "solo se aplicará a consultas de diagnóstico en MODO SEGURO. ¿Guardar esta configuración?",
                        parent=self,default="no"):
                    return
            sql.save_settings(self.base,settings,password,diagnostic=diagnostic)
            self.password.set("")
            self._report("Conexión guardada en Windows. Comprobando…")
            self._run(False)
        except sql.SqlError as exc:
            self._report(str(exc), ok=False)

    def _run(self, synchronize):
        if self.busy:
            return
        try:
            settings,password=self._credentials()
            temporary_tls=settings.diagnostic_untrusted if not synchronize else False
            if temporary_tls and (synchronize or not self.app.cfg.get("app",{}).get("modo_prueba",True)):
                raise sql.SqlError("El certificado sin validar solo se permite para probar conexión en MODO SEGURO.")
            if temporary_tls and settings != sql.load_settings(self.base) and not messagebox.askyesno("Diagnóstico temporal TLS",
                   "La conexión seguirá cifrada, pero no se verificará la identidad del servidor. Solo se consultará el esquema y los contadores. ¿Continuar esta vez?",
                   parent=self,default="no"):
                return
            if synchronize and settings.user.casefold()!=sql.APP_USER.casefold():
                raise sql.SqlError("Para sincronizar utiliza POLARISBOT_APP, no sa.")
            if synchronize and not messagebox.askyesno("Sincronizar historial",
                   "Se copiarán las solicitudes del historial local a PolarisBot. No se emitirá ni repetirá ninguna factura. ¿Continuar?",
                   parent=self,default="no"):
                return
        except sql.SqlError as exc:
            self._report(str(exc), ok=False)
            return
        self.password.set("")
        self.auto_tested=True
        self.busy=True
        for button in (self.test,self.save,self.sync):button.configure(state="disabled")
        self._report("Consultando SQL Server…" if not synchronize else "Sincronizando historial…")
        self.test.configure(text="Probando…" if not synchronize else "Probar conexión")
        path=self.app.cola.store.path
        def work():
            try:
                result=sql.synchronize(settings,password,path) if synchronize else None
                self.events.put((result,sql.check(settings,password,test_untrusted=temporary_tls),None))
            except Exception as exc:
                self.events.put((None,None,sql.friendly_error(exc)))
        threading.Thread(target=work,daemon=True,name="ARY-SQL-Central").start()

    def _poll(self):
        try:
            result,check,error=self.events.get_nowait()
        except queue.Empty:
            pass
        else:
            self.busy=False
            self.test.configure(text="Probar conexión")
            for button in (self.test,self.save,self.sync):button.configure(state="normal")
            if error:
                self.stats.set("Estaciones: —    |    Solicitudes SQL: —    |    CFDI verificados: —")
                self._report(error, ok=False)
            else:
                self.stats.set(f"Estaciones: {check['stations']}    |    Solicitudes SQL: {check['jobs']}    |    CFDI verificados: {check['verified']}")
                if result is not None:
                    self._report(f"Historial sincronizado: {result['processed']} examinadas, {result['skipped']} omitidas por datos incompletos.", ok=True)
                else:
                    self._report("PolarisBot • SQL Server "+check["version"]+
                                 (" • Cuenta administrativa: solo prueba." if check["admin"] else " • Consulta de esquema correcta.")+
                                 (" • TLS sin verificar: solo diagnóstico." if not check["tls_verified"] else ""), ok=True)
        self.after(180,self._poll)
