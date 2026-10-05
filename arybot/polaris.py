from __future__ import annotations
import os, re, subprocess, time, threading, unicodedata, ctypes, calendar
from dataclasses import dataclass
from pathlib import Path

from .parser import STATIONS, normalize_station
from .diagnostico import Diagnostico, diagnosticar_operacion


class PolarisError(RuntimeError): pass


@dataclass
class Rect:
    left: int; top: int; right: int; bottom: int
    @property
    def width(self): return max(1, self.right-self.left)
    @property
    def height(self): return max(1, self.bottom-self.top)


class PolarisBot:
    """Automatiza el flujo observado en Factura.paso.paso.mp4.

    Flujo real consolidado de los videos del usuario:
      login -> seleccionar/cambiar estación -> Facturación -> Efectivo -> buscar cliente por RFC -> seleccionar
      -> Polaris llena Nombre/RFC/CP/régimen -> Forma de pago
      -> si es tarjeta de crédito/débito, CUENTA se deja VACÍA (no es necesaria)
      -> Uso CFDI -> Fecha del ticket -> Folio del ticket -> Agregar Folio
      -> Aceptar final -> Envío e Impresión de CFDI -> Enviar por correo.

    En MODO SEGURO se detiene DESPUÉS de Agregar Folio y ANTES del Aceptar final/timbrar.
    """
    LOGIN = {"usuario":(0.78,0.27), "password":(0.78,0.45), "aceptar":(0.67,0.90)}
    FACTURA = {
        "buscar_cliente":(0.198,0.120),
        # Nombre/RFC/CP/Régimen NO se tocan: Polaris los llena al elegir cliente.
        "forma_pago":(0.340,0.263),
        "forma_pago_flecha":(0.439,0.263),
        "uso_cfdi":(0.486,0.316),
        "uso_cfdi_flecha":(0.635,0.316),
        # Fecha: el texto y la flecha son zonas distintas del TcxDateEdit.
        # Se usa la flecha directamente para evitar la ambigüedad de HWND anidados de DevExpress.
        "fecha":(0.735,0.235),
        "fecha_flecha":(0.779,0.241),
        "ticket":(0.092,0.610),
        "agregar_folio":(0.302,0.610),
        "aceptar":(0.716,0.932)
    }
    BUSCAR = {
        # Coordenadas relativas verificadas contra el video del usuario.
        # Polaris abre el catálogo buscando por NÚMERO. En v2.5 buscamos por RFC:
        # primero se hace CLICK REAL en el encabezado "R.F.C." de la tabla.
        # Ese clic cambia el criterio superior de "Número" a "R.F.C.".
        # Coordenada verificada sobre el mismo diálogo donde ya funcionaba Nombre.
        "columna_rfc":(0.635,0.188),
        "campo_busqueda":(0.410,0.112),
        "buscar":(0.682,0.112),
        "primera_fila":(0.450,0.232),
        "aceptar":(0.766,0.890)
    }
    ENVIO = {"correo_radio":(0.100,0.322), "correo":(0.475,0.590), "aceptar":(0.800,0.950)}
    # Utilerías -> Cambio de estación (video 25/09/2026).
    # v2.8: ya NO navegamos con HOME+5 DOWN. En el video real eso cayó en
    # "Respaldar". Se hace CLICK visible y directo sobre "Cambio de estación".
    UTIL_MENU=(0.241,0.038)
    UTIL_CAMBIO_ESTACION=(0.304,0.210)
    ESTACION_DLG={
        "campo_numero":(0.475,0.125),
        "buscar":(0.805,0.125),
        "primera_fila":(0.500,0.250),
        "aceptar":(0.675,0.895),
    }
    SERIES_DLG={"aceptar":(0.862,0.960)}
    # Posición visual de cada estación en el catálogo "Seleccione el registro deseado".
    # Se tomó del video del usuario: en esta ventana NO hace falta escribir el número;
    # se selecciona directamente la fila y después Aceptar.
    STATION_ROW_Y={
        "ARY II":0.292,
        "ARY III":0.337,
        "ARY IV":0.381,
        "SERVICIO APACHE":0.425,
        "ARY I":0.469,
        "ARY V":0.513,
        "ARY VI":0.557,
    }

    def __init__(self, base: Path, cfg: dict, log):
        self.base=base; self.cfg=cfg; self.pcfg=cfg["polaris"]; self.log=log
        self.diag=Diagnostico(base,log)
        self._lock = threading.Lock()
        self._pasos_factura={}
        self._imports()

    def _imports(self):
        if os.name != "nt":
            raise PolarisError("Polaris solo puede automatizarse desde Windows.")
        global pyautogui, win32gui, win32process, win32con, win32clipboard, keyring, Desktop
        import pyautogui as _pa; pyautogui=_pa
        import win32gui as _wg; win32gui=_wg
        import win32process as _wp; win32process=_wp
        import win32con as _wc; win32con=_wc
        import win32clipboard as _wcb; win32clipboard=_wcb
        import keyring as _kr; keyring=_kr
        from pywinauto import Desktop as _Desktop; Desktop=_Desktop
        pyautogui.FAILSAFE=True; pyautogui.PAUSE=0.16

    @staticmethod
    def _norm(s):
        s=unicodedata.normalize("NFKD", s or "")
        return re.sub(r"[^A-Z0-9&Ñ]", "", "".join(c for c in s if not unicodedata.combining(c)).upper())

    def save_password(self, password: str):
        if not password: raise PolarisError("La contraseña está vacía.")
        keyring.set_password(self.pcfg["servicio_credencial_windows"], self.pcfg["usuario"], password)

    def has_password(self):
        return bool(keyring.get_password(self.pcfg["servicio_credencial_windows"], self.pcfg["usuario"]))

    def _password(self):
        p=keyring.get_password(self.pcfg["servicio_credencial_windows"], self.pcfg["usuario"])
        if not p: raise PolarisError("No hay contraseña de Polaris guardada. Guárdala en Configuración.")
        return p

    def _enum(self):
        out=[]
        def cb(h,_):
            try:
                if win32gui.IsWindowVisible(h): out.append(h)
            except: pass
        win32gui.EnumWindows(cb,None)
        return out

    def _find_title(self, regex):
        """Busca por título y, para la principal, prefiere la ventana visual TForm_Menu."""
        rx=re.compile(regex,re.I)
        principal=(regex == self.pcfg.get("ventana_principal_regex"))
        matches=[]
        for h in self._enum():
            try:
                title=win32gui.GetWindowText(h) or ""
                if not rx.search(title):
                    continue
                if not principal:
                    return h
                r=self._rect(h)
                cls=(win32gui.GetClassName(h) or "").upper()
                visual=bool(win32gui.IsWindowVisible(h) and r.width>=800 and r.height>=500)
                preferred=(cls=="TFORM_MENU")
                matches.append((int(visual),int(preferred),r.width*r.height,h))
            except Exception:
                pass
        if not matches:
            return None
        matches.sort(reverse=True)
        return matches[0][3]

    def _find_window_any_level(self, regex, *, min_width=0, min_height=0):
        """Busca una ventana por título incluyendo controles/MDI hijos.

        Polaris usa una ventana MDI para ``Facturación de Efectivo``. Esa ventana
        NO siempre aparece en EnumWindows(), por lo que buscar solo ventanas de
        nivel superior hacía que el bot terminara usando un panel interno como
        referencia. Todas las coordenadas relativas quedaban desplazadas.

        Esta función recorre cada ventana superior y todos sus descendientes,
        y devuelve el candidato visible más grande que coincida con el título.
        """
        rx=re.compile(regex,re.I)
        found=[]

        def consider(h):
            try:
                if not win32gui.IsWindowVisible(h):
                    return
                title=(win32gui.GetWindowText(h) or "").strip()
                if not title or not rx.search(title):
                    return
                r=self._rect(h)
                if r.width < min_width or r.height < min_height:
                    return
                found.append((r.width*r.height, h, title, r))
            except Exception:
                pass

        def child_cb(h,_):
            consider(h)

        for top in self._enum():
            consider(top)
            try:
                win32gui.EnumChildWindows(top, child_cb, None)
            except Exception:
                pass

        if not found:
            return None
        # La ventana MDI real de Facturación es el candidato grande con ese título.
        found.sort(key=lambda x:x[0], reverse=True)
        return found[0][1]

    def _is_descendant_or_same(self, parent, child):
        if not parent or not child:
            return False
        p=child
        seen=set()
        while p and p not in seen:
            if p == parent:
                return True
            seen.add(p)
            try:
                p=win32gui.GetParent(p)
            except Exception:
                break
        return False

    def _assert_point_inside_window(self, h, pt, label):
        """Evita mandar teclas a Polaris si una coordenada cayó fuera del MDI esperado."""
        r=self._rect(h)
        x=r.left+int(r.width*pt[0]); y=r.top+int(r.height*pt[1])
        try:
            under=win32gui.WindowFromPoint((x,y))
        except Exception:
            under=None
        if under and not self._is_descendant_or_same(h, under):
            self._screenshot_error(f"punto_{label}")
            raise PolarisError(
                f"Seguridad: el punto '{label}' cayó fuera de la ventana de Facturación "
                f"(rect={r.left},{r.top},{r.right},{r.bottom}). No se enviaron teclas."
            )
        return x,y

    def _rect(self,h):
        l,t,r,b=win32gui.GetWindowRect(h); return Rect(l,t,r,b)

    def _activate(self,h, maximize=False):
        if not h: return
        try:
            if win32gui.IsIconic(h): win32gui.ShowWindow(h,win32con.SW_RESTORE)
            if maximize: win32gui.ShowWindow(h,win32con.SW_MAXIMIZE)
            try: win32gui.SetForegroundWindow(h)
            except: pass
        finally: time.sleep(.35)

    def _click_rel(self,h,pt,wait=.25):
        r=self._rect(h); x=r.left+int(r.width*pt[0]); y=r.top+int(r.height*pt[1]); pyautogui.click(x,y); time.sleep(wait); return x,y

    def _paste(self,text,clear=True):
        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard(); win32clipboard.SetClipboardText(str(text),win32con.CF_UNICODETEXT)
        finally: win32clipboard.CloseClipboard()
        if clear: pyautogui.hotkey("ctrl","a")
        pyautogui.hotkey("ctrl","v"); time.sleep(.18)

    def _copy_current(self):
        pyautogui.hotkey("ctrl","a"); pyautogui.hotkey("ctrl","c"); time.sleep(.2)
        try:
            win32clipboard.OpenClipboard()
            try: return win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT) if win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT) else ""
            finally: win32clipboard.CloseClipboard()
        except: return ""

    def _wait_title(self,regex,timeout=15):
        end=time.time()+timeout
        while time.time()<end:
            h=self._find_title(regex)
            if h: return h
            time.sleep(.2)
        return None

    def _find_parent_containing_text(self, needle: str):
        target=(needle or "").lower()
        matches=[]
        # Incluye ventanas y controles hijos; el diálogo de búsqueda de Polaris es VCL.
        def child_cb(h,_):
            try:
                if target in (win32gui.GetWindowText(h) or "").lower(): matches.append(h)
            except: pass
        for top in self._enum():
            try:
                if target in (win32gui.GetWindowText(top) or "").lower(): matches.append(top)
                win32gui.EnumChildWindows(top, child_cb, None)
            except: pass
        candidates=[]
        for h in matches:
            p=h
            seen=set()
            while p and p not in seen:
                seen.add(p)
                try:
                    r=self._rect(p); area=r.width*r.height
                    if 180000 <= area <= 900000 and r.width>450 and r.height>250 and win32gui.IsWindowVisible(p): candidates.append((area,p))
                    p=win32gui.GetParent(p)
                except: break
        return min(candidates)[1] if candidates else None

    def _wait_text_dialog(self,text,timeout=12):
        end=time.time()+timeout
        while time.time()<end:
            h=self._find_parent_containing_text(text)
            if h: return h
            time.sleep(.2)
        return None

    def _screenshot_error(self,name="error"):
        folder=self.base/"capturas_error"; folder.mkdir(exist_ok=True)
        p=folder/f"{time.strftime('%Y%m%d_%H%M%S')}_{name}.png"
        try: pyautogui.screenshot(str(p))
        except: pass
        return p

    def _pid_window(self, h):
        try:
            return int(win32process.GetWindowThreadProcessId(h)[1])
        except Exception:
            return 0

    @staticmethod
    def _session_id_pid(pid):
        if not pid:
            return None
        sid=ctypes.c_uint(0)
        try:
            ok=ctypes.windll.kernel32.ProcessIdToSessionId(int(pid), ctypes.byref(sid))
            return int(sid.value) if ok else None
        except Exception:
            return None

    @staticmethod
    def _token_elevated(pid=None):
        """True/False si puede consultarse; None si Windows no permite leerlo."""
        from ctypes import wintypes
        PROCESS_QUERY_LIMITED_INFORMATION=0x1000
        TOKEN_QUERY=0x0008
        TokenElevation=20
        k=ctypes.windll.kernel32; a=ctypes.windll.advapi32
        proc=None; close_proc=False
        try:
            if pid is None:
                proc=k.GetCurrentProcess()
            else:
                k.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
                k.OpenProcess.restype=wintypes.HANDLE
                proc=k.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION,False,int(pid))
                if not proc:
                    return None
                close_proc=True
            token=wintypes.HANDLE()
            if not a.OpenProcessToken(proc,TOKEN_QUERY,ctypes.byref(token)):
                return None
            try:
                class TOKEN_ELEVATION(ctypes.Structure):
                    _fields_=[('TokenIsElevated',wintypes.DWORD)]
                elev=TOKEN_ELEVATION(); ret=wintypes.DWORD()
                ok=a.GetTokenInformation(token,TokenElevation,ctypes.byref(elev),ctypes.sizeof(elev),ctypes.byref(ret))
                return bool(elev.TokenIsElevated) if ok else None
            finally:
                k.CloseHandle(token)
        except Exception:
            return None
        finally:
            if close_proc and proc:
                try:k.CloseHandle(proc)
                except Exception:pass

    def _validar_misma_sesion_windows(self, h):
        """Evita automatizar Polaris de otra sesión/elevación de Windows."""
        pid=self._pid_window(h)
        bot_sid=self._session_id_pid(os.getpid())
        pol_sid=self._session_id_pid(pid)
        if bot_sid is not None and pol_sid is not None and bot_sid != pol_sid:
            raise PolarisError(
                f"Polaris está en otra sesión de Windows (bot={bot_sid}, Polaris={pol_sid}). "
                "Abre bot y Polaris en el mismo escritorio de usuario."
            )
        bot_admin=self._token_elevated()
        pol_admin=self._token_elevated(pid)
        if bot_admin is not None and pol_admin is not None and bot_admin != pol_admin:
            raise PolarisError(
                "Bot y Polaris tienen distinto nivel de permisos de Windows. "
                "Ábrelos ambos normal o ambos como administrador; no mezcles niveles."
            )
        self.log(
            "Sesión Windows verificada: bot y Polaris comparten escritorio"
            + (f"; administrador={bot_admin}." if bot_admin is not None else ".")
        )
        return True

    def _esperar_principal_estable(self, timeout=30.0, stable_seconds=2.0):
        """Espera una ventana principal grande, visible, habilitada y estable."""
        end=time.monotonic()+float(timeout)
        stable_from=None; last=None; main=None
        while time.monotonic()<end:
            pyautogui.failSafeCheck()
            main=self._find_title(self.pcfg["ventana_principal_regex"])
            if not main:
                stable_from=None; last=None; time.sleep(.20); continue
            try:
                r=self._rect(main); title=(win32gui.GetWindowText(main) or '').strip()
                good=(win32gui.IsWindowVisible(main) and win32gui.IsWindowEnabled(main)
                      and r.width>=800 and r.height>=500)
                snap=(title,r.left,r.top,r.right,r.bottom)
            except Exception:
                stable_from=None; last=None; time.sleep(.20); continue
            if not good:
                stable_from=None; last=snap; time.sleep(.20); continue
            if snap != last:
                stable_from=time.monotonic(); last=snap
            elif stable_from is None:
                stable_from=time.monotonic()
            if stable_from is not None and time.monotonic()-stable_from>=stable_seconds:
                self._validar_misma_sesion_windows(main)
                self.log(f"Polaris principal estable: {r.width}x{r.height}; título='{title}'.")
                return main
            time.sleep(.20)
        self._screenshot_error('inicio_polaris_no_estable')
        raise PolarisError("Polaris abrió, pero su ventana principal no quedó estable/lista a tiempo.")

    def _esperar_login_o_principal_listo(self, timeout=90.0, stable_seconds=2.0):
        """Espera el arranque REAL de Polaris sin confundir splash/principal temprana.

        Algunas compilaciones crean primero una ventana con título de Polaris que ya
        coincide con ``ventana_principal_regex`` pero todavía es pequeña, está
        deshabilitada o será sustituida por ``Entrada al Sistema``. Antes se tomaba
        esa coincidencia como "Polaris abrió" y después el bot esperaba solamente la
        principal hasta agotar el tiempo.

        Aquí se observa durante todo el arranque:
        - si aparece Entrada al Sistema, se devuelve para hacer login;
        - si la principal queda grande, visible, habilitada y estable, se devuelve;
        - una splash o principal transitoria nunca se automatiza.
        """
        end=time.monotonic()+float(timeout)
        stable_from=None
        last=None
        last_note=0.0
        while time.monotonic()<end:
            pyautogui.failSafeCheck()

            login=self._find_title(self.pcfg["ventana_login_regex"])
            if login and self._window_exists_visible(login):
                self._validar_misma_sesion_windows(login)
                self.log("Entrada al Sistema detectada durante el arranque; se continuará con el login.")
                return "login", login

            main=self._find_title(self.pcfg["ventana_principal_regex"])
            if main:
                try:
                    r=self._rect(main)
                    title=(win32gui.GetWindowText(main) or '').strip()
                    good=(win32gui.IsWindowVisible(main) and win32gui.IsWindowEnabled(main)
                          and r.width>=800 and r.height>=500)
                    snap=(title,r.left,r.top,r.right,r.bottom)
                except Exception:
                    good=False
                    snap=None
                if good:
                    if snap != last:
                        stable_from=time.monotonic()
                        last=snap
                    elif stable_from is None:
                        stable_from=time.monotonic()
                    if stable_from is not None and time.monotonic()-stable_from>=stable_seconds:
                        self._validar_misma_sesion_windows(main)
                        self.log(f"Polaris principal listo durante arranque: {r.width}x{r.height}; título='{title}'.")
                        return "principal", main
                else:
                    stable_from=None
                    last=snap
                    now=time.monotonic()
                    if now-last_note>=8.0:
                        self.log("Polaris todavía está cargando; se espera una principal estable o Entrada al Sistema.")
                        last_note=now
            else:
                stable_from=None
                last=None
            time.sleep(.25)

        self._screenshot_error("inicio_polaris_login_o_principal")
        raise PolarisError(
            "Polaris abrió, pero no llegó a Entrada al Sistema ni a una ventana principal "
            "estable dentro del tiempo de arranque."
        )

    def _activar_como_usuario(self, h, maximize=True):
        """Restaura y lleva Polaris al frente; confirma por PID antes de continuar."""
        if not h:
            raise PolarisError('No hay ventana de Polaris para activar.')
        self._validar_misma_sesion_windows(h)
        try:
            if win32gui.IsIconic(h):
                win32gui.ShowWindow(h,win32con.SW_RESTORE)
            if maximize:
                win32gui.ShowWindow(h,win32con.SW_MAXIMIZE)
            try: win32gui.BringWindowToTop(h)
            except Exception: pass
            try: win32gui.SetForegroundWindow(h)
            except Exception: pass
        finally:
            time.sleep(.55)
        target_pid=self._pid_window(h)
        fg=win32gui.GetForegroundWindow()
        fg_pid=self._pid_window(fg)
        if target_pid and fg_pid != target_pid:
            # Un clic en la barra superior imita la selección manual de la aplicación.
            r=self._rect(h)
            pyautogui.click(r.left+min(max(180,r.width//3),max(20,r.width-30)), r.top+12)
            time.sleep(.45)
            fg=win32gui.GetForegroundWindow(); fg_pid=self._pid_window(fg)
        if target_pid and fg_pid != target_pid:
            self._screenshot_error('polaris_no_primer_plano')
            raise PolarisError(
                "Polaris no quedó en primer plano. No se enviarán clics/teclas. "
                "Revisa permisos de Windows o una ventana que esté robando el foco."
            )
        return h

    def _open(self):
        main=self._find_title(self.pcfg["ventana_principal_regex"])
        login=self._find_title(self.pcfg["ventana_login_regex"])
        if main:
            self.log("Polaris ya estaba abierto: usando EXACTAMENTE esa sesión; no se abre otra instancia.")
            self._validar_misma_sesion_windows(main)
            return 'existente'
        if login:
            self.log("Polaris ya estaba abierto en Entrada al Sistema: usando esa misma instancia.")
            self._validar_misma_sesion_windows(login)
            return 'login_existente'
        exe=(self.pcfg.get("executable") or "").strip()
        if not exe or not Path(exe).exists():
            raise PolarisError("Configura la ruta real de Polaris.exe en la pestaña Configuración.")
        # os.startfile usa el Shell de Windows: equivale mucho más al doble clic del usuario
        # que crear el proceso directamente con subprocess.Popen. Hereda usuario y escritorio.
        self.log("Polaris no está abierto. Iniciándolo mediante Windows Shell (modo usuario)...")
        try:
            os.startfile(exe, 'open', cwd=str(Path(exe).parent), show_cmd=1)
        except TypeError:
            # Compatibilidad con Python/Windows donde startfile no exponga cwd/show_cmd.
            old=os.getcwd()
            try:
                os.chdir(str(Path(exe).parent)); os.startfile(exe)
            finally:
                os.chdir(old)
        except Exception as exc:
            raise PolarisError(f"Windows no pudo abrir Polaris como aplicación de usuario: {exc}") from exc
        self.log("Esperando Entrada al Sistema o ventana principal; no se automatiza durante la carga...")
        end=time.monotonic()+90.0
        while time.monotonic()<end:
            login=self._find_title(self.pcfg["ventana_login_regex"])
            main=self._find_title(self.pcfg["ventana_principal_regex"])
            if login:
                self._validar_misma_sesion_windows(login); return 'shell_login'
            if main:
                self._validar_misma_sesion_windows(main); return 'shell_principal'
            time.sleep(.20)
        self._screenshot_error('polaris_no_abre_shell')
        raise PolarisError("Polaris no mostró Entrada al Sistema ni ventana principal después del arranque por Windows Shell.")

    def _login(self):
        login=self._find_title(self.pcfg["ventana_login_regex"])
        if not login: return
        self.log("Iniciando sesión en Polaris como usuario interactivo...")
        self._activar_como_usuario(login,maximize=False)
        # Login también se teclea como persona; sin Ctrl+V.
        self._click_rel(login,self.LOGIN["usuario"],wait=.20)
        pyautogui.hotkey('ctrl','a'); pyautogui.write(str(self.pcfg["usuario"]),interval=.10)
        pyautogui.press('tab'); time.sleep(.20)
        self._click_rel(login,self.LOGIN["password"],wait=.20)
        pyautogui.hotkey('ctrl','a'); pyautogui.write(str(self._password()),interval=.10)
        pyautogui.press('tab'); time.sleep(.20)
        self._click_rel(login,self.LOGIN["aceptar"],wait=.25)
        try:
            self._esperar_principal_estable(timeout=60.0,stable_seconds=1.8)
        except Exception:
            self._screenshot_error("login"); raise

    def ensure_ready(self):
        source=self._open()

        # No asumir que la primera ventana con título "Polaris Facturacion" ya es
        # operable. Con los EXE actuales puede aparecer antes que el login o mientras
        # VCL todavía está armando la principal.
        state, handle=self._esperar_login_o_principal_listo(timeout=90.0,stable_seconds=1.8)

        if state == "login":
            self._login()
            main=self._esperar_principal_estable(timeout=60.0,stable_seconds=1.8)
        else:
            main=handle

        self._activar_como_usuario(main,maximize=True)

        # Una última estabilidad después de maximizar evita tocar controles mientras
        # VCL recalcula posiciones/tamaños.
        main=self._esperar_principal_estable(timeout=25.0,stable_seconds=1.2)
        self.log(
            "Polaris listo para automatización. Origen="+str(source)+
            ". Arranque validado sin automatizar splash/ventanas transitorias."
        )
        return main

    def _station_title_matches(self, title: str, station: str) -> bool:
        key=normalize_station(station)
        if not key or key not in STATIONS:
            return False
        want=self._norm(STATIONS[key]["polaris"])
        got=self._norm(title or "")
        return bool(want and want in got)

    def _window_tree_text(self, h):
        """Devuelve título + textos hijos. Sirve para leer barra de estado/modal VCL."""
        parts=[]
        try:
            parts.append(win32gui.GetWindowText(h) or "")
        except Exception:
            pass
        def cb(ch,_):
            try:
                txt=win32gui.GetWindowText(ch) or ""
                if txt:
                    parts.append(txt)
            except Exception:
                pass
        try:
            win32gui.EnumChildWindows(h,cb,None)
        except Exception:
            pass
        return " | ".join(parts)

    def _station_is_active(self, main, station):
        """Confirma estación por título y, como respaldo, por 'ES <número>'."""
        key=normalize_station(station)
        if not key or key not in STATIONS:
            return False
        title=win32gui.GetWindowText(main) or ""
        if self._station_title_matches(title,key):
            return True
        info=STATIONS[key]
        all_text=self._window_tree_text(main)
        return bool(re.search(rf"\bES\s*{re.escape(info['numero'])}\b", all_text, re.I))

    def _pid(self, h):
        # GetWindowThreadProcessId pertenece a win32process, NO a win32gui.
        # En v3.1 la excepción se ocultaba y la limpieza no encontraba ventanas.
        try:
            return win32process.GetWindowThreadProcessId(h)[1] or None
        except Exception:
            return None

    def _class_name(self, h):
        try:
            return win32gui.GetClassName(h) or ""
        except Exception:
            return ""

    def _window_exists_visible(self, h):
        try:
            return bool(h and win32gui.IsWindow(h) and win32gui.IsWindowVisible(h))
        except Exception:
            return False

    def _same_polaris_process(self, main, h):
        pid=self._pid(main)
        return bool(pid and h and self._pid(h)==pid)

    def _cleanup_chain(self, h):
        """Ancestros/propietarios; incluye modales VCL que no son hijos MDI."""
        seen=set()
        while h and h not in seen and len(seen)<40:
            seen.add(h)
            yield h
            try:
                owner=win32gui.GetWindow(h, getattr(win32con,"GW_OWNER",4))
                h=owner or win32gui.GetParent(h)
            except Exception:
                break

    def _cleanup_protected(self, main, h):
        """No cerrar principal, sus propietarios, login ni contenedor MDI."""
        if not h or h==main or h in set(self._cleanup_chain(main)):
            return True
        title=win32gui.GetWindowText(h) or ""
        nt=self._norm(title)
        cls=self._class_name(h).upper()
        if cls in ("TAPPLICATION","MDICLIENT","TMDICLIENT"):
            return True
        if any(k in cls for k in ("LOGIN","LOGON","SIGNIN")):
            return True
        if any(k in nt for k in ("ENTRADAALSISTEMA","INICIARSESION","INICIODESESION")):
            return True
        try:
            rx=self.pcfg.get("ventana_login_regex")
            if rx and re.search(rx,title,re.I):
                return True
        except re.error:
            pass
        return False

    def _cleanup_main_ok(self, main):
        if not self._window_exists_visible(main) or not self._pid(main):
            raise PolarisError(
                "No pude identificar la ventana/proceso principal de Polaris. "
                "No se cerró ninguna ventana ni se intentó cambiar estación."
            )
        title=win32gui.GetWindowText(main) or ""
        login=self.pcfg.get("ventana_login_regex")
        try:
            is_login=bool(login and re.search(login,title,re.I))
        except re.error:
            is_login=False
        if (is_login or "ENTRADAALSISTEMA" in self._norm(title)
                or any(k in self._class_name(main).upper() for k in ("LOGIN","LOGON","SIGNIN"))):
            raise PolarisError("Polaris sigue en el inicio de sesión. No se cierra esa ventana.")

    def _cleanup_windows(self, main):
        """Instantánea limitada al proceso elegido; nunca enumera datos de BD."""
        self._cleanup_main_ok(main)
        pid=self._pid(main)
        found={}
        def add(h):
            if self._window_exists_visible(h) and self._pid(h)==pid:
                found[h]=None
        tops=self._enum()
        if main not in tops:
            tops.append(main)
        for top in tops:
            if self._pid(top)!=pid:
                continue
            add(top)
            win32gui.EnumChildWindows(top,lambda h,_: add(h) or True,None)
        return list(found)

    def _cleanup_form_kind(self, main, h):
        """Clasifica formularios, no sus botones, paneles o rejillas."""
        if not self._window_exists_visible(h) or self._cleanup_protected(main,h):
            return None
        if not self._same_polaris_process(main,h):
            return None
        title=(win32gui.GetWindowText(h) or "").strip()
        cls=self._class_name(h).upper()
        excluded=("BUTTON","BITBTN","SPEEDBUTTON","EDIT","STATIC","LABEL","PANEL",
                  "GROUP","COMBO","LISTBOX","LISTVIEW","TREEVIEW","STATUS",
                  "TOOLBAR","TAB","GRID","HEADER","SCROLL","MENU","TOOLTIP")
        r=self._rect(h)
        if r.width<120 or r.height<60:
            return None
        exstyle=win32gui.GetWindowLong(h,win32con.GWL_EXSTYLE)
        style=win32gui.GetWindowLong(h,win32con.GWL_STYLE)
        mdi=bool(exstyle & getattr(win32con,"WS_EX_MDICHILD",0x40))
        dialog=cls=="#32770"
        caption=bool(style & getattr(win32con,"WS_CAPTION",0xC00000))
        form=bool("FORM" in cls or "FRM" in cls)
        if any(k in cls for k in excluded) and not (mdi or dialog or (form and caption)):
            return None
        known=bool(re.search(
            r"^(?:Accesos al Sistema|Facturaci[oó]n de Efectivo|"
            r"Seleccione el cliente deseado|Seleccione el registro deseado|"
            r"Selecci[oó]n de Series de facturaci[oó]n|"
            r"Env[ií]o e Impresi[oó]n de CFDI)\s*$",title,re.I))
        info=bool(re.search(r"^(?:Error|Aviso|Advertencia|Informaci[oó]n|Mensaje)\b",title,re.I))
        if not (mdi or dialog or known or (form and caption) or (info and caption)):
            return None
        chain=set(self._cleanup_chain(h))
        connected=bool(main in chain or (chain & set(self._cleanup_chain(main))))
        # Los formularios independientes de propietario desconocido no se cierran
        # solo por compartir proceso. Se reportan como bloqueo para revisión.
        safe=bool(connected or known or info)
        return (safe,title or "(sin título)",cls,r,mdi,dialog)

    def _popup_candidates(self, main):
        """MDI y modales conocidos del proceso, con los modales superiores primero."""
        found=[]
        foreground=win32gui.GetForegroundWindow()
        for h in self._cleanup_windows(main):
            try:
                item=self._cleanup_form_kind(main,h)
                if not item or not item[0]:
                    continue
                _,title,cls,r,mdi,dialog=item
                depth=len(list(self._cleanup_chain(h)))
                # Una ventana superior modal puede deshabilitar a la principal;
                # se atiende antes de pedir el cierre de un MDI que esté debajo.
                priority=(int(h==foreground),int(dialog or not mdi),depth,-r.width*r.height)
                found.append((priority,(depth,r.width*r.height,h,title,cls,r)))
            except Exception:
                if self._window_exists_visible(h):
                    raise PolarisError("No pude inspeccionar una ventana de Polaris; no se cambió estación.")
        found.sort(key=lambda row:row[0],reverse=True)
        return [row for _,row in found]

    def _has_unsaved_prompt(self, main):
        """No responde Sí/No ni confirma guardados, descartes o cierre de sesión."""
        risky=("GUARDAR CAMBIOS","DESEA GUARDAR","GUARDAR LOS CAMBIOS",
               "CAMBIOS SIN GUARDAR","DESEA CONSERVAR","DESEA DESCARTAR",
               "DESCARTAR CAMBIOS","PERDERAN LOS CAMBIOS","PERDER LOS DATOS",
               "CAMBIOS NO GUARDADOS","MODIFICACIONES SIN GUARDAR",
               "GUARDAR MODIFICACIONES","DESEA ABANDONAR","DESEA SALIR","CERRAR SESION",
               "FINALIZAR SESION","ESTA SEGURO","SEGURO QUE",
               "DESEA CANCELAR","DESEA ELIMINAR","DESEA BORRAR",
               "DESEA TIMBRAR","DESEA FACTURAR","DESEA EMITIR")
        for h in self._cleanup_windows(main):
            try:
                item=self._cleanup_form_kind(main,h)
                if not item:
                    continue
                title=item[1]
                txt=self._norm(self._window_tree_text(h))
                if any(self._norm(term) in txt for term in risky):
                    return h
                if re.search(r"^(?:Confirmaci[oó]n|Confirmar|Confirm|Confirmation|Pregunta|Question)\b",title,re.I):
                    return h
                buttons=set()
                def button(ch,_):
                    if not self._window_exists_visible(ch):
                        return
                    cls=self._class_name(ch).upper()
                    if any(k in cls for k in ("BUTTON","BITBTN","SPEEDBUTTON")):
                        label=(win32gui.GetWindowText(ch) or "").replace("&","")
                        buttons.add(self._norm(label))
                win32gui.EnumChildWindows(h,button,None)
                if ("NO" in buttons and ("SI" in buttons or "YES" in buttons)) or buttons & {
                    "DESCARTAR","NOGUARDAR","DISCARD","DONTSAVE"
                }:
                    return h
            except Exception:
                if self._window_exists_visible(h):
                    raise PolarisError("No pude revisar un aviso de Polaris; se detuvo la limpieza.")
        return None

    def _check_cleanup_prompt(self, main):
        prompt=self._has_unsaved_prompt(main)
        if prompt:
            self._screenshot_error("confirmacion_antes_estacion")
            raise PolarisError(
                "Polaris mostró una confirmación: '"+(win32gui.GetWindowText(prompt) or "sin título")+
                "'. El bot no respondió Sí/No ni confirmó guardar/descartar cambios. "
                "Revísala manualmente; no se intentó cambiar estación."
            )

    def _cleanup_enabled(self, h):
        """IsWindowEnabled también para los padres CHILD, no para los owners modales."""
        seen=set()
        while h and h not in seen:
            seen.add(h)
            if not win32gui.IsWindowEnabled(h):
                return False
            style=win32gui.GetWindowLong(h,win32con.GWL_STYLE)
            if not style & getattr(win32con,"WS_CHILD",0x40000000):
                return True
            h=win32gui.GetParent(h)
        return True

    def _close_polaris_form(self, main, h, title):
        """Solicita cerrar solo el HWND validado. Sin coordenadas ni Alt+F4/Enter."""
        self._cleanup_main_ok(main)
        self._check_cleanup_prompt(main)
        if not self._window_exists_visible(h):
            return True
        item=self._cleanup_form_kind(main,h)
        if not item or not item[0] or item[1]!=title:
            raise PolarisError("La ventana de Polaris cambió; se detuvo el cierre por seguridad.")
        if not self._cleanup_enabled(h):
            return False
        # PostMessage no cambia el foco ni destruye por la fuerza la ventana.
        # Polaris puede pedir confirmación; la vigilancia de abajo se detiene ahí.
        pyautogui.failSafeCheck()
        try:
            win32gui.PostMessage(h,win32con.WM_CLOSE,0,0)
        except Exception as exc:
            if not self._window_exists_visible(h):
                return True
            raise PolarisError("Windows no permitió cerrar la ventana de Polaris. "
                               "Revisa que el bot y Polaris tengan el mismo nivel de permisos.") from exc
        end=time.monotonic()+2.5
        while time.monotonic()<end:
            pyautogui.failSafeCheck()
            self._check_cleanup_prompt(main)
            if not self._window_exists_visible(h):
                return True
            # Si apareció otro modal, volver a enumerar y atenderlo primero.
            if not self._cleanup_enabled(h):
                return False
            time.sleep(.15)
        return False

    def _cleanup_before_station_change(self, main):
        """Limpia antes de Utilerías; reenumera después de cada cierre individual."""
        self._cleanup_main_ok(main)
        self.log("Limpieza v3.1.1: cerrando ventanas de Polaris antes de cambiar estación...")
        closed=[]
        attempted=set()
        stable=0
        end=time.monotonic()+35.0
        try:
            while time.monotonic()<end and len(attempted)<32:
                pyautogui.failSafeCheck()
                self._cleanup_main_ok(main)
                self._check_cleanup_prompt(main)
                candidates=self._popup_candidates(main)
                if not candidates:
                    # No afirmar 'limpio' si quedó un formulario no identificable.
                    unknown=[]
                    for h in self._cleanup_windows(main):
                        item=self._cleanup_form_kind(main,h)
                        if item and not item[0]:
                            unknown.append(item[1])
                    if unknown:
                        raise PolarisError("Ventana de Polaris no identificada para cierre seguro: "
                                           +", ".join(unknown)+". Ciérrala manualmente.")
                    if not win32gui.IsWindowEnabled(main):
                        raise PolarisError("La ventana principal sigue bloqueada por un diálogo. "
                                           "No se intentó cambiar estación; revisa Polaris.")
                    stable+=1
                    if stable>=3:
                        self.log("Polaris listo. Ventanas cerradas: "+(" | ".join(closed) or "ninguna"))
                        return True
                    time.sleep(.2)
                    continue
                stable=0
                ready=[row for row in candidates if self._cleanup_enabled(row[2])]
                if not ready:
                    raise PolarisError("Hay ventanas de Polaris bloqueadas por un modal. "
                                       "Revísalo manualmente; no se cambió estación.")
                _depth,_area,h,title,cls,_rect=ready[0]
                identity=(h,cls,title)
                if identity in attempted:
                    raise PolarisError("La ventana de Polaris '"+title+"' no respondió al cierre. "
                                       "No se insistió ni se cambió estación.")
                attempted.add(identity)
                self.log("Cerrando ventana de Polaris: '"+title+"'...")
                if self._close_polaris_form(main,h,title):
                    closed.append(title)
                # No reutilizar la lista: el cierre puede mostrar un nuevo diálogo.
                time.sleep(.15)
            raise PolarisError("Se alcanzó el límite de limpieza de ventanas. "
                               "Revisa Polaris antes de volver a intentar el cambio de estación.")
        except Exception:
            self._screenshot_error("limpieza_ventanas_detenida")
            raise

    def _cleanup_before_station_change_quick(self, main):
        """Limpieza rápida para la VM antes de abrir Cambio de estación.

        Cierra primero 'Accesos al Sistema' de forma directa y, si no queda
        ninguna otra ventana operativa de Polaris, continúa inmediatamente.
        Solo usa la limpieza completa como respaldo cuando realmente quedan
        otros formularios abiertos.
        """
        self._cleanup_main_ok(main)
        pyautogui.failSafeCheck()
        self.log("VM-P05: limpieza rápida antes de cambio de estación...")

        # 1) Cerrar directamente Accesos al Sistema si está visible.
        access=[]
        for row in self._popup_candidates(main):
            try:
                _depth,_area,h,title,cls,_rect=row
                if self._norm(title)=="ACCESOSALSISTEMA":
                    access.append((h,title))
            except Exception:
                pass
        for h,title in access:
            if not self._window_exists_visible(h):
                continue
            self.log("VM-P05: cerrando 'Accesos al Sistema'...")
            pyautogui.failSafeCheck()
            try:
                win32gui.PostMessage(h,win32con.WM_CLOSE,0,0)
            except Exception as exc:
                raise PolarisError(
                    "Windows no permitió cerrar 'Accesos al Sistema'. "
                    "Revisa que bot y Polaris tengan el mismo nivel de permisos."
                ) from exc
            end=time.monotonic()+3.0
            while time.monotonic()<end and self._window_exists_visible(h):
                pyautogui.failSafeCheck()
                time.sleep(.08)
            if self._window_exists_visible(h):
                raise PolarisError("'Accesos al Sistema' no respondió al cierre.")

        # 2) Dar un instante a Delphi para devolver el foco/habilitar la principal.
        end=time.monotonic()+2.0
        while time.monotonic()<end:
            pyautogui.failSafeCheck()
            if self._window_exists_visible(main) and win32gui.IsWindowEnabled(main):
                break
            time.sleep(.08)

        # 3) Si ya no quedan formularios, continuar YA; sin tres rondas de escaneo.
        remaining=self._popup_candidates(main)
        if not remaining and win32gui.IsWindowEnabled(main):
            self.log("VM-P05: Polaris limpio; continúa cambio de estación.")
            return True

        # 4) Si quedó otra ventana real, conservar la limpieza segura original.
        names=[]
        for row in remaining:
            try:
                names.append(row[3])
            except Exception:
                pass
        self.log("VM-P05: quedan ventanas de Polaris: "+(" | ".join(names) or "sin título")
                 +". Se usa limpieza segura de respaldo.")
        return self._cleanup_before_station_change(main)

    def _close_access_window(self, *, required=False):
        """Compatibilidad con v3.1: usa el mismo cierre verificado, no clics en la X."""
        main=self._find_title(self.pcfg["ventana_principal_regex"])
        if not main:
            if required:
                raise PolarisError("No encuentro la ventana principal de Polaris.")
            return False
        found=any(self._norm(row[3])=="ACCESOSALSISTEMA" for row in self._popup_candidates(main))
        if found:
            self._cleanup_before_station_change(main)
        return found

    def _close_station_blockers(self):
        main=self._find_title(self.pcfg["ventana_principal_regex"])
        if not main:
            raise PolarisError("No encuentro la ventana principal de Polaris.")
        return self._cleanup_before_station_change(main)

    def _series_dialog(self, timeout=10):
        end=time.time()+timeout
        while time.time()<end:
            h=self._find_window_any_level(
                r".*Selecci[oó]n de Series de facturaci[oó]n.*",
                min_width=240, min_height=280,
            )
            if h:
                return h
            time.sleep(.20)
        return None

    def _station_menu_path(self, main):
        """Localiza Cambio de estación por el ID nativo 140, no por texto.

        En esta instalación Delphi expone los textos de los submenús de forma
        inconsistente en RDP, pero los ID de comando sí permanecen estables.
        Se busca el único submenú superior que contenga el ID 140 y se valida
        que esa opción esté habilitada antes de usarla.
        """
        from ctypes import wintypes as w
        u=ctypes.WinDLL("user32",use_last_error=True)
        u.GetMenu.argtypes=[w.HWND]; u.GetMenu.restype=w.HMENU
        u.GetMenuItemCount.argtypes=[w.HMENU]; u.GetMenuItemCount.restype=ctypes.c_int
        u.GetSubMenu.argtypes=[w.HMENU,ctypes.c_int]; u.GetSubMenu.restype=w.HMENU
        u.GetMenuItemID.argtypes=[w.HMENU,ctypes.c_int]; u.GetMenuItemID.restype=w.UINT
        u.GetMenuState.argtypes=[w.HMENU,w.UINT,w.UINT]; u.GetMenuState.restype=w.UINT

        MF_BYPOSITION=0x0400
        MF_DISABLED=0x0002
        MF_GRAYED=0x0001
        MF_SEPARATOR=0x0800

        menu=u.GetMenu(main)
        if not menu:
            raise PolarisError("Polaris no expone su menú principal de Windows.")

        matches=[]
        top_count=u.GetMenuItemCount(menu)
        for top_pos in range(max(0,top_count)):
            submenu=u.GetSubMenu(menu,top_pos)
            if not submenu:
                continue
            sub_count=u.GetMenuItemCount(submenu)
            for sub_pos in range(max(0,sub_count)):
                command_id=int(u.GetMenuItemID(submenu,sub_pos)) & 0xFFFFFFFF
                if command_id==140:
                    state=int(u.GetMenuState(submenu,sub_pos,MF_BYPOSITION)) & 0xFFFFFFFF
                    matches.append((top_pos,submenu,sub_pos,state,sub_count))

        if len(matches)!=1:
            raise PolarisError(
                f"No se pudo identificar una única ruta a Cambio de estación (ID 140). "
                f"Coincidencias: {len(matches)}. No se ejecutó ninguna otra opción."
            )

        top_pos,submenu,sub_pos,state,sub_count=matches[0]
        if state==0xFFFFFFFF or state & (MF_DISABLED|MF_GRAYED|MF_SEPARATOR):
            raise PolarisError("Cambio de estación (ID 140) está deshabilitado en Polaris.")

        # Evidencia medida en esta VM: Utilerías está en posición 6 y contiene 14 items.
        # No se exige el texto porque Delphi/RDP puede devolverlo vacío.
        if top_pos!=6 or sub_count<7:
            raise PolarisError(
                f"Se encontró ID 140, pero la estructura del menú cambió "
                f"(menú superior={top_pos}, elementos={sub_count}). No se hizo clic."
            )
        return menu,top_pos,submenu,sub_pos,140

    def _open_station_catalog(self, main):
        """Abre Cambio de estación por la ruta nativa ID 140 medida en la VM."""
        from ctypes import wintypes as w

        self._activate(main,maximize=True)
        pyautogui.failSafeCheck()

        u=ctypes.WinDLL("user32",use_last_error=True)
        u.GetMenuItemRect.argtypes=[w.HWND,w.HMENU,w.UINT,ctypes.POINTER(w.RECT)]
        u.GetMenuItemRect.restype=w.BOOL
        u.SendMessageW.argtypes=[w.HWND,w.UINT,w.WPARAM,w.LPARAM]
        u.SendMessageW.restype=w.LPARAM
        WM_COMMAND=getattr(win32con,"WM_COMMAND",0x0111)

        menu,util_pos,submenu,target_pos,command_id=self._station_menu_path(main)

        def rect_menu(owner,hmenu,pos):
            rr=w.RECT()
            if not u.GetMenuItemRect(owner,hmenu,pos,ctypes.byref(rr)):
                return None
            result=(int(rr.left),int(rr.top),int(rr.right),int(rr.bottom))
            return result if result[2]>result[0] and result[3]>result[1] else None

        # Abrir el sexto menú superior (Utilerías) usando el rectángulo real
        # devuelto por Windows, sin porcentajes de resolución.
        top_rect=rect_menu(main,menu,util_pos)
        if top_rect:
            x=(top_rect[0]+top_rect[2])//2
            y=(top_rect[1]+top_rect[3])//2
            self.log(f"VM-P06: ruta ID 140 localizada. Menú 6 rect={top_rect}.")
            pyautogui.moveTo(x,y,duration=.08)
            pyautogui.click()

            deadline=time.monotonic()+2.5
            item_rect=None
            while time.monotonic()<deadline:
                pyautogui.failSafeCheck()
                candidate=rect_menu(0,submenu,target_pos)
                if candidate:
                    cx=(candidate[0]+candidate[2])//2
                    cy=(candidate[1]+candidate[3])//2
                    try:
                        under=win32gui.WindowFromPoint((cx,cy))
                        if under and win32gui.GetClassName(under)=="#32768":
                            item_rect=candidate
                            break
                    except Exception:
                        pass
                time.sleep(.04)

            if item_rect:
                cx=(item_rect[0]+item_rect[2])//2
                cy=(item_rect[1]+item_rect[3])//2
                self.log(f"VM-P06: clic real en Cambio de estación ID 140 rect={item_rect}.")
                pyautogui.moveTo(cx,cy,duration=.08)
                pyautogui.click()
                dlg=self._wait_text_dialog("Seleccione el registro deseado",5)
                if dlg:
                    return dlg

        # Delphi a veces no materializa el popup en RDP. El mismo ID ya fue
        # localizado y validado dentro del menú real, así que se envía directamente.
        try:
            pyautogui.press("esc")
        except Exception:
            pass
        self._activate(main,maximize=True)
        self.log("VM-P06: enviando WM_COMMAND validado ID 140.")
        u.SendMessageW(main,WM_COMMAND,command_id,0)
        dlg=self._wait_text_dialog("Seleccione el registro deseado",7)
        if dlg:
            return dlg

        self._screenshot_error("vm_p06_cambio_estacion_id140")
        raise PolarisError(
            "Polaris no abrió 'Seleccione el registro deseado' después de ejecutar "
            "el comando nativo validado Cambio de estación (ID 140)."
        )

    def _find_descendant_text(self, root, wanted):
        """Busca un control hijo visible por texto exacto/normalizado."""
        target=self._norm(wanted)
        found=[]
        def cb(h,_):
            try:
                if not win32gui.IsWindowVisible(h):
                    return
                txt=(win32gui.GetWindowText(h) or "").strip()
                if txt and self._norm(txt)==target:
                    r=self._rect(h)
                    if r.width>20 and r.height>12:
                        found.append((r.width*r.height,h,r,txt))
            except Exception:
                pass
        try:
            win32gui.EnumChildWindows(root,cb,None)
        except Exception:
            pass
        if not found:
            return None
        found.sort(key=lambda x:x[0])
        return found[0][1]

    def _click_button_text_or_rel(self, root, text, rel, *, wait=.45):
        """Pulsa un botón VCL por texto y usa coordenada relativa como respaldo."""
        h=self._find_descendant_text(root,text)
        if h:
            try:
                r=self._rect(h)
                x=(r.left+r.right)//2; y=(r.top+r.bottom)//2
                pyautogui.moveTo(x,y,duration=.12)
                pyautogui.click()
                time.sleep(wait)
                return True
            except Exception:
                pass
        self._click_rel(root,rel,wait=wait)
        return False

    def _station_dialog_alive(self):
        return self._wait_text_dialog("Seleccione el registro deseado",.35)

    def _accept_station_row(self, dlg):
        """Acepta la estación seleccionada y confirma que aparece el diálogo de series."""
        self.log("Estación seleccionada. Pulsando Aceptar...")
        self._click_button_text_or_rel(dlg,"Aceptar",self.ESTACION_DLG["aceptar"],wait=.65)
        series=self._series_dialog(2.5)
        if series:
            return series

        # Algunos controles VCL reciben el clic visual pero no disparan el evento.
        # Si el catálogo sigue abierto, ENTER activa el botón Aceptar predeterminado.
        dlg2=self._station_dialog_alive()
        if dlg2:
            self.log("Aceptar no respondió al primer clic; reintentando con ENTER...")
            self._activate(dlg2)
            pyautogui.press("enter")
            series=self._series_dialog(3.0)
            if series:
                return series

        # Último reintento físico sobre la misma zona del botón.
        dlg3=self._station_dialog_alive()
        if dlg3:
            self.log("Segundo reintento: clic físico en Aceptar...")
            self._click_rel(dlg3,self.ESTACION_DLG["aceptar"],wait=.65)
            series=self._series_dialog(3.0)
            if series:
                return series
        return None

    def _accept_series_dialog(self, series):
        """Conserva las series predeterminadas y cierra el diálogo con Aceptar."""
        self._activate(series)
        self.log("Series de facturación: conservando valores y pulsando Aceptar...")
        self._click_button_text_or_rel(series,"Aceptar",self.SERIES_DLG["aceptar"],wait=.65)

        # Confirma que el diálogo desapareció; si no, ENTER como respaldo VCL.
        if self._series_dialog(.7):
            self.log("El diálogo de Series sigue abierto; reintentando Aceptar con ENTER...")
            series2=self._series_dialog(.3)
            if series2:
                self._activate(series2)
                pyautogui.press("enter")
                time.sleep(.65)
        return self._series_dialog(.5) is None

    def _change_station(self, main, station, force=False):
        """Cambia la estación antes de abrir Facturación de Efectivo.

        Flujo real del video:
          Utilerías -> CLICK en Cambio de estación
          -> buscar por Número -> seleccionar -> Aceptar
          -> Selección de Series de facturación -> conservar valores -> Aceptar.

        ``force=True`` se usa en el botón de PRUEBA para que puedas ver el flujo
        incluso si ya estás parado en esa misma estación.
        """
        key=normalize_station(station)
        if not key or key not in STATIONS:
            raise PolarisError(
                f"Estación no reconocida: '{station}'. Usa ARY I, ARY II, ARY III, "
                "ARY IV, ARY V, ARY VI o SERVICIO APACHE."
            )
        info=STATIONS[key]

        # v3.1: antes de decidir si cambia o no la estación, dejar Polaris
        # completamente limpio de ventanas MDI/modales que puedan tapar Utilerías.
        # Solo se cierran ventanas DEL PROPIO POLARIS; si aparece una confirmación
        # de cambios sin guardar, el bot se detiene en vez de descartarlos.
        self._cleanup_before_station_change_quick(main)
        main=self._find_title(self.pcfg["ventana_principal_regex"]) or main

        if not force and self._station_is_active(main,key):
            self.log(f"Estación ya activa en Polaris: {key} ({info['numero']}). No es necesario cambiarla.")
            return main

        title=win32gui.GetWindowText(main) or ""
        self.log(f"Cambiando estación en Polaris -> {key} ({info['numero']}). Actual: {title}")

        dlg=self._open_station_catalog(main)
        if not dlg:
            self._screenshot_error("cambio_estacion_sin_dialogo")
            raise PolarisError(
                "No apareció 'Seleccione el registro deseado'. "
                "El bot NO continuó ni abrió Respaldar."
            )

        self._activate(dlg)
        row_y=self.STATION_ROW_Y.get(key)
        if row_y is None:
            raise PolarisError(f"No hay posición configurada para la estación {key}.")

        # Exactamente como en el video: seleccionar la fila de la estación y luego Aceptar.
        self.log(f"Catálogo de estaciones abierto. Seleccionando fila {key} ({info['numero']})...")
        self._click_rel(dlg,(0.535,row_y),wait=.35)

        series=self._accept_station_row(dlg)
        if not series:
            self._screenshot_error("cambio_estacion_no_acepto_registro")
            raise PolarisError(
                "Seleccioné la estación, pero Polaris no aceptó el registro o no abrió "
                "'Selección de Series de facturación'."
            )

        if not self._accept_series_dialog(series):
            self._screenshot_error("cambio_estacion_no_acepto_series")
            raise PolarisError(
                "Polaris abrió 'Selección de Series de facturación', pero no pude "
                "confirmarla con Aceptar."
            )

        end=time.time()+8
        last_title=""
        while time.time()<end:
            main2=self._find_title(self.pcfg["ventana_principal_regex"]) or main
            last_title=win32gui.GetWindowText(main2) or ""
            if self._station_is_active(main2,key):
                self.log(f"Estación activa CONFIRMADA: {key} -> {info['polaris']} ({info['numero']})")
                self._activate(main2,maximize=True)
                return main2
            time.sleep(.25)

        self._screenshot_error("cambio_estacion_no_confirmado")
        raise PolarisError(
            f"Polaris no confirmó la estación {key}. Título actual: '{last_title}'."
        )

    def test_station_only(self, station):
        with self._lock:
            main=self.ensure_ready()
            self._change_station(main,station,force=True)
            return f"estación activa: {normalize_station(station)}"

    def test_cleanup_only(self):
        with self._lock:
            main=self.ensure_ready()
            self._cleanup_before_station_change_quick(main)
            return "Polaris limpio: ventanas emergentes cerradas"

    def _invoice_window(self):
        # IMPORTANTE v2.4:
        # "Facturación de Efectivo" es una ventana MDI HIJA de Polaris. No basta
        # con EnumWindows(); hay que buscar también descendientes.
        h=self._find_window_any_level(
            r"^\s*Facturaci[oó]n de Efectivo\s*$",
            min_width=650,
            min_height=450,
        )
        if h:
            return h

        # Fallback conservador para instalaciones donde el título cambie un poco.
        h=self._find_window_any_level(
            r".*Facturaci[oó]n de Efectivo.*",
            min_width=650,
            min_height=450,
        )
        if h:
            return h

        # Último fallback legado; se valida después antes de hacer clics críticos.
        return self._find_parent_containing_text("Datos de Facturación")

    def _open_cash_invoice(self, main):
        h=self._invoice_window()
        if h: self._activate(h); return h
        self.log("Polaris: Facturación -> Efectivo")
        r=self._rect(main)
        # Menú Facturación observado en el video. Después Down+Enter abre el primer elemento: Efectivo.
        pyautogui.click(r.left+int(r.width*.124), r.top+int(r.height*.038)); time.sleep(.35)
        pyautogui.press("down"); pyautogui.press("enter")
        end=time.time()+10
        while time.time()<end:
            h=self._invoice_window()
            if h:
                r=self._rect(h)
                # En los videos del usuario la ventana real mide aprox. 820x635.
                # Si obtenemos un panel demasiado pequeño, no seguimos a ciegas.
                if r.width < 650 or r.height < 450:
                    self._screenshot_error("factura_rect_invalido")
                    raise PolarisError(
                        f"Detecté 'Facturación de Efectivo' con tamaño inesperado "
                        f"{r.width}x{r.height}. Se detuvo antes de hacer clics."
                    )
                self.log(
                    f"Ventana Facturación detectada correctamente: "
                    f"{r.width}x{r.height} en ({r.left},{r.top})."
                )
                self._activate(h)
                return h
            time.sleep(.25)
        self._screenshot_error("abrir_factura")
        raise PolarisError("No se abrió la ventana 'Facturación de Efectivo'.")

    def _captura_factura(self):
        from .factura_captura import CapturaFactura
        if getattr(self, '_captura317', None) is None:
            self._captura317 = CapturaFactura(self)
        return self._captura317

    def _verificar_captura(self, inv, sol):
        """Compatibilidad v3.1: no relee controles DevExpress antes de timbrar.

        El diagnóstico real del proyecto mostró que el RFC visible estaba cargado,
        pero la relectura encontraba un TcxLabel en vez del editor y detenía el
        proceso justo antes de Aceptar. El flujo v3.1 que ya funcionó no hacía
        esta relectura.
        """
        self.log('REVISIÓN ESTABLE v3.1: se omite la relectura DevExpress que detenía el Aceptar final.')
        return True

    def _select_client(self, inv, rfc):
        rfc=(rfc or "").strip().upper().replace(" ", "")
        if not rfc:
            raise PolarisError("No recibí RFC para buscar al cliente en Polaris.")

        self.log(f"Buscando cliente por RFC: {rfc}")
        self._click_rel(inv,self.FACTURA["buscar_cliente"])
        dlg=self._wait_text_dialog("Seleccione el cliente deseado",10)
        if not dlg:
            raise PolarisError("No apareció la búsqueda de clientes.")
        if not self._same_polaris_process(inv,dlg):
            raise PolarisError("El buscador de clientes pertenece a otro proceso. No se capturan datos.")
        self._activate(dlg)

        # Flujo estable v2.5/v3.1:
        # 1) clic real en encabezado R.F.C.; 2) clic en el campo superior;
        # 3) escribir RFC con teclado real y salir con Tab; 4) Buscar; 5) primera coincidencia; 6) Aceptar.
        # La detección de controles DevExpress anidados ya está corregida; se usa
        # captura por teclado real para que Polaris reciba OnKey/OnExit.
        self.log('Catálogo de clientes: activando búsqueda por RFC (flujo estable v3.1)...')
        rdlg=self._rect(dlg)
        hx=rdlg.left+int(rdlg.width*self.BUSCAR["columna_rfc"][0])
        hy=rdlg.top+int(rdlg.height*self.BUSCAR["columna_rfc"][1])
        pyautogui.moveTo(hx,hy,duration=.25)
        pyautogui.click(hx,hy)
        time.sleep(.55)

        self.log('RFC: escribiendo carácter por carácter (sin portapapeles)...')
        self._captura_factura().escribir_rel(
            dlg, self.BUSCAR["campo_busqueda"], "rfc", rfc
        )
        self.log('RFC escrito y confirmado después de salir del campo. Ejecutando Buscar...')
        self._click_rel(dlg,self.BUSCAR["buscar"],wait=.20)
        time.sleep(1.2)
        self._click_rel(dlg,self.BUSCAR["primera_fila"])
        self._click_rel(dlg,self.BUSCAR["aceptar"])
        time.sleep(1.0)

        inv=self._invoice_window() or inv
        rinv=self._rect(inv)
        if rinv.width < 650 or rinv.height < 450:
            self._screenshot_error("factura_post_cliente")
            raise PolarisError(
                f"No recuperé la ventana MDI real de Facturación después de seleccionar cliente "
                f"(detectado {rinv.width}x{rinv.height})."
            )
        self.log(
            f"Facturación activa tras seleccionar cliente: "
            f"{rinv.width}x{rinv.height} en ({rinv.left},{rinv.top})."
        )
        self._activate(inv)
        self.log("Cliente aceptado. Polaris cargó automáticamente Nombre/RFC/CP/Régimen; el bot no toca esos campos.")
        self._pasos_factura['cliente']=True
        self.diag.evento('FACTURACION: cliente','OK','Cliente seleccionado por RFC',{'rfc_capturado':True})
        return inv

    def _combo_selected_text(self):
        """Intenta copiar el texto seleccionado del combo que tiene el foco."""
        try:
            win32clipboard.OpenClipboard()
            try: win32clipboard.EmptyClipboard()
            finally: win32clipboard.CloseClipboard()
        except: pass
        pyautogui.hotkey("ctrl","c"); time.sleep(.15)
        try:
            win32clipboard.OpenClipboard()
            try:
                if win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
                    return win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT) or ""
            finally: win32clipboard.CloseClipboard()
        except: pass
        return ""

    def _same_text(self,a,b):
        def n(s):
            s=unicodedata.normalize("NFKD",s or "")
            s="".join(c for c in s if not unicodedata.combining(c))
            return re.sub(r"[^A-Z0-9]+"," ",s.upper()).strip()
        na,nb=n(a),n(b)
        return bool(na and nb and (na==nb or na in nb or nb in na))

    def _set_cfdi_usage(self, inv, usage):
        """Maneja Uso CFDI sin quedarse atrapado en ``Cuenta``.

        Hallazgo del video del usuario:
        - al elegir TARJETA DE CRÉDITO/DÉBITO, Polaris habilita ``Cuenta``;
        - ``Cuenta`` NO es obligatoria para este flujo;
        - si el cliente no manda Uso CFDI, Polaris ya trae
          ``GASTOS EN GENERAL.`` y NO hay que tocar el combo;
        - por eso, para G03 brincamos directamente al Folio del ticket.

        Solo si el cliente pidió un Uso CFDI diferente abrimos ese combo.
        Esto elimina el paso que dejaba el foco visualmente en ``Cuenta``.
        """
        target=(usage or "GASTOS EN GENERAL.").strip()

        def plain(v):
            v=unicodedata.normalize("NFKD",v or "")
            v="".join(c for c in v if not unicodedata.combining(c))
            return re.sub(r"[^A-Z0-9]+"," ",v.upper()).strip()

        aliases={
            "G01":"ADQUISICION DE MERCANCIAS.",
            "G02":"DEVOLUCIONES, DESCUENTOS O BONIFICACIONES.",
            "G03":"GASTOS EN GENERAL.",
            "S01":"SIN EFECTOS FISCALES.",
            "CP01":"PAGOS.",
            "CN01":"NOMINA.",
            "P01":"POR DEFINIR",
        }
        canon=aliases.get(target.upper().replace(" ",""), target)
        canon_plain=plain(canon)

        # CASO NORMAL ARY: Polaris ya muestra GASTOS EN GENERAL por defecto.
        # NO hacemos clic en Cuenta ni enviamos HOME/DOWN/ENTER. El siguiente
        # paso (_add_ticket) hace clic directamente en Folio del ticket.
        if canon_plain == plain("GASTOS EN GENERAL."):
            self.log("Uso CFDI: GASTOS EN GENERAL. ya es el valor por defecto de Polaris; Cuenta se deja vacía y se brinca DIRECTO al Folio del ticket.")
            return "GASTOS EN GENERAL."

        order=[
            "ADQUISICION DE MERCANCIAS.",
            "DEVOLUCIONES, DESCUENTOS O BONIFICACIONES.",
            "GASTOS EN GENERAL.",
            "CONSTRUCCIONES.",
            "MOBILIARIO Y EQUIPO DE OFICINA POR INVERSIONES.",
            "EQUIPO DE TRANSPORTE.",
            "EQUIPO DE COMPUTO Y ACCESORIOS.",
            "DADOS, TROQUELES, MOLDES, MATRICES Y HERRAMENTAL.",
            "COMUNICACIONES TELEFONICAS.",
            "COMUNICACIONES SATELITALES.",
            "OTRA MAQUINARIA Y EQUIPO.",
            "HONORARIOS MEDICOS, DENTALES Y GASTOS HOSPITALARIOS.",
            "GASTOS MEDICOS POR INCAPACIDAD O DISCAPACIDAD.",
            "GASTOS FUNERALES.",
            "DONATIVOS.",
            "INTERESES REALES EFECTIVAMENTE PAGADOS POR CREDITOS HIPOTECARIOS.",
            "APORTACIONES VOLUNTARIAS AL SAR.",
            "PRIMAS POR SEGUROS DE GASTOS MEDICOS.",
            "GASTOS DE TRANSPORTACION ESCOLAR OBLIGATORIA.",
            "DEPOSITOS EN CUENTAS PARA EL AHORRO Y PLANES DE PENSIONES.",
            "PAGOS POR SERVICIOS EDUCATIVOS (COLEGIATURAS).",
            "SIN EFECTOS FISCALES.",
            "PAGOS.",
            "NOMINA.",
            "POR DEFINIR",
        ]

        idx=None
        for i,item in enumerate(order):
            if plain(item)==canon_plain:
                idx=i; break
        if idx is None:
            for i,item in enumerate(order):
                a,b=plain(item),canon_plain
                if a and b and (a in b or b in a):
                    idx=i; break
        if idx is None:
            raise PolarisError(f"Uso CFDI no soportado por el selector automático: '{target}'.")

        self.log(f"Uso CFDI distinto de G03: seleccionando {target}...")
        self._activate(inv)

        # Coordenada Y corregida contra el video (0.316). Primero hacemos un
        # clic al CUERPO del combo para abandonar Cuenta; luego abrimos flecha.
        self._assert_point_inside_window(inv,self.FACTURA["uso_cfdi"],"uso_cfdi")
        self._click_rel(inv,self.FACTURA["uso_cfdi"],wait=.20)
        self._click_rel(inv,self.FACTURA["uso_cfdi_flecha"],wait=.30)
        pyautogui.press("home")
        time.sleep(.10)
        if idx:
            pyautogui.press("down",presses=idx,interval=.045)
        pyautogui.press("enter")
        time.sleep(.40)

        got=self._combo_selected_text()
        if got and not self._same_text(got,canon):
            self.log(f"Uso CFDI no confirmado al primer intento ({got}); reintentando...")
            self._click_rel(inv,self.FACTURA["uso_cfdi"],wait=.18)
            self._click_rel(inv,self.FACTURA["uso_cfdi_flecha"],wait=.25)
            pyautogui.press("home")
            if idx:
                pyautogui.press("down",presses=idx,interval=.045)
            pyautogui.press("enter")
            time.sleep(.35)
            got2=self._combo_selected_text()
            if got2 and not self._same_text(got2,canon):
                self._screenshot_error("uso_cfdi")
                raise PolarisError(f"No pude seleccionar Uso CFDI '{target}'. Polaris muestra '{got2}'.")

        self.log(f"Uso CFDI seleccionado: {target}")
        self._pasos_factura['cfdi']=target
        self.diag.evento('FACTURACION: Uso CFDI','OK','Selección enviada',{'seleccion_no_vacia':bool(target)})
        return target

    def _payment_text_at(self, inv):
        """Lee el texto visible del control Forma de Pago sin usar select().

        Polaris es VCL. WindowFromPoint suele devolver el TComboBox o un hijo.
        Recorremos padres y tomamos el primer texto que parezca una forma de pago.
        Esta lectura es SOLO para verificar; nunca debe congelar el bot.
        """
        try:
            r=self._rect(inv)
            x=r.left+int(r.width*self.FACTURA["forma_pago"][0])
            y=r.top+int(r.height*self.FACTURA["forma_pago"][1])
            hwnd=win32gui.WindowFromPoint((x,y))
            texts=[]
            for _ in range(6):
                if not hwnd: break
                try:
                    txt=(win32gui.GetWindowText(hwnd) or "").strip()
                    cls=(win32gui.GetClassName(hwnd) or "").strip()
                    if txt: texts.append((cls,txt))
                    hwnd=win32gui.GetParent(hwnd)
                except Exception:
                    break
            # Primero preferimos texto de ComboBox/TComboBox.
            for cls,txt in texts:
                if "COMBO" in cls.upper() and txt:
                    return txt
            # Fallback: cualquier texto corto que coincida con las opciones conocidas.
            known=("EFECTIVO","CHEQUE","TRANSFERENCIA","TARJETA","MONEDERO","DINERO",
                   "VALES","DACION","DACIÓN","SUBROGACION","SUBROGACIÓN","CONSIGNACION",
                   "CONSIGNACIÓN","CONDONACION","CONDONACIÓN","COMPENSACION","COMPENSACIÓN",
                   "NOVACION","NOVACIÓN","CONFUSION","CONFUSIÓN","REMISION","REMISIÓN",
                   "PRESCRIPCION","PRESCRIPCIÓN","SATISFACCION","SATISFACCIÓN","POR DEFINIR",
                   "ANTICIPOS","INTERMEDIARIO")
            for _cls,txt in texts:
                if any(k in txt.upper() for k in known): return txt
        except Exception:
            pass
        return ""

    def _set_payment(self, inv, payment):
        """Selecciona Forma de Pago SIN leer/verificar el TComboBox de Polaris.

        Corrección v2.3:
        Después de elegir TARJETA DE CRÉDITO/DÉBITO, Polaris mueve el foco a
        ``Cuenta``. En versiones anteriores el bot intentaba leer de nuevo el
        combo Forma de Pago usando Win32 para verificar la selección. Ese control
        VCL puede bloquear la llamada y el usuario veía el cursor detenido en
        ``Cuenta`` sin llegar al Folio.

        Ahora se imita el video y se continúa inmediatamente:
          flecha Forma de Pago -> HOME -> DOWN... -> ENTER -> siguiente paso.
        ``Cuenta`` nunca se toca, lee ni valida.
        """
        def norm(value):
            value=unicodedata.normalize("NFKD", value or "")
            value="".join(c for c in value if not unicodedata.combining(c))
            value=re.sub(r"[^A-Z0-9]+", " ", value.upper()).strip()
            return re.sub(r"\s+", " ", value)

        aliases={
            "": "EFECTIVO",
            "EFECTIVO": "EFECTIVO",
            "CASH": "EFECTIVO",
            "CHEQUE": "CHEQUE NOMINATIVO",
            "CHEQUE NOMINATIVO": "CHEQUE NOMINATIVO",
            "TRANSFERENCIA": "TRANSFERENCIA ELECTRONICA DE FONDOS",
            "TRANSFERENCIA ELECTRONICA": "TRANSFERENCIA ELECTRONICA DE FONDOS",
            "TRANSFERENCIA ELECTRONICA DE FONDOS": "TRANSFERENCIA ELECTRONICA DE FONDOS",
            "TARJETA": "TARJETA DE CREDITO",
            "TARJETA CREDITO": "TARJETA DE CREDITO",
            "TARJETA DE CREDITO": "TARJETA DE CREDITO",
            "CREDITO": "TARJETA DE CREDITO",
            "MONEDERO": "MONEDERO ELECTRONICO",
            "MONEDERO ELECTRONICO": "MONEDERO ELECTRONICO",
            "DINERO ELECTRONICO": "DINERO ELECTRONICO",
            "VALES": "VALES DE DESPENSA",
            "VALES DE DESPENSA": "VALES DE DESPENSA",
            "DACION EN PAGO": "DACION EN PAGO",
            "PAGO POR SUBROGACION": "PAGO POR SUBROGACION",
            "PAGO POR CONSIGNACION": "PAGO POR CONSIGNACION",
            "CONDONACION": "CONDONACION",
            "COMPENSACION": "COMPENSACION",
            "NOVACION": "NOVACION",
            "CONFUSION": "CONFUSION",
            "REMISION DE DEUDA": "REMISION DE DEUDA",
            "PRESCRIPCION O CADUCIDAD": "PRESCRIPCION O CADUCIDAD",
            "A SATISFACCION DEL ACREEDOR": "A SATISFACCION DEL ACREEDOR",
            "DEBITO": "TARJETA DE DEBITO",
            "TARJETA DE DEBITO": "TARJETA DE DEBITO",
            "TARJETA DE SERVICIOS": "TARJETA DE SERVICIOS",
            "POR DEFINIR": "POR DEFINIR",
            "APLICACION DE ANTICIPOS": "APLICACION DE ANTICIPOS",
            "INTERMEDIARIO PAGOS": "INTERMEDIARIO PAGOS",
        }
        requested=norm(payment)
        target=aliases.get(requested, requested or "EFECTIVO")
        order=[
            "EFECTIVO",
            "CHEQUE NOMINATIVO",
            "TRANSFERENCIA ELECTRONICA DE FONDOS",
            "TARJETA DE CREDITO",
            "MONEDERO ELECTRONICO",
            "DINERO ELECTRONICO",
            "VALES DE DESPENSA",
            "DACION EN PAGO",
            "PAGO POR SUBROGACION",
            "PAGO POR CONSIGNACION",
            "CONDONACION",
            "COMPENSACION",
            "NOVACION",
            "CONFUSION",
            "REMISION DE DEUDA",
            "PRESCRIPCION O CADUCIDAD",
            "A SATISFACCION DEL ACREEDOR",
            "TARJETA DE DEBITO",
            "TARJETA DE SERVICIOS",
            "POR DEFINIR",
            "APLICACION DE ANTICIPOS",
            "INTERMEDIARIO PAGOS",
        ]
        if target not in order:
            raise PolarisError(f"Forma de pago no soportada: '{payment}'.")

        self._activate(inv)

        # EFECTIVO es el valor con el que abre Facturación de Efectivo. No hay
        # necesidad de tocar el control si eso es lo solicitado.
        if target == "EFECTIVO":
            self.log("Forma de pago: EFECTIVO (valor inicial de Polaris). Se continúa sin tocar Cuenta.")
            self._pasos_factura['pago']=target
            self.diag.evento('FACTURACION: forma de pago','OK','Valor inicial efectivo',{'seleccion_no_vacia':True})
            return target

        idx=order.index(target)
        self.log(f"Forma de pago: seleccionando {target}...")
        self._assert_point_inside_window(inv,self.FACTURA["forma_pago_flecha"],"forma_pago")
        self._click_rel(inv,self.FACTURA["forma_pago_flecha"],wait=.30)
        pyautogui.press("home")
        time.sleep(.10)
        if idx:
            pyautogui.press("down",presses=idx,interval=.050)
        pyautogui.press("enter")
        # Enter cierra el combo y, en tarjetas, Polaris deja el foco en Cuenta.
        # Eso es normal; el siguiente paso hará CLICK DIRECTO en Folio/CFDI.
        time.sleep(.45)
        self.log(f"Forma de pago enviada: {target}. No se verifica el combo y no se toca Cuenta.")
        self._pasos_factura['pago']=target
        self.diag.evento('FACTURACION: forma de pago','OK','Selección enviada',{'seleccion_no_vacia':bool(target)})
        return target

    def test_payment_only(self, payment):
        """Prueba aislada de Forma de Pago; no lee ni valida ``Cuenta``."""
        inv=self._invoice_window()
        if not inv:
            raise PolarisError("Abre primero la ventana 'Facturación de Efectivo' en Polaris.")
        self._activate(inv)
        target=self._set_payment(inv,payment)
        return f"selección confirmada en Polaris: {target}"

    @staticmethod
    def _calendar_day_cell(year, month, day):
        """Casilla del calendario Polaris: domingo primero, máximo seis semanas."""
        weeks=calendar.Calendar(firstweekday=6).monthdayscalendar(int(year),int(month))
        while len(weeks)<6:
            weeks.append([0]*7)
        for row,week in enumerate(weeks[:6]):
            for col,value in enumerate(week):
                if value==int(day):
                    return col,row
        raise PolarisError("No pude calcular la casilla del día solicitado.")

    def _date_outer_control(self, inner, inv):
        """Sube del editor interior al TcxDateEdit que incluye la flecha del calendario."""
        cur=inner
        chosen=inner
        for _ in range(8):
            if not cur or cur==inv:
                break
            cls=self._class_name(cur).upper()
            r=self._rect(cur)
            if 'DATE' in cls or 'DROPDOWN' in cls:
                chosen=cur
                break
            parent=win32gui.GetParent(cur)
            if not parent or parent==cur:
                break
            try:
                pr=self._rect(parent)
                if pr.width>=r.width+10 and abs(pr.top-r.top)<=6 and abs(pr.bottom-r.bottom)<=10:
                    chosen=parent
            except Exception:
                pass
            cur=parent
        return chosen

    def _calendar_popup(self, inv, outer, timeout=2.5):
        """Localiza el popup de calendario perteneciente al mismo proceso de Polaris."""
        o=self._rect(outer)
        end=time.time()+timeout

        def score(h):
            try:
                if not self._window_exists_visible(h) or not self._same_polaris_process(inv,h) or h==inv:
                    return None
                r=self._rect(h)
                if not (150<=r.width<=430 and 105<=r.height<=340):
                    return None
                dx=abs(((r.left+r.right)//2)-((o.left+o.right)//2))
                dy=abs(r.top-o.bottom)
                if dx>430 or dy>300:
                    return None
                cls=self._class_name(h).upper()
                bonus=-90 if ('CALENDAR' in cls or 'POPUP' in cls or 'DATE' in cls) else 0
                return dx+dy+bonus
            except Exception:
                return None

        while time.time()<end:
            found=[]
            def top_cb(h,_):
                s=score(h)
                if s is not None:
                    found.append((s,h))
                return True
            try:
                win32gui.EnumWindows(top_cb,None)
            except Exception:
                pass

            def child_cb(h,_):
                s=score(h)
                if s is not None:
                    found.append((s,h))
                return True
            try:
                win32gui.EnumChildWindows(inv,child_cb,None)
            except Exception:
                pass

            for px,py in (
                ((o.left+o.right)//2,o.bottom+35),
                (o.right-10,o.bottom+55),
            ):
                try:
                    cur=win32gui.WindowFromPoint((px,py))
                    for _ in range(6):
                        if not cur:
                            break
                        s=score(cur)
                        if s is not None:
                            found.append((s,cur))
                        cur=win32gui.GetParent(cur)
                except Exception:
                    pass

            if found:
                found.sort(key=lambda item:item[0])
                seen=set()
                for _,h in found:
                    if h not in seen:
                        return h
                    seen.add(h)
            time.sleep(.12)

        self._screenshot_error("calendario_no_detectado")
        raise PolarisError("No pude identificar de forma segura el calendario de Polaris.")

    def _calendar_click_rel(self, popup, xrel, yrel, label, wait=.28):
        r=self._rect(popup)
        x=r.left+int(r.width*float(xrel))
        y=r.top+int(r.height*float(yrel))
        try:
            under=win32gui.WindowFromPoint((x,y))
            if under and not self._same_polaris_process(popup,under):
                raise PolarisError("Otra aplicación cubre el calendario de Polaris.")
        except PolarisError:
            raise
        except Exception:
            pass
        pyautogui.moveTo(x,y,duration=.12)
        pyautogui.click()
        time.sleep(wait)
        self.diag.evento(
            'FACTURACION: fecha','PASO_CALENDARIO',label,
            {'paso':label,'x_rel':round(float(xrel),3),'y_rel':round(float(yrel),3)}
        )

    def _date_hwnd_at_point(self, inv):
        """Devuelve el HWND interior bajo la zona de texto de Fecha.

        TcxDateEdit suele exponer varios HWND anidados. Para Fecha NO se exige un
        único Edit: WindowFromPoint ya nos da el control interior real visible.
        """
        self._activate(inv)
        r=self._rect(inv)
        pt=self.FACTURA["fecha"]
        x=r.left+int(r.width*pt[0])
        y=r.top+int(r.height*pt[1])
        h=win32gui.WindowFromPoint((x,y))
        if not h or not self._is_descendant_or_same(inv,h):
            self._screenshot_error("fecha_control_no_encontrado")
            raise PolarisError("No pude localizar el control visible de Fecha en Polaris.")
        if not self._same_polaris_process(inv,h):
            self._screenshot_error("fecha_control_otro_proceso")
            raise PolarisError("Otra aplicación está cubriendo el campo Fecha.")
        self.diag.evento(
            'FACTURACION: fecha','CONTROL_FECHA_LOCALIZADO',
            'Control interior de Fecha localizado por punto visible.',
            {'clase':self._class_name(h)}
        )
        return h

    def _read_date_control(self, inv):
        """Lee Fecha sin pasar por la validación estricta de Edit único."""
        h=self._date_hwnd_at_point(inv)
        value=''
        try:
            value=self._captura_factura().texto.leer(h)
        except Exception:
            try:
                value=win32gui.GetWindowText(h) or ''
            except Exception:
                value=''
        value=str(value or '').strip()
        self.diag.evento(
            'FACTURACION: fecha','LECTURA_FECHA',
            'Lectura del control Fecha.',
            {'vacio':not bool(value),'longitud':len(value),'clase':self._class_name(h)}
        )
        return h,value

    def _set_ticket_date(self, inv, fecha_ticket):
        """Selecciona la fecha con el calendario nativo de Polaris, como en el video."""
        from datetime import datetime, date
        from .parser import normalize_ticket_date, ticket_date_display
        from .factura_captura import coincide

        iso=normalize_ticket_date(fecha_ticket)
        if not iso:
            raise PolarisError("La fecha del ticket está vacía.")
        target=datetime.strptime(iso,'%Y-%m-%d').date()
        visible=ticket_date_display(iso)

        self.etapa_alerta="FACTURACION: fecha del ticket"
        self.log(f"Fecha del ticket: seleccionando {visible} con el calendario nativo de Polaris...")
        self._activate(inv)

        inner,current_text=self._read_date_control(inv)
        current=None
        for fmt in ('%d/%m/%Y','%d-%m-%Y','%Y-%m-%d'):
            try:
                current=datetime.strptime(current_text,fmt).date()
                break
            except ValueError:
                pass
        if current is None:
            current=date.today()
            self.diag.evento(
                'FACTURACION: fecha','AVISO',
                'No se pudo leer la fecha inicial; se usó el año actual sólo para navegación.',
                {'fecha_inicial_legible':False}
            )

        # Abrir la flecha visible del TcxDateEdit directamente. No dependemos de
        # identificar el wrapper exterior, porque DevExpress expone varios HWND.
        ir=self._rect(inv)
        ax=ir.left+int(ir.width*self.FACTURA["fecha_flecha"][0])
        ay=ir.top+int(ir.height*self.FACTURA["fecha_flecha"][1])

        popup=None
        for intento in range(1,3):
            self.log(f"Fecha: abriendo calendario nativo de Polaris (intento {intento})...")
            pyautogui.moveTo(ax,ay,duration=.12)
            pyautogui.click()
            time.sleep(.35)
            try:
                popup=self._calendar_popup(inv,inner,timeout=1.4)
                break
            except Exception:
                popup=None
                time.sleep(.20)

        if not popup:
            self._screenshot_error("fecha_calendario_no_abre")
            self.diag.evento(
                'FACTURACION: fecha','ERROR',
                'La flecha de Fecha fue pulsada pero el calendario no apareció.',
                {'fecha_objetivo':visible}
            )
            raise PolarisError("Polaris no abrió el calendario de Fecha. No se continuará al folio.")

        self.diag.evento(
            'FACTURACION: fecha','CALENDARIO_ABIERTO',
            'Calendario nativo de Polaris detectado.',
            {'fecha_objetivo':visible}
        )

        # Mismo gesto del video: encabezado mes/año -> vista de meses del año.
        self._calendar_click_rel(popup,.50,.105,'abrir vista de meses/año',wait=.30)

        # Navegar por año usando flechas del calendario.
        diff=target.year-current.year
        if abs(diff)>15:
            self._screenshot_error("fecha_anio_fuera_rango")
            raise PolarisError("La fecha del ticket está demasiado alejada para navegarla de forma segura.")
        if diff:
            x=.955 if diff>0 else .045
            for _ in range(abs(diff)):
                self._calendar_click_rel(popup,x,.105,'cambiar año',wait=.17)

        # Vista de meses 4x3, como se observa en Polaris.
        mi=target.month-1
        mrow,mcol=divmod(mi,4)
        month_x=(.13,.38,.62,.87)[mcol]
        month_y=(.30,.53,.77)[mrow]
        self._calendar_click_rel(popup,month_x,month_y,'seleccionar mes',wait=.34)

        # Vista mensual: domingo primero y seis filas.
        col,row=self._calendar_day_cell(target.year,target.month,target.day)
        day_x=.09 + (.1375*col)
        day_y=.35 + (.0955*row)
        self._calendar_click_rel(popup,day_x,day_y,'seleccionar día',wait=.45)

        # Verificar que el campo Fecha quedó EXACTAMENTE con la fecha solicitada.
        end=time.time()+3.0
        stable=0
        while time.time()<end:
            try:
                _,observed=self._read_date_control(inv)
                match=coincide('fecha',observed,visible)
            except Exception:
                match=False
            stable=stable+1 if match else 0
            if stable>=2:
                break
            time.sleep(.16)

        if stable<2:
            self._screenshot_error("fecha_calendario_no_confirmada")
            self.diag.evento(
                'FACTURACION: fecha','ERROR',
                'El calendario no dejó la fecha solicitada. No se continúa al folio.',
                {'fecha_objetivo':visible,'coincide':False}
            )
            raise PolarisError("La fecha elegida en el calendario no coincide con la fecha del ticket. No se continuará al folio.")

        self._pasos_factura['fecha']=iso
        self.diag.evento(
            'FACTURACION: fecha','OK',
            'Fecha seleccionada con calendario y confirmada.',
            {'fecha_objetivo':visible,'coincide':True}
        )
        self.log("Fecha del ticket seleccionada con el calendario y confirmada. Continuando al folio...")
        return iso

    def _add_ticket(self, inv, ticket):
        """Captura el Folio sin usar Ctrl+A global.

        El video de error mostró que, cuando la referencia de ventana era
        incorrecta, ``Ctrl+A`` no llegaba al campo Folio y Polaris interpretaba
        el atajo a nivel de aplicación, abriendo ``Accesos al Sistema``.

        Desde v2.4:
        - se exige la ventana MDI real de Facturación;
        - se valida que el punto Folio pertenezca a esa ventana;
        - el campo está vacío en una factura nueva, por lo que NO se usa Ctrl+A;
        - se pega el folio directamente y después se pulsa Agregar Folio.
        """
        ticket=str(ticket or "").strip()
        if not ticket:
            raise PolarisError("El Ticket/Folio está vacío.")

        r=self._rect(inv)
        if r.width < 650 or r.height < 450:
            self._screenshot_error("folio_ventana_invalida")
            raise PolarisError(
                f"No voy a capturar el folio porque la referencia de Facturación "
                f"no es la ventana MDI real ({r.width}x{r.height})."
            )

        self.log(f"Capturando Folio del ticket: {ticket}")
        self._activate(inv)

        # Escritura real de teclado: cada carácter genera eventos normales de
        # Windows y después se sale con Tab para que Polaris confirme el valor.
        self._assert_point_inside_window(inv,self.FACTURA["ticket"],"folio_ticket")
        self.log("Folio: escribiendo carácter por carácter (sin portapapeles)...")
        self._captura_factura().escribir_rel(
            inv, self.FACTURA["ticket"], "folio", ticket
        )
        time.sleep(.35)

        self.log("Folio escrito, confirmado y abandonado con Tab. Pulsando Agregar Folio...")
        self._assert_point_inside_window(inv,self.FACTURA["agregar_folio"],"agregar_folio")
        self._click_rel(inv,self.FACTURA["agregar_folio"],wait=.35)
        time.sleep(2.0)

        # Si por algún motivo se abrió "Accesos al Sistema", detectarlo y parar.
        bad=self._find_window_any_level(r"^\s*Accesos al Sistema\s*$",min_width=300,min_height=200)
        if bad:
            self._screenshot_error("accesos_sistema_inesperado")
            raise PolarisError(
                "Polaris abrió 'Accesos al Sistema' inesperadamente. "
                "Se detuvo antes de timbrar; esto indica que el foco no estaba en Folio."
            )

        from .factura_final import PantallaFinalFactura, FacturaYaProcesada
        main=self._find_title(r"Polaris Facturaci")
        final = PantallaFinalFactura(self) if main else None
        aviso = final._aviso(main,{main,inv}) if final else None
        if aviso:
            partes=[win32gui.GetWindowText(aviso) or '']
            try:
                for h in final._children(aviso):
                    cls=self._class_name(h).upper()
                    if not any(k in cls for k in ('EDIT','COMBO')):
                        partes.append(win32gui.GetWindowText(h) or '')
            except Exception:
                partes=[]
            msg=self._norm(' '.join(partes))
            if 'FACTURAD' in msg and any(k in msg for k in ('YA','EXIST','PREVI','ANTERIOR')):
                botones=[]
                for h in final._children(aviso):
                    cls=self._class_name(h).upper(); cap=final._caption(h)
                    if any(k in cls for k in ('BUTTON','BITBTN')) and cap in {'ACEPTAR','OK'}:
                        botones.append(h)
                if len(botones)==1:
                    final._click(main,aviso,botones[0])
                raise FacturaYaProcesada('Polaris indicó que el folio ya está facturado. No se volvió a timbrar.')
            raise PolarisError("Polaris mostró una validación al agregar el folio. Revisa la pantalla; no se repite ni se timbra.")
        if not main:
            raise PolarisError("No se encontró la ventana principal de Polaris después de agregar el folio.")
        self.log("Agregar Folio ejecutado con la captura estable v3.1.")
        self._pasos_factura['folio']=str(ticket)
        self.diag.evento('FACTURACION: folio','OK','Folio escrito, confirmado y agregado',{'vacio':False,'longitud':len(str(ticket))})

    def _copy_current_fresh(self):
        """Copia el texto del control enfocado sin reutilizar portapapeles viejo."""
        try:
            win32clipboard.OpenClipboard()
            try:
                win32clipboard.EmptyClipboard()
            finally:
                win32clipboard.CloseClipboard()
        except Exception:
            pass
        pyautogui.hotkey("ctrl","a")
        pyautogui.hotkey("ctrl","c")
        time.sleep(.20)
        try:
            win32clipboard.OpenClipboard()
            try:
                if win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
                    return (win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT) or "").strip()
                return ""
            finally:
                win32clipboard.CloseClipboard()
        except Exception:
            return ""

    def _send_dialog_estable(self, main, inv, email):
        """Final estable: detectar el diálogo REAL y luego conservar el envío v3.1.

        El diálogo actual de ``Envío e Impresión de CFDI`` es pequeño (aprox.
        220-300 px de ancho). El detector legado ``_wait_text_dialog`` solo aceptaba
        ancestros de más de 450 px, por lo que el diálogo SÍ aparecía en Polaris pero
        el bot lo descartaba hasta agotar el tiempo.

        Se usa el detector específico de ``PantallaFinalFactura`` para esperar el
        diálogo pequeño y, además, reconocer avisos/progreso de CFDI sin volver a
        pulsar Aceptar. Una vez localizado, el envío conserva el flujo v3.1 que ya
        funcionaba en esta instalación.
        """
        from .factura_final import PantallaFinalFactura, FinalFacturaError
        final=PantallaFinalFactura(self)
        try:
            dlg=final._esperar_envio(main,inv)
        except FinalFacturaError as exc:
            self._screenshot_error("sin_dialogo_envio")
            raise PolarisError(str(exc)) from exc

        self.log("Diálogo 'Envío e Impresión de CFDI' detectado y listo.")
        self._activate(dlg)
        self.etapa_alerta="ENVIO_CORREO: seleccionar Enviar por Correo"
        self.log("FINAL 2/4: seleccionando Enviar por Correo.")
        self._assert_point_inside_window(dlg,self.ENVIO["correo_radio"],"enviar_por_correo")
        self._click_rel(dlg,self.ENVIO["correo_radio"],wait=.35)

        self.etapa_alerta="ENVIO_CORREO: revisar destinatario"
        self._assert_point_inside_window(dlg,self.ENVIO["correo"],"correo_destino")
        self._click_rel(dlg,self.ENVIO["correo"],wait=.25)
        current=self._copy_current_fresh()
        if current:
            from .factura_final import validar_correo
            validar_correo(current)
            self.diag.evento('ENVIO_CORREO: destinatario','OK','Polaris ya trae correo válido; se conserva',{'vacio':False,'longitud':len(current)})
            self.log("FINAL 3/4: Polaris ya trae correo; se conserva sin sobrescribir.")
        else:
            email=str(email or "").strip()
            if not email:
                raise PolarisError("El correo está vacío en Polaris y la solicitud no contiene correo destino. No se envió.")
            self.log("FINAL 3/4: correo vacío en Polaris; escribiendo carácter por carácter (sin portapapeles).")
            self._captura_factura().escribir_rel(
                dlg, self.ENVIO["correo"], "correo", email
            )
            time.sleep(.25)
            confirmed=self._copy_current_fresh()
            if not confirmed:
                self.diag.evento('ENVIO_CORREO: destinatario','ERROR','Campo de correo quedó vacío',{'vacio':True})
                raise PolarisError("El correo no quedó escrito en Polaris. No se pulsó Aceptar de envío.")
            from .factura_final import validar_correo
            validar_correo(confirmed)
            self.diag.evento('ENVIO_CORREO: destinatario','OK','Correo escrito y confirmado',{'vacio':False,'longitud':len(confirmed)})

        # Igual que en v3.1: se conservan las selecciones XML/PDF que Polaris ya trae.
        self.etapa_alerta="ENVIO_CORREO: Aceptar envío"
        self.log("FINAL 4/4: pulsando Aceptar de envío.")
        self._assert_point_inside_window(dlg,self.ENVIO["aceptar"],"aceptar_envio")
        self._click_rel(dlg,self.ENVIO["aceptar"],wait=.40)

        end=time.time()+max(8.0,min(90.0,float(self.pcfg.get("espera_envio",30))))
        while time.time()<end:
            if not self._window_exists_visible(dlg):
                self.log("CFDI terminado y envío por correo solicitado en Polaris.")
                return "ENVIO_SOLICITADO"
            time.sleep(.25)
        self._screenshot_error("dialogo_envio_no_cerro")
        raise PolarisError("Se pulsó Aceptar de envío, pero el diálogo no cerró. No se repetirá automáticamente; revisa Polaris.")

    def _guardia_campos_solicitud(self, sol, test_mode):
        efectivos={
            'estacion':str(getattr(sol,'estacion','') or '').strip(),
            'rfc':str(getattr(sol,'rfc','') or '').strip(),
            'ticket':str(getattr(sol,'ticket','') or '').strip(),
            'fecha_ticket':str(getattr(sol,'fecha_ticket','') or '').strip(),
            'forma_pago':str(getattr(sol,'forma_pago','') or 'EFECTIVO').strip(),
            'uso_cfdi':str(getattr(sol,'uso_cfdi','') or 'GASTOS EN GENERAL.').strip(),
        }
        vacios=[k for k,v in efectivos.items() if not v]
        if not test_mode:
            correo=str(getattr(sol,'correo_destino','') or getattr(sol,'remitente','') or '').strip()
            if not correo:vacios.append('correo_destino')
        self.diag.evento('FACTURACION: guardia inicial','OK' if not vacios else 'ERROR',
                          'Validación de datos recibidos',{'campos_vacios':vacios,'modo_seguro':bool(test_mode)})
        if vacios:
            raise PolarisError('Solicitud incompleta. Campos vacíos: '+', '.join(vacios)+'. No se abrió Polaris.')

    def _guardia_antes_aceptar(self, sol):
        faltan=[]
        if not self._pasos_factura.get('cliente'):faltan.append('cliente/RFC')
        if not self._pasos_factura.get('pago'):faltan.append('forma de pago')
        if not self._pasos_factura.get('cfdi'):faltan.append('Uso CFDI')
        if not self._pasos_factura.get('fecha'):faltan.append('fecha del ticket')
        if not self._pasos_factura.get('folio'):faltan.append('folio')
        self.diag.evento('TIMBRADO: guardia final','OK' if not faltan else 'ERROR',
                          'Checklist antes de Aceptar',{'faltan':faltan})
        if faltan:
            raise PolarisError('Guardia final: faltan pasos confirmados ('+', '.join(faltan)+'). No se pulsó Aceptar.')

    @diagnosticar_operacion('FACTURACION')
    def invoice(self, sol, test_mode=True):
        self.etapa_alerta="FACTURACION: comprobaciones previas"
        self._pasos_factura={}
        self._guardia_campos_solicitud(sol,test_mode)
        from .factura_final import validar_correo
        if not isinstance(test_mode, bool):
            raise PolarisError("El modo debe ser explícitamente seguro o producción.")
        with self._lock:
            self._captura317=None
            if not test_mode:
                validar_correo(sol.correo_destino or sol.remitente)
                if self.cfg.get("app", {}).get("modo_prueba", True) is not False:
                    raise PolarisError("MODO SEGURO activo; no se iniciará una factura real.")

            self.etapa_alerta="FACTURACION: preparar Polaris / estación"
            main=self.ensure_ready()
            main=self._change_station(main,sol.estacion)
            inv=self._open_cash_invoice(main)

            self.etapa_alerta="FACTURACION: buscar cliente por RFC"
            inv=self._select_client(inv,sol.rfc)

            self.etapa_alerta="FACTURACION: forma de pago y Uso CFDI"
            payment_target=self._set_payment(inv,sol.forma_pago)
            if payment_target in ("TARJETA DE CREDITO","TARJETA DE DEBITO"):
                self.log("Tarjeta seleccionada. Cuenta queda VACÍA; no se toca ni se valida.")
                time.sleep(.20)
            cfdi_target=self._set_cfdi_usage(inv,sol.uso_cfdi)
            if cfdi_target and 'cfdi' not in self._pasos_factura:self._pasos_factura['cfdi']=cfdi_target

            self._set_ticket_date(inv,sol.fecha_ticket)
            self.log("Continuando a Folio del ticket...")
            self.etapa_alerta="FACTURACION: agregar folio"
            self._add_ticket(inv,sol.ticket)
            self._verificar_captura(inv,sol)
            self._guardia_antes_aceptar(sol)

            if test_mode:
                self.log("MODO SEGURO: folio agregado correctamente. NO se presionó el Aceptar final y NO se timbró.")
                return "PRUEBA_OK"

            self.etapa_alerta="TIMBRADO: Aceptar factura"
            self.log("FINAL 1/4: pulsando Aceptar final para timbrar CFDI.")
            self._assert_point_inside_window(inv,self.FACTURA["aceptar"],"aceptar_final")
            self._click_rel(inv,self.FACTURA["aceptar"],wait=.45)
            return self._send_dialog_estable(main,inv,sol.correo_destino or sol.remitente)

