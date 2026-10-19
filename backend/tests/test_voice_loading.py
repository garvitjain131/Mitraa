"""Cached model loading must avoid network probes, bad GPUs and silence decoding."""
import sys
import threading
from types import ModuleType, SimpleNamespace
import pytest
from mitraa.assistant.voice import Transcriber


def fake_runtime(monkeypatch,tmp_path,cuda_count=0,break_cuda=False):
    folder=tmp_path/'snapshot'; folder.mkdir()
    for name in ('model.bin','config.json','tokenizer.json'): (folder/name).write_text('fixture')
    calls=[]
    class Model:
        feature_extractor=SimpleNamespace(mel_filters=SimpleNamespace(shape=(128,201)))
        def __init__(self,path,**kwargs):
            calls.append(('model',path,kwargs))
            self.device=kwargs['device']
        def transcribe(self,*args,**kwargs): pytest.fail('loader decoded silence')
        def encode(self,features):
            calls.append(('encode',features.shape))
            if break_cuda: raise RuntimeError('CUDA driver incompatible')
    faster=ModuleType('faster_whisper'); faster.WhisperModel=Model
    utils=ModuleType('faster_whisper.utils')
    def download(name,**kwargs):
        calls.append(('resolve',name,kwargs))
        assert kwargs=={'local_files_only':True}, 'unexpected network request'
        return str(folder)
    utils.download_model=download
    ct=ModuleType('ctranslate2'); ct.get_cuda_device_count=lambda:cuda_count
    hub=ModuleType('huggingface_hub.errors'); hub.LocalEntryNotFoundError=type('LocalEntryNotFoundError',(Exception,),{})
    monkeypatch.setitem(sys.modules,'faster_whisper',faster)
    monkeypatch.setitem(sys.modules,'faster_whisper.utils',utils)
    monkeypatch.setitem(sys.modules,'ctranslate2',ct)
    monkeypatch.setitem(sys.modules,'huggingface_hub.errors',hub)
    monkeypatch.setattr('mitraa.assistant.voice._add_cuda_dll_dirs',lambda:None)
    return calls


def test_cached_cpu_load_has_no_network_gpu_attempt_or_decode(monkeypatch,tmp_path):
    calls=fake_runtime(monkeypatch,tmp_path)
    t=Transcriber('small','auto',''); t._load()
    assert t.ready.is_set() and t.error is None and t.description.endswith('CPU')
    assert len([c for c in calls if c[0]=='resolve'])==1
    assert [c[2]['device'] for c in calls if c[0]=='model']==['cpu']
    assert not any(c[0]=='encode' for c in calls)


def test_gpu_failure_reuses_cached_path_for_cpu(monkeypatch,tmp_path):
    calls=fake_runtime(monkeypatch,tmp_path,cuda_count=1,break_cuda=True)
    t=Transcriber('small','auto',''); t._load()
    assert t.error is None and t.description.endswith('CPU')
    models=[c for c in calls if c[0]=='model']
    assert [c[2]['device'] for c in models]==['cuda','cpu'] and models[0][1]==models[1][1]
    assert len([c for c in calls if c[0]=='resolve'])==1


def test_load_async_is_idempotent(monkeypatch):
    t=Transcriber('small','cpu',''); release=threading.Event(); calls=[]
    def load():
        calls.append(True); release.wait(2); t.ready.set()
    monkeypatch.setattr(t,'_load',load)
    t.load_async(); first=t._load_thread; t.load_async()
    assert t._load_thread is first
    release.set(); first.join(2); t.load_async()
    assert calls==[True]


def test_gpu_warmup_uses_actual_filter_bank_dimensions(monkeypatch,tmp_path):
    calls=fake_runtime(monkeypatch,tmp_path,cuda_count=1)
    t=Transcriber('large-v3','auto',''); t._load()
    assert t.error is None and t.description.endswith('CUDA')
    assert ('encode',(128,3000)) in calls
    assert [c[2]['device'] for c in calls if c[0]=='model']==['cuda']
