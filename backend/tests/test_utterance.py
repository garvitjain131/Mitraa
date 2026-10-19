"""Deterministic endpoint/resource tests; synthetic frames do not prove room accuracy."""
import threading
from types import SimpleNamespace
import numpy as np
import pytest
from mitraa.assistant.utterance import Endpointer, FrameResampler, record_until_silence
from mitraa.assistant.voice import VoiceInput, Transcriber
from mitraa.config import Settings


def feed(ep, seconds, speech, level=None):
    result=None
    for _ in range(round(seconds/.03)):
        result=ep.feed((.03 if speech else .001) if level is None else level,speech)
        if result: break
    return result


def test_immediate_speech_has_no_silent_calibration():
    ep=Endpointer()
    feed(ep,.18,True)
    assert ep.speech_started and ep.voiced_seconds>=.15
    assert feed(ep,.6,True) is None
    assert feed(ep,1.1,False)=='end'


def test_brief_pauses_keep_utterance_and_silence_finalizes_once():
    ep=Endpointer(); feed(ep,.6,True)
    assert feed(ep,.3,False) is None
    assert feed(ep,.3,True) is None
    assert feed(ep,.57,False) is None
    assert feed(ep,.03,False)=='end'
    elapsed=ep.elapsed
    assert ep.feed(.05,True)=='end' and ep.elapsed==elapsed


def test_endpoint_default_and_setting_stop_after_six_hundred_ms():
    settings=Settings(_env_file=None,mitraa_end_silence=.6)
    for ep in (Endpointer(),Endpointer.from_settings(settings)):
        feed(ep,.6,True)
        assert feed(ep,.57,False) is None
        assert feed(ep,.03,False)=='end'
        assert ep.quiet_seconds==pytest.approx(.6)


def test_endpoint_silence_remains_configurable():
    ep=Endpointer.from_settings(Settings(_env_file=None,mitraa_end_silence=1.2))
    feed(ep,.6,True)
    assert feed(ep,.9,False) is None
    assert feed(ep,.3,False)=='end'


def test_noise_bursts_and_no_speech_timeout():
    ep=Endpointer(start_timeout=2)
    feed(ep,.06,True); feed(ep,.5,False); feed(ep,.06,True)
    assert not ep.speech_started
    assert feed(ep,2,False)=='no_speech'


def test_maximum_and_valid_short_speech():
    ep=Endpointer(max_seconds=1)
    assert feed(ep,1.2,True)=='max'
    short=Endpointer(); feed(short,.18,True)
    assert feed(short,1.1,False)=='end' and short.short_speech


def test_noise_adapts_only_on_non_speech():
    ep=Endpointer(); feed(ep,.5,False,level=.01); before=ep.noise_floor
    feed(ep,.5,True,level=.08)
    assert ep.noise_floor==before

class FakeSD:
    def __init__(self, values, *, status=False, cancel=None):
        self.values=values; self.closed=False; self.opened=0; self.status=status; self.cancel=cancel
    def query_devices(self,kind): return {'default_samplerate':16000}
    def InputStream(self,**kwargs):
        owner=self
        class Stream:
            def __enter__(self):
                owner.opened+=1; self.stop=threading.Event()
                def emit():
                    for value in owner.values:
                        if self.stop.wait(.001): break
                        kwargs['callback'](np.full((480,1),value,dtype=np.float32),480,None,owner.status)
                        if owner.cancel: owner.cancel.set(); break
                self.thread=threading.Thread(target=emit); self.thread.start(); return self
            def __exit__(self,*exc):
                self.stop.set(); self.thread.join(); owner.closed=True
        return Stream()

class FakeVAD:
    def is_speech(self,pcm,rate):
        assert rate==16000 and len(pcm)==960
        return max(np.frombuffer(pcm,dtype='<i2'))>400

@pytest.mark.parametrize('outcome',['end','max','no_speech','cancelled','error'])
def test_capture_closes_stream_on_every_path(outcome):
    cancel=threading.Event()
    values=([.03]*20+[.001]*40) if outcome=='end' else ([.03]*40 if outcome=='max' else [.001]*50)
    sd=FakeSD(values,status=outcome=='error',cancel=cancel if outcome=='cancelled' else None)
    ep=Endpointer(start_timeout=.6,max_seconds=.9 if outcome=='max' else 30)
    if outcome=='error':
        with pytest.raises(RuntimeError,match='overflow'): record_until_silence(ep,lambda _:None,cancel,sd_module=sd,vad=FakeVAD())
    else:
        result,audio=record_until_silence(ep,lambda _:None,cancel,sd_module=sd,vad=FakeVAD())
        assert result==outcome
        if outcome in ('end','max'): assert len(audio)>0
        else: assert audio is None
    assert sd.closed and sd.opened==1


def test_preroll_and_trailing_padding_retained():
    sd=FakeSD([.001]*12+[.03]*20+[.001]*40)
    outcome,audio=record_until_silence(Endpointer(),lambda _:None,threading.Event(),sd_module=sd,vad=FakeVAD())
    assert outcome=='end'
    assert audio[0]==pytest.approx(.001)
    assert np.count_nonzero(np.isclose(audio,.03))==20*480
    # Tail is padding, not the entire endpoint silence.
    assert np.allclose(audio[-480:],.001) and len(audio)<(12+20+34)*480

@pytest.mark.parametrize('rate',[16000,44100,48000])
def test_native_rate_resampling_has_correct_duration_and_pitch(rate):
    samples=round(rate*.03); x=np.sin(2*np.pi*1000*np.arange(samples)/rate).astype(np.float32)
    resampler=FrameResampler(rate); resampler.convert(x); y=resampler.convert(x)
    assert len(y)==480
    peak=np.argmax(abs(np.fft.rfft(y)))*16000/len(y)
    assert peak==pytest.approx(1000,abs=34)

class ReadyTranscriber:
    def __init__(self): self.ready=threading.Event(); self.ready.set(); self.error=None; self.calls=0
    def transcribe(self,audio,**kwargs): self.calls+=1; return 'yes','en'


def test_no_speech_never_transcribes_or_submits(monkeypatch):
    import mitraa.assistant.voice as voice
    monkeypatch.setattr(voice,'record_until_silence',lambda *a,**kw:('no_speech',None))
    t=ReadyTranscriber(); events=[]; item=VoiceInput(t,events.append,Settings())
    item._run(threading.Event())
    assert t.calls==0 and not any(k=='voice_text' for k,v in events)
    assert item.capture_closed.is_set()


def test_cancel_loading_never_opens_microphone(monkeypatch):
    import mitraa.assistant.voice as voice
    monkeypatch.setattr(voice,'record_until_silence',lambda *a,**kw:pytest.fail('capture while loading'))
    t=ReadyTranscriber(); t.ready.clear(); item=VoiceInput(t,lambda _:None,Settings())
    item.start(); item.close()
    assert not item.active and item.capture_closed.is_set()


def test_cancel_transcription_drops_result_and_capture_already_closed(monkeypatch):
    import mitraa.assistant.voice as voice
    cancel=threading.Event(); events=[]
    class Transcriber(ReadyTranscriber):
        def transcribe(self,audio,**kw):
            assert item.capture_closed.is_set()
            cancel.set(); return 'stale command','en'
    monkeypatch.setattr(voice,'record_until_silence',lambda *a,**kw:('end',np.ones(4800)))
    item=VoiceInput(Transcriber(),events.append,Settings()); item._run(cancel)
    assert not any(k=='voice_text' for k,v in events)


def test_transcription_confidence_rejects_silence_but_allows_short_command():
    t=Transcriber('unused','cpu','en'); t.ready.set()
    segment=lambda text,no_speech,logprob:SimpleNamespace(text=text,no_speech_prob=no_speech,avg_logprob=logprob,compression_ratio=1)
    class Model:
        def transcribe(self,*a,**kw):
            return iter([segment('hallucination',.95,-.1),segment('gibberish',.1,-2),segment('Yes',.05,-.2)]),SimpleNamespace(language='en')
    t._model=Model()
    assert t.transcribe(np.zeros(4800),short=True)[0]=='Yes'

def test_failed_vad_closes_stream_without_energy_fallback():
    class BrokenVAD:
        def is_speech(self,*args): raise RuntimeError('VAD failed')
    sd=FakeSD([.03]*10)
    with pytest.raises(RuntimeError,match='VAD failed'):
        record_until_silence(Endpointer(),lambda _:None,threading.Event(),sd_module=sd,vad=BrokenVAD())
    assert sd.closed

def test_installed_vad_accepts_exact_local_pcm_frames():
    import webrtcvad
    assert not webrtcvad.Vad(2).is_speech(bytes(960),16000)

def test_playback_must_finish_before_capture(monkeypatch):
    import mitraa.assistant.voice as voice
    idle=threading.Event(); captured=threading.Event()
    def record(*args,**kwargs):
        assert idle.is_set()
        captured.set(); return 'no_speech',None
    monkeypatch.setattr(voice,'record_until_silence',record)
    item=VoiceInput(ReadyTranscriber(),lambda _:None,Settings(),playback_idle=idle)
    item.start()
    assert not captured.wait(.15)
    idle.set(); item._thread.join(2)
    assert captured.is_set() and not item.active

def test_stream_start_failure_releases_allocated_device():
    class BrokenDevice:
        closed=False
        def query_devices(self,kind): return {'default_samplerate':16000}
        def InputStream(self,**kwargs):
            owner=self
            class Stream:
                def __enter__(self): raise RuntimeError('device start failed')
                def __exit__(self,*args): pass
                def close(self): owner.closed=True
            return Stream()
    sd=BrokenDevice()
    with pytest.raises(RuntimeError,match='device start failed'):
        record_until_silence(Endpointer(),lambda _:None,threading.Event(),sd_module=sd,vad=FakeVAD())
    assert sd.closed
