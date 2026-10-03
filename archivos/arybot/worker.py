from __future__ import annotations
import threading
from .gmail_client import HistoryExpired, NeedGmailAuth
from .gmail_limites import GmailEnEspera


class BotWorker(threading.Thread):
    def __init__(self, cfg, db, gmail, processor, log, status_cb=None):
        super().__init__(daemon=True)
        self.cfg=cfg; self.db=db; self.gmail=gmail; self.processor=processor
        self.log=log; self.status_cb=status_cb
        self.stop_event=threading.Event(); self.wake_event=threading.Event()

    def stop(self): self.stop_event.set(); self.wake_event.set()
    def wake(self): self.wake_event.set()

    def _alertar(self, etapa, error):
        # Nunca generar un correo por cada rechazo de cuota: usaría la misma cuota.
        if isinstance(error, GmailEnEspera):
            return
        service=getattr(self.processor, 'alertas', None)
        if service:
            try:
                service.reportar('SISTEMA', etapa, error, contexto={'origen':'Monitor Gmail'},
                                 modo_seguro=bool(self.cfg['app'].get('modo_prueba', True)))
            except Exception:
                self.log('No se guardó la alerta del monitor. El checkpoint permanece local.')

    def run(self):
        try:
            self._loop()
        except Exception as exc:
            self._alertar('Monitor Gmail detenido', exc)
            self.log(f'Error del monitor Gmail: {exc}')
        finally:
            self.log('Recepción Gmail detenida. La cola de Polaris y los avisos tienen controles independientes.')
            if self.status_cb: self.status_cb(False)

    def _loop(self):
        hid=self.db.meta_get('gmail_history_id')
        connected=False
        expired=False
        failures=0
        interval=max(20, int(self.cfg['app'].get('intervalo_segundos', 20)))
        while not self.stop_event.is_set():
            wait=interval
            try:
                if not connected:
                    self.gmail.connect(interactive=False)
                    connected=True
                    if self.status_cb: self.status_cb(True)
                if not hid:
                    hid=self.gmail.current_history_id()
                    self.db.meta_set('gmail_history_id', hid)
                    self.log('Historial vencido: se inicia desde ahora; revise manualmente correos previos no recibidos.'
                             if expired else 'Inicio seguro: se vigilará Gmail DESDE AHORA; no se procesan correos viejos.')
                msgs,new_hid=self.gmail.new_messages_from_history(hid)
                if msgs: self.log(f'Gmail: {len(msgs)} correo(s) recibido(s) en esta pasada.')
                for message in msgs:
                    if self.stop_event.is_set(): break
                    self.processor.process(message)
                if not self.stop_event.is_set() and new_hid and new_hid != hid:
                    # Guardar primero en disco. Un fallo nunca adelanta el cursor en memoria.
                    self.db.meta_set('gmail_history_id', new_hid)
                    hid=new_hid
                failures=0
            except GmailEnEspera as exc:
                wait=max(interval, exc.segundos)
                self.log(str(exc))
                self.log('Checkpoint conservado. No se crea otra alerta por cuota; Polaris no se reejecuta por Gmail.')
            except NeedGmailAuth as exc:
                self._alertar('Conectar monitor Gmail', exc)
                self.log(str(exc))
                return
            except HistoryExpired:
                # Conserva en disco el cursor anterior hasta obtener uno válido.
                hid=None; expired=True
                wait=max(interval, 60)
            except Exception as exc:
                failures=min(failures+1, 6)
                wait=max(interval, min(900, 30*2**(failures-1)))
                if failures == 1:
                    self._alertar('Recepción Gmail', exc)
                self.log(f'Error del monitor Gmail: {exc}')
                self.log(f'Se conserva el checkpoint. Próximo intento en {wait} s; sin duplicar trabajos ya recibidos.')
            if not self.stop_event.is_set(): self.wake_event.wait(wait)
            self.wake_event.clear()
