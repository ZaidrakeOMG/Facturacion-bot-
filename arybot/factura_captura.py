"""Captura de factura por teclado + salida del control + lectura independiente.

El texto visible no prueba que un parámetro interno/CFDI sea válido. Este módulo
comprueba únicamente los controles Windows; no consulta ni modifica SQL.
Nunca escribe RFC/nombre/CP/régimen del receptor: sólo verifica los que Polaris
cargó al elegir el cliente. Sólo RFC de búsqueda, fecha del ticket, folio y correo son entradas.
"""
from __future__ import annotations
import ctypes
import json
import re
import time
import unicodedata
from .alta_texto import TextoWindows
from .teclado_factura import TecladoFactura

class CapturaFacturaError(RuntimeError): pass

def canon(value):
    s=unicodedata.normalize('NFKD',str(value or ''))
    return re.sub(r'[^A-Z0-9Ñ&]+',' ',''.join(c for c in s if not unicodedata.combining(c)).upper()).strip()

def coincide(campo,a,b):
    if campo=='rfc':return str(a).strip().rstrip('_ ').upper()==str(b).strip().upper()
    if campo=='folio':
        return bool(re.fullmatch(r'[0-9]+',str(a).strip()) and re.fullmatch(r'[0-9]+',str(b).strip())
                    and int(str(a).strip())==int(str(b).strip()))
    if campo=='fecha':
        def f(v):
            m=re.fullmatch(r'\s*(\d{1,2})/(\d{1,2})/(\d{4})\s*',str(v or ''))
            return (int(m.group(3)),int(m.group(2)),int(m.group(1))) if m else None
        return bool(f(a) and f(a)==f(b))
    return str(a).strip()==str(b).strip()

class LecturaCombo(TextoWindows):
    CB_GETCOUNT=0x0146;CB_GETCURSEL=0x0147;CB_GETLBTEXT=0x0148
    CB_GETLBTEXTLEN=0x0149;CB_GETDROPPEDSTATE=0x0157
    MENSAJES=frozenset((TextoWindows.WM_GETTEXT,CB_GETCOUNT,CB_GETCURSEL,
                        CB_GETLBTEXT,CB_GETLBTEXTLEN,CB_GETDROPPEDSTATE))
    def _numero(self,h,m,w=0):
        return ctypes.c_ssize_t(self._message(h,m,w)).value
    def opciones(self,h):
        n=self._numero(h,self.CB_GETCOUNT)
        if not 1<=n<=120:raise CapturaFacturaError('No se puede leer el catálogo del selector. No se elige por posición supuesta.')
        values=[]
        for i in range(n):
            size=self._numero(h,self.CB_GETLBTEXTLEN,i)
            if not 0<size<512:raise CapturaFacturaError('Opción del selector vacía o no legible.')
            buf=ctypes.create_unicode_buffer(size+1)
            got=ctypes.c_ssize_t(self._message(h,self.CB_GETLBTEXT,i,ctypes.addressof(buf))).value
            if got<0 or got>size:raise CapturaFacturaError('No se pudo leer una opción del selector.')
            values.append(buf.value)
        return values
    def actual(self,h):
        idx=self._numero(h,self.CB_GETCURSEL);items=self.opciones(h)
        if not 0<=idx<len(items):raise CapturaFacturaError('El selector tiene texto pero no un elemento seleccionado.')
        return idx,items[idx]
    def abierto(self,h):return bool(self._numero(h,self.CB_GETDROPPEDSTATE))

class CapturaFactura:
    # Geometría del formulario grabado, no del escritorio. Se comprueba clase,
    # pertenencia y unicidad; nunca se escribe usando sólo coordenadas.
    RECEPTOR={'cliente':(.126,.121),'rfc':(.370,.121),'nombre':(.365,.166),
              'cp':(.130,.207),'regimen':(.385,.373)}
    def __init__(self,bot,*,texto=None,teclado=None,combo=None,reloj=None,dormir=None,foco=None):
        self.b=bot;self.texto=texto or TextoWindows();self.teclado=teclado or TecladoFactura()
        self.combo=combo or LecturaCombo();self.reloj=reloj or time.monotonic
        self.dormir=dormir or time.sleep;self.foco_override=foco;self.eventos=[]
    @property
    def w(self):
        from . import polaris
        return polaris.win32gui
    def foco(self,root):
        if self.foco_override:return self.foco_override(root)
        from . import polaris
        from ctypes import wintypes as wt
        class GI(ctypes.Structure):
            _fields_=[('cbSize',wt.DWORD),('flags',wt.DWORD),('active',wt.HWND),
                ('focus',wt.HWND),('capture',wt.HWND),('menu',wt.HWND),('move',wt.HWND),
                ('caret',wt.HWND),('rc',wt.RECT)]
        u=ctypes.WinDLL('user32',use_last_error=True)
        u.GetGUIThreadInfo.argtypes=[wt.DWORD,ctypes.POINTER(GI)];u.GetGUIThreadInfo.restype=wt.BOOL
        g=GI();g.cbSize=ctypes.sizeof(GI)
        tid=polaris.win32process.GetWindowThreadProcessId(root)[0]
        return g.focus if u.GetGUIThreadInfo(tid,ctypes.byref(g)) else None
    def _evento(self,campo,etapa,**extra):
        self.eventos.append(dict(campo=campo,etapa=etapa,**extra));self.eventos=self.eventos[-100:]
        diag=getattr(self.b,'diag',None)
        if diag:
            try:diag.evento('CAMPO: '+str(campo),etapa,etapa,dict(extra, campo=str(campo)))
            except Exception:pass
        self.diagnostico()
    def diagnostico(self):
        try:
            folder=self.b.base/'logs';folder.mkdir(parents=True,exist_ok=True)
            (folder/'diagnostico_captura_factura.json').write_text(json.dumps(
                {'version':'3.4.0','eventos':self.eventos,
                 'nota':'Sólo campos/etapas/booleanos/clases. No incluye valores de RFC, correo, folio ni contraseña.'},
                ensure_ascii=False,indent=2),encoding='utf-8')
        except OSError:pass
    def _guard(self,root,h=None):
        from . import polaris
        polaris.pyautogui.failSafeCheck()
        if not self.b._window_exists_visible(root) or not self.w.IsWindowEnabled(root):
            raise CapturaFacturaError('Polaris abrió una validación o el formulario dejó de estar disponible. No se continúa.')
        fg=self.w.GetForegroundWindow()
        if not fg or not self.b._same_polaris_process(root,fg):
            raise CapturaFacturaError('Otra aplicación tomó el foco. No se envían más teclas.')
        if h is not None:
            if (not self.b._is_descendant_or_same(root,h) or not self.b._window_exists_visible(h)
                    or not self.w.IsWindowEnabled(h) or not self.b._is_descendant_or_same(h,self.foco(root))):
                raise CapturaFacturaError('El foco no está en el campo esperado. No se envían más teclas.')
    def control(self,root,pt,campo,tipo='edit',lectura=False):
        self._guard(root)
        r=self.b._rect(root);x=r.left+int(r.width*pt[0]);y=r.top+int(r.height*pt[1])
        under=self.w.WindowFromPoint((x,y))
        if not under or not self.b._is_descendant_or_same(root,under):
            raise CapturaFacturaError('Una ventana cubre el campo '+campo+'. No se hará clic.')
        def valid(h):
            cls=self.b._class_name(h).upper()
            return ('COMBO' in cls if tipo=='combo' else ('EDIT' in cls and 'COMBO' not in cls))
        candidates=[]
        def cb(h,_):
            if (self.b._window_exists_visible(h) and self.b._is_descendant_or_same(root,h)
                    and self.b._same_polaris_process(root,h) and valid(h)):
                q=self.b._rect(h)
                if q.left<=x<q.right and q.top<=y<q.bottom:candidates.append(h)
        self.w.EnumChildWindows(root,cb,None)
        # Anidar un Edit dentro de un Combo no autoriza editarlo como texto.
        if tipo=='edit':
            cur=under
            while cur and cur!=root:
                if 'COMBO' in self.b._class_name(cur).upper():
                    raise CapturaFacturaError('El campo '+campo+' es un selector, no una entrada de texto.')
                cur=self.w.GetParent(cur)
        unique=list(dict.fromkeys(candidates))

        # DevExpress suele exponer UN mismo campo como dos HWND anidados
        # (TcxCustomTextEdit -> TcxCustomInnerTextEdit). Eso no es ambigüedad.
        # En cambio, dos Edit hermanos que se superponen SÍ siguen siendo
        # ambiguos y detienen la captura.
        h=None
        if under in unique and valid(under):
            # Sólo aceptar WindowFromPoint si todos los demás candidatos son
            # wrappers/ancestros del control interior real.
            if all(cand==under or self.b._is_descendant_or_same(cand,under)
                   for cand in unique):
                h=under
        if h is None and unique:
            # Buscar un único candidato que sea descendiente de todos los demás.
            # Si existe, es el control interior real. Dos hermanos no cumplen esto.
            innermost=[cand for cand in unique
                       if all(other==cand or self.b._is_descendant_or_same(other,cand)
                              for other in unique)]
            if len(innermost)==1:
                h=innermost[0]

        if h is None:
            self._evento(campo,'CONTROL_NO_IDENTIFICADO',clase=self.b._class_name(under),coincidencias=len(unique))
            raise CapturaFacturaError('No pude identificar un único control de '+campo+'. Revisa el diagnóstico; no se escribe a ciegas.')
        if len(unique)>1:
            self._evento(campo,'CONTROL_ANIDADO_RESUELTO',clase=self.b._class_name(h),coincidencias=len(unique))
        if not lectura and not self.w.IsWindowEnabled(h):raise CapturaFacturaError('El campo '+campo+' está deshabilitado.')
        self._evento(campo,'CONTROL_IDENTIFICADO',clase=self.b._class_name(h))
        return h
    def enfocar(self,root,h):
        from . import polaris
        self._guard(root);r=self.b._rect(h);x=(r.left+r.right)//2;y=(r.top+r.bottom)//2
        under=self.w.WindowFromPoint((x,y))
        if not self.b._is_descendant_or_same(h,under):raise CapturaFacturaError('Otro control cubre la entrada. No se escribió.')
        polaris.pyautogui.click(x,y);self.dormir(.2);self._guard(root,h)
    def salir(self,root,h,campo):
        # Sólo Tab si aún estamos en el campo. Enter jamás valida texto libre.
        self._guard(root)
        if self.b._is_descendant_or_same(h,self.foco(root)):
            self.teclado.tecla('tab',lambda:self._guard(root,h))
        end=self.reloj()+2
        while self.reloj()<end:
            self._guard(root);focus=self.foco(root)
            if (focus and not self.b._is_descendant_or_same(h,focus)
                    and self.b._is_descendant_or_same(root,focus)):
                self.dormir(.2);self._guard(root)
                self._evento(campo,'SALIDA_DE_CAMPO_CONFIRMADA');return
            self.dormir(.12)
        raise CapturaFacturaError('Polaris no dejó salir del campo '+campo+'. Revisa la validación; no se continúa.')
    def confirmar(self,root,h,campo,expected):
        end=self.reloj()+2.4;stable=0
        while self.reloj()<end:
            self._guard(root)
            try:value=self.texto.leer(h);match=coincide(campo,value,expected)
            except Exception:match=False
            stable=stable+1 if match else 0
            if stable>=2:
                self._evento(campo,'LECTURA_CONFIRMADA',coincide=True);return
            self.dormir(.15)
        # WM_COPY dirigido al Edit después de abandonar el campo: NO se
        # recupera foco ni se copia el portapapeles que dejó el bot.
        try:
            value=self.texto.copiar(h,lambda:self._guard(root))
            match=coincide(campo,value,expected)
        except Exception:match=False
        self._evento(campo,'COPIA_NUEVA_POST_SALIDA',coincide=match)
        if not match:raise CapturaFacturaError('No se confirmó '+campo+' después de salir del campo. No se continúa a Aceptar.')
    def escribir(self,root,h,campo,value):
        value=str(value or '')
        if not value.strip():
            self._evento(campo,'VALOR_VACIO_BLOQUEADO',vacio=True,longitud=0)
            raise CapturaFacturaError('El valor de '+campo+' está vacío. No se borra ni se continúa.')
        self.enfocar(root,h);guard=lambda:self._guard(root,h)
        self._evento(campo,'ANTES_DE_ESCRIBIR',vacio=False,longitud=len(value),clase=self.b._class_name(h))
        # Se comprueba representación de TODO el texto antes de borrar.
        self.teclado.validar_texto(h,value);guard();self.texto._seleccionar(h)
        self.teclado.tecla('backspace',guard)
        self.teclado.escribir(h,value,guard)
        self._evento(campo,'TECLADO_ENVIADO',longitud=len(value))
        self.salir(root,h,campo)
        self.confirmar(root,h,campo,value)
        self._evento(campo,'CAMPO_CONFIRMADO_NO_VACIO',vacio=False,longitud=len(value))
    def escribir_rel(self,root,pt,campo,value):
        h=self.control(root,pt,campo);self.escribir(root,h,campo,value);return h
    def seleccionar(self,root,pt,campo,target):
        h=self.control(root,pt,campo,tipo='combo')
        items=self.combo.opciones(h);matches=[i for i,v in enumerate(items) if canon(v)==canon(target)]
        if len(matches)!=1:
            self._evento(campo,'OPCION_NO_UNICA',opciones=len(items))
            raise CapturaFacturaError('La opción solicitada no se encontró de forma única en el catálogo de '+campo+'.')
        idx=matches[0]
        self.enfocar(root,h)
        # Clic en flecha dentro del Combo, no en un Edit vecino.
        from . import polaris
        r=self.b._rect(h);x=r.right-8;y=(r.top+r.bottom)//2
        if not self.b._is_descendant_or_same(h,self.w.WindowFromPoint((x,y))):
            raise CapturaFacturaError('No se localizó la flecha de '+campo+'.')
        if not self.combo.abierto(h):polaris.pyautogui.click(x,y);self.dormir(.2)
        if not self.combo.abierto(h):raise CapturaFacturaError('No se abrió la lista de '+campo+'. No se pulsa Enter.')
        guard=lambda:self._guard_combo(root,h)
        # Selección EXPLÍCITA aunque la etiqueta inicial muestre G03/Efectivo.
        self.teclado.tecla('home',guard)
        if idx==0 and len(items)>1:
            self.teclado.tecla('down',guard);self.teclado.tecla('up',guard)
        else:
            for _ in range(idx):self.teclado.tecla('down',guard)
        actual,value=self.combo.actual(h)
        if actual!=idx or canon(value)!=canon(target):
            raise CapturaFacturaError('El selector '+campo+' no quedó en la opción pedida. No se pulsa Enter.')
        # Enter se permite SÓLO con esa lista comprobada abierta y enfocada.
        if not self.combo.abierto(h):raise CapturaFacturaError('La lista se cerró antes de confirmar. No se pulsa Enter.')
        self.teclado.tecla('enter',guard)
        self.dormir(.3)
        if self.combo.abierto(h):raise CapturaFacturaError('La lista '+campo+' no confirmó el cierre.')
        self.salir(root,h,campo)
        self.validar_combo(root,h,campo,target)
        return items[idx]
    def _guard_combo(self,root,h):
        self._guard(root)
        focus=self.foco(root)
        # Win32 ComboLBox es popup de lista, no un campo de otro formulario.
        owned=False
        if focus and self.b._same_polaris_process(root,focus) and self.b._class_name(focus).upper()=='COMBOLBOX':
            # La lista sólo se admite si pertenece al combo o su parent/owner.
            parent=self.w.GetParent(focus)
            owner=self.w.GetWindow(focus,4)
            owned=parent==h or owner==h
        if not (self.b._is_descendant_or_same(h,focus) or owned) or not self.combo.abierto(h):
            raise CapturaFacturaError('El foco salió del selector. No se envían flechas ni Enter.')
    def validar_combo(self,root,h,campo,target):
        for _ in range(2):
            self._guard(root);_,actual=self.combo.actual(h)
            if canon(actual)!=canon(target):raise CapturaFacturaError('Polaris no conserva la selección de '+campo+'.')
            self.dormir(.15)
        self._evento(campo,'SELECCION_CONFIRMADA',coincide=True)
    def verificar_receptor(self,inv,rfc):
        """Sólo lectura de campos que Polaris llena. No los 'repara'."""
        handles={k:self.control(inv,pt,k,lectura=True) for k,pt in self.RECEPTOR.items()}
        end=self.reloj()+6;bad=[];stable=0
        while self.reloj()<end:
            self._guard(inv);values={}
            for name,h in handles.items():
                try:values[name]=self.texto.leer(h).strip()
                except Exception:values[name]=''
            ok={'cliente':bool(re.fullmatch(r'[0-9]+',values['cliente']) and int(values['cliente'])>0),
                'rfc':coincide('rfc',values['rfc'],rfc),'nombre':bool(values['nombre']),
                'cp':bool(re.fullmatch(r'[0-9]{5}',values['cp'])),
                'regimen':bool(re.match(r'[0-9]{3}\b',values['regimen']))}
            bad=[k for k,v in ok.items() if not v];stable=stable+1 if not bad else 0
            if stable>=2:
                self._evento('receptor','CAMPOS_CARGADOS_CONFIRMADOS',**ok);return
            self.dormir(.25)
        self._evento('receptor','CARGA_NO_CONFIRMADA',campos=bad)
        raise CapturaFacturaError('Polaris no confirmó los datos del cliente ('+', '.join(bad)+'). No se timbra ni se rellenan a ciegas.')
