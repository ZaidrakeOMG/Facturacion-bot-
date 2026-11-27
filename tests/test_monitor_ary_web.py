"""Tests aislados: el monitor NO debe escribir/modificar los datos de la cola."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from MONITOR_ARY_WEB import instantanea, preparar_clave, obtener_clave


class MonitorSoloLecturaTests(unittest.TestCase):
    def test_clave_persistente(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/"clave.txt"
            self.assertTrue(preparar_clave(path))
            first=obtener_clave(path)
            self.assertFalse(preparar_clave(path))
            self.assertEqual(first,obtener_clave(path))
            self.assertGreaterEqual(len(first),32)

    def test_lectura_sin_mutaciones_y_sin_datos_fiscales(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/"cola.sqlite3"
            with sqlite3.connect(path) as c:
                c.executescript("""
                  CREATE TABLE trabajos(id TEXT,tipo TEXT,origen TEXT,seguro INTEGER,
                    estado TEXT,datos TEXT,creado REAL,actualizado REAL,iniciado REAL,
                    riesgo_cfdi INTEGER,archivado INTEGER);
                  CREATE TABLE avisos(id TEXT,trabajo TEXT,estado TEXT);
                  CREATE TABLE ajustes(clave TEXT,valor TEXT);
                """)
                datos=json.dumps({"estacion":"ARY VI","ticket":"00012345",
                                  "rfc":"SENSIBLE", "correo":"privado@example.test"})
                c.execute("INSERT INTO trabajos VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    ("abcd1234ef567890","FACTURA","Portal web",0,"EN_COLA",
                     datos,1.,2.,None,0,0))
                c.execute("INSERT INTO avisos VALUES (?,?,?)",
                    ("a1","abcd1234ef567890","PENDIENTE"))
                c.execute("INSERT INTO ajustes VALUES (?,?)",("pausada","0"))
            before=path.read_bytes()
            out=instantanea(path)
            self.assertTrue(out["disponible"])
            self.assertEqual(out["conteos"],{"EN_COLA":1})
            self.assertEqual(out["avisos"],{"PENDIENTE":1})
            self.assertFalse(out["pausada"])
            self.assertEqual(out["trabajos"][0]["folio"],"••••2345")
            payload=json.dumps(out)
            self.assertNotIn("SENSIBLE",payload)
            self.assertNotIn("privado@example.test",payload)
            self.assertEqual(before,path.read_bytes())

    def test_base_inexistente(self):
        with tempfile.TemporaryDirectory() as d:
            out=instantanea(Path(d)/"inexistente.sqlite3")
            self.assertFalse(out["disponible"])


if __name__=="__main__":
    unittest.main()
