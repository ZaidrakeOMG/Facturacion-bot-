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
        self.status = tk.StringVar(value="Sin conexión comprobada.")
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
        ttk.Label(form,text="Base de datos: PolarisBot • TLS con certificado verificado").grid(row=4,column=0,columnspan=2,sticky="w",pady=8)
        actions = ttk.Frame(form);actions.grid(row=5,column=0,columnspan=2,sticky="w")
        self.test = ttk.Button(actions,text="Probar conexión",command=lambda:self._run(False))
        self.test.pack(side="left")
        self.save = ttk.Button(actions,text="Guardar cuenta limitada",command=self._save)
        self.save.pack(side="left",padx=8)
        self.connection_result = ttk.Label(form,textvariable=self.status,foreground=BLUE,
                                           font=("Segoe UI",11,"bold"),wraplength=620)
        self.connection_result.grid(row=6,column=0,columnspan=2,sticky="ew",pady=(10,4))
        ttk.Label(form,text="La contraseña se borra al iniciar la prueba por seguridad.",
                  foreground=MUTED,wraplength=620).grid(row=7,column=0,columnspan=2,sticky="w",pady=4)

        mirror = ttk.LabelFrame(self,text="Historial central",padding=12)
        mirror.pack(fill="x")
        ttk.Label(mirror,textvariable=self.stats,wraplength=710,font=("Segoe UI",11,"bold")).pack(anchor="w")
        ttk.Label(mirror,text="Copia estados y metadatos operativos desde SQLite. No emite CFDI ni modifica las bases de venta. "
                            "La cola local sigue funcionando aunque SQL no responda.",foreground=MUTED,wraplength=710).pack(anchor="w",pady=8)
        self.sync = ttk.Button(mirror,text="Sincronizar historial local",command=lambda:self._run(True))
        self.sync.pack(anchor="w")
        ttk.Label(self,text="Para pruebas puedes usar sa sin guardar su contraseña. Para sincronizar, utiliza POLARISBOT_APP. "
                            "No se guardan RFC, nombres, correos, contraseñas ni constancias en SQL.",foreground=MUTED,wraplength=720).pack(anchor="w",pady=12)
        self.after(180,self._poll)

    def _report(self, message, *, ok=None):
        prefix = "CONECTADO: " if ok is True else "ERROR: " if ok is False else ""
        self.status.set(prefix + message)
        self.connection_result.configure(foreground=GREEN if ok is True else RED if ok is False else BLUE)
        self.app.log("SQL | " + prefix + message)

    def _settings(self):
        try:
            return sql.Settings(self.server.get().strip(),int(self.port.get()),self.user.get().strip()).validate()
        except ValueError:
            raise sql.SqlError("Introduce un puerto numérico.") from None

    def _credentials(self):
        settings = self._settings()
        password = self.password.get() or sql.get_password(settings)
        if not password:
            raise sql.SqlError("Introduce la contraseña de SQL.")
        return settings,password

    def _save(self):
        try:
            settings,password=self._credentials()
            sql.save_settings(self.base,settings,password)
            self.password.set("")
            self._report("Cuenta limitada guardada en el Administrador de credenciales de Windows.")
        except sql.SqlError as exc:
            self._report(str(exc), ok=False)

    def _run(self, synchronize):
        if self.busy:
            return
        try:
            settings,password=self._credentials()
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
        self.busy=True
        for button in (self.test,self.save,self.sync):button.configure(state="disabled")
        self._report("Consultando SQL Server…" if not synchronize else "Sincronizando historial…")
        self.test.configure(text="Probando…" if not synchronize else "Probar conexión")
        path=self.app.cola.store.path
        def work():
            try:
                result=sql.synchronize(settings,password,path) if synchronize else None
                self.events.put((result,sql.check(settings,password),None))
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
                                 (" • Cuenta administrativa: solo prueba." if check["admin"] else " • Consulta de esquema correcta."), ok=True)
        self.after(180,self._poll)
