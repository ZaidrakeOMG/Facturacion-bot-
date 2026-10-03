"""Buzón de avisos al cliente: hilo propio, sin tocar las ventanas de Polaris."""
from __future__ import annotations
import base64
import threading
from email.message import EmailMessage
from email import policy
from .cola_modelo import HEADER_AVISO, ASUNTO_AVISO, ajustes_cola, texto_cliente
from .factura_final import validar_correo
from .alertas_correo import ErrorEnvioAlerta


def construir_aviso(sender,row):
    recipient=validar_correo(row['correo'])
    ident=row['id']
    if not ident or any(c not in '0123456789abcdef' for c in ident):raise ValueError('ID de aviso inválido.')
    if not row['asunto'].startswith(ASUNTO_AVISO):raise ValueError('Falta encabezado de seguimiento.')
    if any(x in sender+row['asunto'] for x in '\r\n'):raise ValueError('Cabecera de correo inválida.')
    em=EmailMessage(policy=policy.SMTP)
    em['From']=validar_correo(sender);em['To']=recipient;em['Subject']=row['asunto']
    em['Message-ID']=f'<ary-solicitud-{ident}@grupoary.com>'
    em[HEADER_AVISO]='1';em['Auto-Submitted']='auto-generated';em['X-Auto-Response-Suppress']='All'
    em.set_content(row['cuerpo'])
    return em


class TransporteAvisos:
    def __init__(self,base,cfg):self.base=base;self.cfg=cfg
    def enviar(self,row):
        from .gmail_client import GmailClient
        service=None;started=False
        try:
            # Cliente exclusivo de ESTA operación de correo; no comparte httplib2.Http.
            client=GmailClient(self.base,self.cfg,lambda *_:None)
            client.connect(interactive=False);service=client.require()
            em=construir_aviso(client.email,row)
            raw=base64.urlsafe_b64encode(em.as_bytes()).decode('ascii')
            started=True
            return service.users().messages().send(userId='me',body={'raw':raw}).execute(num_retries=0)
        except Exception as exc:
            status=getattr(getattr(exc,'resp',None),'status',None)
            uncertain=started and not (isinstance(status,int) and 400<=status<500 and status!=408)
            raise ErrorEnvioAlerta('El envío no quedó confirmado; revise Enviados antes de reenviar.' if uncertain
                                  else 'No se envió el aviso. Revise la conexión, autorización o cuota de Gmail.',incierto=uncertain) from exc
        finally:
            if service:
                try:service.close()
                except Exception:pass


class AvisosCliente:
    def __init__(self,base,cfg,store,log,*,sender=None,on_event=None):
        self.base=base;self.cfg=cfg;self.store=store;self.log=log;self.sender=sender
        self.on_event=on_event or (lambda *_:None)
        self._stop=threading.Event();self._wake=threading.Event();self.thread=None;self.sending=False

    def publicar(self,job,event,reason=''):
        if not job.get('correo'):return
        opts=ajustes_cola(self.cfg)
        if event=='recibida' and job['estado'] not in {'EN_COLA','EJECUTANDO'}:return
        if event=='procesando' and job['seguro']:return
        subject,body=texto_cliente(job,event,opts,eta=self.store.eta(job['id'],opts['margen_minutos']),reason=reason)
        send=opts['avisar_cliente'] and (not job['seguro'] or opts['avisar_en_modo_seguro'])
        self.store.notice(job,event,subject,body,send=send)
        self._wake.set();self.on_event('aviso',job['id'])

    def enviar_una(self):
        opts=ajustes_cola(self.cfg)
        if not opts['avisar_cliente']:return False
        row=self.store.claim_notice()
        if not row:return False
        job=self.store.get(row['trabajo']);self.sending=True
        try:
            if job['seguro'] and not opts['avisar_en_modo_seguro']:
                self.store.notice_state(row['id'],'SIMULADA','El modo seguro no permite este aviso real.');return True
            # Redacta el recibo con el estado ACTUAL, evitando prometer un ETA obsoleto.
            event=row['evento']
            if event in {'recibida','procesando'}:
                if job['estado'] in {'ENVIO_SOLICITADO','ALTA_CONFIRMADA','CANCELADA','REVISION_REQUERIDA','REVISION_CERRADA'}:
                    self.store.notice_state(row['id'],'SUPERADA','Un resultado posterior sustituyó este aviso de avance.');return True
                subject,body=texto_cliente(job,event,opts,eta=self.store.eta(job['id'],opts['margen_minutos']))
                row=dict(row,asunto=subject,cuerpo=body)
                # El historial debe corresponder al contenido realmente enviado.
                with self.store.connect(True) as c:
                    c.execute('UPDATE avisos SET asunto=?,cuerpo=? WHERE id=?',(subject,body,row['id']))
            sender=self.sender or TransporteAvisos(self.base,self.cfg)
            response=sender.enviar(row)
            if not isinstance(response,dict) or not response.get('id'):
                self.store.notice_state(row['id'],'ENVIO_INCIERTO','Gmail no devolvió identificador; no se reenvía automáticamente.')
            else:
                self.store.notice_state(row['id'],'ENVIADA',gmail_id=str(response['id']))
                self.log('Aviso de seguimiento aceptado por Gmail. Recepción en buzón no verificada.')
        except Exception as exc:
            cause=exc.__cause__ if isinstance(exc,ErrorEnvioAlerta) and exc.__cause__ is not None else exc
            status=getattr(getattr(cause,'resp',None),'status',None)
            raw=str(cause)
            quota=status in (403,429) and ('rateLimitExceeded' in raw or 'Quota exceeded' in raw or status==429)
            if quota:
                self.store.defer_notice(row['id'],60,'Cuota temporal de Gmail. El correo se reintentará; NO se repite Polaris.')
                self.log('Aviso al cliente aplazado 60s por cuota de Gmail. La facturación NO se repite.')
            else:
                uncertain=not isinstance(exc,ErrorEnvioAlerta) or exc.incierto
                self.store.notice_state(row['id'],'ENVIO_INCIERTO' if uncertain else 'ERROR_ENVIO',
                                        'Revise Gmail Enviados y la autorización. El aviso permanece local; no repite operaciones de Polaris.')
                self.log('Aviso al cliente pendiente de revisión. La facturación/alta NO se vuelve a ejecutar.')
        finally:self.sending=False;self.on_event('aviso',job['id'])
        return True

    def iniciar(self):
        if self.thread and self.thread.is_alive():return
        self._stop.clear()
        def loop():
            while not self._stop.is_set():
                try:
                    if self.enviar_una():continue
                except Exception:self.log('No se pudo consultar el buzón local de avisos.')
                self._wake.wait(1);self._wake.clear()
        self.thread=threading.Thread(target=loop,daemon=True,name='ARY-Avisos-Cliente');self.thread.start()

    def detener(self):self._stop.set();self._wake.set()
    def reenviar(self,ident):self.store.retry_notice(ident);self._wake.set()
