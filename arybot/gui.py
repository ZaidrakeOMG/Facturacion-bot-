from __future__ import annotations
import os, sys, threading, queue, time, webbrowser, calendar
from pathlib import Path
from datetime import datetime, date
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from .config import ConfigManager
from .db import DB
from .gmail_client import GmailClient, NeedGmailAuth, WrongGmailAccount
from .polaris import PolarisBot, PolarisError
from .processor import Processor
from .worker import BotWorker
from .parser import Solicitud, normalize_payment, normalize_cfdi_usage, normalize_station, normalize_ticket_date, STATIONS, USO_CFDI
from .ocr_ticket import find_tesseract
from .alta_gui import AltaClientesTab
from .alertas import AlertasInternas
from .alertas_gui import AlertasTab
from .cola_operaciones import ColaOperaciones
from .cola_gui import ColaTab
from .alta_clientes import AltaClientes
from .diagnostico import Diagnostico
from .diagnostico_gui import DiagnosticoTab

BASE=Path(__file__).resolve().parent.parent


MESES_ES = (
    "", "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
    "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"
)
DIAS_ES = ("Dom", "Lun", "Mar", "Mié", "Jue", "Vie", "Sáb")


class SelectorFecha(ttk.Frame):
    """Selector visual de fecha sin dependencias externas.

    El usuario NO escribe la fecha: la selecciona en un calendario.
    La variable recibe DD/MM/AAAA para mantener compatibilidad con el bot.
    """
    def __init__(self, master, variable, width=45):
        super().__init__(master)
        self.variable = variable
        self._popup = None
        self._shown_year = date.today().year
        self._shown_month = date.today().month

        self.entry = ttk.Entry(
            self,
            textvariable=self.variable,
            width=width,
            state="readonly"
        )
        self.entry.pack(side="left", fill="x", expand=True)

        self.btn = ttk.Button(
            self,
            text="📅",
            width=4,
            command=self.abrir
        )
        self.btn.pack(side="left", padx=(5, 0))

        self.entry.bind("<Button-1>", lambda _e: self.abrir())
        self.entry.bind("<Return>", lambda _e: self.abrir())

    @staticmethod
    def _parse(value):
        value = str(value or "").strip()
        for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y"):
            try:
                return datetime.strptime(value, fmt).date()
            except ValueError:
                pass
        return None

    def abrir(self):
        if self._popup is not None and self._popup.winfo_exists():
            self._popup.lift()
            self._popup.focus_force()
            return

        selected = self._parse(self.variable.get()) or date.today()
        self._shown_year = selected.year
        self._shown_month = selected.month

        top = tk.Toplevel(self)
        self._popup = top
        top.title("Selecciona la fecha del ticket")
        top.resizable(False, False)
        top.transient(self.winfo_toplevel())

        try:
            x = self.winfo_rootx()
            y = self.winfo_rooty() + self.winfo_height() + 3
            top.geometry(f"+{x}+{y}")
        except Exception:
            pass

        top.protocol("WM_DELETE_WINDOW", self._cerrar)
        top.bind("<Escape>", lambda _e: self._cerrar())

        self._calendar_frame = ttk.Frame(top, padding=10)
        self._calendar_frame.pack(fill="both", expand=True)

        # Impide hacer clic detrás mientras se selecciona la fecha.
        try:
            top.grab_set()
        except Exception:
            pass

        self._dibujar()

    def _cerrar(self):
        if self._popup is not None:
            try:
                self._popup.grab_release()
            except Exception:
                pass
            try:
                self._popup.destroy()
            except Exception:
                pass
        self._popup = None

    def _mover_mes(self, delta):
        month = self._shown_month + int(delta)
        year = self._shown_year
        if month < 1:
            month = 12
            year -= 1
        elif month > 12:
            month = 1
            year += 1
        self._shown_year = year
        self._shown_month = month
        self._dibujar()

    def _ir_hoy(self):
        today = date.today()
        self._shown_year = today.year
        self._shown_month = today.month
        self._dibujar()

    def _limpiar(self):
        self.variable.set("")
        self._cerrar()

    def _elegir(self, day):
        chosen = date(self._shown_year, self._shown_month, int(day))
        if chosen > date.today():
            messagebox.showwarning(
                "Fecha del ticket",
                "No puedes seleccionar una fecha futura.",
                parent=self._popup
            )
            return
        self.variable.set(chosen.strftime("%d/%m/%Y"))
        self._cerrar()

    def _dibujar(self):
        frame = self._calendar_frame
        for child in frame.winfo_children():
            child.destroy()

        header = ttk.Frame(frame)
        header.grid(row=0, column=0, columnspan=7, sticky="ew", pady=(0, 8))

        ttk.Button(
            header, text="◀", width=4,
            command=lambda: self._mover_mes(-1)
        ).pack(side="left")

        ttk.Label(
            header,
            text=f"{MESES_ES[self._shown_month]} {self._shown_year}",
            font=("Segoe UI", 10, "bold"),
            anchor="center"
        ).pack(side="left", expand=True, fill="x", padx=10)

        ttk.Button(
            header, text="▶", width=4,
            command=lambda: self._mover_mes(1)
        ).pack(side="right")

        for col, name in enumerate(DIAS_ES):
            ttk.Label(
                frame, text=name, anchor="center",
                font=("Segoe UI", 9, "bold")
            ).grid(row=1, column=col, padx=2, pady=2, sticky="nsew")

        cal = calendar.Calendar(firstweekday=6)
        weeks = cal.monthdayscalendar(self._shown_year, self._shown_month)
        today = date.today()
        selected = self._parse(self.variable.get())

        for row_idx, week in enumerate(weeks, start=2):
            for col_idx, day_num in enumerate(week):
                if not day_num:
                    ttk.Label(frame, text="").grid(
                        row=row_idx, column=col_idx,
                        padx=2, pady=2, ipadx=7, ipady=4
                    )
                    continue

                current = date(
                    self._shown_year, self._shown_month, day_num
                )
                label = str(day_num)
                if selected == current:
                    label = f"[{day_num}]"

                state = "disabled" if current > today else "normal"
                ttk.Button(
                    frame,
                    text=label,
                    width=4,
                    state=state,
                    command=lambda d=day_num: self._elegir(d)
                ).grid(
                    row=row_idx, column=col_idx,
                    padx=2, pady=2, ipadx=2, ipady=2
                )

        bottom_row = 2 + len(weeks)
        bottom = ttk.Frame(frame)
        bottom.grid(
            row=bottom_row, column=0, columnspan=7,
            sticky="ew", pady=(8, 0)
        )
        ttk.Button(
            bottom, text="Hoy",
            command=lambda: self._elegir(date.today().day)
            if (self._shown_year, self._shown_month) ==
               (date.today().year, date.today().month)
            else self._ir_hoy()
        ).pack(side="left")
        ttk.Button(
            bottom, text="Limpiar",
            command=self._limpiar
        ).pack(side="right")

        for col in range(7):
            frame.columnconfigure(col, weight=1)


class App:
    def __init__(self,root):
        self.root=root; self.root.title("ARY Facturación Bot - Python v4.9 fecha hoy"); self.root.geometry("1040x740"); self.root.minsize(900,620)
        self.q=queue.Queue(); self.ui_events=queue.Queue(); self.worker=None; self._direct_busy=False; self._direct_frozen=[]
        self.diagnostico=Diagnostico(BASE)
        self._install_exception_hooks()
        self.cfgm=ConfigManager(BASE); self.cfg=self.cfgm.get(); self.db=DB(BASE/"data"/"arybot.sqlite3")
        self.gmail=GmailClient(BASE,self.cfg,self.log)
        try: self.alertas=AlertasInternas(BASE,self.cfg,self.log)
        except Exception:
            self.alertas=None;self.log("Alertas internas no disponibles: revisa la carpeta local del bot.")
        self._polaris_error = ""
        try:
            self.polaris=PolarisBot(BASE,self.cfg,self.log)
            self.polaris.diag=self.diagnostico
            self.diagnostico.log=self.log
        except Exception as e:
            self.polaris=None
            self._polaris_error=type(e).__name__+": "+str(e)
            self.log("Polaris no disponible: "+self._polaris_error)
            self.diagnostico.capturar('INICIALIZAR POLARIS', e)

        self.alta_service=AltaClientes(self.polaris) if self.polaris else None
        self.cola=ColaOperaciones(BASE,self.cfg,self.polaris,self.log,alertas=self.alertas,
                                 alta=self.alta_service,db=self.db,
                                 on_event=lambda kind,ident:self.ui_events.put((kind,ident)))
        self.processor=Processor(BASE,self.cfg,self.db,self.gmail,self.polaris,self.log,alertas=self.alertas,cola=self.cola)
        self._direct_job_id=None
        self._build(); self._sync_form_from_cfg(); self.root.after(100,self._drain_logs); self.root.after(800,self._startup_checks)
        self.root.protocol("WM_DELETE_WINDOW",self.close)
        if self.alertas: self.alertas.iniciar()
        self.cola.iniciar_hilos()
        self.root.after(150,self._drain_cola_events)
        self.root.after(500,self._estado_vm)

    def log(self,msg):
        self.q.put((time.strftime("%H:%M:%S"),str(msg)))
        try:
            if getattr(self,'diagnostico',None):
                self.diagnostico.evento('LOG','INFO',str(msg))
        except Exception:pass

    def _install_exception_hooks(self):
        def callback_error(tp, val, tb):
            import traceback
            try:self.diagnostico.capturar('ERROR DE BOTÓN / INTERFAZ', val, bot=getattr(self,'polaris',None))
            except Exception:pass
            self.log('ERROR DE INTERFAZ: '+type(val).__name__+': '+str(val))
            if hasattr(self,'direct_status'):self.direct_status.set('ERROR: '+str(val))
            traceback.print_exception(tp,val,tb)
        self.root.report_callback_exception=callback_error
        old_sys=sys.excepthook
        def sys_hook(tp,val,tb):
            try:self.diagnostico.capturar('EXCEPCION NO CONTROLADA',val,bot=getattr(self,'polaris',None),contexto={'hilo':'principal'})
            except Exception:pass
            old_sys(tp,val,tb)
        sys.excepthook=sys_hook
        if hasattr(threading,'excepthook'):
            old_thread=threading.excepthook
            def thread_hook(args):
                try:self.diagnostico.capturar('EXCEPCION DE HILO',args.exc_value,bot=getattr(self,'polaris',None),contexto={'hilo':getattr(args.thread,'name','')})
                except Exception:pass
                old_thread(args)
            threading.excepthook=thread_hook

    def _avisar_error(self, categoria, etapa, error, *, contexto=None, safe=True, leer_polaris=True):
        service=getattr(self,"alertas",None)
        if not service:return
        bot=getattr(self,"polaris",None) if leer_polaris else None
        step=getattr(bot,"etapa_alerta",None) if bot else None
        if not isinstance(step,str) or not step:step=etapa
        if categoria=="FACTURACION" and step.startswith("ENVIO_CORREO"):
            categoria="ENVIO_FACTURA"
        service.reportar(categoria,step,error,contexto=contexto,modo_seguro=safe,bot=bot)


    def _build(self):
        style=ttk.Style();
        try: style.theme_use("vista")
        except: pass
        outer=ttk.Frame(self.root,padding=12); outer.pack(fill="both",expand=True)
        head=ttk.Frame(outer); head.pack(fill="x")
        ttk.Label(head,text="ARY Facturación Bot",font=("Segoe UI",18,"bold")).pack(side="left")
        ttk.Label(head,text="Base original + adaptador VM R1 (03/oct/2026)",font=("Segoe UI",10)).pack(side="left",padx=12)
        self.mode_var=tk.BooleanVar(value=bool(self.cfg["app"].get("modo_prueba",True)))
        self.mode_check=ttk.Checkbutton(head,text="MODO SEGURO (no presiona Aceptar final)",variable=self.mode_var,command=self._mode_changed)
        self.mode_check.pack(side="right")

        st=ttk.Frame(outer); st.pack(fill="x",pady=(10,8))
        self.gmail_status=ttk.Label(st,text="Gmail: comprobando..."); self.gmail_status.pack(side="left",padx=(0,20))
        self.bot_status=ttk.Label(st,text="Bot: detenido"); self.bot_status.pack(side="left",padx=(0,20))
        self.polaris_status=ttk.Label(st,text="Polaris: comprobando..."); self.polaris_status.pack(side="left")

        self.estado_vm_var=tk.StringVar(value='VM: comprobando módulo de Polaris y cola...')
        ttk.Label(outer,textvariable=self.estado_vm_var,wraplength=960).pack(fill='x',pady=(0,6))
        nb=ttk.Notebook(outer); self.nb=nb; nb.pack(fill="both",expand=True)
        self.tab_panel=ttk.Frame(nb,padding=10); self.tab_test=ttk.Frame(nb,padding=10); self.tab_cfg=ttk.Frame(nb,padding=10)
        nb.add(self.tab_panel,text="Panel"); nb.add(self.tab_test,text="Prueba Polaris"); nb.add(self.tab_cfg,text="Configuración")
        self._build_panel(); self._build_test(); self._build_cfg()
        self.alta_clientes_tab=AltaClientesTab(nb,self)
        nb.insert(2,self.alta_clientes_tab,text="Alta de cliente")
        self.alertas_tab=AlertasTab(nb,self)
        nb.insert(3,self.alertas_tab,text="Alertas internas")
        self.cola_tab=ColaTab(nb,self)
        nb.insert(4,self.cola_tab,text="Cola y avisos")
        self.diagnostico_tab=DiagnosticoTab(nb,self)
        nb.insert(5,self.diagnostico_tab,text="Diagnóstico")

    def _build_panel(self):
        bar=ttk.Frame(self.tab_panel); bar.pack(fill="x")
        ttk.Button(bar,text="Conectar Gmail",command=self.connect_gmail).pack(side="left",padx=(0,6))
        self.btn_start=ttk.Button(bar,text="▶ Iniciar bot",command=self.start_bot); self.btn_start.pack(side="left",padx=6)
        self.btn_stop=ttk.Button(bar,text="■ Detener",command=self.stop_bot,state="disabled"); self.btn_stop.pack(side="left",padx=6)
        ttk.Button(bar,text="Procesar último correo",command=self.process_latest).pack(side="left",padx=6)
        ttk.Button(bar,text="Empezar a vigilar desde ahora",command=self.reset_checkpoint).pack(side="left",padx=6)
        ttk.Separator(self.tab_panel).pack(fill="x",pady=10)
        ttk.Label(self.tab_panel,text="Eventos del bot",font=("Segoe UI",11,"bold")).pack(anchor="w")
        self.logbox=tk.Text(self.tab_panel,height=25,wrap="word",font=("Consolas",9),state="disabled")
        self.logbox.pack(fill="both",expand=True,pady=(5,0))

    def _build_test(self):
        ttk.Label(self.tab_test,text="Facturación directa / prueba en Polaris",font=("Segoe UI",13,"bold")).grid(row=0,column=0,columnspan=4,sticky="w",pady=(0,6))
        self.direct_hint=tk.StringVar()
        self.direct_hint_label=ttk.Label(self.tab_test,textvariable=self.direct_hint,wraplength=820)
        self.direct_hint_label.grid(row=1,column=0,columnspan=4,sticky="w",pady=(0,14))
        labels=[("Estación",0),("RFC del cliente",1),("Ticket/Folio",2),("Fecha del ticket",3),("Forma de pago",4),("Uso CFDI (opcional)",5),("Correo destino",6)]
        self.test_station=tk.StringVar(value="ARY I")
        self.test_rfc=tk.StringVar(); self.test_ticket=tk.StringVar(); self.test_fecha_ticket=tk.StringVar(); self.test_payment=tk.StringVar(value="EFECTIVO"); self.test_cfdi=tk.StringVar(value=USO_CFDI["G03"]); self.test_email=tk.StringVar()
        vars=[self.test_station,self.test_rfc,self.test_ticket,self.test_fecha_ticket,self.test_payment,self.test_cfdi,self.test_email]
        for text,row in labels:
            ttk.Label(self.tab_test,text=text).grid(row=row+2,column=0,sticky="e",padx=8,pady=6)
            if row==0:
                w=ttk.Combobox(self.tab_test,textvariable=vars[row],values=list(STATIONS.keys()),width=45,state="readonly")
            elif row==3:
                w=SelectorFecha(self.tab_test, vars[row], width=45)
            elif row==4:
                w=ttk.Combobox(self.tab_test,textvariable=vars[row],values=["EFECTIVO","TARJETA DE CREDITO","TARJETA DE DEBITO","TRANSFERENCIA"],width=45)
            elif row==5:
                w=ttk.Combobox(self.tab_test,textvariable=vars[row],values=list(USO_CFDI.values()),width=56)
            else:
                w=ttk.Entry(self.tab_test,textvariable=vars[row],width=56)
            w.grid(row=row+2,column=1,columnspan=2,sticky="w",pady=6)
        ttk.Label(self.tab_test,text="Primero cambia/valida la ESTACIÓN. Después busca al cliente por RFC. Polaris llena Nombre/RFC/CP/Régimen solo. Uso CFDI vacío = GASTOS EN GENERAL.",foreground="#3a4a5a",wraplength=820).grid(row=9,column=0,columnspan=4,sticky="w",pady=(6,0))
        self.btn_test=ttk.Button(self.tab_test,text="Ejecutar prueba segura en Polaris (NO TIMBRA)",command=self.test_polaris)
        self.btn_test.grid(row=10,column=1,sticky="w",pady=18)
        self.btn_station=ttk.Button(self.tab_test,text="Probar SOLO cambio de estación",command=self.test_station_only)
        self.btn_station.grid(row=10,column=2,sticky="w",padx=8,pady=18)
        self.btn_payment=ttk.Button(self.tab_test,text="Probar SOLO Forma de Pago",command=self.test_payment_only)
        self.btn_payment.grid(row=11,column=1,sticky="w",pady=6)
        self.btn_cleanup=ttk.Button(self.tab_test,text="Probar SOLO limpieza de ventanas",command=self.test_cleanup_only)
        self.btn_cleanup.grid(row=11,column=2,sticky="w",padx=8,pady=6)
        self.direct_status=tk.StringVar(value="Listo. La solicitud se agrega a Cola y avisos; no necesitas detener Gmail.")
        ttk.Label(self.tab_test,textvariable=self.direct_status,wraplength=850,foreground="#3a4a5a").grid(row=12,column=0,columnspan=4,sticky="w",pady=(6,0))
        ttk.Label(self.tab_test,text="Captura: la fecha se elige en calendario; RFC y folio usan teclado real. Pago y Uso CFDI se seleccionan explícitamente.",foreground="#3a4a5a",wraplength=820).grid(row=13,column=0,columnspan=4,sticky="w",pady=(6,0))
        ttk.Label(self.tab_test,text="Seguro de emergencia: mueve el mouse a la esquina superior izquierda para detener PyAutoGUI.",foreground="#8a2d2d",wraplength=820).grid(row=14,column=0,columnspan=4,sticky="w",pady=(6,0))
        self.tab_test.columnconfigure(3,weight=1)
        self.mode_var.trace_add("write",lambda *_:self._sync_invoice_mode())
        self._sync_invoice_mode()

    def _sync_invoice_mode(self):
        if not hasattr(self, "btn_test"): return
        safe=bool(self.mode_var.get())
        self.btn_test.configure(text=("Encolar preparación (NO TIMBRA)" if safe else "Encolar factura y correo (REAL)"))
        self.direct_hint_label.configure(foreground="#334a60" if safe else "#9a261b")
        self.direct_hint.set(
            "MODO SEGURO: la solicitud entra a la cola y se prepara SIN Aceptar. Después requiere liberar la revisión."
            if safe else
            "PRODUCCIÓN EN COLA: Aceptar factura → Enviar por Correo → XML + PDF. Se confirma antes de encolar.")

    def _freeze_direct(self):
        self._direct_busy=True;self._direct_frozen=[]
        roots=[self.tab_test,self.tab_panel,self.tab_cfg]
        alta=getattr(self,"alta_clientes_tab",None)
        if alta: roots.append(alta)
        alerttab=getattr(self,"alertas_tab",None)
        if alerttab: roots.append(alerttab)
        def visit(w):
            for child in w.winfo_children():
                if isinstance(child,(ttk.Button,ttk.Entry,ttk.Combobox,ttk.Checkbutton,ttk.Spinbox)):
                    self._direct_frozen.append((child,tuple(child.state())))
                    child.state(["disabled"])
                visit(child)
        for root in roots: visit(root)
        self._direct_frozen.append((self.mode_check,tuple(self.mode_check.state())))
        self.mode_check.state(["disabled"])

    def _finish_direct(self, result=None, error=None):
        self._direct_busy=False
        for widget,states in self._direct_frozen:
            try:
                widget.state(["!disabled","!readonly"])
                widget.state(states)
            except tk.TclError: pass
        self._direct_frozen=[]
        self._sync_invoice_mode()
        if error:
            self.direct_status.set("DETENIDO: "+str(error))
            messagebox.showwarning("Facturación detenida",str(error))
        elif result=="PRUEBA_OK":
            self.direct_status.set("PREPARADA SIN TIMBRAR. MODO SEGURO: no se pulsó Aceptar ni se envió correo.")
        elif result=="ENVIO_SOLICITADO":
            self.direct_status.set("ENVÍO SOLICITADO en Polaris (XML + PDF). Verifica el buzón; no repitas la factura.")
            messagebox.showinfo("Envío solicitado","Polaris cerró el diálogo de envío después de Aceptar.\nSe solicitó el envío de XML + PDF. Esto no confirma recepción en el buzón.")
        else:
            self.direct_status.set("Resultado pendiente de revisión: no repitas la factura automáticamente.")

    def _build_cfg(self):
        self.exe_var=tk.StringVar(); self.user_var=tk.StringVar(); self.pass_var=tk.StringVar(); self.tess_var=tk.StringVar(); self.interval_var=tk.IntVar(); self.auto_missing=tk.BooleanVar(); self.auto_done=tk.BooleanVar(); self.subjects_var=tk.StringVar()
        r=0
        ttk.Label(self.tab_cfg,text="Polaris",font=("Segoe UI",12,"bold")).grid(row=r,column=0,columnspan=4,sticky="w",pady=(0,8)); r+=1
        ttk.Label(self.tab_cfg,text="Polaris.exe").grid(row=r,column=0,sticky="e",padx=6,pady=5)
        ttk.Entry(self.tab_cfg,textvariable=self.exe_var,width=70).grid(row=r,column=1,columnspan=2,sticky="ew",pady=5)
        ttk.Button(self.tab_cfg,text="Examinar...",command=self.pick_exe).grid(row=r,column=3,padx=6); r+=1
        ttk.Label(self.tab_cfg,text="Usuario").grid(row=r,column=0,sticky="e",padx=6,pady=5); ttk.Entry(self.tab_cfg,textvariable=self.user_var,width=30).grid(row=r,column=1,sticky="w"); r+=1
        ttk.Label(self.tab_cfg,text="Contraseña Polaris").grid(row=r,column=0,sticky="e",padx=6,pady=5); ttk.Entry(self.tab_cfg,textvariable=self.pass_var,show="*",width=30).grid(row=r,column=1,sticky="w"); ttk.Button(self.tab_cfg,text="Guardar segura en Windows",command=self.save_password).grid(row=r,column=2,sticky="w"); r+=1
        ttk.Separator(self.tab_cfg).grid(row=r,column=0,columnspan=4,sticky="ew",pady=12); r+=1
        ttk.Label(self.tab_cfg,text="Correo / OCR",font=("Segoe UI",12,"bold")).grid(row=r,column=0,columnspan=4,sticky="w",pady=(0,8)); r+=1
        ttk.Label(self.tab_cfg,text="Revisar cada (seg)").grid(row=r,column=0,sticky="e",padx=6,pady=5); ttk.Spinbox(self.tab_cfg,from_=5,to=300,textvariable=self.interval_var,width=8).grid(row=r,column=1,sticky="w"); r+=1
        ttk.Label(self.tab_cfg,text="Asunto contiene").grid(row=r,column=0,sticky="e",padx=6,pady=5); ttk.Entry(self.tab_cfg,textvariable=self.subjects_var,width=50).grid(row=r,column=1,columnspan=2,sticky="w"); ttk.Label(self.tab_cfg,text="Separado por comas").grid(row=r,column=3,sticky="w"); r+=1
        ttk.Label(self.tab_cfg,text="Tesseract.exe (opcional)").grid(row=r,column=0,sticky="e",padx=6,pady=5); ttk.Entry(self.tab_cfg,textvariable=self.tess_var,width=70).grid(row=r,column=1,columnspan=2,sticky="ew"); ttk.Button(self.tab_cfg,text="Examinar...",command=self.pick_tess).grid(row=r,column=3,padx=6); r+=1
        ttk.Checkbutton(self.tab_cfg,text="Opción histórica: avisos ahora se configuran en Cola y avisos", state="disabled",variable=self.auto_missing).grid(row=r,column=1,columnspan=3,sticky="w",pady=4); r+=1
        ttk.Checkbutton(self.tab_cfg,text="Recepción, resultado y errores: apartado Cola y avisos", state="disabled",variable=self.auto_done).grid(row=r,column=1,columnspan=3,sticky="w",pady=4); r+=1
        ttk.Button(self.tab_cfg,text="Guardar configuración",command=self.save_config).grid(row=r,column=1,sticky="w",pady=14)
        self.tab_cfg.columnconfigure(2,weight=1)

    def _sync_form_from_cfg(self):
        p=self.cfg["polaris"]; a=self.cfg["app"]; o=self.cfg["ocr"]
        self.exe_var.set(p.get("executable","")); self.user_var.set(p.get("usuario","SUPERVISOR")); self.tess_var.set(o.get("tesseract_cmd","") or find_tesseract("")); self.interval_var.set(int(a.get("intervalo_segundos",20))); self.auto_missing.set(bool(a.get("auto_responder_faltantes",False))); self.auto_done.set(bool(a.get("auto_responder_completada",False))); self.subjects_var.set(", ".join(a.get("asunto_palabras",["FACTURA","CFDI"])))

    def _startup_checks(self):
        def work():
            try:
                self.gmail.connect(interactive=False)
                self.ui_events.put(('startup','Gmail: '+str(self.gmail.email)))
            except Exception:self.ui_events.put(('startup','Gmail: falta conectar'))
        threading.Thread(target=work,daemon=True,name='ARY-Comprobar-Gmail').start()
        if self.polaris:
            exe=self.cfg['polaris'].get('executable','');pw=self.polaris.has_password()
            self.polaris_status.config(text=f"Polaris: {'ruta OK' if exe and Path(exe).exists() else 'falta ruta'} / {'clave OK' if pw else 'falta clave'}")
        else:self.polaris_status.config(text='Polaris: no disponible')

    def _drain_logs(self):
        try:
            while True:
                ts,msg=self.q.get_nowait(); self.logbox.config(state="normal"); self.logbox.insert("end",f"{ts} | {msg}\n"); self.logbox.see("end"); self.logbox.config(state="disabled")
        except queue.Empty: pass
        self.root.after(120,self._drain_logs)

    def _mode_changed(self):
        new=bool(self.mode_var.get())
        if not new:
            if hasattr(self,'cola') and self.cola.ocupada:
                self.mode_var.set(True)
                self.log('Espera a que termine la captura para autorizar producción.');return
            if not messagebox.askyesno("Activar producción","Desactivar MODO SEGURO permite que Polaris presione el Aceptar final y TIMBRE CFDI reales. ¿Confirmas?"):
                self.mode_var.set(True); return
        self.cfg["app"]["modo_prueba"]=new; self.cfgm.save(); self.log("Modo cambiado a " + ("SEGURO" if new else "PRODUCCIÓN"))

    def connect_gmail(self):
        if self.cola.ocupada:
            self.log('Espera a que Polaris termine para abrir la autorización Gmail.');return
        win=tk.Toplevel(self.root)
        win.title("Conectar Gmail - ARY Facturación Bot")
        win.geometry("900x560")
        win.minsize(760,500)
        win.transient(self.root)
        win.grab_set()

        body=ttk.Frame(win,padding=16); body.pack(fill="both",expand=True)
        ttk.Label(body,text="Autorizar Gmail",font=("Segoe UI",14,"bold")).pack(anchor="w")
        ttk.Label(
            body,
            text=(
                f"Cuenta requerida: {self.gmail.expected_account}\n\n"
                "Esta versión NO depende de que 127.0.0.1 acepte la conexión. "
                "Primero abre el vínculo de Google y autoriza la cuenta."
            ),
            wraplength=840,
            justify="left"
        ).pack(anchor="w",pady=(6,10))

        url_var=tk.StringVar(value="Generando vínculo de autorización...")
        entry=ttk.Entry(body,textvariable=url_var)
        entry.pack(fill="x",pady=(0,8))

        status_var=tk.StringVar(value="Preparando autorización...")
        ttk.Label(body,textvariable=status_var,wraplength=840).pack(anchor="w",pady=(0,10))

        btns=ttk.Frame(body); btns.pack(fill="x")

        def copy_link():
            u=url_var.get().strip()
            if not u.startswith("http"):
                return
            self.root.clipboard_clear(); self.root.clipboard_append(u); self.root.update()
            status_var.set("Vínculo copiado. Pégalo en el navegador que tú elijas.")

        def open_default():
            u=url_var.get().strip()
            if u.startswith("http"):
                webbrowser.open(u,new=1)
                status_var.set("Vínculo abierto. Autoriza emisioncfdi@grupoary.com y vuelve al bot.")

        ttk.Button(btns,text="Copiar vínculo",command=copy_link).pack(side="left",padx=(0,8))
        ttk.Button(btns,text="Abrir en navegador predeterminado",command=open_default).pack(side="left",padx=8)

        ttk.Separator(body).pack(fill="x",pady=16)

        ttk.Label(
            body,
            text="PASO 2 — Después de pulsar PERMITIR en Google",
            font=("Segoe UI",11,"bold")
        ).pack(anchor="w")
        ttk.Label(
            body,
            text=(
                "Es NORMAL que Chrome muestre 'No se puede acceder a este sitio' / ERR_CONNECTION_REFUSED. "
                "NO recargues la página. Copia la DIRECCIÓN COMPLETA de la barra del navegador; debe empezar "
                "con http://127.0.0.1:... y contener ?state=...&code=... Luego pégala aquí:"
            ),
            wraplength=840,
            justify="left"
        ).pack(anchor="w",pady=(6,8))

        return_var=tk.StringVar()
        return_entry=ttk.Entry(body,textvariable=return_var)
        return_entry.pack(fill="x",pady=(0,8))

        complete_bar=ttk.Frame(body); complete_bar.pack(fill="x")

        def paste_return():
            try:
                return_var.set(self.root.clipboard_get().strip())
                status_var.set("URL de retorno pegada. Pulsa 'Completar conexión'.")
            except Exception:
                status_var.set("No pude leer texto del portapapeles. Pega la URL con Ctrl+V.")

        def finish_ui_success(email):
            self.gmail_status.config(text=f"Gmail: {email}")
            self.log(f"Autorización Gmail lista con {email}.")
            messagebox.showinfo("Gmail conectado",f"Cuenta conectada correctamente:\n{email}",parent=win)
            try: win.grab_release()
            except Exception: pass
            win.destroy()

        def finish_ui_error(exc):
            status_var.set(f"Error: {exc}")
            self.log(f"Error conectando Gmail: {exc}")
            btn_complete.config(state="normal")
            messagebox.showerror("Gmail",str(exc),parent=win)

        def complete_auth():
            callback=return_var.get().strip()
            if not callback:
                messagebox.showwarning("Gmail","Pega primero la URL completa que quedó en la barra del navegador.",parent=win)
                return
            btn_complete.config(state="disabled")
            status_var.set("Completando autorización con Google...")

            def worker():
                try:
                    email=self.gmail.finish_manual_auth(callback)
                except Exception as exc:
                    self.root.after(0,lambda e=exc:finish_ui_error(e))
                else:
                    self.root.after(0,lambda em=email:finish_ui_success(em))

            threading.Thread(target=worker,daemon=True).start()

        ttk.Button(complete_bar,text="Pegar desde portapapeles",command=paste_return).pack(side="left",padx=(0,8))
        btn_complete=ttk.Button(complete_bar,text="Completar conexión",command=complete_auth)
        btn_complete.pack(side="left",padx=8)

        def close_dialog():
            try: self.gmail.cancel_manual_auth()
            except Exception: pass
            try: win.grab_release()
            except Exception: pass
            win.destroy()

        ttk.Button(complete_bar,text="Cancelar",command=close_dialog).pack(side="right")

        ttk.Label(
            body,
            text=(
                "No pegues aquí la URL de accounts.google.com. Debes pegar la URL final de 127.0.0.1 "
                "que aparece DESPUÉS de autorizar. Esa URL solo se usa localmente para terminar OAuth."
            ),
            foreground="#555555",
            wraplength=840
        ).pack(anchor="w",pady=(16,0))

        try:
            url,redirect_uri=self.gmail.start_manual_auth()
            url_var.set(url)
            status_var.set(
                f"Vínculo listo. El retorno será {redirect_uri} — si el navegador dice conexión rechazada, es esperado."
            )
            entry.icursor(0)
        except Exception as e:
            url_var.set("")
            status_var.set(f"Error: {e}")
            self.log(f"Error preparando OAuth de Gmail: {e}")

        win.protocol("WM_DELETE_WINDOW",close_dialog)

    def start_bot(self):
        if self.worker and self.worker.is_alive():return
        self.save_config(silent=True)
        self.worker=BotWorker(self.cfg,self.db,self.gmail,self.processor,self.log,self._worker_status)
        self.worker.start();self.btn_start.config(state='disabled');self.btn_stop.config(state='normal')
        self.bot_status.config(text='Gmail: recepción iniciando...')
        self.log('Gmail recibirá solicitudes; la cola de Polaris se ejecuta automáticamente cuando la pantalla está libre.')

    def _worker_status(self,running):
        self.ui_events.put(('gmail_status',bool(running)))
    def _set_worker_ui(self,running):
        self.bot_status.config(text='Gmail: recibiendo solicitudes' if running else 'Gmail: recepción detenida')
        self.btn_start.config(state='disabled' if running else 'normal')
        self.btn_stop.config(state='normal' if running else 'disabled')

    def stop_bot(self):
        if self.worker:self.worker.stop()
        self.log('Deteniendo SOLO la recepción Gmail. Para detener nuevos trabajos de pantalla usa Pausar cola.')

    def reset_checkpoint(self):
        if self.worker and self.worker.is_alive():
            self.log('Detén solo la recepción Gmail antes de cambiar el punto de inicio.');return
        if self.cola.ocupada:
            self.log('No se abre una confirmación mientras Polaris está capturando.');return
        if not messagebox.askyesno('Punto de inicio Gmail',
            'Se ignorarán correos anteriores al nuevo punto de recepción que aún no estén en cola. '
            'Los trabajos ya en cola no se borran. ¿Confirmas?',default='no'):return
        def work():
            try:
                self.gmail.connect(interactive=False);hid=self.gmail.current_history_id()
                self.db.meta_set('gmail_history_id',hid);self.log('Punto de inicio de recepción actualizado.')
            except Exception:self.log('No se pudo cambiar el checkpoint Gmail.')
        threading.Thread(target=work,daemon=True).start()

    def process_latest(self):
        def work():
            try:
                self.gmail.connect(interactive=False)
                for msg in self.gmail.latest_matching(10):
                    if not self.db.existe(msg['id']):
                        self.processor.process(msg);return
                self.log('No hay solicitudes recientes nuevas. Los correos registrados no se repiten.')
            except Exception as exc:
                self._avisar_error('SISTEMA','Recepción manual Gmail',exc,
                    safe=bool(self.cfg['app'].get('modo_prueba',True)),leer_polaris=False)
                self.log('No se pudo recibir el correo. Ninguna operación se inició en pantalla desde este hilo.')
        threading.Thread(target=work,daemon=True,name='ARY-Recepcion-Manual').start()

    def test_polaris(self):
        if not self._preparar_prueba_vm():return
        estacion=normalize_station(self.test_station.get())
        rfc=self.test_rfc.get().strip().upper().replace(' ','');ticket=self.test_ticket.get().strip()
        fecha_ticket=self.test_fecha_ticket.get().strip()
        fp=normalize_payment(self.test_payment.get());uso=normalize_cfdi_usage(self.test_cfdi.get())
        em=self.test_email.get().strip();safe=bool(self.mode_var.get())
        sol=Solicitud(estacion=estacion,rfc=rfc,ticket=ticket,fecha_ticket=fecha_ticket,forma_pago=fp,uso_cfdi=uso,correo_destino=em,remitente=em)
        try:
            from .cola_modelo import factura_datos
            from .factura_final import RegistroFinalFactura
            factura_datos(sol)
            previo=RegistroFinalFactura(BASE,estacion,ticket).comprobar_libre()
            if previo:
                self.log('Ticket con checkpoint local previo ('+previo+'). Se volverá a CONSULTAR en Polaris; el checkpoint no se toma como factura.')
        except Exception as exc:self.direct_status.set(str(exc));return
        if not safe:
            if self.cola.ocupada:
                self.direct_status.set('Polaris está trabajando. Espera a que termine antes de confirmar otra prueba REAL.')
                return
            if not messagebox.askyesno('Encolar factura REAL',
                f'Estación: {estacion}\nRFC: {rfc}\nFolio: {ticket}\nFecha ticket: {fecha_ticket}\nPago: {fp}\nUso CFDI: {uso}\nCorreo: {em}\n\n'
                'La cola emitirá una factura real y solicitará el correo cuando llegue su turno. '
                'Confirma que el ticket no fue facturado: esta versión no consulta la BD. '
                'Los avisos al cliente se envían si están activados. ¿Autorizar y encolar?',default='no'):return
        try:
            job,new=self.cola.encolar_factura(sol,safe=safe)
            self._direct_job_id=job['id']
            self.direct_status.set('RECIBIDA: '+job['id'][:12]+'. Se ejecutará automáticamente cuando Polaris esté libre.')
            self.log('Solicitud de factura agregada a la cola única de pantalla.')
        except Exception as exc:self.direct_status.set(str(exc))

    def test_station_only(self):
        st=normalize_station(self.test_station.get())
        if not st:self.direct_status.set('Selecciona una estación válida.');return
        self._enqueue_aux('ESTACION',{'estacion':st})

    def test_payment_only(self):
        self._enqueue_aux('PAGO',{'forma_pago':normalize_payment(self.test_payment.get())})

    def test_cleanup_only(self):
        self._enqueue_aux('LIMPIEZA',{})

    def _enqueue_aux(self,kind,data):
        if not self._preparar_prueba_vm():return
        try:
            job,_=self.cola.encolar_prueba(kind,data)
            self._direct_job_id=job['id'];self.direct_status.set('Prueba recibida. Se ejecutará automáticamente cuando Polaris esté libre.')
        except Exception as exc:self.direct_status.set(str(exc))

    def _exigir_polaris_vm(self):
        if self.polaris is not None:
            return True
        detail=getattr(self,'_polaris_error','') or 'El módulo no pudo inicializarse.'
        msg=('POLARIS NO DISPONIBLE: '+detail+
             '. Revisa logs/arranque_vm.txt o ejecuta COMPROBAR_VM.bat. No se encoló ninguna operación.')
        self.log(msg)
        self.direct_status.set(msg)
        self.estado_vm_var.set(msg)
        self.nb.select(self.tab_test)
        return False

    def _preparar_prueba_vm(self):
        if not self._exigir_polaris_vm():return False
        st=self.cola.store.status()
        if st.get('bloqueo'):
            msg=('COLA EN REVISIÓN: '+str(st.get('motivo',''))+
                 ' Ve a Cola y avisos, selecciona la prueba/captura pendiente y usa Liberar revisión '+
                 'después de revisar Polaris. No se añadió otra prueba.')
            self.direct_status.set(msg);self.log(msg)
            return False
        if self.cola.ocupada:
            self.direct_status.set('Polaris está trabajando. Espera a que termine. No se añadió otra prueba.')
            return False
        # Aplica al bot los valores que el operador está viendo, no un usuario antiguo del JSON.
        user=self.user_var.get().strip()
        exe=self.exe_var.get().strip()
        if not user or not exe or not Path(exe).is_file():
            self.direct_status.set('En Configuración completa el usuario y la ruta real de Polaris.exe.')
            self.nb.select(self.tab_cfg);return False
        try:
            self.cfg['polaris']['usuario']=user
            self.cfg['polaris']['executable']=exe
            self.polaris.pcfg=self.cfg['polaris']
            self.cfgm.save()
            if not self.polaris.has_password():
                self.direct_status.set('Guarda en Configuración la contraseña de '+user+' en ESTA cuenta de Windows.')
                self.nb.select(self.tab_cfg);return False
        except Exception as exc:
            self.direct_status.set('No se pudo preparar Polaris: '+str(exc));self.log(str(exc));return False
        return True

    def _estado_vm(self):
        try:
            if not self.polaris:
                text='POLARIS NO DISPONIBLE: '+getattr(self,'_polaris_error','Revisa COMPROBAR_VM.bat')
            else:
                st=self.cola.store.status()
                if st.get('activa'):
                    text='POLARIS TRABAJANDO: '+str(getattr(self.polaris,'etapa_alerta','Preparando ventana...'))
                elif st.get('bloqueo'):
                    text='COLA EN REVISIÓN: '+str(st.get('motivo',''))+' | Cola y avisos > Liberar revisión.'
                elif st.get('pausada'):
                    text='COLA PAUSADA: '+str(st.get('motivo',''))+' | Cola y avisos > Iniciar / continuar cola.'
                else:
                    text='POLARIS DISPONIBLE | Cola libre | Adaptador VM sobre el original; pantalla sin modificar.'
            self.estado_vm_var.set(text)
        except Exception as exc:
            self.estado_vm_var.set('No se pudo leer el estado de la cola: '+str(exc))
        self.root.after(600,self._estado_vm)

    def _drain_cola_events(self):
        try:
            for _ in range(100):
                kind,ident=self.ui_events.get_nowait()
                if kind=='gmail_status':self._set_worker_ui(ident)
                elif kind=='startup':
                    self.gmail_status.config(text=ident)
                elif kind=='trabajo' and ident==self._direct_job_id:
                    job=self.cola.store.get(ident)
                    if job:self.direct_status.set(job['estado']+': '+job['motivo'])
        except queue.Empty:pass
        self.root.after(150,self._drain_cola_events)

    def pick_exe(self):
        if hasattr(self,'cola') and self.cola.ocupada:
            self.log('Espera a que termine Polaris antes de cambiar estos ajustes.');return
        p=filedialog.askopenfilename(title="Selecciona Polaris.exe",filetypes=[("Ejecutable","*.exe"),("Todos","*.*")])
        if p: self.exe_var.set(p)
    def pick_tess(self):
        if hasattr(self,'cola') and self.cola.ocupada:
            self.log('Espera a que termine Polaris antes de cambiar estos ajustes.');return
        p=filedialog.askopenfilename(title="Selecciona tesseract.exe",filetypes=[("Ejecutable","*.exe"),("Todos","*.*")])
        if p: self.tess_var.set(p)

    def save_password(self):
        if not self._exigir_polaris_vm():return
        if hasattr(self,'cola') and self.cola.ocupada:
            self.log('Espera a que termine Polaris antes de cambiar estos ajustes.');return
        try:
            self.cfg["polaris"]["usuario"]=self.user_var.get().strip() or "SUPERVISOR"; self.cfgm.save(); self.polaris.pcfg=self.cfg["polaris"]; self.polaris.save_password(self.pass_var.get()); self.pass_var.set(""); self.log("Contraseña de Polaris guardada en el Administrador de credenciales de Windows."); self._startup_checks()
        except Exception as e: messagebox.showerror("Contraseña",str(e))

    def save_config(self,silent=False):
        if hasattr(self,'cola') and self.cola.ocupada:
            self.log('La configuración de Polaris no se cambia durante una captura.');return
        try:
            self.cfg["polaris"]["executable"]=self.exe_var.get().strip(); self.cfg["polaris"]["usuario"]=self.user_var.get().strip() or "SUPERVISOR"; self.cfg["ocr"]["tesseract_cmd"]=self.tess_var.get().strip(); self.cfg["app"]["intervalo_segundos"]=int(self.interval_var.get()); self.cfg["app"]["auto_responder_faltantes"]=bool(self.auto_missing.get()); self.cfg["app"]["auto_responder_completada"]=bool(self.auto_done.get()); self.cfg["app"]["asunto_palabras"]=[x.strip().upper() for x in self.subjects_var.get().split(",") if x.strip()]; self.cfg["app"]["modo_prueba"]=bool(self.mode_var.get()); self.cfgm.save()
            if self.polaris: self.polaris.pcfg=self.cfg["polaris"]
            if not silent: self.log("Configuración guardada."); self._startup_checks()
        except Exception as e:
            if not silent: messagebox.showerror("Configuración",str(e))

    def close(self):
        if self.cola.ocupada:
            self.log('Espera a que termine la operación de Polaris antes de cerrar.');return
        alerts=getattr(self,'alertas',None)
        if self.cola.avisos.sending or (alerts and alerts.sending):
            self.log('Espera a que termine el envío de correo antes de cerrar.');return
        # No persistimos una pausa manual al cerrar. Las solicitudes EN_COLA
        # quedan guardadas y arrancarán automáticamente al abrir de nuevo.
        if self.worker and self.worker.is_alive():self.worker.stop()
        self.cola.detener()
        if alerts:alerts.detener()
        self.log('Cerrando hilos. Las solicitudes pendientes quedan guardadas y se reanudarán automáticamente al abrir.')
        def wait():
            threads=[getattr(self.cola,'thread',None),getattr(self.cola.avisos,'thread',None),self.worker,
                     getattr(alerts,'_thread',None) if alerts else None]
            if any(t and t.is_alive() for t in threads):self.root.after(200,wait)
            else:self.root.destroy()
        self.root.after(200,wait)


def main():
    if sys.platform != "win32":
        root=tk.Tk(); root.withdraw(); messagebox.showerror("Windows requerido","Este bot controla Polaris y debe ejecutarse en Windows."); return
    from .instancia import InstanciaBot
    root=tk.Tk();guard=InstanciaBot(BASE)
    try:guard.adquirir()
    except Exception as exc:
        root.withdraw();messagebox.showerror('Instancia de ARY Bot',str(exc));root.destroy();return
    try:App(root);root.mainloop()
    finally:guard.cerrar()
