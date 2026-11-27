"""Offline tests for central SQL mirror. No server or credentials required."""
import json
from datetime import datetime, timezone

import pytest

from arybot import sql_central as sql


def sample(**overrides):
    row = dict(id="abc123", tipo="FACTURA", origen="Portal", seguro=0,
               estado="ENVIO_SOLICITADO", creado=1700000000, actualizado=1700000030,
               iniciado=1700000005, terminado=1700000030,
               datos=json.dumps(dict(estacion="ARY VI", ticket="000123", fecha_ticket="2023-11-14",
                    forma_pago="TARJETA DE CRÉDITO", uso_cfdi="GASTOS EN GENERAL.",
                    rfc="AAA010101AAA", razon_social="PRIVADO", correo_destino="cliente@example.com")))
    row.update(overrides)
    return row


def test_only_allowlisted_operational_fields():
    row = sql.to_record(sample())
    assert row[:10] == ("abc123","PORTAL","FACTURA","ARY_VI","000123","2023-11-14",
                       "TARJETA DE CREDITO","G03",False,"ENVIO_SOLICITADO")
    assert "AAA010101AAA" not in str(row)
    assert "cliente@example.com" not in str(row)
    assert "PRIVADO" not in str(row)
    assert row[10] == datetime.fromtimestamp(1700000000, timezone.utc).replace(tzinfo=None)


def test_bad_rows_are_not_invented():
    assert sql.to_record(sample(datos="{}")) is None
    assert sql.to_record(sample(estado="TIMBRADA")) is None
    assert sql.to_record(sample(terminado=1)) is None
    row = sql.to_record(sample(datos=json.dumps({"estacion":"ARY VI","forma_pago":"secreto"})))
    assert row[4] is None and row[6] is None


def test_connection_string_requires_tls_and_fixed_database():
    string = sql.connection_string(sql.Settings("sql.example.local"), "fake};DATABASE=Other")
    assert "DATABASE=PolarisBot;" in string
    assert "PWD={fake}};DATABASE=Other};" in string
    assert "Encrypt=yes;TrustServerCertificate=no;" in string
    temporary = sql.connection_string(sql.Settings("sql.example.local"), "fake", test_untrusted=True)
    assert "Encrypt=yes;TrustServerCertificate=yes;" in temporary
    assert "TrustServerCertificate=no;" in sql.connection_string(sql.Settings("sql.example.local"), "fake")
    with pytest.raises(sql.SqlError):
        sql.connection_string(sql.Settings("host;DATABASE=Other"), "fake")


def test_admin_never_used_for_sync(tmp_path):
    with pytest.raises(sql.SqlError, match="POLARISBOT_APP"):
        sql.synchronize(sql.Settings("localhost",1433,"sa"),"fake",tmp_path/"missing.db")
    with pytest.raises(sql.SqlError):
        sql.save_settings(tmp_path,sql.Settings("localhost",1433,"sa"),"fake")


def test_save_admin_diagnostic_in_local_vault_only(tmp_path, monkeypatch):
    class Vault:
        def __init__(self):
            self.values = {}
        def set_password(self, service, account, password):
            self.values[service, account] = password
        def get_password(self, service, account):
            return self.values.get((service, account))
    vault = Vault()
    monkeypatch.setattr(sql, "_vault", lambda: vault)
    settings = sql.Settings("sql.example.local", 1433, "sa", True)
    sql.save_settings(tmp_path, settings, "fake-secret", diagnostic=True)
    assert sql.load_settings(tmp_path) == settings
    assert sql.get_password(settings) == "fake-secret"
    assert "fake-secret" not in sql.config_path(tmp_path).read_text()
    with pytest.raises(sql.SqlError, match="POLARISBOT_APP"):
        sql.synchronize(settings, "fake-secret", tmp_path/"missing.db")
    assert "TrustServerCertificate=no;" in sql.connection_string(settings, "fake-secret")

