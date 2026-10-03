"""Recepción Gmail: valida y ENCOLA. Nunca maneja la pantalla ni timbra en este hilo."""
from __future__ import annotations
import threading
from .parser import parse_body, classify_attachments, detect_station
from .constancia import read_constancia
from .ocr_ticket import read_ticket
from .cola_modelo import (es_alta,leer_alta,HEADER_AVISO,ASUNTO_AVISO,SolicitudDuplicada)
from .factura_final import validar_correo, FinalFacturaError
from .cliente_model import AltaClienteError
from .gmail_limites import GmailEnEspera

class Processor:
    def __init__(self,base,cfg,db,gmail,polaris,log,alertas=None,cola=None):
        self.base=base;self.cfg=cfg;self.db=db;self.gmail=gmail;self.polaris=polaris
        self.log=log;self.alertas=alertas;self.cola=cola;self._ingest=threading.Lock()

    def _subject_allowed(self,subject):
        words=[x.upper() for x in self.cfg['app'].get('asunto_palabras',[]) if x]
        return not words or any(w in (subject or '').upper() for w in words) or es_alta(subject,'')

    def process(self,msg,force=False):
        # Dos fuentes de recepción (monitor + último correo) pueden entrar aquí.
        with self._ingest:return self._receive(msg)

    def _receive(self,msg):
        if self.cola is None:raise RuntimeError('Falta el coordinador de cola; no se ejecuta Polaris desde Gmail.')
        mid=str(msg['id']);source_id='gmail:'+mid;b=self.gmail.basics(msg)
        from .alertas_modelo import ETIQUETA_HEADER,PREFIJO_ASUNTO
        headers={h.get('name','').lower():h.get('value','') for h in msg.get('payload',{}).get('headers',[])}
        auto=headers.get('auto-submitted','no').lower()
        account=(self.cfg.get('gmail',{}).get('cuenta_esperada') or self.cfg['app'].get('correo') or '').lower()
        if (headers.get(ETIQUETA_HEADER.lower())=='1' or headers.get(HEADER_AVISO.lower())=='1'
            or PREFIJO_ASUNTO in b['subject'] or ASUNTO_AVISO in b['subject']
            or auto not in ('','no') or b['from_email'].lower()==account
            or any(x in msg.get('labelIds',[]) for x in ('SENT','DRAFT','TRASH','SPAM'))):
            self.log('Aviso automático/interno ignorado como solicitud.');return 'AVISO_INTERNO'
        old=self.cola.store.source(source_id)
        if old:return old['estado']
        # No repetir registros de las versiones anteriores aunque se pulse Procesar último.
        if self.db.existe(mid):return 'YA_REGISTRADA'
        if not self._subject_allowed(b['subject']):return 'IGNORADA'
        try:recipient=validar_correo(b['from_email'])
        except Exception:
            self.log('Solicitud sin remitente de respuesta válido; no se envía ningún correo.');return 'REMITENTE_INVALIDO'
        safe=bool(self.cfg['app'].get('modo_prueba',True));kind='FACTURA';sol=None
        data={};reason='';job=None
        try:
            body=self.gmail.body_text(msg)
            if len(body)>100000:body=body[:100000]
            kind='ALTA' if es_alta(b['subject'],body) else 'FACTURA'
            if kind=='ALTA':
                sol=leer_alta(b['subject'],body,recipient)
                job,_=self.cola.encolar_alta(sol,source='Gmail',source_id=source_id,safe=safe,correo=recipient)
                data=job['datos']
            else:
                sol=parse_body(b['subject']+'\n'+body,recipient)
                paths=self.gmail.download_attachments(msg,self.base/'descargas'/mid)
                imgs,pdfs=classify_attachments(paths)
                sol.foto_ticket=imgs[0] if imgs else None;sol.constancia_pdf=pdfs[0] if pdfs else None
                if sol.constancia_pdf:
                    info=read_constancia(sol.constancia_pdf)
                    for key in ('rfc','razon_social','codigo_postal','regimen_fiscal'):
                        if not getattr(sol,key):setattr(sol,key,info.get(key,''))
                if sol.foto_ticket and (not sol.ticket or not sol.estacion):
                    folio,text=read_ticket(sol.foto_ticket,self.cfg.get('ocr',{}).get('tesseract_cmd',''),self.cfg.get('ocr',{}).get('idiomas','spa+eng'))
                    if not sol.ticket:sol.ticket=folio or ''
                    if not sol.estacion:sol.estacion=detect_station(text)
                missing=sol.missing()
                if missing:raise ValueError('; '.join(missing))
                job,_=self.cola.encolar_factura(sol,source='Gmail',source_id=source_id,safe=safe,correo=recipient)
                data=job['datos']
        except GmailEnEspera:
            # No convertir un rechazo temporal de Gmail en una solicitud con error fiscal.
            raise
        except SolicitudDuplicada:
            reason='Hay una solicitud activa para ese folio/RFC y estación. Espera su resultado. Esto no confirma que el ticket ya esté facturado.'
            job,_=self.cola.registrar_incompleta(kind,{},source_id=source_id,correo=recipient,reason=reason,safe=safe,duplicate=True)
            if self.alertas:
                self.alertas.reportar('SISTEMA','Recepción: solicitud duplicada',reason,
                    contexto={'origen':'Gmail','id_solicitud':job['id'],'correo_cliente':recipient},modo_seguro=safe)
        except (ValueError,AltaClienteError,FinalFacturaError) as exc:
            # Solo textos de validación del programa; nunca enviar trazas/ventanas al cliente.
            reason=str(exc)[:500]
            if '\n' in reason:reason='Revise los campos de la solicitud y vuelva a enviarla completa.'
            job,_=self.cola.registrar_incompleta(kind,{},source_id=source_id,correo=recipient,reason=reason,safe=safe)
        except Exception as exc:
            # Un fallo de lectura/adjuntos requiere atención; no opera Polaris ni envía errores técnicos al cliente.
            reason='La solicitud no pudo validarse. El área de facturación revisará el caso.'
            job,_=self.cola.registrar_revision_entrada(kind,source_id=source_id,correo=recipient,safe=safe)
            if self.alertas:
                self.alertas.reportar('SISTEMA','Leer solicitud Gmail',exc,
                    contexto={'origen':'Gmail','id_solicitud':job['id'],'correo_cliente':recipient},modo_seguro=safe)

        self.db.crear(mid,msg.get('threadId',''),recipient,b['subject'])
        self.db.actualizar(mid,estado=job['estado'],detalle=reason or 'En cola: '+job['id'][:12],
                           estacion=data.get('estacion',''),rfc=data.get('rfc',''),ticket=data.get('ticket',''),forma_pago=data.get('forma_pago',''))
        labels=self.cfg.get('gmail',{}).get('labels',{})
        name=labels.get('procesando' if job['estado']=='EN_COLA' else 'esperando')
        if name:
            try:self.gmail.add_label(mid,name)
            except Exception:self.log('La solicitud quedó localmente aunque no se pudo etiquetar Gmail.')
        self.log('Solicitud recibida: '+job['id'][:12]+' — '+job['tipo']+' — '+job['estado'])
        return job['estado']
