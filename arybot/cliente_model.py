"""Datos del alta: cuatro campos del cliente + estación del contexto interno.

No consulta servicios web, no importa controladores SQL, no guarda datos personales.
"""
from __future__ import annotations
from dataclasses import dataclass
import hashlib
import re
from .parser import normalize_station, STATIONS


class AltaClienteError(RuntimeError):
    """Validación o protección que impide continuar un alta."""


@dataclass(frozen=True)
class AltaCliente:
    estacion: str
    rfc: str
    idcif: str
    telefono: str
    correo: str

    @classmethod
    def crear(cls, estacion: str, rfc: str, idcif: str, telefono: str, correo: str) -> 'AltaCliente':
        # No transformar números a int: conserva ceros iniciales del idCIF/teléfono.
        vals=(estacion,rfc,idcif,telefono,correo)
        if any(not isinstance(x,str) for x in vals):
            raise AltaClienteError('Los cinco valores deben recibirse como texto.')
        if any(any(ord(ch)<32 for ch in x) for x in vals):
            raise AltaClienteError('No se permiten saltos de línea ni caracteres de control.')
        st=normalize_station(estacion)
        if st not in STATIONS:
            raise AltaClienteError('Selecciona la estación de trabajo; no es un dato fiscal adicional.')
        rf=rfc.strip().upper()
        if not re.fullmatch(r'[A-ZÑ&]{3,4}[0-9]{6}[A-Z0-9]{3}',rf):
            raise AltaClienteError('Revisa el RFC: debe tener 12 o 13 caracteres, sin espacios.')
        if rf in ('XAXX010101000','XEXX010101000'):
            raise AltaClienteError('El RFC genérico no se da de alta con idCIF en este apartado.')
        ci=idcif.strip()
        if not re.fullmatch(r'[0-9]{11}',ci):
            raise AltaClienteError('Captura los 11 dígitos del idCIF, como texto, sin espacios.')
        ph=telefono.strip()
        if not re.fullmatch(r'\+?[0-9 ()-]{7,24}',ph):
            raise AltaClienteError('Revisa el teléfono; usa dígitos y, opcionalmente, +, espacios o guiones.')
        ph=re.sub(r'[ ()-]','',ph)
        if not 7<=len(ph.lstrip('+'))<=15:
            raise AltaClienteError('Revisa la longitud del teléfono (7 a 15 dígitos).')
        em=correo.strip()
        if len(em)>254 or not re.fullmatch(r'[A-Za-z0-9.!#$%&\x27*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,63}',em):
            raise AltaClienteError('Escribe un solo correo válido, por ejemplo cliente@empresa.com.')
        local,domain=em.rsplit('@',1)
        if local.startswith('.') or local.endswith('.') or '..' in em or len(local)>64:
            raise AltaClienteError('El correo tiene un formato inválido.')
        return cls(st,rf,ci,ph,local+'@'+domain.lower())

    def validar(self) -> 'AltaCliente':
        """Revalida también instancias creadas sin pasar por crear()."""
        return self.crear(self.estacion,self.rfc,self.idcif,self.telefono,self.correo)

    def clave_local(self) -> str:
        # Huella del RFC/estación; no conserva los cuatro datos en claro.
        s=STATIONS[self.estacion]['numero']+'|'+self.rfc
        return hashlib.sha256(s.encode('utf-8')).hexdigest()

    def campos_cliente(self) -> dict:
        return {'rfc':self.rfc,'idcif':self.idcif,'telefono':self.telefono,'correo':self.correo}
