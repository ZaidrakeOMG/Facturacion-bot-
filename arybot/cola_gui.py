"""Panel local de solicitudes, estimados y buzón de seguimiento."""
from __future__ import annotations
import tkinter as tk
from tkinter import ttk,messagebox
from datetime import datetime
from .cola_modelo import ajustes_cola,tracking,texto_cliente,ESTADOS_REVISION,ESTADOS_TERMINALES

class ColaTab(ttk.Frame):
    def __init__(self,parent,app):
        super().__init__(parent,padding=10);self.app=app;self.cola=app.cola;self._closed=False
        self.status=tk.StringVar();self.options=ajustes_cola(app.cfg)
        ttk.Label(self,text='Cola de trabajo y avisos al cliente',font=('Segoe UI',13,'bold')).pack(anchor='w')
        self.explanation=ttk.Label(self,text='Gmail recibe y los correos se envían en segundo plano. Una sola operación controla Polaris. '
                           'No uses teclado/ratón en este escritorio mientras el robot está capturando.',
                  wraplength=950,foreground='#374c60')
        self.explanation.pack(anchor='w',pady=(5,8))
        self.bind('<Configure>',self._reflow,add='+')
        self.status_label=ttk.Label(self,textvariable=self.status,wraplength=950);self.status_label.pack(fill='x',pady=(0,8))
        bar=ttk.Frame(self);bar.pack(fill='x')
        ttk.Button(bar,text='▶ Iniciar / continuar cola',command=self.resume).pack(side='left',padx=(0,8))
        ttk.Button(bar,text='Pausar después de la actual',command=self.pause).pack(side='left',padx=(0,8))
        ttk.Button(bar,text='Actualizar',command=self.refresh).pack(side='left')
        self.nb=ttk.Notebook(self);self.nb.pack(fill='both',expand=True,pady=(10,0))
        jobs=ttk.Frame(self.nb,padding=6);opts=ttk.Frame(self.nb,padding=12);mails=ttk.Frame(self.nb,padding=6)
        self.nb.add(jobs,text='Solicitudes');self.nb.add(opts,text='Avisos y tiempos');self.nb.add(mails,text='Correos al cliente')
        cols=('id','tipo','origen','modo','estacion','folio','estado')
        self.tree=ttk.Treeview(jobs,columns=cols,show='headings',height=12,selectmode='browse')
        labels=('Solicitud','Tipo','Origen','Modo','Estación','Folio / RFC','Estado')
        widths=(115,80,80,60,95,125,205)
        for c,label,w in zip(cols,labels,widths):self.tree.heading(c,text=label);self.tree.column(c,width=w,minwidth=40)
        vs=ttk.Scrollbar(jobs,orient='vertical',command=self.tree.yview);self.tree.configure(yscrollcommand=vs.set)
        vs.pack(side='right',fill='y');self.tree.pack(fill='both',expand=True)
        acts=ttk.Frame(jobs);acts.pack(fill='x',pady=8)
        for txt,fn in [('Ver detalle',self.detail),('Revisar alta seleccionada',self.open_alta),
                       ('Liberar revisión',self.release),('Retirar de la cola',self.cancel)]:
            ttk.Button(acts,text=txt,command=fn).pack(side='left',padx=(0,7))
        ttk.Label(jobs,text='Liberar revisión NO toca Polaris ni repite la operación. Primero resuelve la captura/aviso manualmente.',
                  wraplength=850).pack(anchor='w')
        self.enabled=tk.BooleanVar(value=self.options['avisar_cliente'])
        self.in_safe=tk.BooleanVar(value=self.options['avisar_en_modo_seguro'])
        self.fact=tk.IntVar(value=self.options['minutos_factura']);self.alta=tk.IntVar(value=self.options['minutos_alta'])
        self.margin=tk.IntVar(value=self.options['margen_minutos'])
        ttk.Checkbutton(opts,text='Enviar avisos al cliente: recepción, proceso, resultado o revisión',variable=self.enabled).grid(row=0,column=0,columnspan=3,sticky='w',pady=5)
        ttk.Checkbutton(opts,text='Enviar avisos REALES también en MODO SEGURO (identificados como PRUEBA)',variable=self.in_safe).grid(row=1,column=0,columnspan=3,sticky='w',pady=5)
        for r,label,var in [(2,'Minutos base por factura',self.fact),(3,'Minutos base por alta',self.alta),(4,'Margen adicional (minutos)',self.margin)]:
            ttk.Label(opts,text=label).grid(row=r,column=0,sticky='e',pady=6,padx=8)
            ttk.Spinbox(opts,from_=1,to=240,textvariable=var,width=7).grid(row=r,column=1,sticky='w')
        ttk.Label(opts,text='Valores iniciales configurables, no tiempos medidos. El estimado suma los trabajos anteriores. '
                           'Con una revisión/pausa o un alta anterior sin aprobación, no se promete una hora de finalización.',
                  wraplength=820,foreground='#516171').grid(row=5,column=0,columnspan=3,sticky='w',pady=10)
        ttk.Button(opts,text='Guardar avisos y tiempos',command=self.save).grid(row=6,column=0,sticky='w',pady=5)
        ttk.Button(opts,text='Ver ejemplo del mensaje (NO envía)',command=self.preview).grid(row=6,column=1,columnspan=2,sticky='w',padx=8)
        self.opts_status=tk.StringVar(value='Los avisos no contienen mensajes técnicos, OCR ni adjuntos del cliente.')
        ttk.Label(opts,textvariable=self.opts_status,wraplength=800).grid(row=7,column=0,columnspan=3,sticky='w',pady=10)
        opts.columnconfigure(2,weight=1)
        mcols=('fecha','tipo','correo','estado')
        self.mailtree=ttk.Treeview(mails,columns=mcols,show='headings',height=12,selectmode='browse')
        for c,label,w in zip(mcols,('Creado','Aviso','Destinatario','Estado'),(145,115,255,190)):
            self.mailtree.heading(c,text=label);self.mailtree.column(c,width=w)
        ms=ttk.Scrollbar(mails,orient='vertical',command=self.mailtree.yview);self.mailtree.configure(yscrollcommand=ms.set)
        ms.pack(side='right',fill='y');self.mailtree.pack(fill='both',expand=True)
        mb=ttk.Frame(mails);mb.pack(fill='x',pady=8)
        ttk.Button(mb,text='Ver mensaje',command=self.mail_detail).pack(side='left',padx=6)
        ttk.Button(mb,text='Reenviar aviso seleccionado (REAL)',command=self.retry_mail).pack(side='left',padx=6)
        ttk.Label(mails,text='ENVIADA = Gmail aceptó el mensaje; no confirma recepción. Reenviar SOLO manda el aviso, nunca repite Polaris.',wraplength=870).pack(anchor='w')
        self._poll=self.after(500,self._tick);self.bind('<Destroy>',self._destroy,add='+')
        self.refresh()

    def _reflow(self,event):
        if event.widget is self:
            width=max(320,event.width-28)
            self.explanation.configure(wraplength=width)
            if hasattr(self,'status_label'):self.status_label.configure(wraplength=width)

    def _can_dialog(self):
        # Consultar detalles/opciones del bot no debe dejar una pausa persistente.
        # Solo se bloquean estos cuadros mientras Polaris está realmente trabajando.
        if self.cola.ocupada:
            self.status.set('Polaris está trabajando. Espera a que termine antes de abrir este cuadro.')
            return False
        return True

    def selected(self):
        sel=self.tree.selection()
        return self.cola.store.get(sel[0]) if sel else None

    def _show(self,title,text):
        win=tk.Toplevel(self);win.title(title);win.geometry('830x500')
        box=tk.Text(win,wrap='word',padx=12,pady=12);box.pack(fill='both',expand=True);box.insert('1.0',text);box.configure(state='disabled')
        ttk.Button(win,text='Cerrar',command=win.destroy).pack(pady=8)

    def resume(self):
        if not self._can_dialog():return
        rows=[r for r in self.cola.store.list(10000) if r['estado']=='EN_COLA']
        real=sum(not r['seguro'] for r in rows)
        if real and not messagebox.askyesno('Continuar cola con trabajos reales',
            f'Hay {real} solicitud(es) REAL(es) en cola. Las facturas pueden timbrarse y enviarse. '
            'Las altas se preparan y requieren aprobación individual antes de guardar.\n\n'
            'Confirma que Polaris no tiene capturas/avisos pendientes y deja libre el teclado y ratón. ¿Continuar?',parent=self,default='no'):return
        try:self.cola.iniciar_cola()
        except Exception as exc:messagebox.showwarning('Cola',str(exc),parent=self)
        self.refresh()

    def pause(self):self.cola.pausar();self.refresh()
    def release(self):
        if not self._can_dialog():return
        job=self.selected()
        if not job:return
        if not messagebox.askyesno('Liberar revisión local',
            'Primero revisa Polaris y termina o cancela MANUALMENTE cualquier captura/aviso pendiente. '
            'Este botón no guarda, no cierra ventanas, no cancela documentos ni repite el trabajo.\n\n'
            '¿Ya resolviste la pantalla y deseas liberar la revisión?',parent=self,default='no'):return
        try:self.cola.liberar_revision(job['id'],confirmado=True)
        except Exception as exc:messagebox.showwarning('Revisión',str(exc),parent=self)
        self.refresh()

    def cancel(self):
        if not self._can_dialog():return
        job=self.selected()
        if not job:return
        if not messagebox.askyesno('Retirar solicitud','¿Retirar esta solicitud pendiente antes de iniciar? No se cancela ninguna factura existente.',parent=self,default='no'):return
        try:self.cola.cancelar(job['id'])
        except Exception as exc:messagebox.showwarning('Cola',str(exc),parent=self)
        self.refresh()

    def detail(self):
        if not self._can_dialog():return
        job=self.selected()
        if not job:return
        data=job['datos'];eta=self.cola.store.eta(job['id'],ajustes_cola(self.app.cfg)['margen_minutos'])
        lines=[tracking(job['id']),job['tipo']+' — '+job['estado'],'Modo: '+('SEGURO' if job['seguro'] else 'REAL'),
               'Origen: '+job['origen'],'Correo de avisos: '+job['correo'],
               'Estimado: '+(f'{eta[0]}–{eta[1]} minutos' if eta else 'Sin estimado fiable mientras espera revisión/pausa'),
               'Detalle: '+job['motivo'],'']
        lines += [f'{k}: {v}' for k,v in data.items()]
        self._show('Detalle de solicitud','\n'.join(lines))

    def open_alta(self):
        if not self._can_dialog():return
        job=self.selected()
        if job and job['tipo']=='ALTA':
            self.app.alta_clientes_tab.load_job(job)
            self.app.nb.select(self.app.alta_clientes_tab)

    def save(self):
        try:
            raw={'cola':{'avisar_cliente':self.enabled.get(),'avisar_en_modo_seguro':self.in_safe.get(),
                         'minutos_factura':self.fact.get(),'minutos_alta':self.alta.get(),'margen_minutos':self.margin.get()}}
            opts=ajustes_cola(raw)
            if opts['avisar_en_modo_seguro'] and not self.options['avisar_en_modo_seguro']:
                if not self._can_dialog():return
                if not messagebox.askyesno('Avisos reales en pruebas',
                    'Los clientes de solicitudes en MODO SEGURO recibirán avisos identificados como PRUEBA. '
                    'No se envían automáticamente los avisos históricos SIMULADOS. ¿Activar?',parent=self,default='no'):return
            self.app.cfg['cola']=opts;self.app.cfgm.save();self.options=opts
            self.opts_status.set('Ajustes guardados. Se usarán para nuevas solicitudes. Los trabajos ya admitidos conservan su tiempo base.')
        except Exception as exc:messagebox.showwarning('Avisos',str(exc),parent=self)

    def preview(self):
        if not self._can_dialog():return
        opts=ajustes_cola(self.app.cfg)
        job={'id':'a1b2c3d4e5f600000000000000000000','tipo':'FACTURA','estado':'EN_COLA','seguro':False,
             'datos':{'estacion':'ARY VI','ticket':'EJEMPLO'}}
        subject,body=texto_cliente(job,'recibida',opts,eta=(opts['minutos_factura'],opts['minutos_factura']+opts['margen_minutos']))
        self._show('Vista previa — NO envía',subject+'\n\n'+body)

    def _mail(self):
        sel=self.mailtree.selection()
        if not sel:return None
        return next((r for r in self.cola.store.notices(10000) if r['id']==sel[0]),None)
    def mail_detail(self):
        if not self._can_dialog():return
        row=self._mail()
        if row:self._show('Aviso '+row['estado'],'Para: '+row['correo']+'\n'+row['asunto']+'\n\n'+row['cuerpo']+'\n\n'+row['detalle'])
    def retry_mail(self):
        if not self._can_dialog():return
        row=self._mail()
        if not row:return
        if not messagebox.askyesno('Reenviar aviso REAL',
            'Revisa Gmail Enviados: el intento anterior pudo haberse recibido.\n\nPara: '+row['correo']+'\nAsunto: '+row['asunto']+
            '\n\nEsto SOLO reenvía el aviso, nunca registra clientes ni vuelve a facturar. ¿Confirmas?',parent=self,default='no'):return
        try:self.cola.avisos.reenviar(row['id'])
        except Exception as exc:messagebox.showwarning('Aviso',str(exc),parent=self)
        self.refresh()

    @staticmethod
    def _sync(tree,rows):
        selected=tree.selection();existing=set(tree.get_children());new=set()
        for index,(ident,values) in enumerate(rows):
            new.add(ident)
            if ident in existing:tree.item(ident,values=values)
            else:tree.insert('', 'end', iid=ident, values=values)
            tree.move(ident,'',index)
        for ident in existing-new:tree.delete(ident)
        if selected and selected[0] in new:tree.selection_set(selected[0])

    def refresh(self):
        state=self.cola.store.status();c=state['conteos']
        self.status.set(('POLARIS TRABAJANDO' if state['activa'] else ('COLA PAUSADA' if state['pausada'] else 'COLA ACTIVA'))+
                        f" | Pendientes: {c.get('EN_COLA',0)} | "+state['motivo'])
        # La pestaña Solicitudes representa trabajo pendiente/revisión, no historial.
        # Los estados terminales ya resueltos no se quedan aparentando estar en cola.
        rows=[r for r in self.cola.store.list() if r['estado'] not in ESTADOS_TERMINALES]
        self._sync(self.tree,[(r['id'],(tracking(r['id']),r['tipo'],r['origen'],'SEGURO' if r['seguro'] else 'REAL',
                              r['datos'].get('estacion',''),r['datos'].get('ticket') or r['datos'].get('rfc',''),r['estado'])) for r in rows])
        notices=self.cola.store.notices()
        self._sync(self.mailtree,[(r['id'],(datetime.fromtimestamp(r['creado']).strftime('%d/%m %H:%M:%S'),r['evento'],r['correo'],r['estado'])) for r in notices])
    def _tick(self):
        if self._closed:return
        try:self.refresh()
        except Exception:pass
        self._poll=self.after(1000,self._tick)
    def _destroy(self,event):
        if event.widget is self:
            self._closed=True
            try:self.after_cancel(self._poll)
            except tk.TclError:pass
