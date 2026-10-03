"""Arranque opcional del bot existente más portal local; no sustituye sus archivos."""
from __future__ import annotations
import json
from pathlib import Path
import sys
import threading
import webbrowser


def main():
    if sys.platform != 'win32':
        print('El bot de Polaris requiere Windows con una sesión de escritorio abierta.')
        return
    import tkinter as tk
    from tkinter import ttk, messagebox
    root = tk.Tk()
    root.withdraw()
    try:
        from waitress import create_server
        from .gui import App, BASE
        from .instancia import InstanciaBot
        from .portal_http import PortalWSGI, cargar_ajustes
        from .portal_red import PortalRedWSGI
    except ImportError as exc:
        messagebox.showerror('Instalación del portal',
            'Falta una dependencia. Ejecuta instalar_portal.py con el mismo Python del bot.\n\n' + str(exc), parent=root)
        root.destroy()
        return
    guard = InstanciaBot(BASE)
    try:
        guard.adquirir()
        settings = cargar_ajustes(BASE)
    except Exception as exc:
        messagebox.showerror('Portal ARY', str(exc), parent=root)
        guard.cerrar()
        root.destroy()
        return

    class PortalApp(App):
        def __init__(self, parent):
            self.portal = None
            self.web_server = None
            self.web_thread = None
            super().__init__(parent)
            self.portal = PortalWSGI(self.cola, self.cfg, settings, BASE)
            self._build_portal_tab()
            try:
                args = dict(host=settings['escuchar_en'], port=settings['puerto'], threads=4,
                            max_request_body_size=16384, max_request_header_size=16384,
                            connection_limit=50, channel_timeout=30, expose_tracebacks=False,
                            clear_untrusted_proxy_headers=True)
                # No usar trusted_proxy: PortalRedWSGI verifica primero el par TCP real.
                network_app = PortalRedWSGI(self.portal, settings)
                self.web_server = create_server(network_app, **args)
                self.web_thread = threading.Thread(target=self.web_server.run, daemon=True, name='ARY-Portal-HTTP')
                self.web_thread.start()
                self.portal_status.set('Portal activo en ' + settings['escuchar_en'] + ':' + str(settings['puerto'])
                    + ('. Solicitudes desde IIS: ' + settings['proxy_confiable'] if network_app.lan else '. Solo modo local.'))
                self.log('Portal web activo. Las solicitudes se registran en la misma Cola y avisos.')
            except Exception as exc:
                self.portal.pausar_recepcion()
                self.portal_status.set('Portal no iniciado: revisa el puerto y la instalación de Waitress.')
                self.log('El portal no arrancó: ' + str(exc))
            self.root.title('ARY Facturación Bot v3.1.9 + Portal web 1.1 LAN')
            self.root.deiconify()

        def _build_portal_tab(self):
            tab = ttk.Frame(self.nb, padding=16)
            self.nb.add(tab, text='Portal web')
            ttk.Label(tab, text='Alta de clientes y facturación desde la página', font=('Segoe UI', 14, 'bold')).pack(anchor='w')
            self.portal_status = tk.StringVar(value='Iniciando portal...')
            ttk.Label(tab, textvariable=self.portal_status, wraplength=850).pack(anchor='w', pady=(12, 12))
            self.portal_mode = tk.StringVar()
            ttk.Label(tab, textvariable=self.portal_mode, wraplength=850, foreground='#96256a').pack(anchor='w', pady=(0, 12))
            bar = ttk.Frame(tab)
            bar.pack(anchor='w', pady=6)
            ttk.Button(bar, text='Abrir formulario', command=self._open_page).pack(side='left', padx=(0, 10))
            self.pause_web_btn = ttk.Button(bar, text='Pausar recepción web', command=self._toggle_receiving)
            self.pause_web_btn.pack(side='left')
            self.real_web = tk.BooleanVar(value=settings['permitir_facturacion_real'])
            ttk.Checkbutton(tab, variable=self.real_web, command=self._change_real,
                text='Permitir solicitudes REALES del portal cuando el bot esté fuera de MODO SEGURO').pack(anchor='w', pady=(18, 10))
            info = (
                '1. Mantén MODO SEGURO. Comprueba primero /api/facturacion/salud desde IIS; luego configura el proxy.\n\n'
                '2. Las entradas se muestran como «Portal web» en Cola y avisos. Pulsa Iniciar cola cuando Polaris esté listo. '
                'El portal nunca inicia una segunda automatización de pantalla.\n\n'
                '3. Para un alta: espera PREPARADA_ALTA; selecciona la solicitud en Cola y avisos → Revisar alta seleccionada. '
                'Revisa que el RFC no exista y autoriza Aceptar desde el módulo original.\n\n'
                '4. Para publicar: solo los archivos para_el_sitio van a IIS. Configura HTTPS y la regla del API según LEEME. '
                'No subas esta carpeta del bot, sus credenciales, tokens ni bases de datos al sitio.\n\n'
                '5. Activar solicitudes reales permite que el bot facture cuando la cola esté activa. '
                'El portal valida formato, pero NO acredita propiedad del ticket, identidad del solicitante o compatibilidad fiscal. '
                'Antes de habilitarlo públicamente, establece la verificación de identidad/ticket y los controles de atención de ARY.\n\n'
                'Cerrar este bot detiene la recepción web. La página no afirma que una factura fue enviada solo por recibir la solicitud.'
            )
            ttk.Label(tab, text=info, justify='left', wraplength=860).pack(anchor='w', pady=(8, 0))
            self._mode_trace = self.mode_var.trace_add('write', lambda *_: self.root.after_idle(self._sync_portal_mode))
            self._sync_portal_mode()

        def _sync_portal_mode(self):
            if self.portal:
                self.portal_mode.set('Recepción en PRUEBA: no timbra.' if self.portal.modo_seguro()
                    else 'RECEPCIÓN REAL: las nuevas facturas podrán emitirse al procesar la cola. Las altas conservan revisión.')

        def _open_page(self):
            if not self.cola.store.pause_if_idle():
                self.log('No se abre el navegador mientras Polaris usa la pantalla. Usa otro equipo para el portal publicado.')
                return
            self.log('Cola pausada para la vista local. Vuelve al bot e inicia la cola cuando Polaris esté listo.')
            origin = settings['origenes_permitidos'][0] if settings['https_publico'] else 'http://127.0.0.1:' + str(settings['puerto'])
            webbrowser.open(origin + '/ary_facturacion.htm')

        def _toggle_receiving(self):
            if not self.web_server:
                self.log('El servidor del portal no está activo.')
                return
            with self.portal.lock:
                self.portal.aceptando = not self.portal.aceptando
                enabled = self.portal.aceptando
            self.pause_web_btn.configure(text='Pausar recepción web' if enabled else 'Reanudar recepción web')
            self.portal_status.set('Recepción web activa.' if enabled else 'Recepción web pausada; las solicitudes ya recibidas se conservan.')

        def _change_real(self):
            previous = settings['permitir_facturacion_real']
            if self.cola.ocupada:
                self.real_web.set(previous)
                self.log('No cambies el modo ni abras diálogos mientras Polaris trabaja.')
                return
            requested = bool(self.real_web.get())
            if requested and not self.cola.store.pause_if_idle():
                self.real_web.set(previous)
                self.log('Espera a que termine Polaris antes de autorizar el portal real.')
                return
            if requested:
                self.log('Cola pausada para revisar el permiso del portal. Iníciala manualmente al terminar.')
            if requested and not messagebox.askyesno('Autorizar solicitudes reales del portal',
                'Con el bot fuera de MODO SEGURO, las nuevas solicitudes web podrán facturarse al iniciar la cola.\n\n'
                'Confirma que probaste el flujo y estableciste la revisión de identidad/propiedad del ticket para tu publicación. '
                'Las solicitudes antiguas conservarán su modo. ¿Autorizar?', parent=self.root, default='no'):
                self.real_web.set(previous)
                return
            updated = dict(settings, permitir_facturacion_real=requested)
            try:
                path = Path(BASE) / 'portal_config.json'
                tmp = path.with_suffix('.json.tmp')
                tmp.write_text(json.dumps(updated, ensure_ascii=False, indent=2), encoding='utf-8')
                tmp.replace(path)
                with self.portal.lock:
                    settings.update(updated)
                self._sync_portal_mode()
            except Exception:
                settings['permitir_facturacion_real'] = previous
                self.real_web.set(previous)
                self.log('No se guardó el modo del portal. Se conservó el anterior.')

        def close(self):
            if not self.portal:
                return super().close()
            with self.portal.lock:
                alerts = getattr(self, 'alertas', None)
                if self.cola.ocupada or self.cola.avisos.sending or (alerts and alerts.sending):
                    return super().close()
                self.portal.aceptando = False
                return super().close()

    app = None
    try:
        app = PortalApp(root)
        root.mainloop()
    except Exception as exc:
        try:
            messagebox.showerror('Arranque del portal', str(exc), parent=root)
        except Exception:
            print('No se pudo iniciar el portal:', exc)
    finally:
        if app:
            if app.portal:
                app.portal.pausar_recepcion()
            if app.web_server:
                app.web_server.close()
            app.cola.detener()
        guard.cerrar()
