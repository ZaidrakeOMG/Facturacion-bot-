from __future__ import annotations
import json
import os
import tkinter as tk
from tkinter import ttk, messagebox


class DiagnosticoTab(ttk.Frame):
    def __init__(self, notebook, app):
        super().__init__(notebook,padding=10)
        self.app=app;self.diag=app.diagnostico
        self.status=tk.StringVar(value='Sin errores capturados en esta sesión.')
        top=ttk.Frame(self);top.pack(fill='x')
        ttk.Label(top,text='Diagnóstico de Polaris',font=('Segoe UI',13,'bold')).pack(side='left')
        ttk.Button(top,text='Actualizar',command=self.refresh).pack(side='right',padx=4)
        ttk.Button(top,text='Capturar estado ahora',command=self.capture).pack(side='right',padx=4)
        ttk.Button(top,text='Abrir carpeta',command=self.open_folder).pack(side='right',padx=4)
        ttk.Button(top,text='Copiar último error',command=self.copy_last).pack(side='right',padx=4)
        ttk.Label(self,textvariable=self.status,wraplength=980,foreground='#7a2020').pack(fill='x',pady=(8,6))
        ttk.Label(self,text='La bitácora guarda pasos, clases de controles, foco, ventana activa, campos vacío/no vacío, captura de pantalla y excepción. No guarda RFC, correo, folio ni contraseñas.',wraplength=980,foreground='#3a4a5a').pack(fill='x',pady=(0,8))
        self.box=tk.Text(self,wrap='none',font=('Consolas',9),state='disabled')
        sy=ttk.Scrollbar(self,orient='vertical',command=self.box.yview);sx=ttk.Scrollbar(self,orient='horizontal',command=self.box.xview)
        self.box.configure(yscrollcommand=sy.set,xscrollcommand=sx.set)
        self.box.pack(fill='both',expand=True,side='left');sy.pack(side='right',fill='y');sx.pack(side='bottom',fill='x')
        self.after(800,self._tick)

    def _tick(self):
        try:self.refresh()
        except Exception:pass
        self.after(1400,self._tick)

    def refresh(self):
        last=self.diag.ultimo_error()
        if last:
            self.status.set('Último error: '+str(last.get('etapa',''))+' | '+str(last.get('error','')))
        events=self.diag.ultimos_eventos(250)
        lines=[]
        for e in events:
            d=e.get('datos') or {}
            extra=''
            if d:
                extra=' | '+json.dumps(d,ensure_ascii=False,separators=(',',':'))
            lines.append(f"{e.get('fecha','')} | {e.get('estado','')} | {e.get('etapa','')} | {e.get('detalle','')}{extra}")
        self.box.configure(state='normal');self.box.delete('1.0','end');self.box.insert('end','\n'.join(lines));self.box.see('end');self.box.configure(state='disabled')

    def capture(self):
        bot=getattr(self.app,'polaris',None)
        path=self.diag.capturar(getattr(bot,'etapa_alerta','CAPTURA MANUAL') if bot else 'CAPTURA MANUAL',bot=bot,manual=True)
        if path: self.status.set('Captura guardada en '+str(path));self.refresh()

    def open_folder(self):
        try:
            os.startfile(str(self.diag.folder))
        except Exception as e:messagebox.showerror('Diagnóstico',str(e),parent=self)

    def copy_last(self):
        last=self.diag.ultimo_error()
        if not last:
            messagebox.showinfo('Diagnóstico','Todavía no hay un error capturado.',parent=self);return
        compact={k:last.get(k) for k in ('id','fecha','etapa','error','contexto')}
        text=json.dumps(compact,ensure_ascii=False,indent=2)
        self.clipboard_clear();self.clipboard_append(text);self.update()
        self.status.set('Último error copiado al portapapeles.')
