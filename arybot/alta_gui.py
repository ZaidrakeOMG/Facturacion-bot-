"""Pestaña independiente del flujo de facturación existente."""
from __future__ import annotations
import queue
import threading
import tkinter as tk
from tkinter import ttk,messagebox
from .cliente_model import AltaCliente,AltaClienteError
from .alta_clientes import AltaClientes
from .parser import STATIONS


class AltaClientesTab(ttk.Frame):
    def __init__(self,parent,app):
        super().__init__(parent,padding=10)
        self.app=app
        self.busy=False;self.holding=False;self._events=queue.Queue();self._frozen=[]
        self.service=app.alta_service
        self.job_id=None;self._last_state=None
        self.station=tk.StringVar(value=app.test_station.get())
        self.rfc=tk.StringVar();self.idcif=tk.StringVar();self.phone=tk.StringVar();self.email=tk.StringVar()
        self.reviewed=tk.BooleanVar(value=False)
        self.status=tk.StringVar(value='Listo: Clientes de Efectivo. La prueba no pulsa Aceptar.')
        self.nombre=tk.StringVar();self.cp=tk.StringVar();self.numero=tk.StringVar()
        self._build()
        self._trace=app.mode_var.trace_add('write',lambda *_:self._sync_buttons())
        self._review_trace=self.reviewed.trace_add('write',lambda *_:self._sync_buttons())
        self._closed=False
        self._poll=self.after(100,self._drain)
        self.bind('<Destroy>',self._on_destroy,add='+')

    @property
    def bloquea_otros_flujos(self): return self.busy or self.holding

    def _build(self):
        self.canvas=tk.Canvas(self,highlightthickness=0,borderwidth=0)
        scroll=ttk.Scrollbar(self,orient='vertical',command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right',fill='y')
        self.canvas.pack(side='left',fill='both',expand=True)
        body=ttk.Frame(self.canvas,padding=(0,0,12,12))
        body.columnconfigure(1,weight=1)
        self.body=body
        item=self.canvas.create_window((0,0),window=body,anchor='nw')
        body.bind('<Configure>',lambda _e:self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        def resize(event):
            self.canvas.itemconfigure(item,width=event.width)
            for child in body.winfo_children():
                if isinstance(child,ttk.Label) and int(float(child.cget('wraplength') or 0))>0:
                    child.configure(wraplength=max(350,event.width-32))
        self.canvas.bind('<Configure>',resize)
        self.canvas.bind('<MouseWheel>',lambda e:self.canvas.yview_scroll(int(-e.delta/120),'units'))
        self.canvas.bind('<Button-4>',lambda e:self.canvas.yview_scroll(-2,'units'))
        self.canvas.bind('<Button-5>',lambda e:self.canvas.yview_scroll(2,'units'))
        ttk.Label(body,text='Alta de cliente de efectivo',font=('Segoe UI',13,'bold')).grid(row=0,column=0,columnspan=3,sticky='w')
        ttk.Label(body,text='Clientes de Efectivo → Nuevo → RFC / idCIF → lupa → Teléfono 1 / Correo Electrónico → Aceptar.',
                  wraplength=880).grid(row=1,column=0,columnspan=3,sticky='w',pady=(4,12))
        ttk.Label(body,text='Estación de trabajo').grid(row=2,column=0,sticky='e',padx=8,pady=4)
        self.context=ttk.Combobox(body,textvariable=self.station,values=list(STATIONS),state='readonly',width=35)
        self.context.grid(row=2,column=1,sticky='w')
        ttk.Label(body,text='Contexto del sistema; no es un quinto dato fiscal.',foreground='#526173').grid(row=3,column=1,sticky='w')
        ttk.Label(body,text='Los cuatro datos del cliente',font=('Segoe UI',10,'bold')).grid(row=4,column=0,columnspan=3,sticky='w',pady=(12,4))
        self.entries=[]
        for i,(name,var) in enumerate((('RFC',self.rfc),('idCIF (11 dígitos)',self.idcif),('Teléfono',self.phone),('Correo electrónico',self.email)),5):
            ttk.Label(body,text=name).grid(row=i,column=0,sticky='e',padx=8,pady=5)
            ent=ttk.Entry(body,textvariable=var,width=54)
            ent.grid(row=i,column=1,sticky='ew',padx=(0,20));self.entries.append(ent)
        ttk.Label(body,text='Polaris consulta los datos fiscales con su lupa. No se pide nombre, domicilio ni régimen al cliente.',
                  wraplength=880,foreground='#526173').grid(row=9,column=0,columnspan=3,sticky='w',pady=(6,9))
        bar=ttk.Frame(body);bar.grid(row=10,column=0,columnspan=3,sticky='w')
        self.btn_prepare=ttk.Button(bar,text='Preparar alta (SIN ACEPTAR)',command=self.prepare)
        self.btn_prepare.pack(side='left',padx=(0,10))
        self.btn_accept=ttk.Button(bar,text='Aceptar alta en Polaris',command=self.accept,state='disabled')
        self.btn_accept.pack(side='left',padx=(0,10))
        self.btn_release=ttk.Button(bar,text='Liberar revisión',command=self.release,state='disabled')
        self.btn_release.pack(side='left')
        self.chk=ttk.Checkbutton(body,text='Revisión del operador: confirmé en Polaris que este RFC no está registrado.',
                                variable=self.reviewed)
        self.chk.grid(row=11,column=0,columnspan=3,sticky='w',pady=(10,2))
        ttk.Label(body,text='La búsqueda automática de duplicados aún no está conectada. Esta casilla NO se pedirá al cliente en la página.',
                  wraplength=880,foreground='#8a4c10').grid(row=12,column=0,columnspan=3,sticky='w')
        box=ttk.LabelFrame(body,text='Resultado de la preparación',padding=8)
        box.grid(row=13,column=0,columnspan=3,sticky='ew',pady=(10,6));box.columnconfigure(1,weight=1)
        for r,(label,var) in enumerate((('Nombre / razón social',self.nombre),('Código postal',self.cp),('Número de cliente',self.numero))):
            ttk.Label(box,text=label).grid(row=r,column=0,sticky='e',padx=(0,8),pady=2)
            ttk.Entry(box,textvariable=var,state='readonly').grid(row=r,column=1,sticky='ew')
        ttk.Label(body,textvariable=self.status,wraplength=880,justify='left').grid(row=14,column=0,columnspan=3,sticky='w',pady=(6,4))
        ttk.Label(body,text='MODO SEGURO: solo prepara. Aceptar sí registra al cliente mediante Polaris; no genera una factura.\n'
                            'Gmail también puede recibir solicitudes de alta. El guardado conserva la revisión y autorización del operador.',
                  wraplength=880,foreground='#526173').grid(row=15,column=0,columnspan=3,sticky='w',pady=(2,0))
        self._sync_buttons()

    def _sol(self):
        return AltaCliente.crear(self.station.get(),self.rfc.get(),self.idcif.get(),self.phone.get(),self.email.get())

    def _freeze(self):
        # Solo se bloquean los campos de ESTA solicitud, nunca la recepción Gmail.
        pass

    def _thaw(self):
        self._frozen=[]

    def _sync_buttons(self):
        on=bool(self.service)
        self.btn_prepare.configure(state='normal' if on and not self.busy and not self.holding else 'disabled')
        can_accept=(on and not self.busy and self.holding and self.service.pendiente is not None
                    and self.reviewed.get() and not self.app.mode_var.get())
        self.btn_accept.configure(state='normal' if can_accept else 'disabled')
        self.btn_release.configure(state='normal' if self.holding and not self.busy else 'disabled')
        for entry in self.entries: entry.configure(state='disabled' if self.busy or self.holding else 'normal')
        self.context.configure(state='disabled' if self.busy or self.holding else 'readonly')
        self.chk.configure(state='disabled' if self.busy else 'normal')

    def _ready(self):
        if not self.service:
            messagebox.showerror('Polaris','El control de Polaris no está disponible.',parent=self);return False
        return True

    def _run(self,kind,func,sol=None):
        raise RuntimeError('La pantalla se opera únicamente a través de la cola compartida.')

    def prepare(self):
        if not self._ready() or self.busy or self.holding:return
        try:
            sol=self._sol()
            job,_=self.app.cola.encolar_alta(sol,safe=bool(self.app.mode_var.get()))
        except Exception as exc:
            self.status.set(str(exc));return
        self.job_id=job['id'];self._last_state=None
        self.reviewed.set(False);self.nombre.set('');self.cp.set('');self.numero.set('')
        self.status.set('ALTA EN COLA. Se preparará por turno sin Aceptar. Gmail y avisos siguen activos.')
        self.busy=True;self._sync_buttons()

    def accept(self):
        if not self._ready() or self.busy:return
        if self.app.mode_var.get():
            self.status.set('MODO SEGURO: no se pulsa Aceptar.');return
        if not self.reviewed.get():
            self.status.set('Primero confirme que revisó en Polaris que el RFC no está registrado.');return
        if self.app.cola.ocupada:return
        try:sol=self._sol()
        except Exception as exc:self.status.set(str(exc));return
        if not messagebox.askyesno('Registrar cliente REAL',
            f'Se pulsará Aceptar UNA VEZ en Clientes de Efectivo.\n\nEstación: {sol.estacion}\nRFC: {sol.rfc}\n'
            f'Nombre: {self.nombre.get()}\n\nEsto guarda un cliente mediante Polaris; no factura. ¿Autorizas?',
            parent=self,default='no'):return
        try:
            self.app.cola.aceptar_alta(self.job_id,autorizado=True,inexistencia_revisada=True)
            self.busy=True;self.status.set('Aceptar autorizado para esta alta, una sola vez.');self._sync_buttons()
        except Exception as exc:self.status.set(str(exc))

    def release(self):
        if self.busy or self.app.cola.ocupada:return
        if not messagebox.askyesno('Liberar revisión',
            'Resuelve MANUALMENTE el formulario o aviso pendiente en Polaris antes de continuar. '
            'Este botón no guarda, no cancela ni cierra ventanas. ¿Ya lo revisaste?',parent=self,default='no'):return
        try:self.app.cola.liberar_revision(self.job_id,confirmado=True)
        except Exception as exc:self.status.set(str(exc));return
        self.holding=False;self.busy=False;self.reviewed.set(False);self._sync_buttons()
        self.status.set('Revisión liberada. Inicia/continúa la cola cuando Polaris esté listo.')

    def _drain(self):
        if self.job_id:
            try:
                job=self.app.cola.store.get(self.job_id)
                if job:
                    state=job['estado'];self.busy=state in {'EN_COLA','EJECUTANDO','ACEPTAR_ALTA'}
                    self.holding=state in {'PREPARADA_ALTA','REVISION_REQUERIDA'}
                    if state!=self._last_state:
                        self._last_state=state;self.status.set(state+': '+job['motivo'])
                        data=job['resultado']
                        if state=='PREPARADA_ALTA':
                            self.nombre.set(data.get('nombre',''));self.cp.set(data.get('codigo_postal',''))
                            self.numero.set('Alta NO guardada')
                        elif state=='ALTA_CONFIRMADA':
                            self.numero.set(data.get('numero_cliente',''));self.reviewed.set(False)
                        self._sync_buttons()
            except Exception:pass
        if not self._closed and self.winfo_exists():self._poll=self.after(300,self._drain)

    def load_job(self,job):
        if job['tipo']!='ALTA':return
        self.job_id=job['id'];self._last_state=None
        d=job['datos'];self.station.set(d.get('estacion',''));self.rfc.set(d.get('rfc',''))
        self.idcif.set(d.get('idcif',''));self.phone.set(d.get('telefono',''));self.email.set(d.get('correo',''))
        self.reviewed.set(False);self.nombre.set('');self.cp.set('');self.numero.set('')
        self.busy=job['estado'] in {'EN_COLA','EJECUTANDO','ACEPTAR_ALTA'}
        self.holding=job['estado'] in {'PREPARADA_ALTA','REVISION_REQUERIDA'}
        self._sync_buttons()

    def _on_destroy(self,event):
        if event.widget is not self:return
        self._closed=True
        try:self.after_cancel(self._poll)
        except tk.TclError:pass
        try:self.app.mode_var.trace_remove('write',self._trace)
        except tk.TclError:pass
        try:self.reviewed.trace_remove('write',self._review_trace)
        except tk.TclError:pass
