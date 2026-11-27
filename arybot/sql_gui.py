"""Conexión SQL Server de ARY: prueba de acceso desde la VM, sin tocar Polaris."""
from __future__ import annotations
import queue
import threading
import tkinter as tk
from tkinter import ttk
from .dashboard import BLUE, MUTED, GREEN, RED
from . import sql_central as sql

DEFAULT_SQL_SERVER = "10.20.20.155"

class SqlTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, padding=14)
        self.app=app
        self.base=app.cola.base
        self.events=queue.Queue()
        self.busy=False
        saved=sql.load_settings(self.base)
        self.server=tk.StringVar(value=saved.server or DEFAULT_SQL_SERVER)
        self.port=tk.StringVar(value=str(saved.port))
        self.user=tk.StringVar(value=saved.user if saved.server else "sa")
        self.password=tk.StringVar(value="")
        self.status=tk.StringVar(value="SQL Server preparado. Pulsa Conectar para comprobar la VM.")
        self.detail=tk.StringVar(value="Conexión: pendiente • Base del bot: pendiente")
        self.auto_tested=False

        ttk.Label(self,text="Conexión SQL Server",font=("Segoe UI",18,"bold")).pack(anchor="w")
        ttk.Label(self,text="Servidor local de ARY. El historial de facturas permanece en SQLite hasta verificar la base SQL.",
                  foreground=MUTED,wraplength=800).pack(anchor="w",pady=(5,15))
        form=ttk.Frame(self)
        form.pack(fill="x")
        for i,(label,var) in enumerate((("Servidor",self.server),("Puerto",self.port),
                                        ("Usuario",self.user),("Contraseña",self.password))):
            ttk.Label(form,text=label).grid(row=i,column=0,sticky="w",padx=(0,12),pady=6)
            ttk.Entry(form,textvariable=var,show="*" if i==3 else "",width=35).grid(
                row=i,column=1,sticky="ew",pady=6)
        form.columnconfigure(1,weight=1)
        self.btn=ttk.Button(form,text="Guardar y probar conexión",command=self.connect_sql)
        self.btn.grid(row=4,column=1,sticky="w",pady=(10,14))
        self.result=ttk.Label(self,textvariable=self.status,foreground=BLUE,wraplength=800)
        self.result.pack(anchor="w",pady=(5,10))
        ttk.Label(self,textvariable=self.detail,foreground=MUTED,wraplength=800).pack(anchor="w")
        ttk.Label(self,text="Introduce la contraseña una vez en esta VM. "
                  "Se guarda en el Administrador de credenciales de Windows, no en GitHub ni en archivos del proyecto. "
                  "La prueba no modifica las bases de datos de Polaris.",
                  foreground=MUTED,wraplength=800).pack(anchor="w",pady=(20,0))
        self.after(200,self._poll)
        self.bind("<Map>",self._auto_test,add="+")

    def _settings(self):
        try:
            return sql.Settings(
                server=self.server.get().strip(),
                port=int(self.port.get()),
                user=self.user.get().strip()).validate()
        except ValueError:
            raise sql.SqlError("El puerto debe ser numérico.") from None

    def _auto_test(self,event=None):
        if self.busy or self.auto_tested:return
        saved=sql.load_settings(self.base)
        if not saved.server or not sql.get_password(saved):return
        self.auto_tested=True
        self._launch(saved,sql.get_password(saved))

    def connect_sql(self):
        if self.busy:return
        try:
            settings=self._settings()
            password=self.password.get() or sql.get_password(settings)
            if not password:
                raise sql.SqlError("Introduce la contraseña SQL en esta VM.")
            sql.save_settings(self.base,settings,password,diagnostic=True)
        except sql.SqlError as exc:
            self.status.set("No se guardó: "+str(exc))
            self.result.configure(foreground=RED)
            return
        self.password.set("")
        self.auto_tested=True
        self._launch(settings,password)

    def _launch(self,settings,password):
        self.busy=True
        self.btn.configure(state="disabled")
        self.status.set("Comprobando acceso a "+settings.server+"…")
        self.result.configure(foreground=BLUE)
        def work():
            try:
                data=sql.probar_sql_servidor(settings,password)
                self.events.put((data,None))
            except Exception as exc:
                self.events.put((None,sql.friendly_error(exc)))
        threading.Thread(target=work,daemon=True,name="ARY-Prueba-SQL").start()

    def _poll(self):
        try:
            data,error=self.events.get_nowait()
        except queue.Empty:
            pass
        else:
            self.busy=False
            self.btn.configure(state="normal")
            if error:
                self.status.set("No se pudo conectar: "+error)
                self.result.configure(foreground=RED)
                self.detail.set("Conexión SQL pendiente. SQLite sigue funcionando.")
            else:
                self.status.set("Conexión SQL correcta con "+data["servidor"])
                self.result.configure(foreground=GREEN)
                self.detail.set(
                    "SQL Server "+data["version"]+
                    (" • Base PolarisBot encontrada (sin escrituras)." if data["bot_database"] else
                     " • La base propia PolarisBot no existe: no se sincronizarán solicitudes automáticamente."))
        self.after(200,self._poll)
