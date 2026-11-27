"""Device analytics regressions: no raw UA, no leaks, no duplicates, no invented history."""
import json
import unittest
from arybot.dispositivo_origen import clasificar, datos_equivalentes
from monitor_reportes import generar_reporte


class DeviceAnalyticsTests(unittest.TestCase):
    def test_classifies_phone_pc_tablet_and_unknown(self):
        cases=[
            ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X)", "", "telefono"),
            ("Mozilla/5.0 (Linux; Android 15; Pixel 9) AppleWebKit/537.36 Chrome/120 Mobile Safari/537.36", "?1", "telefono"),
            ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36", "?0", "computadora"),
            ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Safari/605", "", "computadora"),
            ("Mozilla/5.0 (iPad; CPU OS 16_4 like Mac OS X)", "?1", "tableta"),
            ("Mozilla/5.0 (Linux; Android 14; SM-T230) Chrome/120 Safari/537.36", "?0", "tableta"),
            ("", "", "no_identificado"),
            ("some-bot/1.1", "", "no_identificado"),
        ]
        for ua,hint,expected in cases:
            self.assertEqual(clasificar(ua,hint),expected)

    def test_fiscal_identity_does_not_change_with_device(self):
        common={"estacion":"ARY VI","ticket":"000123","rfc":"EJEMPLO123","forma_pago":"EFECTIVO"}
        self.assertTrue(datos_equivalentes(
            {**common,"dispositivo_origen":"telefono"}, {**common,"dispositivo_origen":"computadora"}
        ))
        self.assertTrue(datos_equivalentes(common,{**common,"dispositivo_origen":"telefono"}))
        self.assertFalse(datos_equivalentes(common,{**common,"ticket":"000124"}))

    def test_real_success_only_and_older_history_unknown(self):
        def job(idx, device, state, *, mode=False, kind="FACTURA",origin="Portal web"):
            data={"estacion":"ARY VI"}
            if device is not None:
                data["dispositivo_origen"]=device
            return {
                "id":"f"+str(idx)*16,"datos":json.dumps(data),"tipo":kind,"origen":origin,
                "seguro":int(mode),"estado":state,"motivo":"","riesgo_cfdi":0,
                "creado":1723456789,"actualizado":1723456790,
            }
        rows=[
            job(1,"telefono","ENVIO_SOLICITADO"),
            job(2,"computadora","ENVIO_SOLICITADO"),
            job(3,"telefono","YA_FACTURADO"),
            job(4,"tableta","ENVIO_SOLICITADO"),
            job(5,None,"ENVIO_SOLICITADO"),
            job(6,"telefono","REVISION_REQUERIDA"),
            job(7,"telefono","CANCELADA"),
            job(8,"telefono","ENVIO_SOLICITADO",mode=True),
            job(9,"telefono","ALTA_CONFIRMADA",kind="ALTA"),
            job(0,"telefono","YA_FACTURADO",origin="Manual"),
        ]
        report=generar_reporte(rows,ahora=1723456790)
        d=report["dispositivos"]
        self.assertEqual(d["telefono"]["si"],2)
        self.assertEqual(d["telefono"]["total"],4)
        self.assertEqual(d["computadora"]["si"],1)
        self.assertEqual(d["tableta"]["si"],1)
        self.assertEqual(d["no_identificado"]["si"],2)
        self.assertEqual(d["no_identificado"]["total"],2)
        self.assertEqual(sum(v["si"] for v in d.values()),report["facturas"]["si"])
        self.assertEqual(sum(v["total"] for v in d.values()),sum(
            report["facturas"][key] for key in ("si","no","pendiente","revision")
        ))
        self.assertNotIn("User-Agent",json.dumps(report))
        self.assertNotIn("Windows NT",json.dumps(report))


if __name__=="__main__":
    unittest.main()
