"""Pruebas sin escritorio Windows real para recuperar foco de Polaris."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from arybot import polaris


def _bot(monkeypatch, *, ventana_inicial):
    activo = [ventana_inicial]
    habilitar_foco = [False]
    clics = Mock()
    alt = Mock(side_effect=lambda key: habilitar_foco.__setitem__(0, True))

    def set_foreground(h):
        if habilitar_foco[0]:
            activo[0] = h

    monkeypatch.setattr(
        polaris, 'win32gui',
        SimpleNamespace(
            IsIconic=lambda h: False,
            ShowWindow=Mock(),
            BringWindowToTop=Mock(),
            SetForegroundWindow=set_foreground,
            GetForegroundWindow=lambda: activo[0],
            WindowFromPoint=lambda pt: 55,
        ), raising=False)
    monkeypatch.setattr(polaris, 'win32con',
                        SimpleNamespace(SW_RESTORE=9, SW_MAXIMIZE=3),
                        raising=False)
    monkeypatch.setattr(polaris, 'pyautogui',
                        SimpleNamespace(press=alt, click=clics),
                        raising=False)
    b = polaris.PolarisBot.__new__(polaris.PolarisBot)
    b._validar_misma_sesion_windows = Mock()
    b._pid_window = lambda h: 123 if h == 55 else (999 if h else None)
    b._screenshot_error = Mock()
    b.log = Mock()
    b._rect = Mock(return_value=polaris.Rect(0, 0, 1000, 800))
    b._is_descendant_or_same = Mock(return_value=True)
    return b, alt, clics


def test_alt_recupera_foco_sin_clic_a_ciegas(monkeypatch):
    bot, alt, clicks = _bot(monkeypatch, ventana_inicial=99)
    assert bot._activar_como_usuario(55) == 55
    alt.assert_called_once_with('alt')
    clicks.assert_not_called()
    bot._screenshot_error.assert_not_called()


def test_sin_escritorio_interactivo_no_envia_clics(monkeypatch):
    bot, alt, clicks = _bot(monkeypatch, ventana_inicial=0)
    with pytest.raises(polaris.PolarisError, match='no tiene ventana en primer plano'):
        bot._activar_como_usuario(55)
    alt.assert_not_called()
    clicks.assert_not_called()
    bot._screenshot_error.assert_called_once_with('polaris_no_primer_plano')
