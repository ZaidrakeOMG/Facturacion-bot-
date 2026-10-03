"""Identifica una afirmación explícita del aviso de Polaris. No consulta el SAT.

No usa el historial local como prueba, ni interpreta una pregunta, un error de
consulta o 'no facturado' como confirmación. No realiza OCR ni operaciones de red.
"""
from __future__ import annotations
import re
import unicodedata


def confirma_ya_facturado(texto, ticket):
    text = ''.join(c for c in unicodedata.normalize('NFKD', str(texto or ''))
                   if not unicodedata.combining(c)).upper()
    text = re.sub(r'\s+', ' ', text).strip()
    folio = str(ticket or '').strip()
    if not re.fullmatch(r'[0-9]{1,20}', folio) or not text or '?' in text:
        return False
    mentioned = re.findall(r'\b(?:FOLIO|TICKET)(?:\s+(?:NUMERO|NO\.?|NRO\.?))?\s*[:#-]?\s*([0-9]+)\b', text)
    if any(int(x) != int(folio) for x in mentioned):
        return False
    # No se transforma 'no se pudo verificar si ya...' en un éxito fiscal.
    if re.search(r'\b(?:SI|VERIFIQUE|VERIFICAR|COMPRUEBE|COMPROBAR|PODRIA|PUEDE|QUIZA|CONSULTE)\b.*\bFACTURAD[OA]\b', text):
        # 'No se puede agregar: el folio ya está facturado' también se deja a revisión.
        return False
    if re.search(r'\b(?:NO|NUNCA)\s+(?:(?:SE|HA|HABIA|ESTA|ESTABA|FUE|ESTE|SIDO|ENCUENTRA|YA|ES)\s+){0,5}FACTURAD[OA]\b', text):
        return False
    if re.search(r'\bYA\s+NO\b', text):
        return False
    patterns = (
        r'\bYA\s+(?:(?:SE|ESTA|ESTABA|ESTE|ENCUENTRA|ENCONTRABA|FUE|HA|HABIA|SIDO|ES)\s+){0,5}FACTURAD[OA]\b',
        r'\b(?:FUE|ESTA|SE ENCUENTRA)\s+FACTURAD[OA]\s+(?:ANTERIORMENTE|PREVIAMENTE)\b',
    )
    return any(re.search(p, text) for p in patterns)
