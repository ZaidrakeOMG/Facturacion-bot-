"""Pruebas sin Windows/Polaris real: selección, menús, límites y arranque."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import ast
import subprocess
import sys
import pytest
from arybot import compat_vm as vm
from arybot import polaris as old
import ejecutar_vm as launch


def bare():
    b=object.__new__(vm.PolarisBotVM)
    b.pcfg={'ventana_principal_regex':'.*Polaris Facturacion.*',
            'ventana_login_regex':'.*Entrada al Sistema.*Polaris.*'}
    b.cfg={'polaris':b.pcfg}
    b.log=Mock()
    return b


@pytest.mark.parametrize('a,b', [('&Utilerías','utilerias'),('Cambio de estación...','Cambio de estación'),
                                ('&Efectivo\tCtrl+E','efectivo'),('FACTURACIÓN','facturacion')])
def test_menu_text(a,b):assert vm.texto_menu(a)==vm.texto_menu(b)


def test_exact_match_only():
    items=[vm.ItemMenu(1,'Clientes de Efectivo',0,0)]
    with pytest.raises(vm.PolarisError):vm.elegir_item(items,'Efectivo')


@pytest.mark.parametrize('state',[1,2,3,0xFFFFFFFF])
def test_disabled_never_forced(state):
    with pytest.raises(vm.PolarisError):vm.elegir_item([vm.ItemMenu(1,'Efectivo',0,state)],'Efectivo')


def test_duplicate_menu_caption_rejected():
    with pytest.raises(vm.PolarisError):vm.elegir_item([vm.ItemMenu(0,'Efectivo',0,0),vm.ItemMenu(1,'&Efectivo',0,0)],'Efectivo')


def test_main_window_ignores_tapplication_and_login(monkeypatch):
    b=bare()
    titles={1:'Polaris Facturacion',2:'Entrada al Sistema de Polaris Facturacion',3:'Polaris Facturacion --> ARY I'}
    classes={1:'TApplication',2:'TForm_Login',3:'TForm_Menu'}
    monkeypatch.setattr(old,'win32gui',SimpleNamespace(GetWindowText=lambda h:titles[h],GetClassName=lambda h:classes[h]),raising=False)
    b._enum=lambda:[1,2,3]
    b._rect=lambda h:old.Rect(0,0,3000 if h==1 else 1600,1800 if h==1 else 900)
    assert b._find_title(b.pcfg['ventana_principal_regex'])==3


def test_no_real_main_returns_none(monkeypatch):
    b=bare()
    monkeypatch.setattr(old,'win32gui',SimpleNamespace(GetWindowText=lambda h:'Polaris Facturacion',GetClassName=lambda h:'TApplication'),raising=False)
    b._enum=lambda:[1]
    assert b._find_title(b.pcfg['ventana_principal_regex']) is None


def test_unrelated_title_search_remains_original(monkeypatch):
    b=bare();b._enum=lambda:[11]
    monkeypatch.setattr(old,'win32gui',SimpleNamespace(GetWindowText=lambda h:'Entrada al Sistema de Polaris Facturacion'),raising=False)
    assert b._find_title(b.pcfg['ventana_login_regex'])==11


@pytest.mark.parametrize('name',['invoice','_login','_select_client','_set_payment','_set_cfdi_usage',
                                 '_set_ticket_date','_add_ticket','_change_station','_accept_station_row',
                                 '_accept_series_dialog','_cleanup_before_station_change'])
def test_capture_logic_inherited_unchanged(name):
    assert getattr(vm.PolarisBotVM,name) is getattr(old.PolarisBot,name)


def test_native_menu_clicks_exact_rectangles(monkeypatch):
    b=bare();b._es_principal=lambda h:True;b._activar_como_usuario=Mock();b._check_cleanup_prompt=Mock()
    b._click_menu_item=Mock();b._pid_window=lambda h:22
    huge=2**42+55
    api=SimpleNamespace(menu=lambda h:huge,items=lambda m:[vm.ItemMenu(7,'&Utilerías',huge+1,0)] if m==huge else [vm.ItemMenu(5,'Cambio de estación...',0,0)],
                        rect=Mock(side_effect=[(350,20,400,40),(350,150,500,180)]))
    monkeypatch.setattr(vm,'MenuWindows',lambda:api)
    monkeypatch.setattr(old,'win32gui',SimpleNamespace(IsWindowEnabled=lambda h:True,WindowFromPoint=lambda xy:22,GetClassName=lambda h:'#32768'),raising=False)
    b._menu_path(22,'Utilerías','Cambio de estación')
    assert b._click_menu_item.call_args_list[0].args==(22,(350,20,400,40),'Utilerías')
    assert b._click_menu_item.call_args_list[1].args==(22,(350,150,500,180),'Cambio de estación')
    assert api.rect.call_args_list[1].args==(None,huge+1,5)


def test_missing_menu_stops_no_coordinate_fallback(monkeypatch):
    b=bare();b._es_principal=lambda h:True;b._activar_como_usuario=Mock();b._check_cleanup_prompt=Mock();b._click_menu_item=Mock()
    monkeypatch.setattr(vm,'MenuWindows',lambda:SimpleNamespace(menu=lambda h:None))
    monkeypatch.setattr(old,'win32gui',SimpleNamespace(IsWindowEnabled=lambda h:True,WindowFromPoint=lambda xy:22,GetClassName=lambda h:'#32768'),raising=False)
    with pytest.raises(vm.PolarisError):b._menu_path(22,'Utilerías','Cambio de estación')
    b._click_menu_item.assert_not_called()


def test_station_menu_not_repeated_if_dialog_missing():
    b=bare();b._menu_path=Mock();b._wait_text_dialog=Mock(return_value=None);b._screenshot_error=Mock()
    with pytest.raises(vm.PolarisError):b._open_station_catalog(12)
    assert b._menu_path.call_count==1


def test_station_menu_returns_real_dialog():
    b=bare();b._menu_path=Mock();b._wait_text_dialog=Mock(return_value=41)
    assert b._open_station_catalog(12)==41


def test_foreground_guard_blocks_click(monkeypatch):
    b=bare();b._pid_window=lambda h:h
    pa=SimpleNamespace(failSafeCheck=Mock(),click=Mock())
    monkeypatch.setattr(old,'pyautogui',pa,raising=False)
    monkeypatch.setattr(old,'win32gui',SimpleNamespace(GetForegroundWindow=lambda:99),raising=False)
    with pytest.raises(vm.PolarisError):b._click_menu_item(1,(1,2,10,20),'Efectivo')
    pa.click.assert_not_called()


def test_occlusion_guard_blocks_click(monkeypatch):
    b=bare();b._pid_window=lambda h:h
    pa=SimpleNamespace(failSafeCheck=Mock(),click=Mock())
    monkeypatch.setattr(old,'pyautogui',pa,raising=False)
    monkeypatch.setattr(old,'win32gui',SimpleNamespace(GetForegroundWindow=lambda:1,WindowFromPoint=lambda xy:99),raising=False)
    with pytest.raises(vm.PolarisError):b._click_menu_item(1,(1,2,10,20),'Efectivo')
    pa.click.assert_not_called()


def test_probe_success_same_executable():
    result=launch.probe(sys.executable,['json'])
    assert not result['errores']
    assert Path(result['exe']).resolve()==Path(sys.executable).resolve()


def test_probe_missing_module_is_explicit():
    result=launch.probe(sys.executable,['module_that_does_not_exist_ary_20261003'])
    assert 'ModuleNotFoundError' in next(iter(result['errores'].values()))


def test_probe_nonexistent_interpreter():
    assert launch.probe('/does/not/exist/python.exe')['errores']


def test_global_python_not_accepted_as_local_venv(tmp_path,monkeypatch):
    monkeypatch.setattr(launch,'ENV',tmp_path/'.venv')
    assert not launch.valid_local_env({'prefix':'/a','base':'/a'})
    assert not launch.valid_local_env({'prefix':'/other/venv','base':'/a'})
    assert launch.valid_local_env({'prefix':str(tmp_path/'.venv'),'base':'/a'})


def app_fragment(tmp_path):
    """Extrae los métodos nuevos: no importa Gmail ni crea una ventana real."""
    src=(Path(vm.__file__).parent/'gui.py').read_text(encoding='utf-8')
    cls=next(n for n in ast.parse(src).body if isinstance(n,ast.ClassDef) and n.name=='App')
    selected=[n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name in {'_exigir_polaris_vm','_preparar_prueba_vm'}]
    module=ast.Module(body=[ast.ClassDef(name='AppTests',bases=[],keywords=[],body=selected,decorator_list=[])],type_ignores=[])
    namespace={'Path':Path}
    exec(compile(ast.fix_missing_locations(module),'<fragment>','exec'),namespace)
    a=namespace['AppTests']()
    a.log=Mock();a.direct_status=Mock();a.estado_vm_var=Mock();a.nb=Mock();a.tab_test=object();a.tab_cfg=object()
    exe=tmp_path/'PolarisFacturacion.exe';exe.touch()
    a.user_var=SimpleNamespace(get=lambda:'FACTURA');a.exe_var=SimpleNamespace(get=lambda:str(exe))
    a.cfg={'polaris':{'usuario':'SUPERVISOR'},'app':{'modo_prueba':True}}
    a.cfgm=Mock();a.polaris=SimpleNamespace(has_password=lambda:True)
    a.cola=SimpleNamespace(ocupada=False,store=SimpleNamespace(status=lambda:{'bloqueo':''}))
    return a


def test_gui_uses_visible_user_not_old_supervisor(tmp_path):
    a=app_fragment(tmp_path)
    assert a._preparar_prueba_vm()
    assert a.polaris.pcfg['usuario']=='FACTURA'
    assert a.cfg['app']['modo_prueba'] is True


def test_gui_no_silent_return_without_module(tmp_path):
    a=app_fragment(tmp_path);a.polaris=None;a._polaris_error='ImportError: win32ui'
    assert not a._preparar_prueba_vm()
    assert 'win32ui' in a.direct_status.set.call_args.args[0]
    a.log.assert_called()


def test_gui_pending_review_not_overridden(tmp_path):
    a=app_fragment(tmp_path);a.cola.store.status=lambda:{'bloqueo':'previous','motivo':'Alta preparada'}
    assert not a._preparar_prueba_vm()
    assert 'Liberar revisión' in a.direct_status.set.call_args.args[0]
    a.cfgm.save.assert_not_called()


def test_gui_missing_credentials_not_ignored(tmp_path):
    a=app_fragment(tmp_path);a.polaris.has_password=lambda:False
    assert not a._preparar_prueba_vm()
    assert 'FACTURA' in a.direct_status.set.call_args.args[0]


def test_gui_busy_not_overridden(tmp_path):
    a=app_fragment(tmp_path);a.cola.ocupada=True
    assert not a._preparar_prueba_vm()
    a.cfgm.save.assert_not_called()


def test_geometry_uses_existing_win32_placement_api(monkeypatch):
    import ctypes
    b=bare()
    current=[0,0,1936,1056]
    class Fn:
        def __init__(self,fn):self.fn=fn
        def __call__(self,*args):return self.fn(*args)
    def fill(mon, ptr):
        info=ptr._obj
        info.rcWork.left=0;info.rcWork.top=0;info.rcWork.right=1920;info.rcWork.bottom=1040
        return 1
    u=SimpleNamespace(MonitorFromWindow=Fn(lambda *args:2**42+1),GetMonitorInfoW=Fn(fill))
    monkeypatch.setattr(vm.ctypes,'WinDLL',lambda *args,**kw:u,raising=False)
    def setpos(h,after,x,y,width,height,flags):current[:]=[x,y,x+width,y+height]
    # Deliberately NO IsZoomed: pywin32 uses GetWindowPlacement.
    wg=SimpleNamespace(GetWindowPlacement=lambda h:(0,3,(0,0),(0,0),(0,0,100,100)),
                       IsIconic=lambda h:False,ShowWindow=Mock(),SetWindowPos=Mock(side_effect=setpos))
    wc=SimpleNamespace(SW_SHOWMAXIMIZED=3,SW_RESTORE=9,SW_MAXIMIZE=3,SWP_NOZORDER=4,SWP_NOACTIVATE=16)
    monkeypatch.setattr(old,'win32gui',wg,raising=False);monkeypatch.setattr(old,'win32con',wc,raising=False)
    monkeypatch.setattr(vm.time,'sleep',lambda n:None)
    b._rect=lambda h:old.Rect(*current)
    b._ajustar_principal(99)
    assert current==[0,0,1616,868]
    assert wg.SetWindowPos.call_args.args[4:6]==(1616,868)


def test_popup_must_be_visible_before_click(monkeypatch):
    b=bare();b._pid_window=lambda h:7
    wg=SimpleNamespace(WindowFromPoint=lambda xy:22,GetClassName=lambda h:'TForm_Menu')
    monkeypatch.setattr(old,'win32gui',wg,raising=False)
    monkeypatch.setattr(vm.time,'sleep',lambda n:None)
    api=SimpleNamespace(rect=lambda *args:(100,100,200,120))
    with pytest.raises(vm.PolarisError):b._rect_menu_visible(api,22,55,0,1,timeout=.005)


def test_two_main_windows_not_selected_arbitrarily(monkeypatch):
    b=bare()
    monkeypatch.setattr(old,'win32gui',SimpleNamespace(GetWindowText=lambda h:'Polaris Facturacion --> ARY',GetClassName=lambda h:'TForm_Menu'),raising=False)
    b._enum=lambda:[1,2];b._rect=lambda h:old.Rect(0,0,1600,900)
    with pytest.raises(vm.PolarisError):b._find_title(b.pcfg['ventana_principal_regex'])
