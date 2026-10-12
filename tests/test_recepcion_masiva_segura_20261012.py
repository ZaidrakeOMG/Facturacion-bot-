"""Simulación segura de 20, 50 y 100 solicitudes detrás de IIS.

Ejecuta el WSGI y SQLite temporal SIN red, sin Polaris y sin timbrado real.
La IP de cada navegador se inyecta sólo mediante el proxy IIS confiable.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import date
import io
import json

import pytest

from arybot.cola_operaciones import ColaOperaciones
from arybot.portal_http import PortalWSGI, DEFAULTS
from arybot.portal_red import PortalRedWSGI


ORIGEN = "https://www.grupoary.com.mx"
PROXY = "10.20.20.154"


class PolarisProhibido:
    def __getattr__(self, name):
        raise AssertionError("La prueba de recepción NO debe abrir Polaris: "+name)


@pytest.fixture
def simulado(tmp_path):
    config = {"app": {"modo_prueba": True}, "cola": {"avisar_cliente": False}}
    settings = dict(
        DEFAULTS,
        escuchar_en="10.20.20.182",
        proxy_confiable=PROXY,
        origenes_permitidos=[ORIGEN],
        https_publico=True,
        permitir_facturacion_real=False,
        max_solicitudes_pendientes=100,
    )
    cola = ColaOperaciones(tmp_path, config, PolarisProhibido(), lambda *args: None)
    backend = PortalWSGI(cola, config, settings, tmp_path, secret=b"T" * 48)
    app = PortalRedWSGI(backend, settings)
    return cola, backend, settings, app


def pedir(app, ip, method, route, *, body=None, cookie="", csrf="", request_id="a" * 32,
          peer=PROXY, extra=None):
    raw = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else b""
    environ = {
        "PATH_INFO": "/api/facturacion/" + route,
        "REQUEST_METHOD": method,
        "REMOTE_ADDR": peer,
        "CONTENT_TYPE": "application/json",
        "CONTENT_LENGTH": str(len(raw)),
        "wsgi.input": io.BytesIO(raw),
        "HTTP_ORIGIN": ORIGEN,
        "HTTP_COOKIE": cookie,
        "HTTP_X_ARY_CSRF": csrf,
        "HTTP_X_ARY_REQUEST_ID": request_id,
        "HTTP_X_ARY_MODE": "seguro",
        "HTTP_X_ARY_CLIENT_IP": ip,
        "HTTP_X_ARY_PROXY_HTTPS": "on",
    }
    environ.update(extra or {})
    response = {}

    def start(status, headers):
        response["code"] = int(status.split()[0])
        response["headers"] = dict(headers)

    result = b"".join(app(environ, start))
    data = json.loads(result.decode("utf-8"))
    return response["code"], response["headers"], data


def factura(folio):
    email = "prueba@example.com"
    return {
        "tipo": "FACTURA",
        "estacion": "ARY I",
        "rfc": "ABC010101AB1",
        "ticket": str(folio),
        "fecha_ticket": date.today().strftime("%d/%m/%Y"),
        "forma_pago": "TARJETA DE DEBITO",
        "uso_cfdi": "G03",
        "correo": email,
        "correo_confirmacion": email,
        "confirmado": True,
        "sitio_web": "",
    }


def navegador(app, ip, serial):
    status, headers, payload = pedir(app, ip, "GET", "opciones")
    assert status == 200, (status, payload)
    return (ip, headers["Set-Cookie"].split(";", 1)[0], payload["csrf"], f"{serial:032x}")


def enviar(app, user, folio, *, extra=None):
    ip, cookie, csrf, request_id = user
    return pedir(app, ip, "POST", "solicitudes", body=factura(folio),
                 cookie=cookie, csrf=csrf, request_id=request_id, extra=extra)


@pytest.mark.parametrize("cantidad", (20, 50, 100))
def test_clientes_con_ip_distinta_se_registran_hasta_capacidad(simulado, cantidad):
    cola, _, _, app = simulado
    for i in range(cantidad):
        ip = f"198.51.100.{i + 10}"
        user = navegador(app, ip, i + 1)
        status, _, data = enviar(app, user, 800000 + i)
        assert status == 201, (i, status, data)
    assert cola.store.status()["conteos"]["EN_COLA"] == cantidad
    assert cola.store.status()["activa"] == ""
    # No hay consumidor de Polaris en esta simulación: las solicitudes están pendientes.


def test_solicitud_101_recibe_503_sin_perder_las_100(simulado):
    cola, _, _, app = simulado
    for i in range(100):
        user = navegador(app, f"198.51.100.{i + 10}", i + 1)
        assert enviar(app, user, 810000 + i)[0] == 201
    otro = navegador(app, "198.51.100.111", 102)
    code, _, result = enviar(app, otro, 999101)
    assert code == 503
    assert "cola" in result["error"].lower()
    assert cola.store.status()["conteos"]["EN_COLA"] == 100


def test_20_clientes_independientes_en_paralelo(simulado):
    cola, _, _, app = simulado
    browsers = [navegador(app, f"198.51.100.{i + 10}", i + 1) for i in range(20)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda row: enviar(app, row[1], 820000 + row[0]),
                                enumerate(browsers)))
    assert [code for code, _, _ in results] == [201] * 20
    assert cola.store.status()["conteos"]["EN_COLA"] == 20


def test_misma_ip_se_restringe_a_ocho_envios_por_defecto(simulado):
    cola, _, _, app = simulado
    ip = "198.51.100.77"
    results = []
    for i in range(9):
        user = navegador(app, ip, i + 1)
        results.append(enviar(app, user, 830000 + i)[0])
    assert results == [201] * 8 + [429]
    assert cola.store.status()["conteos"]["EN_COLA"] == 8


def test_limite_configurado_30_admite_20_usuarios_tras_nat(simulado):
    cola, _, settings, app = simulado
    # Una modificación deliberada de la configuración del bot, nunca un header del navegador.
    settings["limite_envios_10_minutos"] = 30
    ip = "198.51.100.77"
    codes = []
    for i in range(20):
        user = navegador(app, ip, i + 1)
        codes.append(enviar(app, user, 840000 + i)[0])
    assert codes == [201] * 20
    assert cola.store.status()["conteos"]["EN_COLA"] == 20


def test_no_se_puede_eludir_ip_por_x_forwarded_for(simulado):
    cola, _, _, app = simulado
    ip = "198.51.100.77"
    for i in range(9):
        user = navegador(app, ip, i + 1)
        status, _, _ = enviar(app, user, 850000 + i,
                              extra={"HTTP_X_FORWARDED_FOR": f"203.0.113.{i + 11}"})
        assert status == (201 if i < 8 else 429)
    assert cola.store.status()["conteos"]["EN_COLA"] == 8


def test_encabezados_falsos_no_permiten_saltar_iis(simulado):
    cola, _, _, app = simulado
    code, _, result = pedir(app, "198.51.100.88", "GET", "opciones",
                            peer="10.20.20.200")
    assert code == 403
    assert not cola.store.status()["conteos"].get("EN_COLA", 0)


def test_no_duplica_el_ticket_entre_dos_clientes(simulado):
    cola, _, _, app = simulado
    first = navegador(app, "198.51.100.33", 1)
    second = navegador(app, "198.51.100.44", 2)
    assert enviar(app, first, 861234)[0] == 201
    assert enviar(app, second, 861234)[0] == 409
    assert cola.store.status()["conteos"]["EN_COLA"] == 1
