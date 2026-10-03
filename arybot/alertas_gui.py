"""Pestaña interna: destinatarios, opciones, ejemplo local y envío confirmado."""
from __future__ import annotations
import tkinter as tk
from tkinter import ttk, messagebox
from .alertas_modelo import normalizar_config, destinatarios_internos, contenido_correo, CATEGORIAS


class AlertasTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, padding=10)
        self.app = app; self.service = getattr(app, 'alertas', None); self._closed=False
        if not self.service:
            ttk.Label(self, text='Alertas internas no disponibles. Revisa permisos de la carpeta local del bot.').pack(anchor='w')
            return
        self.canvas = tk.Canvas(self, highlightthickness=0)
        scroll = ttk.Scrollbar(self, orient='vertical', command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right',fill='y'); self.canvas.pack(side='left',fill='both',expand=True)
        self.body=ttk.Frame(self.canvas); self.window=self.canvas.create_window((0,0),window=self.body,anchor='nw')
        self.canvas.bind('<Configure>',lambda e:self.canvas.itemconfigure(self.window,width=e.width))
        self.body.bind('<Configure>',lambda e:self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        self.body.columnconfigure(0,weight=1)
        self.options=self.service.ajustes(); self.email=tk.StringVar()
        self.vars={k:tk.BooleanVar(value=self.options[k]) for k in (
            'activas','enviar_en_modo_seguro','leer_ventana','ocr_respaldo','adjuntar_ventana','incluir_contexto_cliente')}
        self.cats={k:tk.BooleanVar(value=v) for k,v in self.options['categorias'].items()}
        self.status=tk.StringVar(value=self.service.config_error or 'Sin envíos hasta guardar y activar los destinatarios.')
        self._build()
        self.actualizar()
        self._poll=self.after(2000,self._tick)
        self.bind('<Destroy>',self._destroy,add='+')

    def _build(self):
        b=self.body
        ttk.Label(b,text='Alertas internas — Grupo ARY',font=('Segoe UI',14,'bold')).grid(row=0,column=0,sticky='w')
        ttk.Label(b,text='Avisos de alta, facturación y envío. No se mandan al cliente ni responden su hilo.',
                  wraplength=840).grid(row=1,column=0,sticky='w',pady=(4,8))
        ttk.Checkbutton(b,text='Activar correos de alertas internas',variable=self.vars['activas']).grid(row=2,column=0,sticky='w')
        recipients=ttk.LabelFrame(b,text='Destinatarios internos (todos reciben las categorías marcadas)',padding=8)
        recipients.grid(row=3,column=0,sticky='ew',pady=8);recipients.columnconfigure(0,weight=1)
        line=ttk.Frame(recipients);line.grid(row=0,column=0,sticky='ew');line.columnconfigure(0,weight=1)
        ent=ttk.Entry(line,textvariable=self.email,width=45);ent.grid(row=0,column=0,sticky='ew',padx=(0,6))
        ent.bind('<Return>',lambda e:self.agregar())
        ttk.Button(line,text='Agregar correo',command=self.agregar).grid(row=0,column=1,padx=4)
        ttk.Button(line,text='Quitar seleccionado',command=self.quitar).grid(row=0,column=2,padx=4)
        self.list=tk.Listbox(recipients,height=3,exportselection=False)
        self.list.grid(row=1,column=0,sticky='ew',pady=6)
        for e in self.options['destinatarios']:self.list.insert('end',e)
        ttk.Label(recipients,text='Solo @grupoary.com y @grupoary.com.mx. La lista se guarda en este equipo.',
                  wraplength=830,foreground='#526173').grid(row=2,column=0,sticky='w')
        box=ttk.LabelFrame(b,text='Qué avisar y cómo leer la ventana',padding=8)
        box.grid(row=4,column=0,sticky='ew');box.columnconfigure(1,weight=1)
        for r,(k,var) in enumerate(self.cats.items()):
            ttk.Checkbutton(box,text=CATEGORIAS[k],variable=var).grid(row=r,column=0,sticky='w',padx=(0,18),pady=2)
        for r,(k,label) in enumerate((
            ('leer_ventana','Leer el mensaje de la ventana de Polaris'),
            ('ocr_respaldo','OCR local solo si el texto no se puede leer'),
            ('adjuntar_ventana','Adjuntar recorte de la ventana (puede contener datos fiscales)'),
            ('incluir_contexto_cliente','Incluir RFC, folio y correo del cliente en el aviso'))):
            ttk.Checkbutton(box,text=label,variable=self.vars[k]).grid(row=r,column=1,sticky='w',pady=2)
        ttk.Checkbutton(b,text='Enviar alertas reales también cuando MODO SEGURO esté marcado',
                        variable=self.vars['enviar_en_modo_seguro']).grid(row=5,column=0,sticky='w',pady=(8,3))
        ttk.Label(b,text='Sin esta opción, los fallos en modo seguro quedan SIMULADOS/locales. No se envía correo.\n'
                            'OCR usa Tesseract de Configuración; si falta, el aviso sale con el error del bot. Nunca pulsa Aceptar.',
                  wraplength=840,foreground='#526173').grid(row=6,column=0,sticky='w',pady=(0,5))
        bar=ttk.Frame(b);bar.grid(row=7,column=0,sticky='w',pady=6)
        ttk.Button(bar,text='Guardar alertas',command=self.guardar).pack(side='left',padx=(0,7))
        ttk.Button(bar,text='Ver ejemplo (SIN ENVIAR)',command=self.ejemplo).pack(side='left',padx=7)
        ttk.Button(bar,text='Enviar correo de prueba REAL',command=self.probar).pack(side='left',padx=7)
        ttk.Label(b,textvariable=self.status,wraplength=840,foreground='#8a4c10').grid(row=8,column=0,sticky='w',pady=(2,6))
        ttk.Label(b,text='Historial local de alertas',font=('Segoe UI',11,'bold')).grid(row=9,column=0,sticky='w')
        frame=ttk.Frame(b);frame.grid(row=10,column=0,sticky='ew',pady=5);frame.columnconfigure(0,weight=1)
        self.tree=ttk.Treeview(frame,columns=('fecha','operacion','estado','veces'),show='headings',height=5,selectmode='browse')
        for key,title,width in [('fecha','Fecha',160),('operacion','Operación',240),('estado','Estado del aviso',240),('veces','Veces',60)]:
            self.tree.heading(key,text=title);self.tree.column(key,width=width,minwidth=45)
        self.tree.grid(row=0,column=0,sticky='ew')
        ys=ttk.Scrollbar(frame,orient='vertical',command=self.tree.yview);ys.grid(row=0,column=1,sticky='ns');self.tree.configure(yscrollcommand=ys.set)
        self.tree.bind('<Double-1>',lambda e:self.ver())
        actions=ttk.Frame(b);actions.grid(row=11,column=0,sticky='w',pady=5)
        for text,fn in [('Actualizar',self.actualizar),('Ver mensaje',self.ver),
                        ('Reenviar seleccionada (REAL)',self.reenviar),('Cancelar pendiente',self.cancelar)]:
            ttk.Button(actions,text=text,command=fn).pack(side='left',padx=(0,8))
        ttk.Label(b,text='ENVIADA = Gmail aceptó el mensaje; no acredita recepción. ERROR_ENVIO / ENVIO_INCIERTO no se reenvían solos.\n'
                         'Solo se reenvía el aviso interno. No se repite el alta, la factura ni el correo de Polaris.',
                  wraplength=840,foreground='#526173').grid(row=12,column=0,sticky='w',pady=(0,8))

    def _dialogo_permitido(self):
        cola=getattr(self.app,'cola',None)
        if cola is not None and cola.ocupada:
            self.status.set('Polaris está capturando. Espera o pausa después de la operación actual para abrir diálogos. Los envíos en segundo plano continúan.')
            return False
        return True

    def agregar(self):
        if not self._dialogo_permitido():return
        try:
            values=destinatarios_internos(list(self.list.get(0,'end'))+[self.email.get()])
            if not self.email.get().strip():raise ValueError('Escribe el correo a agregar.')
        except ValueError as exc:messagebox.showwarning('Correo interno',str(exc),parent=self);return
        self.list.delete(0,'end')
        for v in values:self.list.insert('end',v)
        self.email.set('');self.status.set('Lista modificada. Pulsa Guardar alertas para aplicarla.')

    def quitar(self):
        for i in reversed(self.list.curselection()):self.list.delete(i)
        self.status.set('Lista modificada; guarda para aplicar la baja.')

    def guardar(self):
        if not self._dialogo_permitido():return
        if self.email.get().strip():
            messagebox.showwarning('Correo pendiente','Pulsa Agregar correo antes de guardar.',parent=self);return False
        try:
            data={k:bool(v.get()) for k,v in self.vars.items()}
            data.update(destinatarios=list(self.list.get(0,'end')),categorias={k:bool(v.get()) for k,v in self.cats.items()})
            valid=normalizar_config(data);old=self.service.ajustes()
            if valid['activas'] and valid != old:
                text='Los errores se enviarán a:\n'+ '\n'.join(valid['destinatarios'])
                text+='\n\n'+('También se enviarán errores ocurridos en MODO SEGURO.' if valid['enviar_en_modo_seguro'] else 'En MODO SEGURO solo se guardarán localmente.')
                if valid['adjuntar_ventana']:text+='\nSe autoriza adjuntar el recorte del aviso; puede contener datos fiscales.'
                text+='\nNo envía al cliente. No guarda nada en Polaris. ¿Guardar estos ajustes?'
                if not messagebox.askyesno('Confirmar alertas internas',text,parent=self,default='no'):return False
            self.service.guardar_ajustes(valid)
            self.status.set('Ajustes guardados. '+('Alertas activadas.' if valid['activas'] else 'Envíos desactivados.'))
            return True
        except Exception as exc:messagebox.showwarning('Alertas',str(exc),parent=self);return False

    def ejemplo(self):
        if not self._dialogo_permitido():return
        ident=self.service.prueba(enviar=False);self.actualizar();self.tree.selection_set(ident);self.ver()
        self.status.set('Ejemplo local creado. No se envió ningún correo ni se operó Polaris.')

    def probar(self):
        if not self._dialogo_permitido():return
        if not self.guardar():return
        options=self.service.ajustes()
        if not options['activas']:
            messagebox.showwarning('Alertas desactivadas','Activa las alertas y guarda destinatarios primero.',parent=self);return
        if not messagebox.askyesno('Enviar prueba REAL',
                'Este botón SÍ envía un correo, incluso en MODO SEGURO.\n\nDestinatarios:\n'+
                '\n'.join(options['destinatarios'])+'\n\nSolo lleva datos de ejemplo, sin captura ni datos de clientes. ¿Enviar?',parent=self,default='no'):return
        try:
            self.service.prueba(enviar=True,autorizado=True);self.actualizar()
            self.status.set('Prueba puesta en cola. Consulta el estado y el buzón de los destinatarios.')
        except Exception as exc:messagebox.showerror('Prueba',str(exc),parent=self)

    def seleccionado(self):
        ids=self.tree.selection()
        return self.service.cola.obtener(ids[0]) if ids else None

    def ver(self):
        if not self._dialogo_permitido():return
        row=self.seleccionado()
        if not row:return
        event=row['evento'];subject,body=contenido_correo(event)
        win=tk.Toplevel(self);win.title('Alerta '+row['id'][:10]);win.geometry('850x600')
        text=tk.Text(win,wrap='word',padx=12,pady=12);text.pack(fill='both',expand=True)
        text.insert('1.0','ESTADO: '+row['estado']+'\nPara: '+', '.join(event['destinatarios'])+
                    '\nAsunto: '+subject+'\nIncidencia del envío: '+row['error_envio']+'\n\n'+body)
        text.configure(state='disabled')
        ttk.Button(win,text='Cerrar',command=win.destroy).pack(pady=6)

    def reenviar(self):
        if not self._dialogo_permitido():return
        row=self.seleccionado()
        if not row:return
        opts=self.service.ajustes()
        text='Se reenviará SOLO el aviso interno a los destinatarios guardados:\n'+'\n'.join(opts['destinatarios'])
        if row['estado'] in ('ENVIADA','ENVIO_INCIERTO'):
            text+='\n\nPuede generar un correo duplicado. Revisa primero Gmail Enviados y el ID de esta alerta.'
        text+='\n\nNo repite el alta ni la facturación. ¿Confirmas el envío REAL?'
        if not messagebox.askyesno('Reenviar alerta',text,parent=self,default='no'):return
        try:self.service.reenviar(row['id'],autorizado=True);self.actualizar()
        except Exception as exc:messagebox.showwarning('Alerta',str(exc),parent=self)

    def cancelar(self):
        if not self._dialogo_permitido():return
        row=self.seleccionado()
        if not row:return
        if not messagebox.askyesno('Cancelar aviso','¿Cancelar solo este correo pendiente? No cambia la solicitud ni Polaris.',parent=self,default='no'):return
        try:self.service.cola.cancelar(row['id']);self.actualizar()
        except Exception as exc:messagebox.showwarning('Alerta',str(exc),parent=self)

    def actualizar(self):
        if not self.service:return
        old=self.tree.selection();rows=self.service.cola.recientes()
        children=self.tree.get_children()
        if children:self.tree.delete(*children)
        for row in rows:
            e=row['evento'];self.tree.insert('', 'end', iid=row['id'], values=(e['fecha'][:19].replace('T',' '),
                CATEGORIAS.get(e['categoria'],e['categoria']),row['estado'],row['repeticiones']))
        if old and self.tree.exists(old[0]):self.tree.selection_set(old[0])

    def _tick(self):
        if self._closed:return
        try:self.actualizar()
        except Exception:self.status.set('No se pudo leer el historial local de alertas.')
        self._poll=self.after(2000,self._tick)

    def _destroy(self,event):
        if event.widget is self:
            self._closed=True
            try:self.after_cancel(self._poll)
            except (tk.TclError,AttributeError):pass
