"""Tests aislados: el monitor NO debe escribir/modificar los datos de la cola."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from MONITOR_ARY_WEB import instantanea, preparar_clave, obtener_clave, verificar_clave, guardar_clave_interactiva


class MonitorSoloLecturaTests(unittest.TestCase):
    def test_clave_persistente(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/"clave.txt"
            self.assertTrue(preparar_clave(path))
            first=obtener_clave(path)
            self.assertFalse(preparar_clave(path))
            self.assertEqual(first,obtener_clave(path))
            self.assertGreaterEqual(len(first),32)

    def test_cambio_credenciales_guarda_solo_hash(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "clave.txt"
            preparar_clave(path)
            with patch("getpass.getpass", side_effect=["TEST-ONLY-SECRET-DO-NOT-USE", "TEST-ONLY-SECRET-DO-NOT-USE"]):
                guardar_clave_interactiva(path)
            stored = obtener_clave(path)
            self.assertTrue(stored.startswith("pbkdf2_sha256$"))
            self.assertNotIn("TEST-ONLY-SECRET-DO-NOT-USE", stored)
            self.assertTrue(verificar_clave("TEST-ONLY-SECRET-DO-NOT-USE", stored))
            self.assertFalse(verificar_clave("incorrecta", stored))

    def test_router_accepts_query_parameters_without_exposing_other_paths(self):
        # URL de navegador con campana de origen no debe romper la portada.
        # Solo se separa la ruta; la autentificacion se mantiene obligatoria.
        from urllib.parse import urlsplit
        self.assertEqual(urlsplit("/?utm_source=chatgpt.com").path, "/")
        self.assertEqual(urlsplit("/api/estado?refresh=1").path, "/api/estado")
        self.assertEqual(urlsplit("/no-existe?x=1").path, "/no-existe")

    def test_lectura_sin_mutaciones_y_sin_datos_fiscales(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/"cola.sqlite3"
            with sqlite3.connect(path) as c:
                c.executescript("""
                  CREATE TABLE trabajos(id TEXT,tipo TEXT,origen TEXT,seguro INTEGER,
                    estado TEXT,datos TEXT,motivo TEXT,creado REAL,actualizado REAL,iniciado REAL,
                    riesgo_cfdi INTEGER,archivado INTEGER);
                  CREATE TABLE avisos(id TEXT,trabajo TEXT,estado TEXT);
                  CREATE TABLE ajustes(clave TEXT,valor TEXT);
                """)
                datos=json.dumps({"estacion":"ARY VI","ticket":"00012345",
                                  "rfc":"SENSIBLE", "correo":"privado@example.test"})
                c.execute("INSERT INTO trabajos VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    ("abcd1234ef567890","FACTURA","Portal web",0,"EN_COLA",
                     datos,"No enviar RFC SENSIBLE",1.,2.,None,0,0))
                c.execute("INSERT INTO avisos VALUES (?,?,?)",
                    ("a1","abcd1234ef567890","PENDIENTE"))
                c.execute("INSERT INTO ajustes VALUES (?,?)",("pausada","0"))
            before=path.read_bytes()
            out=instantanea(path)
            self.assertTrue(out["disponible"])
            self.assertEqual(out["conteos"],{"EN_COLA":1})
            self.assertEqual(out["avisos"],{"PENDIENTE":1})
            self.assertFalse(out["pausada"])
            self.assertEqual(out["trabajos"][0]["grupo"],"pendiente")
            self.assertEqual(out["reporte"]["facturas"]["pendiente"],1)
            payload=json.dumps(out)
            self.assertNotIn("SENSIBLE",payload)
            self.assertNotIn("privado@example.test",payload)
            self.assertEqual(before,path.read_bytes())


    def test_reporte_distingue_exito_no_incierto_prueba_y_alta(self):
        from monitor_reportes import clasificar, generar_reporte
        self.assertEqual(clasificar("FACTURA", "ENVIO_SOLICITADO", False)[0], "si")
        self.assertEqual(clasificar("FACTURA", "YA_FACTURADO", False)[0], "si")
        self.assertEqual(clasificar("FACTURA", "CANCELADA", False)[0], "no")
        self.assertEqual(clasificar("FACTURA", "REVISION_REQUERIDA", False)[0], "revision")
        self.assertEqual(clasificar("FACTURA", "REVISION_CERRADA", False)[0], "revision")
        self.assertEqual(clasificar("FACTURA", "EN_COLA", False)[0], "pendiente")
        self.assertEqual(clasificar("FACTURA", "ENVIO_SOLICITADO", True)[0], "prueba")
        self.assertEqual(clasificar("ALTA", "ALTA_CONFIRMADA", False)[0], "alta_si")
        self.assertEqual(clasificar("FACTURA", "CANCELADA", False, riesgo_cfdi=True)[0], "revision")

        def job(n, tipo, estado, seguro=False, motivo=""):
            return dict(id="1234567890ab"+str(n), tipo=tipo, origen="Portal web",
                        seguro=int(seguro), estado=estado,
                        datos=json.dumps({"estacion":"ARY VI", "rfc":"SENSIBLE",
                                          "correo":"privado@example.test"}),
                        motivo=motivo, creado=1, actualizado=2, iniciado=None,
                        riesgo_cfdi=0)
        casos=[
            job(1,"FACTURA","ENVIO_SOLICITADO"),
            job(2,"FACTURA","YA_FACTURADO"),
            job(3,"FACTURA","CANCELADA"),
            job(4,"FACTURA","REVISION_REQUERIDA",motivo="RFC SENSIBLE, revisar Polaris"),
            job(5,"FACTURA","EN_COLA"),
            job(6,"FACTURA","PRUEBA_CERRADA",True),
            job(7,"ALTA","ALTA_CONFIRMADA"),
        ]
        out=generar_reporte(casos,ahora=86400*10)
        self.assertEqual(out["facturas"]["si"],2)
        self.assertEqual(out["facturas"]["no"],1)
        self.assertEqual(out["facturas"]["revision"],1)
        self.assertEqual(out["facturas"]["pendiente"],1)
        self.assertEqual(out["facturas"]["prueba"],1)
        self.assertEqual(out["facturas"]["ya_facturado"],1)
        self.assertEqual(out["facturas"]["envio_solicitado"],1)
        self.assertEqual(out["altas"]["alta_si"],1)
        self.assertEqual(out["estaciones"][0]["nombre"],"ARY VI")
        serialized=json.dumps(out)
        self.assertNotIn("SENSIBLE",serialized)
        self.assertNotIn("privado@example.test",serialized)
        self.assertNotIn("RFC SENSIBLE",serialized)
        self.assertEqual(out["motivos_no"][0]["motivo"],"Solicitud cancelada")

    def test_reportes_por_periodo_solo_lectura(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/"cola.sqlite3"
            import time
            now=time.time()
            with sqlite3.connect(path) as c:
                c.executescript("""
                  CREATE TABLE trabajos(id TEXT,tipo TEXT,origen TEXT,seguro INTEGER,
                    estado TEXT,datos TEXT,motivo TEXT,creado REAL,actualizado REAL,
                    iniciado REAL,riesgo_cfdi INTEGER,archivado INTEGER);
                  CREATE TABLE avisos(id TEXT,trabajo TEXT,estado TEXT);
                  CREATE TABLE ajustes(clave TEXT,valor TEXT);
                """)
                for i,created in enumerate((now-2*86400,now-80*86400)):
                    c.execute("INSERT INTO trabajos VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                        (str(i),"FACTURA","Portal web",0,"ENVIO_SOLICITADO",
                         json.dumps({"estacion":"ARY VI"}),"",created,created,None,0,0))
            before=path.read_bytes()
            seven=instantanea(path,periodo="7")
            all_time=instantanea(path,periodo="all")
            self.assertEqual(seven["reporte"]["facturas"]["si"],1)
            self.assertEqual(all_time["reporte"]["facturas"]["si"],2)
            self.assertEqual(before,path.read_bytes())

    def test_base_inexistente(self):
        with tempfile.TemporaryDirectory() as d:
            out=instantanea(Path(d)/"inexistente.sqlite3")
            self.assertFalse(out["disponible"])


if __name__=="__main__":
    unittest.main()
