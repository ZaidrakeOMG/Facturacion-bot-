"""Pruebas del aislamiento de transportes del archivo Gmail real con importaciones simuladas.
No instala dependencias, no autoriza cuentas y no usa Internet.
"""
import importlib.util
import sys
import threading
import types
from pathlib import Path
from unittest.mock import Mock,patch
import pytest

@pytest.fixture
def client(tmp_path):
    mods={}
    for name in ('google','google.auth','google.auth.transport','google.auth.transport.requests',
                 'google.oauth2','google.oauth2.credentials','google_auth_oauthlib','google_auth_oauthlib.flow',
                 'googleapiclient','googleapiclient.discovery','googleapiclient.errors'):
        mods[name]=types.ModuleType(name)
    mods['google.auth.transport.requests'].Request=Mock
    mods['google.oauth2.credentials'].Credentials=Mock
    mods['google_auth_oauthlib.flow'].InstalledAppFlow=Mock
    mods['googleapiclient.discovery'].build=Mock
    mods['googleapiclient.errors'].HttpError=type('HttpError',(Exception,),{})
    path=Path(__file__).resolve().parents[1]/'arybot'/'gmail_client.py'
    spec=importlib.util.spec_from_file_location('gmail_module_isolated_v319',path)
    module=importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules,mods):spec.loader.exec_module(module)
    return module.GmailClient(tmp_path,{'app':{},'gmail':{'token_file':'token.json','credentials_file':'credentials.json'}},Mock())

def test_service_is_local_to_thread(client):
    sentinel=object();client.service=sentinel;values=[]
    def run():
        values.append(client.service)
        client.service='thread-service';values.append(client.service)
    t=threading.Thread(target=run);t.start();t.join()
    assert values==[None,'thread-service'];assert client.service is sentinel

def test_two_threads_do_not_share_http(client):
    barrier=threading.Barrier(2);values=[]
    def run(label):
        client.service=label;barrier.wait(timeout=2);values.append(client.service)
    ts=[threading.Thread(target=run,args=(s,)) for s in ('A','B')]
    for t in ts:t.start()
    for t in ts:t.join()
    assert set(values)=={'A','B'}

def test_label_caches_not_shared(client):
    client._label_cache['one']='main';values=[]
    t=threading.Thread(target=lambda:values.append(dict(client._label_cache)));t.start();t.join()
    assert values==[{}]

def test_generation_invalidates_other_thread_service(client):
    ready=threading.Event();done=threading.Event();values=[]
    def run():
        client.service='old';ready.set();done.wait(2);values.append(client.service)
    t=threading.Thread(target=run);t.start();assert ready.wait(1)
    client._generation+=1;done.set();t.join()
    assert values==[None]

def test_no_token_required_to_inspect_empty_service(client):assert client.service is None
