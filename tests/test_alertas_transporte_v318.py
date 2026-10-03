"""Prueba el transporte completo mediante módulos Gmail HTTP simulados. Sin red."""
import base64
import sys
import tempfile
import types
import unittest
from pathlib import Path
from email import message_from_bytes, policy
from unittest.mock import Mock,patch
from arybot.alertas_correo import CorreoAlertasGmail,ErrorEnvioAlerta

class TransporteTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name);(self.path/'token.json').write_text('{}')
        self.service=Mock();self.service.users().getProfile().execute.return_value={'emailAddress':'emisioncfdi@grupoary.com'}
        self.service.users().messages().send().execute.return_value={'id':'correo_simulado'}
        self.build=Mock(return_value=self.service)
        self.creds=Mock(valid=True,expired=False,refresh_token='dummy')
        self.credential_class=Mock();self.credential_class.from_authorized_user_file.return_value=self.creds
        self.http=Mock();self.authorized=Mock()
        defs={
            'google.oauth2.credentials':{'Credentials':self.credential_class},
            'google.auth.transport.requests':{'Request':Mock()},
            'google_auth_httplib2':{'AuthorizedHttp':self.authorized},
            'httplib2':{'Http':self.http},
            'googleapiclient.discovery':{'build':self.build},
            'googleapiclient.errors':{'HttpError':type('HttpError',(RuntimeError,),{})},
        }
        mods={}
        for name,attrs in defs.items():
            mod=types.ModuleType(name)
            for k,v in attrs.items():setattr(mod,k,v)
            mods[name]=mod
        self.patch=patch.dict(sys.modules,mods);self.patch.start();self.addCleanup(self.patch.stop)
        self.sender=CorreoAlertasGmail(self.path,{'gmail':{'token_file':'token.json','cuenta_esperada':'emisioncfdi@grupoary.com'}})
    def send(self):return self.sender.enviar(['ti@grupoary.com'],'[ARY-INTERNO] Prueba','Texto simulado','abc123')
    def test_mime_gmail_sin_threadid(self):
        self.assertEqual(self.send(),{'id':'correo_simulado'})
        call=self.service.users().messages().send.call_args
        self.assertEqual(set(call.kwargs['body']),{'raw'})
        mime=message_from_bytes(base64.urlsafe_b64decode(call.kwargs['body']['raw']),policy=policy.default)
        self.assertEqual(mime['To'],'ti@grupoary.com');self.assertEqual(mime['X-ARY-Alerta-Interna'],'1')
        self.service.users().messages().send().execute.assert_called_with(num_retries=0)
    def test_transport_nuevo_por_evento(self):
        self.send();self.send();self.assertEqual(self.build.call_count,2)
        self.assertEqual(self.http.call_count,2)
    def test_missing_token_no_request_send(self):
        (self.path/'token.json').unlink();self.service.reset_mock()
        with self.assertRaises(ErrorEnvioAlerta) as cm:self.send()
        self.assertFalse(cm.exception.incierto);self.service.users.assert_not_called()
    def test_wrong_account_no_send(self):
        self.service.users().getProfile().execute.return_value={'emailAddress':'otro@gmail.com'}
        with self.assertRaises(ErrorEnvioAlerta) as cm:self.send()
        self.assertFalse(cm.exception.incierto)
    def test_timeout_en_send_es_incierto(self):
        self.service.users().messages().send().execute.side_effect=TimeoutError('socket timeout')
        with self.assertRaises(ErrorEnvioAlerta) as cm:self.send()
        self.assertTrue(cm.exception.incierto)
    def test_error_antes_envio_no_incierto(self):
        self.service.users().getProfile().execute.side_effect=TimeoutError('profile timeout')
        with self.assertRaises(ErrorEnvioAlerta) as cm:self.send()
        self.assertFalse(cm.exception.incierto)
    def test_403_explicito_error_sin_reintento(self):
        e=RuntimeError('quota');e.resp=types.SimpleNamespace(status=403)
        self.service.users().messages().send().execute.side_effect=e
        with self.assertRaises(ErrorEnvioAlerta) as cm:self.send()
        self.assertFalse(cm.exception.incierto);self.assertIn('403',str(cm.exception))
    def test_no_reescribe_token(self):
        before=(self.path/'token.json').read_bytes();self.send()
        self.assertEqual(before,(self.path/'token.json').read_bytes())
    def test_error_no_revela_secreto(self):
        self.service.users().messages().send().execute.side_effect=RuntimeError('Bearer LEAKTOKEN')
        with self.assertRaises(ErrorEnvioAlerta) as cm:self.send()
        self.assertNotIn('LEAKTOKEN',str(cm.exception))

if __name__=='__main__':unittest.main()
