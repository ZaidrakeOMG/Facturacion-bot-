"""Panel local de solicitudes, estimados y buzón de seguimiento."""
from __future__ import annotations
import tkinter as tk
from tkinter import ttk,messagebox
from datetime import datetime
from .cola_modelo import ajustes_cola,tracking,texto_cliente,ESTADOS_REVISION,ESTADOS_TERMINALES,resultado_facturacion

class ColaTab(ttk.Frame):
    def __init__(self,parent,app):
        super().__init__(parent,padding=10);self.app=app;self.cola=app.cola;self._closed=False
        self.status=tk.StringVar();self.options=ajustes_cola(app.cfg)
        ttk.Label(self,text='Resultados de facturación y avisos',font=('Segoe UI',13,'bold')).pack(anchor='w')
        self.explanation=ttk.Label(self,text='El bot atiende automáticamente una solicitud a la vez y pasa a la siguiente cuando Polaris queda libre. SÍ = flujo completado; NO = no completado. Si no se confirma un timbrado, no se vuelve a emitir el mismo folio. '
                           'No uses teclado ni ratón mientras Polaris está capturando.',
                  wraplength=950,foreground='#374c60')
        self.explanation.pack(anchor='w',pady=(5,8))
        self.bind('<Configure>',self._reflow,add='+')
        self.status_label=ttk.Label(self,textvariable=self.status,wraplength=950);self.status_label.pack(fill='x',pady=(0,8))
        bar=ttk.Frame(self);bar.pack(fill='x')
        ttk.Button(bar,text='▶ Reanudar atención automática',command=self.resume).pack(side='left',padx=(0,8))
        ttk.Button(bar,text='Detener después de la actual',command=self.pause).pack(side='left',padx=(0,8))
        ttk.Button(bar,text='Actualizar',command=self.refresh).pack(side='left')
        self.nb=ttk.Notebook(self);self.nb.pack(fill='both',expand=True,pady=(10,0))
        jobs=ttk.Frame(self.nb,padding=6);opts=ttk.Frame(self.nb,padding=12);mails=ttk.Frame(self.nb,padding=6)
        self.nb.add(jobs,text='Solicitudes');self.nb.add(opts,text='Avisos y tiempos');self.nb.add(mails,text='Correos al cliente')
        cols=('id','tipo','estacion','folio','facturado')
        self.tree=ttk.Treeview(jobs,columns=cols,show='headings',height=12,selectmode='browse')
        labels=('Solicitud','Trámite','Estación','Folio / RFC','¿Se facturó?')
        widths=(160,100,140,190,120)
        for c,label,w in zip(cols,labels,widths):self.tree.heading(c,text=label);self.tree.column(c,width=w,minwidth=40)
        vs=ttk.Scrollbar(jobs,orient='vertical',command=self.tree.yview);self.tree.configure(yscrollcommand=vs.set)
        vs.pack(side='right',fill='y');self.tree.pack(fill='both',expand=True)
        acts=ttk.Frame(jobs);acts.pack(fill='x',pady=8)
        for txt,fn in [('Ver detalle',self.detail),('Borrar seleccionada',self.archive_one),
                       ('Borrar todas',self.archive_all),('Revisar alta',self.open_alta),
                       ('Resolver bloqueo comprobado',self.release)]:
            ttk.Button(acts,text=txt,command=fn).pack(side='left',padx=(0,7))
        ttk.Label(jobs,text='Borrar retira la solicitud de esta lista y cancela las que aún no comenzaron. '
                    'Conserva el registro y el motivo en SQLite para auditoría y estadísticas. '
                    'No cancela una factura ni borra un CFDI de Polaris.',
                  wraplength=850).pack(anchor='w')
        mail_enabled=app.cfg.get('app',{}).get('gmail_habilitado',True)
        self.enabled=tk.BooleanVar(value=self.options['avisar_cliente'] and mail_enabled)
        self.in_safe=tk.BooleanVar(value=self.options['avisar_en_modo_seguro'] and mail_enabled)
        self.fact=tk.IntVar(value=self.options['minutos_factura']);self.alta=tk.IntVar(value=self.options['minutos_alta'])
        self.margin=tk.IntVar(value=self.options['margen_minutos'])
        ttk.Checkbutton(opts,text='Enviar avisos al cliente: recepción, proceso, resultado o revisión',variable=self.enabled,state='normal' if mail_enabled else 'disabled').grid(row=0,column=0,columnspan=3,sticky='w',pady=5)
        ttk.Checkbutton(opts,text='Enviar avisos identificados como PRUEBA durante las pruebas',variable=self.in_safe,state='normal' if mail_enabled else 'disabled').grid(row=1,column=0,columnspan=3,sticky='w',pady=5)
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
        ttk.Button(mb,text='Reenviar aviso seleccionado',command=self.retry_mail,state='normal' if mail_enabled else 'disabled').pack(side='left',padx=6)
        ttk.Label(mails,text='Historial de avisos. En esta edición Gmail está desactivado; Polaris conserva su propio envío de factura.',wraplength=870).pack(anchor='w')
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
        if real and not messagebox.askyesno('Reanudar atención automática',
            f'Hay {real} solicitud(es) de emisión pendientes. Se procesarán una por una y podrán timbrarse. '
            'Asegúrate de que Polaris no tenga documentos o avisos abiertos y deja libre el teclado y ratón. ¿Continuar?',parent=self,default='no'):return
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

    def release_all(self):
        if not self._can_dialog():
            return
        try:
            resumen = self.cola.resumen_liberacion_masiva()
        except Exception as exc:
            messagebox.showwarning('Liberación masiva', str(exc), parent=self)
            return
        n = resumen['liberables']
        excluidas = resumen['protegidas']
        if resumen['ejecutando']:
            messagebox.showwarning('Liberación masiva',
                'Espera a que la operación actual de Polaris termine.', parent=self)
            return
        if not n:
            messagebox.showinfo('Liberación masiva',
                'No hay revisiones que puedan cerrarse en lote. '
                + (f'{excluidas} revisión(es) protegida(s) requieren revisión individual.'
                   if excluidas else 'Las solicitudes restantes no son revisiones liberables.'),
                parent=self)
            return
        mensaje = (
            f'Se cerrarán {n} incidencias antiguas no protegidas.\n'
            f'Se conservarán {excluidas} caso(s) sin confirmar por posible '
            'timbrado, correo, alta o cierre incierto.\n\n'
            'CONFIRMACIÓN: revisé que las solicitudes a cerrar NO tienen '
            'documentos timbrados, envíos ni guardados pendientes en Polaris.\n\n'
            'Esto solo cierra su seguimiento interno. '
            'No borra registros, no toca Polaris, no emite facturas, '
            'no envía avisos y no repite trámites.\n\n'
            '¿Confirmas liberar las revisiones indicadas?'
        )
        if not messagebox.askyesno('Liberar todas las revisiones', mensaje,
                                   parent=self, default='no'):
            return
        try:
            resultado = self.cola.liberar_revisiones_masivas(confirmado=True)
        except Exception as exc:
            messagebox.showwarning('Liberación masiva', str(exc), parent=self)
            self.refresh()
            return
        self.refresh()
        messagebox.showinfo('Liberación masiva',
            f"Se cerraron {resultado['liberables']} revisión(es).\n"
            f"Se conservaron {resultado['protegidas']} por seguridad.\n\n"
            'No se alteraron facturas, registros de Polaris ni correos.',
            parent=self)

    def cancel_all(self):
        # No modal while Polaris is capturing; require a second click instead.
        if self.cola.ocupada:
            if not getattr(self,'_bulk_cancel_armed',False):
                self._bulk_cancel_armed=True
                self.status.set('Vuelve a pulsar Retirar todos los pendientes para confirmar. No se interrumpe la actual.')
                self.after(10000,lambda:setattr(self,'_bulk_cancel_armed',False))
                return
        else:
            n=self.cola.store.status()['conteos'].get('EN_COLA',0)
            if not n:return
            if not messagebox.askyesno('Retirar pendientes',
                    f'¿Retirar las {n} solicitudes pendientes? Se conserva la actual y el historial.',
                    parent=self,default='no'):return
        self._bulk_cancel_armed=False
        try:self.cola.cancelar_pendientes(confirmado=True)
        except Exception as exc:self.status.set(str(exc));return
        self.refresh()

    def cancel(self):
        if not self._can_dialog():return
        job=self.selected()
        if not job:return
        if not messagebox.askyesno('Retirar solicitud','¿Retirar esta solicitud pendiente antes de iniciar? No se cancela ninguna factura existente.',parent=self,default='no'):return
        try:self.cola.cancelar(job['id'])
        except Exception as exc:messagebox.showwarning('Cola',str(exc),parent=self)
        self.refresh()

    def _puede_archivar(self):
        # Solo el candado del consumidor indica que el bot está capturando AHORA.
        # Una EJECUTANDO huérfana en SQLite no debe impedir la limpieza tras
        # comprobar la pantalla de Polaris y confirmar con el operador.
        if self.cola._consumer.locked():
            self.status.set('Polaris está capturando. No se puede borrar mientras '
                            'una factura se está procesando. Espera a que termine.')
            return False
        return True

    def archive_one(self):
        if not self._puede_archivar():return
        job=self.selected()
        if not job:return
        codigo=tracking(job['id'])
        uncertain=job['estado'] in ('EJECUTANDO','PREPARADA_FACTURA',
                                    'PREPARADA_ALTA','ACEPTAR_ALTA') or (
                    self.cola.store.status().get('bloqueo')==job['id'])
        message=(
            '¿Quitar '+codigo+' de la lista?\\n\\n'
            'Si todavía no se ha iniciado, se CANCELARÁ y no será procesada. '
            'Si ya terminó, solo se archivará.\\n\\n'
            'IMPORTANTE: la operación y sus datos quedan en SQLite. '
            'Esto NO cancela facturas ni borra CFDI en Polaris.\\n\\n'
        )
        if uncertain:
            message+=(
                'Esta solicitud está atorada o tiene una ventana reservada. '
                'Antes de confirmar, comprueba en Polaris que no existe un '
                'timbrado, envío o alta todavía ejecutándose y cierra la '
                'pantalla pendiente manualmente. El resultado podría ser incierto.\\n\\n')
        message+='¿Confirmas que verificaste Polaris y deseas quitarla de la lista?'
        if not messagebox.askyesno('Borrar solicitud',message,parent=self,default='no'):return
        try:
            resultado=self.cola.archivar_solicitudes(job['id'],confirmado=True)
        except Exception as exc:
            messagebox.showwarning('Borrar solicitud',str(exc),parent=self)
            self.refresh();return
        self.refresh()
        if resultado['requiere_reanudar']:
            messagebox.showinfo('Solicitud borrada de la lista',
                'Se archivó la solicitud.\\n\\nEl registro de un posible CFDI '
                'incierto quedó protegido. Verifica que Polaris está libre '
                'y pulsa Reanudar atención automática cuando corresponda.',
                parent=self)

    def archive_all(self):
        if not self._puede_archivar():return
        visibles=self.cola.store.list(100000)
        if not visibles:
            messagebox.showinfo('Limpiar lista','No hay solicitudes visibles para borrar.',parent=self)
            return
        pendientes=sum(r['estado']=='EN_COLA' for r in visibles)
        atascadas=sum(r['estado'] in ('EJECUTANDO','PREPARADA_FACTURA',
                       'PREPARADA_ALTA','PRUEBA_PENDIENTE','ACEPTAR_ALTA') for r in visibles)
        pregunta=(f'¿Borrar de la lista las {len(visibles)} solicitudes visibles?\\n\\n'
                  f'Solicitudes todavía pendientes que se cancelarán: {pendientes}.\\n'
                  f'Solicitudes atoradas o preparadas: {atascadas}.\\n\\n'
                  'Esto NO elimina facturas de Polaris ni los registros de SQLite. '
                  'El historial seguirá disponible para las estadísticas y para '
                  'impedir el reenvío de un ticket incierto.\\n\\n'
                  'CONFIRMACIÓN: revisé que Polaris no tiene una emisión o alta '
                  'en curso y quiero limpiar esta lista.')
        if not messagebox.askyesno('Borrar todas las solicitudes',pregunta,
                                   parent=self,default='no'):return
        try:
            resultado=self.cola.archivar_solicitudes(confirmado=True)
        except Exception as exc:
            messagebox.showwarning('Borrar todas',str(exc),parent=self)
            self.refresh();return
        self.refresh()
        aviso=(f"Se quitaron {resultado['archivadas']} solicitudes de la pantalla.\\n"
               f"Pendientes canceladas: {resultado['canceladas']}.\\n"
               f"Casos inciertos protegidos: {resultado['riesgos_protegidos']}.\\n\\n"
               'Los datos permanecen guardados en SQLite.')
        if resultado['requiere_reanudar']:
            aviso+='\\n\\nRevisa Polaris y reanuda la atención automática.'
        messagebox.showinfo('Lista limpiada',aviso,parent=self)

    def detail(self):
        if not self._can_dialog():return
        job=self.selected()
        if not job:return
        data=job['datos']
        resultado,motivo=resultado_facturacion(job)
        visible=('SÍ' if resultado=='SÍ' else 'NO') if job['tipo']=='FACTURA' else 'No aplica (no es factura)'
        lines=[tracking(job['id']), 'Trámite: '+job['tipo'],
               '¿Se facturó?: '+visible,
               'Motivo: '+motivo,
               'Origen: '+job['origen'], 'Correo de avisos: '+job['correo'], '']
        if resultado=='SIN CONFIRMAR':
            lines.insert(4,'IMPORTANTE: No se ha confirmado si Polaris emitió el CFDI. '
                            'No intentes facturar otra vez el mismo ticket hasta verificarlo.')
        lines += [f'{k}: {v}' for k,v in data.items()]
        self._show('Resultado y detalles','\n'.join(lines))

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
        if not messagebox.askyesno('Reenviar aviso',
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
        rows=self.cola.store.list()
        pendientes=c.get('EN_COLA',0)
        modo_seguro=bool(self.app.cfg.get('app',{}).get('modo_prueba',True))
        emision_pendiente=any(r['estado']=='EN_COLA' and not r['seguro'] for r in rows)
        if state['pausada']:
            causa=state['motivo'] or 'Revisa Polaris antes de reanudar.'
            estado='ATENCIÓN DETENIDA: '+causa
        elif state['activa']:
            estado='Polaris procesando una solicitud; al terminar continuará con la siguiente.'
        elif modo_seguro and emision_pendiente:
            estado='NO SE PUEDE FACTURAR: MODO SEGURO activado. Autoriza emisión para atender los tickets pendientes.'
        else:
            estado='Atención automática activa.'
        self.status.set(f'{estado} | Solicitudes por atender: {pendientes}')
        tabla=[]
        for r in rows:
            resultado,_motivo=resultado_facturacion(r)
            # La columna de facturación solo muestra SÍ / NO para FACTURAS.
            # NO = sin confirmación de factura en este momento, no una
            # declaración de que Polaris jamás timbró el ticket.
            visible=('SÍ' if resultado=='SÍ' else 'NO') if r['tipo']=='FACTURA' else '—'
            tabla.append((r['id'],(tracking(r['id']),r['tipo'],r['datos'].get('estacion',''),
                r['datos'].get('ticket') or r['datos'].get('rfc',''),visible)))
        self._sync(self.tree,tabla)
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
