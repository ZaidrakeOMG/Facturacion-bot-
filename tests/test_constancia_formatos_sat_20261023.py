"""Variantes de nombres del SAT y controles para evitar facturar a otra persona."""
import pytest
from arybot.constancia_fiscal import _validar_identidad, ConstanciaError


def texto(campos, rfc="ABC010101ABC"):
    return ("\nConstancia de Situación Fiscal\n"
            "Datos de Identificación del Contribuyente\n"
            f"RFC: {rfc}\n{campos}\n"
            "Datos del domicilio registrado\nNombre de Vialidad: JUAREZ\n")


@pytest.mark.parametrize("campo", [
    "Denominación/Razón Social: COMERCIALIZADORA DEL NORTE SA DE CV",
    "Denominación / Razón Social : COMERCIALIZADORA DEL NORTE SA DE CV",
    "Razón Social: COMERCIALIZADORA DEL NORTE SA DE CV",
    "Denominación o Razón Social:\nCOMERCIALIZADORA DEL NORTE SA DE CV",
])
def test_persona_moral_etiquetas_distintas(campo):
    identidad = _validar_identidad(texto(campo))
    assert identidad.razon_social == "COMERCIALIZADORA DEL NORTE SA DE CV"


@pytest.mark.parametrize("campo", [
    "Nombre(s): ANA MARIA\nPrimer Apellido: GARCIA\nSegundo Apellido: LOPEZ",
    "Nombre (s) : ANA MARIA\nApellido Paterno: GARCIA\nApellido Materno: LOPEZ",
    "Nombres\nANA MARIA\nApellido Paterno\nGARCIA\nApellido Materno\nLOPEZ",
])
def test_persona_fisica_diferentes_nombres_y_apellidos(campo):
    identidad = _validar_identidad(texto(campo))
    assert identidad.razon_social == "ANA MARIA GARCIA LOPEZ"


def test_nombre_completo_explicito():
    assert _validar_identidad(texto("Nombre completo: ANA MARIA GARCIA LOPEZ")).razon_social == \
           "ANA MARIA GARCIA LOPEZ"


def test_no_adivina_apellido_ausente():
    with pytest.raises(ConstanciaError, match="nombre o razón social"):
        _validar_identidad(texto("Nombre(s): ANA MARIA\nCódigo Postal: 27000"))


def test_multiple_rfc_distintos_se_detienen():
    with pytest.raises(ConstanciaError, match="más de un RFC"):
        _validar_identidad(texto("Razón Social: EMPRESA DEMO SA DE CV\nRFC: XYZ010101XYZ"))
