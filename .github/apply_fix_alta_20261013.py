"""Aplicador de desarrollo de una sola rama. No se distribuye con el bot."""
from pathlib import Path
import re


def edit(path, old, new, count=1):
    p=Path(path)
    text=p.read_text(encoding='utf-8')
    assert text.count(old)==count, (path, 'Preimagen distinta', old[:100], text.count(old))
    p.write_text(text.replace(old,new),encoding='utf-8')


edit('arybot/cliente_model.py',
     "if not re.fullmatch(r'\\+?[0-9 ()-]{7,24}',ph):",
     "if ph and not re.fullmatch(r'\\+?[0-9 ()-]{7,24}',ph):")
edit('arybot/cliente_model.py', "if not 7<=len(ph.lstrip('+'))<=15:",
     "if telefono.strip() and not 7<=len(ph.lstrip('+'))<=15:")
edit('arybot/cliente_model.py', 'Datos del alta: cuatro campos del cliente + estación del contexto interno.',
     'Datos del alta: RFC, idCIF y correo; teléfono opcional + estación interna.')

edit('arybot/portal_modelo.py', 'def texto(body, key, *, max_len=254):\n    value = body.get(key)',
     "def texto(body, key, *, max_len=254, opcional=False):\n    value = body.get(key, '' if opcional else None)")
edit('arybot/portal_modelo.py', "texto(body, 'telefono', max_len=24)",
     "texto(body, 'telefono', max_len=24, opcional=True)")
edit('arybot/portal_modelo.py', 'from .cliente_model import AltaCliente',
     'from .cliente_model import AltaCliente\nfrom .cliente_facturacion import CODIGO_SIN_REGIMEN, MENSAJE_SIN_REGIMEN')
edit('arybot/portal_modelo.py', "    state = job['estado']\n    messages = {",
     "    state = job['estado']\n    codigo = (job.get('resultado') or {}).get('codigo_cliente', '')\n    sin_regimen = (job['tipo'] == 'FACTURA' and state == 'REVISION_REQUERIDA'\n                   and codigo == CODIGO_SIN_REGIMEN)\n    messages = {")
edit('arybot/portal_modelo.py', "        'mensaje': messages.get(state, 'Consulta el resultado con el área de facturación.'),",
     "        'mensaje': (MENSAJE_SIN_REGIMEN if sin_regimen else\n                    messages.get(state, 'Consulta el resultado con el área de facturación.')),\n        'codigo_cliente': CODIGO_SIN_REGIMEN if sin_regimen else '',")

edit('arybot/alta_clientes.py',
     'Preparar NO pulsa Aceptar. Aceptar requiere una preparación válida, confirmación\nexplícita y revisión previa del operador de que el RFC no exista. No se inventa\nuna búsqueda automática de duplicados que no fue mostrada en el video.',
     'Preparar NO pulsa Aceptar. Registrar termina con Aceptar en modo real y con\nautorización explícita del coordinador. Conserva validación de captura, diario\nlocal y bloqueo de edición visible; no afirma buscar todo el catálogo por RFC.\nLas preparaciones manuales conservan su revisión previa del operador.')
edit('arybot/alta_clientes.py', "            self.ui.escribir(main,form,'telefono',sol.telefono)",
     "            # Si se omite el teléfono, no se inventa ni se pega un sustituto.\n            # _validar_captura exige que el campo de Polaris también esté vacío.\n            if sol.telefono:\n                self.ui.escribir(main,form,'telefono',sol.telefono)")
edit('arybot/alta_clientes.py',
     '    def aceptar(self, solicitud: AltaCliente, *, autorizado=False, inexistencia_revisada=False):',
     '''    def registrar(self, solicitud: AltaCliente, *, autorizado=False):
        """Alta completa solicitada en modo real, Aceptar una sola vez.

        No equivale a una revisión humana de todo el catálogo de clientes.
        Un intento incierto permanece en el diario local y no se reejecuta.
        """
        if autorizado is not True:
            raise AltaClienteError('Falta autorizar el registro automático del cliente.')
        if self.bot.cfg.get('app',{}).get('modo_prueba',True) is not False:
            raise AltaClienteError('MODO SEGURO activo: el registro automático no guarda clientes.')
        sol=solicitud.validar()
        preparada=self.preparar(sol)
        resultado=self.aceptar(sol,autorizado=True,registro_automatico=True)
        return dict(preparada,**resultado)

    def aceptar(self, solicitud: AltaCliente, *, autorizado=False, inexistencia_revisada=False,
                registro_automatico=False):''')
edit('arybot/alta_clientes.py', '            if not autorizado or not inexistencia_revisada:',
     '            if autorizado is not True or not (inexistencia_revisada is True or registro_automatico is True):')

edit('arybot/polaris.py',
     '            inv=self._select_client(inv,sol.rfc)\n\n            self.etapa_alerta="FACTURACION: forma de pago y Uso CFDI"',
     '''            from .cliente_facturacion import exigir_cliente_facturable
            try:
                inv=self._select_client(inv,sol.rfc)
            except Exception:
                # Algunas versiones bloquean/activan la ventana antes de volver.
                exigir_cliente_facturable(self,inv)
                raise
            exigir_cliente_facturable(self,inv)

            self.etapa_alerta="FACTURACION: forma de pago y Uso CFDI"''')

edit('arybot/cola_operaciones.py', 'from .cliente_model import AltaCliente',
     'from .cliente_model import AltaCliente\nfrom .cliente_facturacion import ClienteSinRegimenError, CODIGO_SIN_REGIMEN')
edit('arybot/cola_operaciones.py', "data['idcif'],data['telefono'],data['correo']",
     "data['idcif'],data.get('telefono',''),data['correo']")
edit('arybot/cola_operaciones.py',
     "                    else:\n                        cerrar_sesion=False\n                        result=self.alta.preparar(sol)",
     "                    elif not safe:\n                        result=self.alta.registrar(sol,autorizado=True)\n                        if not isinstance(result,dict) or not result.get('numero_cliente'):\n                            raise ColaError('No se confirmó el número de cliente. Revise Polaris; no se repite.')\n                        self.store.finish(ident,'ALTA_CONFIRMADA',result=result,reason='Alta registrada y número de cliente confirmado en pantalla.')\n                        self._notice(self.store.get(ident),'completada')\n                    else:\n                        cerrar_sesion=False\n                        result=self.alta.preparar(sol)")
edit('arybot/cola_operaciones.py',
     "                if not isinstance(step,str):step='Operación de pantalla'",
     "                if not isinstance(step,str):step='Operación de pantalla'\n                sin_regimen=(kind=='FACTURA' and isinstance(exc,ClienteSinRegimenError)\n                             and step.startswith('FACTURACION:'))\n                alta_incierta=(kind=='ALTA' and step.startswith('ALTA: Aceptar solicitado'))")
edit('arybot/cola_operaciones.py',
     "                if fiscal_incierto:\n                    cerrar_sesion=False",
     "                if alta_incierta:\n                    cerrar_sesion=False\n                    reason=(str(exc)+' | Posible alta guardada. Revise el número de cliente en Polaris antes de liberar la revisión; no repita el registro.')\n                elif fiscal_incierto:\n                    cerrar_sesion=False")
edit('arybot/cola_operaciones.py',
     "                                  hold=fiscal_incierto,reason=reason)\n                self._notice(self.store.get(ident),'revision')",
     "                                  hold=fiscal_incierto or alta_incierta,reason=reason,\n                                  result={'codigo_cliente':CODIGO_SIN_REGIMEN} if sin_regimen else {})\n                self._notice(self.store.get(ident),'cliente_sin_regimen' if sin_regimen else 'revision')")
edit('arybot/cola_operaciones.py',
     "                if fiscal_incierto:\n                    self.log('Resultado fiscal incierto:",
     "                if alta_incierta:\n                    self.log('Alta con guardado incierto: Polaris sigue abierto y la cola espera revisión; no se repite Aceptar.')\n                elif sin_regimen:\n                    self.log(str(exc)+' Se avisó al cliente; las demás solicitudes pueden continuar.')\n                elif fiscal_incierto:\n                    self.log('Resultado fiscal incierto:")
edit('arybot/cola_operaciones.py',
     '                        self.polaris.cerrar_polaris_tras_solicitud(motivo_cierre)',
     "                        self.polaris.cerrar_polaris_tras_solicitud(motivo_cierre)\n                        if job and job['tipo']=='ALTA' and self.alta is not None:\n                            self.alta.liberar()")

edit('arybot/cola_modelo.py', 'from .cliente_model import AltaCliente',
     'from .cliente_model import AltaCliente\nfrom .cliente_facturacion import MENSAJE_SIN_REGIMEN, ACCION_SIN_REGIMEN')
edit('arybot/cola_modelo.py', "'El alta requiere revisión del personal antes de guardar.'",
     "'En modo real el bot valida los datos y solicita guardar el registro; si surge un aviso, el personal debe revisarlo.'")
edit('arybot/cola_modelo.py', "    elif event == 'faltantes':",
     "    elif event == 'cliente_sin_regimen' and job['tipo']=='FACTURA':\n        lines += [MENSAJE_SIN_REGIMEN, ACCION_SIN_REGIMEN]\n    elif event == 'faltantes':")

edit('arybot/alta_gui.py', "('Teléfono',self.phone)", "('Teléfono (opcional)',self.phone)")
edit('arybot/alta_gui.py', 'Los cuatro datos del cliente', 'Datos del cliente (teléfono opcional)')
edit('arybot/alta_gui.py', 'La búsqueda automática de duplicados aún no está conectada. Esta casilla NO se pedirá al cliente en la página.',
     'En modo real se registra automáticamente tras validar la captura. Esta revisión manual se conserva para altas preparadas sin guardar; no hay búsqueda completa del catálogo por RFC.')
edit('arybot/alta_gui.py', 'Gmail también puede recibir solicitudes de alta. El guardado conserva la revisión y autorización del operador.',
     'En modo real, Registrar cliente y las solicitudes de Gmail/portal completan Aceptar una sola vez. Los avisos o guardados inciertos requieren revisión.')
edit('arybot/alta_gui.py', "        self.btn_prepare.configure(state='normal' if on and not self.busy and not self.holding else 'disabled')",
     "        self.btn_prepare.configure(text='Preparar alta (SIN ACEPTAR)' if self.app.mode_var.get() else 'Registrar cliente',\n                                   state='normal' if on and not self.busy and not self.holding else 'disabled')")
edit('arybot/alta_gui.py', "        self.status.set('ALTA EN COLA. Se preparará por turno sin Aceptar. Gmail y avisos siguen activos.')",
     "        self.status.set('ALTA EN COLA. Modo seguro: se preparará sin guardar.' if job['seguro'] else\n                        'ALTA EN COLA. Se validarán los datos y se pulsará Aceptar una sola vez en modo real.')")
edit('arybot/alta_gui.py', "                        elif state=='ALTA_CONFIRMADA':\n                            self.numero.set",
     "                        elif state=='ALTA_CONFIRMADA':\n                            self.nombre.set(data.get('nombre',''));self.cp.set(data.get('codigo_postal',''))\n                            self.numero.set")

for path in ('portal_web/ary_facturacion.htm','para_el_sitio/ary_facturacion.htm'):
    p=Path(path);text=p.read_text(encoding='utf-8')
    text,n=re.subn(r'(<label\b[^>]*\bfor="alta-telefono"[^>]*>).*?(</label>)',
                   r'\1Teléfono (opcional)\2',text,flags=re.S)
    assert n==1,(path,'label teléfono')
    def input_phone(match):
        return re.sub(r'\s+required(?:=(?:"[^"]*"|\x27[^\x27]*\x27))?', '',match.group(0))
    text,n=re.subn(r'<input\b[^>]*\bid="alta-telefono"[^>]*>',input_phone,text)
    assert n==1,(path,'input teléfono')
    text=text.replace('Los cuatro datos que utiliza el alta del bot.', 'RFC, idCIF y correo electrónico. El teléfono es opcional.')
    text=text.replace('El personal revisa el registro antes de guardarlo;', 'En modo real, el bot valida los datos y pulsa Aceptar para guardar el registro;')
    text=text.replace('Revisé mi RFC, idCIF, teléfono y correo, y solicito que se atienda mi alta de cliente en la estación seleccionada.',
                      'Revisé mi RFC, idCIF y correo (teléfono opcional), y autorizo registrar mi alta de cliente en la estación seleccionada.')
    text=text.replace('El registro queda pendiente hasta que el personal confirme el alta en el sistema.',
                      'El registro estará completo cuando Polaris confirme el número de cliente. En modo de prueba no se guarda.')
    p.write_text(text,encoding='utf-8')

edit('portal_web/ary_facturacion.htm',
     "    const state = PROGRESS[data.estado] || {",
     "    const state = {...(PROGRESS[data.estado] || {")
edit('portal_web/ary_facturacion.htm',
     "      msg:data.mensaje || 'Estamos atendiendo tu solicitud.'\n    };",
     "      msg:data.mensaje || 'Estamos atendiendo tu solicitud.'\n    })};\n    if(data.tipo==='ALTA' && data.estado==='EJECUTANDO') {\n      state.title=data.modo_seguro ? 'Preparando prueba de alta…' : 'Registrando cliente…';state.msg=data.modo_seguro ? 'Se preparan los datos sin guardar un cliente.' : 'Estamos validando y guardando tu registro en Polaris.';\n    }\n    if(data.tipo==='FACTURA' && data.codigo_cliente==='CLIENTE_SIN_REGIMEN') {\n      state.title='Alta fiscal incompleta';state.msg=data.mensaje;state.error=true;\n    }")
edit('portal_web/ary_facturacion.htm',
     "const strong=document.createElement('strong'); strong.textContent='Error al facturar.';",
     "const strong=document.createElement('strong'); strong.textContent=data.codigo_cliente==='CLIENTE_SIN_REGIMEN' ? data.mensaje : 'Error al facturar.';")
edit('portal_web/ary_facturacion.htm',
     "    $('receiptMessage').textContent=state ? state.msg : data.mensaje;",
     "    $('receiptMessage').textContent=(data.codigo_cliente==='CLIENTE_SIN_REGIMEN' || data.tipo==='ALTA') ? data.mensaje : state ? state.msg : data.mensaje;")
print('Cambios de alta, teléfono opcional y aviso de régimen aplicados; configuraciones/datos intactos.')
