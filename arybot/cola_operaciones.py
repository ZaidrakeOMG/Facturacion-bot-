"""Un consumidor para TODAS las acciones de pantalla; recepción/correos independientes.

Los trabajos son datos persistidos, no comandos ni SQL proporcionados por clientes.
No se reintenta automáticamente ninguna acción sobre Polaris después de un error.
"""
from __future__ import annotations
import threading
import time
from .cola_local import ColaLocal
from .cola_modelo import (ajustes_cola,datos_alta,factura_datos,como_solicitud,clave_trabajo,
                         ColaError,ESTADOS_REVISION)
from .cliente_model import AltaCliente
from .coincidencia_fiscal import CoincidenciaFiscalError, CODIGO_VERIFICACION_FISCAL
from .cliente_facturacion import (ClienteSinRegimenError, ClienteNoRegistradoError,
                                 CODIGO_SIN_REGIMEN, CODIGO_NO_REGISTRADO)
from .avisos_cliente import AvisosCliente
from .polaris import ReceptorNoVerificadoError

class ColaOperaciones:
    def __init__(self,base,cfg,polaris,log,*,alertas=None,alta=None,db=None,on_event=None,store=None,avisos=None):
        self.base=base;self.cfg=cfg;self.polaris=polaris;self.log=log;self.alertas=alertas;self.alta=alta;self.db=db
        self.on_event=on_event or (lambda *_:None)
        self.store=store or ColaLocal(base)
        recovered=self.store.recover()
        # Se prepara una nueva etapa sólo con una base real del bot.
        # Las pruebas que inyectan un almacén propio no alteran sus registros.
        inicio={'primera_vez':False,'archivadas':0,'requiere_reanudar':False}
        if store is None and cfg.get('app',{}).get('ary_nueva_etapa_magenta',False):
            inicio=self.store.archivar_solicitudes(
                confirmado=True,marca_inicio='ary_v2_nueva_etapa_magenta_20261127')
            if inicio['primera_vez']:
                log('Nueva etapa: se archivaron '+str(inicio['archivadas'])+
                    ' solicitudes antiguas; no se borró el historial SQLite.')
                recovered=[]  # No reenviar avisos de solicitudes archivadas
        self.avisos=avisos or AvisosCliente(base,cfg,self.store,log,on_event=self.on_event)
        self._stop=threading.Event();self._wake=threading.Event();self._consumer=threading.Lock()
        self._lifecycle=threading.RLock();self.thread=None
        # La pausa manual pertenece únicamente a esta ejecución del programa.
        # No se hereda de cierres anteriores ni de cuadros de diálogo.
        self._manual_pause=bool(self.store.meta('pendiente_comprobar_polaris')=='1')
        self._operator_pause=False
        self._last_auto_probe=0.0
        self._auto_probe_reported=False
        for job in recovered:self._notice(job,'revision')
        # Recupera un acuse que no alcanzó a persistirse después de encolar; UNIQUE evita duplicarlo.
        for job in self.store.list(10000):
            if job['estado']=='EN_COLA':self._notice(job,'recibida')

    def _reactivar_automaticamente_tras_migracion(self):
        """La lista archivada no debe detener facturas NUEVAS.

        Comprobar automáticamente las ventanas de Polaris en modo lectura;
        no tocar la solicitud antigua ni intentar repetir su CFDI. Respetar
        cualquier pausa manual y cualquier bloqueo de trabajos actuales.
        """
        if self._operator_pause or self._consumer.locked():
            return False
        if self.store.meta('pendiente_comprobar_polaris')!='1':
            return False
        state=self.store.status()
        if state.get('bloqueo') or state.get('activa'):
            return False
        if not any(r['estado']=='EN_COLA' for r in self.store.list()):
            return False
        now=time.monotonic()
        if now-self._last_auto_probe<3.0:
            return False
        self._last_auto_probe=now
        libre=False
        if self.polaris is not None:
            try:
                libre=self.polaris.pantalla_libre_para_nueva_solicitud() is True
            except Exception as exc:
                if not self._auto_probe_reported:
                    self.log('No se pudo comprobar Polaris automáticamente: '+str(exc))
        if not libre:
            if not self._auto_probe_reported:
                self.log('Una factura nueva está pendiente. El bot comprobará '
                         'automáticamente que Polaris esté libre, sin repetir '
                         'las solicitudes antiguas.')
                self._auto_probe_reported=True
            return False
        try:
            self.store.resume(polaris_comprobado=True)
        except ColaError as exc:
            if not self._auto_probe_reported:
                self.log('Polaris todavía no está disponible: '+str(exc))
                self._auto_probe_reported=True
            return False
        self._manual_pause=False
        self._auto_probe_reported=False
        self.log('Polaris libre. Atención automática reactivada para '
                 'solicitudes nuevas; los folios anteriores siguen archivados.')
        try:self.on_event('cola','')
        except Exception:pass
        self._wake.set()
        return True

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

    def _finalizar_prueba_sin_bloquear(self, ident, kind, result):
        """Una prueba NO timbra; cerrar su pantalla y atender la siguiente.

        Si Polaris no confirma el cierre, se detiene por seguridad para no
        escribir el siguiente cliente sobre una factura todavía abierta.
        """
        try:
            self.polaris.cerrar_polaris_tras_solicitud('PRUEBA SIN EMISIÓN '+kind)
            if kind == 'ALTA' and self.alta is not None:
                self.alta.liberar()
        except Exception as exc:
            motivo=('La prueba NO emitió CFDI, pero Polaris no confirmó que la pantalla '
                    'se cerró sin guardar: '+str(exc)+'. Comprueba la ventana antes de continuar.')
            self.store.finish(ident,'REVISION_REQUERIDA',result={'resultado':'PRUEBA_SIN_EMISION'},
                              hold=True,reason=motivo)
            self.log('No se pasó al siguiente cliente porque no pudo cerrarse la pantalla de prueba: '+str(exc))
            self._notice(self.store.get(ident),'revision')
            return False
        self.store.finish(ident,'PRUEBA_CERRADA',result={'resultado':str(result)[:200]},
                          reason='Prueba terminada SIN emitir CFDI ni registrar cliente; continuando con la siguiente.')
        self._notice(self.store.get(ident),'completada')
        return True

    def _enqueue(self,kind,data,*,source='Formulario',source_id=None,safe=True,correo='',state='EN_COLA',reason=''):
        opts=ajustes_cola(self.cfg)
        minutes=opts['minutos_alta'] if kind=='ALTA' else opts['minutos_factura']
        job,new=self.store.enqueue(kind,data,source=source,source_id=source_id,safe=bool(safe),correo=correo,
                                   clave=clave_trabajo(kind,data) if state=='EN_COLA' else '',minutes=minutes,state=state,reason=reason)
        if new:
            self._notice(job,'recibida' if state=='EN_COLA' else ('faltantes' if state=='ESPERANDO_DATOS' else 'revision'),reason)
            self._changed(job['id'])
            # Toda solicitud EN_COLA despierta el consumidor.
            # Una pausa guardada por una ejecución anterior NO debe dejar trabajos
            # congelados. Solo se respeta la pausa que el operador hizo en ESTA sesión
            # y los bloqueos interactivos reales (alta/factura preparada en pantalla).
            if state=='EN_COLA':
                self._reactivar_automaticamente_tras_migracion()
                st=self.store.status()
                if not self._manual_pause and not st.get('bloqueo'):
                    try:
                        self.store.resume()
                    except ColaError:
                        # Hay una captura interactiva legítima ocupando Polaris.
                        pass
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

        # Si quedó una pausa SOLO por archivar el historial, reanudar
        # automáticamente cuando la pantalla real de Polaris esté libre.
        self._reactivar_automaticamente_tras_migracion()
        # Si quedaron solicitudes EN_COLA de una ejecución anterior, arrancan solas.
        st=self.store.status()
        if st.get('conteos',{}).get('EN_COLA',0) and not st.get('bloqueo') and not self._manual_pause:
            try:self.store.resume()
            except ColaError:pass

        def loop():
            while not self._stop.is_set():
                try:
                    self._reactivar_automaticamente_tras_migracion()
                    if self.ejecutar_una():
                        continue
                except Exception as exc:
                    # Los errores propios de una solicitud se aíslan dentro de ejecutar_una.
                    # Un error del coordinador no debe dejar la cola pausada para siempre.
                    self.log('Error del coordinador de cola: '+str(exc)+'. Se volverá a comprobar automáticamente.')
                    self._wake.wait(1.0)
                    self._wake.clear()
                    continue
                self._wake.wait(.5);self._wake.clear()

        self.thread=threading.Thread(target=loop,daemon=True,name='ARY-Polaris-Cola-Unica')
        self.thread.start()
        self._wake.set()

    def despertar_solicitud_existente(self):
        """Un segundo envío idéntico NO crea otro trabajo ni repite su factura.

        Solo despierta al mismo consumidor del primer envío para que siga
        trabajando cuando Polaris esté libre.
        """
        if self._stop.is_set():
            return
        if self.thread is None or not self.thread.is_alive():
            self.iniciar_hilos()
        self._reactivar_automaticamente_tras_migracion()
        self._wake.set()

    def iniciar_cola(self, *, polaris_comprobado=False):
        self.store.resume(polaris_comprobado=polaris_comprobado)
        self._manual_pause=False
        self._operator_pause=False
        self._wake.set();self.on_event('cola','')

    def pausar(self):
        self._operator_pause=True
        self._manual_pause=True
        self.store.pause('Pausada por el operador en esta sesión; termina la operación actual. Gmail y correos siguen disponibles.')
        self.on_event('cola','')
    def detener(self):
        self._stop.set();self._wake.set();self.avisos.detener()
    @property
    def ocupada(self):return self._consumer.locked() or bool(self.store.status()['activa'])

    def ejecutar_una(self):
        if not self._consumer.acquire(blocking=False):return False
        job=None
        cerrar_sesion=False
        motivo_cierre=""
        try:
            job=self.store.claim(production_allowed=self.cfg.get('app',{}).get('modo_prueba',True) is False)
            if not job:return False
            ident=job['id'];safe=job['seguro'];kind=job['tipo'];data=job['datos']
            cerrar_sesion=kind in {'FACTURA','ALTA'}
            motivo_cierre=kind
            self._changed(ident)
            if job['accion']!='aceptar':self._notice(self.store.get(ident),'procesando')
            try:
                if not self.polaris:raise ColaError('El control de Polaris no está disponible en este equipo.')
                if kind=='FACTURA':
                    result=self.polaris.invoice(como_solicitud(data),test_mode=safe)
                    if result=='PRUEBA_OK' and safe:
                        cerrar_sesion=False
                        self._finalizar_prueba_sin_bloquear(ident,'FACTURA',result)
                    elif result=='ENVIO_SOLICITADO' and not safe:
                        self.store.finish(ident,'ENVIO_SOLICITADO',result={'resultado':result},reason='Polaris solicitó el correo; recepción no verificada.')
                        self._notice(self.store.get(ident),'completada')
                    else:raise ColaError('Resultado de facturación no reconocido. No se repite el intento.')
                elif kind=='ALTA':
                    if self.alta is None:raise ColaError('El módulo de alta no está disponible.')
                    sol=AltaCliente.crear(data['estacion'],data['rfc'],data['idcif'],data.get('telefono',''),data['correo'],
                                           razon_social=data.get('razon_social',''),
                                           calle_constancia=data.get('calle_constancia',''))
                    if job['accion']=='aceptar':
                        if self.cfg.get('app',{}).get('modo_prueba',True) is not False:raise ColaError('MODO SEGURO activo: no se pulsa Aceptar.')
                        result=self.alta.aceptar(sol,autorizado=True,inexistencia_revisada=True)
                        if not isinstance(result,dict) or not result.get('numero_cliente'):raise ColaError('No se confirmó el número de cliente. Revise Polaris; no se repite.')
                        self.store.finish(ident,'ALTA_CONFIRMADA',result=result,reason='Número de cliente confirmado en pantalla.')
                        self._notice(self.store.get(ident),'completada')
                    elif not safe:
                        result=self.alta.registrar(sol,autorizado=True)
                        if not isinstance(result,dict) or not result.get('numero_cliente'):
                            raise ColaError('No se confirmó el número de cliente. Revise Polaris; no se repite.')
                        self.store.finish(ident,'ALTA_CONFIRMADA',result=result,reason='Alta registrada y número de cliente confirmado en pantalla.')
                        self._notice(self.store.get(ident),'completada')
                    else:
                        cerrar_sesion=False
                        result=self.alta.preparar(sol)
                        self._finalizar_prueba_sin_bloquear(ident,'ALTA',result)
                else:
                    cerrar_sesion=False
                    if kind=='ESTACION':result=self.polaris.test_station_only(data['estacion'])
                    elif kind=='PAGO':result=self.polaris.test_payment_only(data['forma_pago'])
                    elif kind=='LIMPIEZA':result=self.polaris.test_cleanup_only()
                    else:raise ColaError('Tipo de operación no permitido.')
                    self.store.finish(ident,'PRUEBA_PENDIENTE',result={'resultado':str(result)},hold=True,
                                      reason='Prueba concluida. Revise la pantalla y libere la revisión para continuar.')
            except Exception as exc:
                diag=getattr(self.polaris,'diag',None) if self.polaris else None
                if diag:
                    try:diag.capturar(getattr(self.polaris,'etapa_alerta','COLA: error de operación'),exc,bot=self.polaris,
                                      contexto={'tipo':kind,'id_solicitud':ident,'origen':job.get('origen','')})
                    except Exception:pass
                from .factura_final import FacturaYaProcesada
                if isinstance(exc, FacturaYaProcesada) and kind == 'FACTURA':
                    self.store.finish(ident,'YA_FACTURADO',result={'resultado':'YA_FACTURADO'},
                                      reason='El folio ya estaba procesado/facturado; no se volvió a timbrar.')
                    self._notice(self.store.get(ident),'ya_facturado')
                    self.log('Folio ya facturado/procesado. Se cerró la solicitud y se avisó al cliente; no se reintentó el timbrado.')
                    self._changed(ident)
                    return True
                step=getattr(self.polaris,'etapa_alerta','Operación de pantalla')
                if not isinstance(step,str):step='Operación de pantalla'
                sin_regimen=(kind=='FACTURA' and isinstance(exc,ClienteSinRegimenError)
                             and step.startswith('FACTURACION:'))
                no_registrado=(kind=='FACTURA' and isinstance(exc,ClienteNoRegistradoError)
                               and step.startswith('FACTURACION:'))
                verificacion_fiscal=(kind=='FACTURA' and isinstance(exc,CoincidenciaFiscalError)
                                     and step.startswith('FACTURACION:'))
                receptor_pendiente=(kind=='FACTURA' and isinstance(exc, ReceptorNoVerificadoError))
                alta_incierta=(kind=='ALTA' and step.startswith('ALTA: Aceptar solicitado'))
                # Después del Aceptar de TIMBRADO, un error puede significar
                # que el CFDI existe aunque todavía no se haya enviado correo.
                # NO cerrar/terminar Polaris ni abrir otro cliente sobre un envío vivo.
                fiscal_incierto=(kind=='FACTURA' and
                                 step.startswith(('TIMBRADO:', 'ENVIO_CORREO:')))
                if receptor_pendiente and not fiscal_incierto:
                    motivo_cierre='ERROR FACTURA'
                    reason=(str(exc)+' | Captura omitida por receptor no verificado. '
                            'Se limpiará Polaris antes de continuar; no se reintenta.')
                elif alta_incierta:
                    cerrar_sesion=False
                    reason=(str(exc)+' | Posible alta guardada. Revise el número de cliente en Polaris antes de liberar la revisión; no repita el registro.')
                elif fiscal_incierto:
                    cerrar_sesion=False
                    reason=(str(exc)+' | Posible CFDI timbrado o correo pendiente. '
                            'Revisa Polaris y el folio antes de liberar esta revisión.')
                else:
                    motivo_cierre='ERROR '+kind
                    reason=str(exc)
                self.store.finish(ident,'REVISION_REQUERIDA',
                                  hold=fiscal_incierto or alta_incierta,reason=reason,
                                  result={'codigo_cliente': (CODIGO_SIN_REGIMEN if sin_regimen
                                                            else CODIGO_NO_REGISTRADO if no_registrado
                                                            else CODIGO_VERIFICACION_FISCAL)}
                                  if (sin_regimen or no_registrado or verificacion_fiscal)
                                     and not (fiscal_incierto or alta_incierta) else {})
                self._notice(self.store.get(ident), 'cliente_sin_regimen' if sin_regimen
                             else 'cliente_no_registrado' if no_registrado
                             else 'verificacion_fiscal' if verificacion_fiscal else 'revision')
                if self.alertas:
                    category='ALTA_CLIENTE' if kind=='ALTA' else ('ENVIO_FACTURA' if step.startswith('ENVIO_CORREO') else 'FACTURACION')
                    context={'origen':job['origen'],'id_solicitud':ident,'estacion':data.get('estacion',''),
                             'rfc':data.get('rfc',''),'folio':data.get('ticket',''),'correo_cliente':job['correo']}
                    try:self.alertas.reportar(category,step,exc,contexto=context,modo_seguro=safe,bot=self.polaris)
                    except Exception:self.log('No se pudo preparar la alerta interna; revise la solicitud.')
                if receptor_pendiente and not fiscal_incierto:
                    self.log('RFC receptor sin confirmar: se omite esta solicitud y se limpia Polaris. No se pulsó Aceptar.')
                elif alta_incierta:
                    self.log('Alta con guardado incierto: Polaris sigue abierto y la cola espera revisión; no se repite Aceptar.')
                elif sin_regimen or no_registrado:
                    self.log(str(exc)+' Se avisó al cliente; las demás solicitudes pueden continuar.')
                elif fiscal_incierto:
                    self.log('Resultado fiscal incierto: Polaris sigue abierto y la cola '
                             'queda pausada hasta liberar esta revisión; no se repetirá timbrado.')
                else:
                    self.log('Error antes del timbrado: solicitud a revisión. '
                             'Las demás solicitudes pueden continuar tras limpiar Polaris.')
            self._changed(ident)
            return True
        finally:
            try:
                if cerrar_sesion and self.polaris:
                    try:
                        self.polaris.cerrar_polaris_tras_solicitud(motivo_cierre)
                        if job and job['tipo']=='ALTA' and self.alta is not None:
                            self.alta.liberar()
                    except Exception as close_exc:
                        self._manual_pause=True
                        self.store.pause('Polaris no confirmó el cierre. Revisar sesión antes de continuar.')
                        self.log('No se pudo cerrar Polaris al terminar la solicitud: '+str(close_exc))
            finally:
                self._consumer.release()
                # Despierta inmediatamente el consumidor: una revisión individual
                # no debe frenar solicitudes distintas que ya estén en cola.
                try:self._wake.set()
                except Exception:pass

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

    def resumen_liberacion_masiva(self):
        return self.store.resumen_liberacion_masiva()

    def liberar_revisiones_masivas(self, *, confirmado=False):
        if confirmado is not True:
            raise ColaError('Confirme primero que revisó en Polaris las solicitudes que se cerrarán.')
        if self.ocupada:
            raise ColaError('Hay una operación en curso. Espere a que termine.')
        result = self.store.liberar_revisiones_masivas()
        self.log('Cierre masivo de revisiones: '
                 + str(result['liberables']) + ' cerrada(s), '
                 + str(result['protegidas']) + ' protegida(s). '
                 'No se volvió a facturar, registrar ni enviar correos.')
        try:
            self.on_event('cola', '')
        except Exception:
            self.log('No se pudo actualizar la vista; los cambios quedaron guardados.')
        self._wake.set()
        return result

    def archivar_solicitudes(self, ident=None, *, confirmado=False):
        """Retira de la lista sin borrar comprobantes, avisos ni fuente de datos.

        El mismo candado que protege la captura de Polaris impide archivar una
        operación REAL mientras la está ejecutando el bot. Una EJECUTANDO
        huérfana (ej. caída de la VM) se permite tras confirmación, dejando
        constancia de resultado incierto y bloqueando nuevas emisiones del
        mismo ticket.
        """
        if confirmado is not True:
            raise ColaError('Confirma que revisaste Polaris antes de borrar solicitudes.')
        if not self._consumer.acquire(blocking=False):
            raise ColaError('Polaris está capturando una factura. Espera o cierra el bot '
                            'de manera controlada antes de borrar una solicitud atorada.')
        try:
            resultado=self.store.archivar_solicitudes(ident,confirmado=True)
            if resultado['requiere_reanudar']:
                # Una solicitud nueva del portal no debe reanudar por accidente
                # después de retirar un bloqueo fiscal. Requiere clic explícito.
                self._manual_pause=True
            if self.alta is not None and (
                    resultado['atascadas'] or resultado['requiere_reanudar']):
                try:self.alta.liberar()
                except Exception:self.log('Una alta retirada podría conservar su ventana: revisa Polaris.')
            self.log(
                'Historial oculto de la lista: '+str(resultado['archivadas'])+
                ' solicitud(es); '+str(resultado['canceladas'])+
                ' pendiente(s) cancelada(s). Los registros siguen en SQLite.'
            )
            try:self.on_event('cola','')
            except Exception:self.log('Se guardó la limpieza, pero no se actualizó la vista automáticamente.')
            self._wake.set()
            return resultado
        finally:
            self._consumer.release()

    def retomar_folio_sin_cfdi(self, estacion, ticket, *, confirmado=False):
        """Recupera solicitud web original después de comprobar NO timbrado.

        Nunca llama invoice(): solo cambia un trabajo anterior a EN_COLA y
        despierta al único consumidor. No interrumpe otra captura.
        """
        if confirmado is not True:
            raise ColaError('Antes de retomar confirma que consultaste Polaris y no existe CFDI.')
        if self.cfg.get('app',{}).get('modo_prueba',True) is not False:
            raise ColaError('Para retomar una factura real, desactiva MODO SEGURO.')
        if not self._consumer.acquire(blocking=False):
            raise ColaError('Polaris está trabajando. Inténtalo al terminar la operación.')
        try:
            st=self.store.status()
            if st.get('activa'):
                raise ColaError('Hay una factura en proceso. Espera que termine.')
            if self.polaris is None or self.polaris.pantalla_libre_para_nueva_solicitud() is not True:
                raise ColaError('Polaris tiene una pantalla abierta o no se pudo comprobar '
                                'que esté libre. No se reactivó el folio.')
            job=self.store.retomar_folio_sin_cfdi(estacion,ticket,confirmado=True)
            try:
                self.store.resume(polaris_comprobado=True)
                self._manual_pause=False
                self._operator_pause=False
            except ColaError as exc:
                self.log('La solicitud fue recuperada, pero la atención sigue detenida: '+str(exc))
            self.log('Se recuperó la solicitud original '+job['id']+
                     ' tras confirmar que el ticket no tiene CFDI en Polaris. '
                     'No se creó una segunda solicitud.')
            self._changed(job['id'])
            self._wake.set()
            return job
        finally:
            self._consumer.release()

    def cancelar_pendientes(self, *, confirmado=False):
        if confirmado is not True:
            raise ColaError('Confirma retirar las solicitudes pendientes.')
        ids=self.store.cancel_pending()
        for ident in ids:
            self._notice(self.store.get(ident),'cancelada')
            self._changed(ident)
        self._wake.set()
        self.log('Cola: '+str(len(ids))+' pendientes retiradas; historial conservado.')
        return len(ids)

    def cancelar(self,ident):
        self.store.cancel(ident);self._notice(self.store.get(ident),'cancelada');self._changed(ident)
