"""Deterministic lifecycle checks; no model download, playback or microphone."""
import sys
import threading
from types import SimpleNamespace as NS
import numpy as np
import pytest
from mitraa.assistant.neural_speaker import NeuralSpeaker

def bare_speaker():
    speaker=NeuralSpeaker.__new__(NeuralSpeaker)
    speaker._interrupt=threading.Event(); speaker._closing=threading.Event()
    speaker._state_lock=threading.Lock(); speaker._generation=0
    speaker.idle=threading.Event()
    return speaker

@pytest.mark.parametrize('cancel',[False,True])
def test_playback_events_and_stream_release(monkeypatch,cancel):
    events=[]; streams=[]; speaker=bare_speaker()
    class Stop(Exception): pass
    class Abort(Exception): pass
    class Stream:
        def __init__(self,**options): self.options=options; self.closed=False; self.running=False; streams.append(self)
        def start(self):
            self.running=True
            try: self.options['callback'](np.zeros((1024,1),dtype=np.float32),1024,None,None)
            except Stop: self.running=False
        @property
        def active(self):
            if cancel: speaker._interrupt.set()
            return self.running
        def abort(self): self.running=False
        def close(self): self.closed=True
    monkeypatch.setitem(sys.modules,'sounddevice',NS(OutputStream=Stream,CallbackStop=Stop,CallbackAbort=Abort))
    samples=np.ones(2048 if cancel else 512,dtype=np.float32)*.1
    speaker._play(samples,24000,lambda item:events.append(item),0)
    assert streams[0].closed and speaker.idle.is_set()
    assert events[0][0]=='speech_started'
    assert events[-1][0]==('speech_stopped' if cancel else 'speech_finished')

def test_cancel_during_synthesis_never_plays_stale_audio(monkeypatch):
    import mitraa.assistant.neural_speaker as module
    started=threading.Event(); release=threading.Event(); done=threading.Event(); events=[]
    class Engine:
        def create(self,*args,**kwargs):
            started.set(); assert release.wait(3)
            return np.ones(2400,dtype=np.float32),24000
    monkeypatch.setattr(module,'load_engine',lambda _:Engine())
    speaker=NeuralSpeaker('unused')
    monkeypatch.setattr(speaker,'_play',lambda *args:pytest.fail('stale speech played'))
    speaker.post=lambda event:(events.append(event),done.set())
    try:
        speaker.say('Hello there.'); assert started.wait(3)
        speaker.stop(); release.set(); assert done.wait(3)
        assert events==[('speech_stopped',None)] and speaker.idle.is_set()
    finally: release.set(); speaker.close()

def test_synthesis_error_releases_idle_and_reports_error(monkeypatch):
    import mitraa.assistant.neural_speaker as module
    done=threading.Event(); events=[]
    class Engine:
        def create(self,*args,**kwargs): raise RuntimeError('test failure')
    monkeypatch.setattr(module,'load_engine',lambda _:Engine())
    speaker=NeuralSpeaker('unused'); speaker.post=lambda event:(events.append(event),done.set())
    try:
        speaker.say('Hello.'); assert done.wait(3)
        assert events[0][0]=='speech_error' and speaker.idle.is_set()
    finally: speaker.close()
