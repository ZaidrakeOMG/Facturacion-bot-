from __future__ import annotations
import threading, time
from googleapiclient.errors import HttpError
from .gmail_client import HistoryExpired, NeedGmailAuth


class BotWorker(threading.Thread):
    def __init__(self,cfg,db,gmail,processor,log,status_cb=None):
        super().__init__(daemon=True)
        self.cfg=cfg; self.db=db; self.gmail=gmail; self.processor=processor; self.log=log; self.status_cb=status_cb
        self.stop_event=threading.Event(); self.wake_event=threading.Event()

    def stop(self): self.stop_event.set(); self.wake_event.set()
    def wake(self): self.wake_event.set()

    def _alertar(self, etapa, error):
        service=getattr(self.processor,"alertas",None)
        if service:service.reportar("SISTEMA",etapa,error,contexto={"origen":"Monitor Gmail"},
                                   modo_seguro=bool(self.cfg["app"].get("modo_prueba",True)))

    def run(self):
        try:
            self._loop()
        except Exception as e:
            self._alertar("Monitor Gmail detenido",e)
            self.log(f"Error del monitor Gmail: {e}")
        finally:
            self.log("Recepción Gmail detenida. La cola de Polaris y los avisos tienen controles independientes.")
            if self.status_cb:self.status_cb(False)

    def _loop(self):
        try:
            self.gmail.connect(interactive=False)
        except Exception as e:
            self._alertar("Conectar monitor Gmail",e)
            self.log(str(e));
            return
        if self.status_cb: self.status_cb(True)
        hid=self.db.meta_get("gmail_history_id")
        if not hid:
            hid=self.gmail.current_history_id(); self.db.meta_set("gmail_history_id",hid)
            self.log("Inicio seguro: se vigilará Gmail DESDE AHORA; no se procesan correos viejos automáticamente.")
        self.log(f"Recepción Gmail activa. Las solicitudes se añaden a la cola; historyId={hid}")
        interval=max(5,int(self.cfg["app"].get("intervalo_segundos",20)))
        quota_backoff = 0
        while not self.stop_event.is_set():
            try:
                msgs,new_hid=self.gmail.new_messages_from_history(hid)
                quota_backoff = 0
                if msgs: self.log(f"Gmail: {len(msgs)} correo(s) nuevo(s) detectado(s).")
                for m in msgs:
                    if self.stop_event.is_set(): break
                    self.processor.process(m)  # Solo valida/encola; nunca toca Polaris en este hilo.
                if not self.stop_event.is_set() and new_hid and new_hid != hid:
                    hid=new_hid; self.db.meta_set("gmail_history_id",hid)
            except HistoryExpired:
                hid=self.gmail.current_history_id(); self.db.meta_set("gmail_history_id",hid)
                self.log("Gmail renovó el checkpoint de historial.")
            except Exception as e:
                status=getattr(getattr(e,'resp',None),'status',None)
                text=str(e)
                quota = status in (403,429) and ('rateLimitExceeded' in text or 'Quota exceeded' in text or status==429)
                if quota:
                    quota_backoff = min(300, max(30, quota_backoff*2 if quota_backoff else 30))
                    self.log(f'Gmail alcanzó temporalmente su cuota. Recepción pausada {quota_backoff}s; no se pierde el checkpoint ni se duplica la cola.')
                else:
                    self._alertar("Monitor Gmail detenido",e)
                    self.log(f"Error del monitor Gmail: {e}")
                    self.log("Se conserva el checkpoint. Se volverá a intentar la recepción; los trabajos ya en cola no se duplican.")
            wait_for = quota_backoff if quota_backoff else interval
            if not self.stop_event.is_set():self.wake_event.wait(wait_for)
            self.wake_event.clear()
