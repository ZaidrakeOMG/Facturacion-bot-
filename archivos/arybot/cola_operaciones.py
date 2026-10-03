"""Un consumidor para TODAS las acciones de pantalla; recepción/correos independientes.

Los trabajos son datos persistidos, no comandos ni SQL proporcionados por clientes.
No se reintenta automáticamente ninguna acción sobre Polaris después de un error.
"""
from __future__ import annotations
import threading
from .cola_local import ColaLocal
from .cola_modelo import (ajustes_cola,datos_alta,factura_datos,como_solicitud,clave_trabajo,
                         ColaError,ESTADOS_REVISION)
from .cliente_model import AltaCliente
from .avisos_cliente import AvisosCliente

class ColaOperaciones:
    def __init__(self,base,cfg,polaris,log,*,alertas=None,alta=None,db=None,on_event=None,store=None,avisos=None):
        self.base=base;self.cfg=cfg;self.polaris=polaris;self.log=log;self.alertas=alertas;self.alta=alta;self.db=db
        self.on_event=on_event or (lambda *_:None)
        self.store=store or ColaLocal(base)
        recovered=self.store.recover()
        self.avisos=avisos or AvisosCliente(base,cfg,self.store,log,on_event=self.on_event)
        self._stop=threading.Event();self._wake=threading.Event();self._consumer=threading.Lock()
        self._lifecycle=threading.RLock();self.thread=None
        self.log('Actualizacion v8 activa: reintentos por ticket y espera compartida de Gmail.')
        for job in recovered:self._notice(job,'revision')
        # Recupera un acuse que no alcanzó a persistirse después de encolar; UNIQUE evita duplicarlo.
        for job in self.store.list(10000):
            if job['estado']=='EN_COLA':self._notice(job,'recibida')

    def _notice(self,job,event,reason=''):
        try:self.avisos.publicar(job,event,reason)
        except Exception:self.log('No pudo guardarse el aviso al cliente. Revise la cola de avisos; no repita Polaris.')

    def _changed(self,ident):
        try:self.on_event('trabajo',ident)
        except Exception:self.log('No se pudo actualizar la vista; el trabajo conserva su estado local.')
        if self.db:
            job=self.store.get(ident)
            source=job.get('fuente_id') or ''
            if source.startswith('gmail:'):
                mid=source[6:]
                try:self.db.actualizar(mid,estado=job['estado'],detalle=job['motivo'] or 'Seguimiento '+ident[:12])
                except Exception:self.log('No se actualizó el historial antiguo. Consulte Cola y avisos para el estado definitivo.')

    def _enqueue(self,kind,data,*,source='Formulario',source_id=None,safe=True,correo='',state='EN_COLA',reason=''):
        opts=ajustes_cola(self.cfg)
        minutes=opts['minutos_alta'] if kind=='ALTA' else opts['minutos_factura']
        job,new=self.store.enqueue(kind,data,source=source,source_id=source_id,safe=bool(safe),correo=correo,
                                   clave=clave_trabajo(kind,data) if state=='EN_COLA' else '',minutes=minutes,state=state,reason=reason)
        if new:
            self._notice(job,'recibida' if state=='EN_COLA' else ('faltantes' if state=='ESPERANDO_DATOS' else 'revision'),reason)
            self._changed(job['id'])
            # Toda solicitud EN_COLA (Portal, Gmail o Prueba Polaris) despierta el consumidor.
            # Si no hay una pausa EXPLÍCITA del operador ni una captura interactiva,
            # se ejecuta en cuanto Polaris esté libre.
            if state=='EN_COLA':
                st=self.store.status()
                motivo=str(st.get('motivo') or '')
                pausa_operador=motivo.startswith('Pausada por el operador')
                if not pausa_operador and not st.get('bloqueo'):
                    try:self.store.resume()
                    except ColaError:pass
            self._wake.set()
        return job,new

    def encolar_factura(self,sol,*,source='Formulario',source_id=None,safe=True,correo=None):
        data=factura_datos(sol)
        return self._enqueue('FACTURA',data,source=source,source_id=source_id,safe=safe,
                             correo=correo if correo is not None else data['correo_destino'])

    def encolar_alta(self,sol,*,source='Formulario',source_id=None,safe=True,correo=None):
        data=datos_alta(sol)
        return self._enqueue('ALTA',data,source=source,source_id=source_id,safe=safe,
                             correo=correo if correo is not None else data['correo'])

    def registrar_incompleta(self,kind,data,*,source_id,correo,reason,safe=True,duplicate=False):
        return self._enqueue(kind,data,source='Gmail',source_id=source_id,safe=safe,correo=correo,
                             state='DUPLICADA' if duplicate else 'ESPERANDO_DATOS',reason=reason)

    def registrar_revision_entrada(self,kind,*,source_id,correo,safe=True):
        return self._enqueue(kind,{},source='Gmail',source_id=source_id,safe=safe,correo=correo,
                             state='REVISION_ENTRADA',reason='La solicitud no pudo validarse; requiere atención del personal.')

    def encolar_prueba(self,kind,data):
        if kind not in {'ESTACION','PAGO','LIMPIEZA'}:raise ValueError('Prueba no admitida.')
        return self._enqueue(kind,data,safe=True)

    def iniciar_hilos(self):
        self.avisos.iniciar()
        if self.thread and self.thread.is_alive():return
        self._stop.clear()
        def loop():
            while not self._stop.is_set():
                try:
                    if self.ejecutar_una():continue
                except Exception:
                    self.store.pause('Error del coordinador. Revise la cola antes de continuar.');self.log('Coordinador detenido por seguridad; revise las solicitudes.')
                self._wake.wait(.5);self._wake.clear()
        self.thread=threading.Thread(target=loop,daemon=True,name='ARY-Polaris-Cola-Unica');self.thread.start()

    def iniciar_cola(self):
        self.store.resume();self._wake.set();self.on_event('cola','')
    def pausar(self):
        self.store.pause('Pausada por el operador; termina la operación actual. Gmail y correos siguen disponibles.');self.on_event('cola','')
    def detener(self):
        self._stop.set();self._wake.set();self.avisos.detener()
    @property
    def ocupada(self):return bool(self.store.status()['activa'])

    def ejecutar_una(self):
        if not self._consumer.acquire(blocking=False):return False
        try:
            job=self.store.claim(production_allowed=self.cfg.get('app',{}).get('modo_prueba',True) is False)
            if not job:return False
            ident=job['id'];safe=job['seguro'];kind=job['tipo'];data=job['datos']
            self._changed(ident)
            if job['accion']!='aceptar':self._notice(self.store.get(ident),'procesando')
            try:
                if not self.polaris:raise ColaError('El control de Polaris no está disponible en este equipo.')
                if kind=='FACTURA':
                    result=self.polaris.invoice(como_solicitud(data),test_mode=safe)
                    if result=='PRUEBA_OK' and safe:
                        self.store.finish(ident,'PREPARADA_FACTURA',result={'resultado':result},hold=True,
                                          reason='Factura preparada SIN TIMBRAR. Resuelva manualmente la captura antes de continuar la cola.')
                    elif result=='ENVIO_SOLICITADO' and not safe:
                        self.store.finish(ident,'ENVIO_SOLICITADO',result={'resultado':result},reason='Polaris solicitó el correo; recepción no verificada.')
                        self._notice(self.store.get(ident),'completada')
                    else:raise ColaError('Resultado de facturación no reconocido. No se repite el intento.')
                elif kind=='ALTA':
                    if self.alta is None:raise ColaError('El módulo de alta no está disponible.')
                    sol=AltaCliente.crear(data['estacion'],data['rfc'],data['idcif'],data['telefono'],data['correo'])
                    if job['accion']=='aceptar':
                        if self.cfg.get('app',{}).get('modo_prueba',True) is not False:raise ColaError('MODO SEGURO activo: no se pulsa Aceptar.')
                        result=self.alta.aceptar(sol,autorizado=True,inexistencia_revisada=True)
                        if not isinstance(result,dict) or not result.get('numero_cliente'):raise ColaError('No se confirmó el número de cliente. Revise Polaris; no se repite.')
                        self.store.finish(ident,'ALTA_CONFIRMADA',result=result,reason='Número de cliente confirmado en pantalla.')
                        self._notice(self.store.get(ident),'completada')
                    else:
                        result=self.alta.preparar(sol)
                        self.store.finish(ident,'PREPARADA_ALTA',result=result,hold=True,
                                          reason='Alta preparada SIN GUARDAR. Requiere revisar duplicados y autorizar Aceptar en Alta de cliente.')
                        self._notice(self.store.get(ident),'revision_alta')
                else:
                    if kind=='ESTACION':result=self.polaris.test_station_only(data['estacion'])
                    elif kind=='PAGO':result=self.polaris.test_payment_only(data['forma_pago'])
                    elif kind=='LIMPIEZA':result=self.polaris.test_cleanup_only()
                    else:raise ColaError('Tipo de operación no permitido.')
                    self.store.finish(ident,'PRUEBA_PENDIENTE',result={'resultado':str(result)},hold=True,
                                      reason='Prueba concluida. Revise la pantalla y libere la revisión para continuar.')
            except Exception as exc:
                from .factura_final import FacturaYaProcesada
                if isinstance(exc, FacturaYaProcesada) and exc.confirmado_polaris and kind == 'FACTURA':
                    self.store.finish(ident,'YA_FACTURADO',result={'resultado':'YA_FACTURADO'},
                                      reason='Polaris confirmó que el folio ya está facturado; no se volvió a timbrar.')
                    self._notice(self.store.get(ident),'ya_facturado')
                    self.log('Polaris confirmó YA FACTURADO. Se registró el aviso al cliente; consulte su estado de envío. No se repitió el timbrado.')
                    self._changed(ident)
                    return True
                step=getattr(self.polaris,'etapa_alerta','Operación de pantalla')
                if not isinstance(step,str):step='Operación de pantalla'
                # El consumidor conserva el bloqueo de pantalla mientras registra esta incidencia.
                self.store.finish(ident,'REVISION_REQUERIDA',hold=False,reason=str(exc))
                # Los detalles de errores van al correo interno, no al cliente.
                if self.alertas:
                    category='ALTA_CLIENTE' if kind=='ALTA' else ('ENVIO_FACTURA' if step.startswith('ENVIO_CORREO') else 'FACTURACION')
                    context={'origen':job['origen'],'id_solicitud':ident,'estacion':data.get('estacion',''),
                             'rfc':data.get('rfc',''),'folio':data.get('ticket',''),'correo_cliente':job['correo']}
                    try:self.alertas.reportar(category,step,exc,contexto=context,modo_seguro=safe,bot=self.polaris)
                    except Exception:self.log('No se pudo preparar la alerta interna; revise la solicitud.')
                self.log('Polaris requiere revisión en esta solicitud. Se apartó el caso y la cola continúa con trabajos distintos.')
            self._changed(ident)
            return True
        finally:self._consumer.release()

    def aceptar_alta(self,ident,*,autorizado,inexistencia_revisada):
        if not autorizado or not inexistencia_revisada:raise ColaError('Falta autorización del operador y revisión de que el RFC no existe.')
        if self.cfg.get('app',{}).get('modo_prueba',True) is not False:raise ColaError('Desactive MODO SEGURO y confirme el alta real.')
        if self.ocupada:raise ColaError('Espere a que termine la operación actual.')
        if self.alta is None or self.alta.pendiente is None:raise ColaError('La preparación no está disponible. No se puede aceptar tras reiniciar el bot.')
        job=self.store.get(ident)
        if job is None:raise ColaError('Solicitud no localizada.')
        data=job['datos'];pending=self.alta.pendiente.solicitud
        if datos_alta(pending)!=data:raise ColaError('La preparación abierta pertenece a otra solicitud. No se pulsa Aceptar.')
        self.store.authorize_alta(ident);self._changed(ident);self._wake.set()

    def liberar_revision(self,ident,*,confirmado=False):
        if not confirmado:raise ColaError('Confirme que resolvió manualmente la captura/aviso de Polaris.')
        if self.ocupada:raise ColaError('No se libera una operación en curso.')
        job=self.store.get(ident)
        if not job or job['estado'] not in ESTADOS_REVISION:raise ColaError('La solicitud no está en revisión.')
        if job['tipo']=='ALTA' and self.alta is not None:self.alta.liberar()
        self.store.release(ident);self._changed(ident)

    def cancelar(self,ident):
        self.store.cancel(ident);self._notice(self.store.get(ident),'cancelada');self._changed(ident)
