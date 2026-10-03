from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pytest
from arybot import compat_vm_rapido as fast
from arybot import compat_vm as base
from arybot import polaris as old
import iniciar_rapido as entry


@pytest.fixture
def scene(tmp_path, monkeypatch):
    s = dict(rect=(0,0,1616,868), title='Polaris Facturacion ARY',
             enabled=True, visible=True, iconic=False, foreground=1,
             main=1, login=None, pid=10, now=100.0)
    b = object.__new__(fast.PolarisBotRapido)
    b.base=tmp_path; b.log=Mock(); b._estable_rapido=None
    b.pcfg={'ventana_principal_regex':'main', 'ventana_login_regex':'login'}
    b._es_principal=lambda h:h==1
    b._rect=lambda h:old.Rect(*s['rect'])
    b._pid_window=lambda h:s['pid'] if h==1 else 99
    b._find_title=lambda regex:s['main'] if regex=='main' else s['login']
    b._validar_misma_sesion_windows=Mock()
    b._ajustar_principal=Mock()
    wg=SimpleNamespace(IsWindow=lambda h:bool(h), IsWindowVisible=lambda h:s['visible'],
                       IsWindowEnabled=lambda h:s['enabled'], IsIconic=lambda h:s['iconic'],
                       GetForegroundWindow=lambda:s['foreground'], GetClassName=lambda h:'TForm_Menu',
                       GetWindowText=lambda h:s['title'], WindowFromPoint=lambda xy:1)
    pa=SimpleNamespace(failSafeCheck=Mock(), click=Mock(), PAUSE=.16, FAILSAFE=True)
    clock=SimpleNamespace(monotonic=lambda:s['now'], perf_counter=lambda:s['now'],
                          sleep=Mock(side_effect=lambda t:s.update(now=s['now']+t)),
                          strftime=lambda *a:'2026-10-03 12:00:00')
    monkeypatch.setattr(old,'win32gui',wg,raising=False)
    monkeypatch.setattr(old,'pyautogui',pa,raising=False)
    monkeypatch.setattr(fast,'time',clock)
    return b,s,wg,pa,clock


def test_recent_validated_window_can_be_reused(scene):
    b,s,wg,pa,clock=scene
    assert b._guardar_estabilidad(1)==1
    assert b._principal_reciente()==1
    b._validar_misma_sesion_windows.assert_called_once_with(1)
    pa.failSafeCheck.assert_called_once()


@pytest.mark.parametrize('key,value',[('rect',(0,0,1700,900)),('title','Polaris Facturacion OTRA'),
    ('enabled',False),('visible',False),('iconic',True),('login',2),('main',2),('pid',99),
    ('now',102.0),('now',99.0)])
def test_recent_invalidated_after_any_change(scene,key,value):
    b,s,wg,pa,clock=scene
    b._guardar_estabilidad(1);s[key]=value
    assert b._principal_reciente() is None


def test_no_cache_for_splash_small_window(scene):
    b,s,*_=scene;s['rect']=(0,0,300,200)
    b._guardar_estabilidad(1)
    assert b._principal_reciente() is None


def test_permission_change_is_not_ignored(scene):
    b,*_=scene;b._guardar_estabilidad(1)
    b._validar_misma_sesion_windows.side_effect=old.PolarisError('permisos')
    with pytest.raises(old.PolarisError):b._principal_reciente()


def test_failed_stability_invalidates_old_cache(scene,monkeypatch):
    b,*_=scene;b._guardar_estabilidad(1)
    monkeypatch.setattr(old.PolarisBot,'_esperar_principal_estable',Mock(side_effect=old.PolarisError('no listo')))
    with pytest.raises(old.PolarisError):b._esperar_principal_estable()
    assert b._estable_rapido is None


def test_login_stability_not_repeated_when_nothing_changed(scene,monkeypatch):
    b,s,wg,pa,clock=scene
    wait=Mock(return_value=1)
    monkeypatch.setattr(old.PolarisBot,'_esperar_principal_estable',wait)
    b._open=Mock(return_value='shell_login')
    b._esperar_login_o_principal_listo=Mock(return_value=('login',2))
    b._login=lambda:b._esperar_principal_estable(timeout=60,stable_seconds=1.8)
    assert b.ensure_ready()==1
    # La comprobación del login se mantuvo; no se repitió después.
    assert wait.call_count==1
    assert wait.call_args.kwargs=={}  # arguments delegated by position
    assert wait.call_args.args==(60,1.8)
    b._esperar_login_o_principal_listo.assert_called_once_with(timeout=90.0,stable_seconds=1.8)


def test_changed_geometry_keeps_full_post_resize_stability(scene,monkeypatch):
    b,s,wg,pa,clock=scene
    wait=Mock(return_value=1)
    monkeypatch.setattr(old.PolarisBot,'_esperar_principal_estable',wait)
    activate=Mock(return_value=1)
    monkeypatch.setattr(old.PolarisBot,'_activar_como_usuario',activate)
    b._open=Mock(return_value='existente')
    b._esperar_login_o_principal_listo=Mock(return_value=('principal',1))
    b._ajustar_principal=lambda h:s.update(rect=(20,20,1636,888))
    assert b.ensure_ready()==1
    wait.assert_called_once_with(25.0,1.2)
    activate.assert_called_once()


def test_old_cache_never_skips_new_operation_readiness(scene,monkeypatch):
    b,*_=scene;b._guardar_estabilidad(1)
    b._open=Mock(side_effect=old.PolarisError('open failed'))
    with pytest.raises(old.PolarisError):b.ensure_ready()
    assert b._estable_rapido is None


@pytest.mark.parametrize('method',['_activar_como_usuario','_activate'])
def test_already_foreground_principal_needs_no_reactivation(scene,monkeypatch,method):
    b,s,wg,pa,clock=scene
    fallback=Mock()
    monkeypatch.setattr(old.PolarisBot,method,fallback)
    getattr(b,method)(1,maximize=True)
    fallback.assert_not_called()
    b._ajustar_principal.assert_called_once_with(1)
    b._validar_misma_sesion_windows.assert_called_once_with(1)


@pytest.mark.parametrize('method',['_activar_como_usuario','_activate'])
@pytest.mark.parametrize('key,value',[('foreground',2),('visible',False),('enabled',False),('iconic',True)])
def test_non_ready_focus_uses_original_activation(scene,monkeypatch,method,key,value):
    b,s,wg,pa,clock=scene;s[key]=value
    fallback=Mock()
    monkeypatch.setattr(old.PolarisBot,method,fallback)
    getattr(b,method)(1,maximize=False)
    fallback.assert_called_once_with(b,1,maximize=False)


def test_login_or_child_activation_remains_original(scene,monkeypatch):
    b,*_=scene;fallback=Mock()
    monkeypatch.setattr(old.PolarisBot,'_activate',fallback)
    b._activate(2)
    fallback.assert_called_once_with(b,2,maximize=False)


@pytest.mark.parametrize('kind',['lost_focus','occluded','invalid_rect','failsafe'])
def test_menu_safety_barriers_block_click(scene,kind):
    b,s,wg,pa,clock=scene;rect=(10,20,40,60)
    if kind=='lost_focus':s['foreground']=2
    if kind=='occluded':wg.WindowFromPoint=lambda xy:2
    if kind=='invalid_rect':rect=(30,40,20,10)
    if kind=='failsafe':pa.failSafeCheck.side_effect=old.PolarisError('stop')
    with pytest.raises(old.PolarisError):b._click_menu_item(1,rect,'Utilerías')
    pa.click.assert_not_called()


def test_menu_pause_reduced_but_global_keyboard_timings_untouched(scene):
    b,s,wg,pa,clock=scene
    b._click_menu_item(1,(10,20,40,60),'Utilerías')
    pa.click.assert_called_once_with(25,40)
    clock.sleep.assert_called_once_with(.12)
    assert pa.PAUSE==.16 and pa.FAILSAFE is True


@pytest.mark.parametrize('method',['_login','invoice','_select_client','_set_payment','_set_cfdi_usage',
    '_set_ticket_date','_add_ticket','_accept_station_row','_accept_series_dialog','_close_polaris_form',
    '_check_cleanup_prompt','_has_unsaved_prompt','_paste','_copy_current','_click_rel'])
def test_business_keyboard_and_safety_methods_unchanged(method):
    assert getattr(fast.PolarisBotRapido,method) is getattr(old.PolarisBot,method)


@pytest.mark.parametrize('method',['_menu_path','_rect_menu_visible','_open_station_catalog'])
def test_menu_detection_and_timeouts_remain_R1(method):
    assert getattr(fast.PolarisBotRapido,method) is getattr(base.PolarisBotVM,method)


def test_cleanup_only_measured_not_rewritten(scene,monkeypatch):
    b,*_=scene;parent=Mock(return_value=True)
    monkeypatch.setattr(old.PolarisBot,'_cleanup_before_station_change',parent)
    assert b._cleanup_before_station_change(1) is True
    parent.assert_called_once_with(1)
    record=json.loads((b.base/'logs'/'tiempos_rapido.jsonl').read_text())
    assert record['estado']=='OK' and record['paso']=='Revisar y cerrar ventanas'


def test_errors_recorded_and_propagated_without_retries(scene,monkeypatch):
    b,*_=scene;parent=Mock(side_effect=old.PolarisError('stop'))
    monkeypatch.setattr(old.PolarisBot,'_change_station',parent)
    with pytest.raises(old.PolarisError):b._change_station(1,'ARY I',force=True)
    parent.assert_called_once_with(1,'ARY I',force=True)
    record=json.loads((b.base/'logs'/'tiempos_rapido.jsonl').read_text())
    assert record['estado']=='ERROR'
    assert 'ARY I' not in json.dumps(record)


def test_patch_targets_exact_base_only(tmp_path,monkeypatch):
    monkeypatch.setattr(entry,'EXPECTED',{'original.py':'incorrect'})
    with pytest.raises(RuntimeError):entry.comprobar_base(tmp_path)
    (tmp_path/'original.py').write_text('do not alter')
    with pytest.raises(RuntimeError):entry.comprobar_base(tmp_path)
    assert (tmp_path/'original.py').read_text()=='do not alter'


def test_non_windows_cannot_start_automation(monkeypatch):
    monkeypatch.setattr(entry.sys,'platform','linux')
    with pytest.raises(RuntimeError,match='requiere Windows'):entry.ejecutar()
