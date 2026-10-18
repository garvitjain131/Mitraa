from __future__ import annotations

import os
import wave
from pathlib import Path

import numpy as np
import pytest

from mitraa.assistant.voice import Endpointer, Speaker, Transcriber, pick_voice, speakable

def test_speakable_cleans_reply():
    text = r"**Moved** resume.pdf to C:\Users\Asus\Downloads. Say 'undo' to revert. See https://example.com/x"
    spoken = speakable(text)
    assert "**" not in spoken and "C:\\" not in spoken and "https" not in spoken
    assert "Downloads" in spoken and "resume.pdf" in spoken


def test_speakable_lists_and_length():
    reply = "I found two:\n1. Documents/resume.pdf\n2. Desktop/resume_old.docx\nWhich one?"
    assert speakable(reply) == "I found two: resume.pdf resume_old.docx Which one?"
    long = " ".join(f"Sentence number {i} is here." for i in range(40))
    spoken = speakable(long, max_chars=100)
    assert len(spoken) < 140 and spoken.endswith("The rest is on screen.")


def test_pick_voice():
    voices = ["Microsoft Hazel - English (United Kingdom)", "Microsoft Heera - English (India)"]
    assert pick_voice(voices, "heera") == 1
    assert pick_voice(voices, "india") == 1
    assert pick_voice(voices, "nobody") is None
    assert pick_voice(voices, "") is None


def test_disabled_speaker_says_nothing():
    speaker = Speaker.__new__(Speaker)  # no COM thread
    speaker.enabled, speaker.available = False, True
    speaker.say("hello")  # must not raise or queue


@pytest.mark.skipif(not os.environ.get("MITRAA_TEST_STT"), reason="set MITRAA_TEST_STT=path/to/sample.wav to run")
def test_whisper_transcribes_sample():
    path = Path(os.environ["MITRAA_TEST_STT"])
    with wave.open(str(path)) as w:
        audio = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
    transcriber = Transcriber("small", "auto", "")
    transcriber.load_async()
    text, language = transcriber.transcribe(audio)
    assert "resume" in text.lower() and language == "en"

@pytest.mark.parametrize('outcome', ['finished', 'stopped', 'error'])
def test_speaker_reports_actual_playback_lifecycle(monkeypatch, outcome):
    import queue
    import threading
    import sys
    from types import ModuleType
    events=[]
    speaker=Speaker.__new__(Speaker)
    speaker._queue=queue.Queue(); speaker._interrupt=threading.Event()
    speaker._generation=0; speaker.rate=0
    speaker._state_lock=threading.Lock()
    speaker.idle=threading.Event(); speaker.idle.set()
    speaker._select_voice=lambda *_: None
    class Engine:
        from types import SimpleNamespace
        Status = SimpleNamespace(RunningState=2)
        def Speak(self, text, flags):
            if text and outcome == 'error': raise RuntimeError('audio unavailable')
        def WaitUntilDone(self, timeout):
            if outcome == 'stopped':
                speaker._interrupt.set()
                return False
            return True
    com=ModuleType('comtypes'); client=ModuleType('comtypes.client')
    com.CoInitialize=lambda: None; com.CoUninitialize=lambda: None
    client.CreateObject=lambda *_: Engine(); com.client=client
    monkeypatch.setitem(sys.modules,'comtypes',com); monkeypatch.setitem(sys.modules,'comtypes.client',client)
    def post(event):
        events.append(event[0])
        if event[0] in ('speech_finished','speech_stopped','speech_error'):
            speaker._queue.put(None)
    speaker._queue.put(('Hello',post,0))
    speaker._run()
    assert events == (['speech_error'] if outcome == 'error' else ['speech_started', 'speech_'+outcome])
