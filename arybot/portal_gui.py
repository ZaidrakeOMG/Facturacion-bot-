"""Bot y portal integrados: un arranque, una cola y diagnósticos sin tocar Polaris."""
from __future__ import annotations
import json
import queue
from pathlib import Path
import sys
import threading
import webbrowser
import urllib.request
import urllib.error


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
        import traceback
        traceback.print_exc()
        messagebox.showerror('Instalación del portal',
            'Falta una dependencia. Ejecuta INSTALAR.bat en esta misma carpeta.\n\n' + str(exc), parent=root)
        root.destroy()
        return 1
    guard = InstanciaBot(BASE)
    try:
        guard.adquirir()
        settings = cargar_ajustes(BASE)
    except Exception as exc:
        import traceback
        traceback.print_exc()
        messagebox.showerror('Portal ARY', str(exc), parent=root)
        guard.cerrar()
        root.destroy()
        return 1

    class PortalApp(App):
        def __init__(self, parent):
            self.portal = None
            self.web_server = None
            self.web_thread = None
            self.portal_diagnostics = queue.Queue()
            self.portal_check_busy = False
            self.portal_closing = False
            super().__init__(parent)
            self.portal = PortalWSGI(self.cola, self.cfg, settings, BASE)
            self._build_portal_tab()
            self._start_portal()
            self.root.title('ARY Facturación Bot v4.3 fecha + Portal integrado 1.2')
            self.nb.select(self.portal_tab)
            self.root.after(150, self._drain_portal_diagnostics)
            self.root.deiconify()

        def _start_portal(self):
            # Reintentar solo el receptor: no crea otra cola ni otro bot de escritorio.
            if self.web_thread and self.web_thread.is_alive():
                self.log('El receptor ya está activo; no se inició una segunda instancia.')
                return
            try:
                args = dict(host=settings['escuchar_en'], port=settings['puerto'], threads=4,
                            max_request_body_size=16384, max_request_header_size=16384,
                            connection_limit=50, channel_timeout=30, expose_tracebacks=False,
                            clear_untrusted_proxy_headers=True)
                network_app = PortalRedWSGI(self.portal, settings)
                # REMOTE_ADDR debe seguir siendo la IP del par TCP, NO X-Forwarded-For.
                self.web_server = create_server(network_app, **args)
                self.web_thread = threading.Thread(target=self.web_server.run, daemon=True,
                                                   name='ARY-Portal-HTTP')
                self.web_thread.start()
                with self.portal.lock:
                    self.portal.aceptando = True
                self.pause_web_btn.configure(state='normal', text='Pausar recepción web')
                self.portal_status.set('Portal activo en ' + settings['escuchar_en'] + ':'
                    + str(settings['puerto']) + ('. Solicitudes desde IIS: '
                    + settings['proxy_confiable'] if network_app.lan else '. Solo modo local.'))
                self.log('Bot y portal activos en una sola instancia. Se utiliza la misma Cola y avisos.')
            except Exception as exc:
                self.portal.pausar_recepcion()
                if self.web_server:
                    try: self.web_server.close()
                    except Exception: pass
                self.web_server = None
                self.web_thread = None
                self.pause_web_btn.configure(state='disabled')
                code = getattr(exc, 'winerror', None) or getattr(exc, 'errno', None)
                if code in (10049, 99):
                    detail = 'Este Windows no tiene la IP ' + settings['escuchar_en'] + '. Inicia el bot en el equipo correcto.'
                elif code in (10048, 98):
                    detail = 'El puerto ' + str(settings['puerto']) + ' está ocupado. Cierra el receptor anterior y reintenta.'
                elif code in (10013, 13):
                    detail = 'Windows denegó el puerto. Revisa los permisos o la reserva del puerto; no desactives el firewall.'
                else:
                    detail = str(exc)
                self.portal_status.set('Receptor NO iniciado. ' + detail)
                self.log('Portal: ' + detail)

        def _health_url(self):
            host = settings['escuchar_en']
            if ':' in host:
                host = '[' + host + ']'
            return 'http://' + host + ':' + str(settings['puerto']) + '/api/facturacion/salud'

        def _copy_health(self):
            if self.cola.ocupada:
                self.log('Espera a que termine Polaris antes de usar el portapapeles.')
                return
            self.root.clipboard_clear()
            self.root.clipboard_append(self._health_url())
            self.log('Dirección de salud copiada. Ábrela desde IIS para comprobar la conexión entre equipos.')

        def _check_health(self):
            if self.portal_check_busy:
                return
            if not self.web_thread or not self.web_thread.is_alive():
                self.portal_check.set('El receptor no está activo. Revisa el mensaje superior y pulsa Reintentar receptor.')
                return
            self.portal_check_busy = True
            self.portal_check.set('Comprobando el receptor en este equipo...')
            url = self._health_url()
            def work():
                try:
                    # No enviar una dirección privada a un proxy HTTP del sistema.
                    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                    req = urllib.request.Request(url, headers={'Accept': 'application/json'})
                    with opener.open(req, timeout=5) as response:
                        raw = response.read(8193)
                        if len(raw) > 8192:
                            raise ValueError('Respuesta demasiado grande.')
                        result = json.loads(raw)
                    if not isinstance(result, dict) or result.get('ok') is not True or result.get('servicio') != 'ARY Portal Facturacion':
                        raise ValueError('La respuesta no corresponde al receptor de ARY.')
                    msg = 'Receptor local OK. Falta comprobar desde IIS ' + settings['proxy_confiable'] + '. Esta prueba no factura.'
                except Exception as exc:
                    msg = 'No se pudo verificar el receptor local: ' + str(exc)
                # El hilo de red jamás modifica widgets Tk ni toca Polaris.
                self.portal_diagnostics.put(msg)
            threading.Thread(target=work, daemon=True, name='ARY-Diagnostico-Local').start()

        def _drain_portal_diagnostics(self):
            try:
                while True:
                    msg = self.portal_diagnostics.get_nowait()
                    self.portal_check_busy = False
                    self.portal_check.set(msg)
                    self.log(msg)
            except queue.Empty:
                pass
            if not self.portal_closing:
                self.root.after(150, self._drain_portal_diagnostics)

        def _build_portal_tab(self):
            tab = ttk.Frame(self.nb, padding=16)
            self.nb.add(tab, text='Portal web')
            self.portal_tab = tab
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
            connection = ttk.LabelFrame(tab, text='Conexión con IIS', padding=10)
            connection.pack(fill='x', pady=(4, 8))
            ttk.Label(connection, text='Bot: ' + settings['escuchar_en'] + ':' + str(settings['puerto'])
                + '     |     IIS autorizado: ' + (settings['proxy_confiable'] or 'local')).pack(anchor='w')
            ttk.Label(connection, text=self._health_url(), wraplength=820).pack(anchor='w', pady=(4, 6))
            buttons = ttk.Frame(connection); buttons.pack(anchor='w')
            ttk.Button(buttons, text='Comprobar receptor', command=self._check_health).pack(side='left', padx=(0, 8))
            ttk.Button(buttons, text='Copiar dirección de prueba', command=self._copy_health).pack(side='left', padx=(0, 8))
            ttk.Button(buttons, text='Reintentar receptor', command=self._start_portal).pack(side='left')
            self.portal_check = tk.StringVar(value='Comprueba la dirección de salud desde el servidor IIS. No registra solicitudes.')
            ttk.Label(connection, textvariable=self.portal_check, wraplength=820).pack(anchor='w', pady=(8, 0))
            info = (
                'Las solicitudes web se muestran en Cola y avisos. En recepción REAL, si Polaris está libre el trabajo inicia; '
                'si ya hay otro trabajo, espera su turno en la cola.\n\n'
                'El alta conserva la revisión del operador: PREPARADA_ALTA → Revisar alta seleccionada. '
                'En MODO SEGURO no se pulsa Aceptar final para timbrar.\n\n'
                'Para IIS: publica solo portal_web/ary_facturacion.htm. La carpeta IIS contiene el fragmento '
                'de la regla y la prueba interna; no reemplaces el web.config completo. '
                'No publiques esta carpeta del bot, credenciales, tokens ni data.\n\n'
                'El tramo IIS → bot usa HTTP privado. Antes de datos reales, protégelo con HTTPS o un canal '
                'IPsec/VPN y establece la revisión de identidad/propiedad del ticket. El filtro por IP no cifra. '
                'Activar recepción real requiere tu confirmación.\n\n'
                'Cerrar el programa detiene la recepción. Las solicitudes y los ajustes se guardan en tu carpeta actual.'
            )
            help_box = tk.Text(tab, height=9, wrap='word', font=('Segoe UI', 9), relief='flat')
            help_box.pack(fill='both', expand=True, pady=(2, 0))
            help_box.insert('1.0', info); help_box.configure(state='disabled')
            self._mode_trace = self.mode_var.trace_add('write', lambda *_: self.root.after_idle(self._sync_portal_mode))
            self._sync_portal_mode()

        def _sync_portal_mode(self):
            if self.portal:
                self.portal_mode.set('Recepción en PRUEBA: no timbra.' if self.portal.modo_seguro()
                    else 'RECEPCIÓN REAL: las nuevas facturas podrán emitirse al procesar la cola. Las altas conservan revisión.')

        def _open_page(self):
            if self.cola.ocupada:
                self.log('No se abre el navegador mientras Polaris usa la pantalla. Espera a que termine o usa otro equipo.')
                return
            # Abrir el portal NO pausa la cola. Antes quedaba una pausa persistente
            # y las solicitudes podían quedarse EN_COLA sin empezar.
            origin = settings['origenes_permitidos'][0] if settings['https_publico'] else 'http://127.0.0.1:' + str(settings['puerto'])
            webbrowser.open(origin + '/ary_facturacion.htm')
            self.log('Portal abierto. La cola continúa automática.')

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
            # Ya se comprobó arriba que Polaris no está ocupado. Cambiar este permiso
            # no necesita pausar la cola y así cancelar el cuadro no deja trabajos congelados.
            if requested and not messagebox.askyesno('Autorizar solicitudes reales del portal',
                'Con el bot fuera de MODO SEGURO, las nuevas solicitudes web se atenderán automáticamente cuando Polaris esté libre; si está ocupado, quedarán en cola.\n\n'
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
                if requested:
                    try:
                        self.cola.iniciar_cola()
                        self.log('Recepción REAL autorizada: cola automática activa. Si Polaris está libre, la solicitud inicia de inmediato.')
                    except Exception as exc:
                        self.log('Recepción REAL autorizada, pero la cola requiere revisión antes de continuar: '+str(exc))
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
                self.portal_closing = True
                return super().close()

    app = None
    exit_code = 0
    try:
        app = PortalApp(root)
        root.mainloop()
    except Exception as exc:
        import traceback
        traceback.print_exc()
        exit_code = 1
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
                try: app.web_server.task_dispatcher.shutdown(cancel_pending=True, timeout=3)
                except Exception: pass
            app.cola.detener()
        guard.cerrar()

    return exit_code
