from __future__ import annotations

import os
import wave
from pathlib import Path

import numpy as np
import pytest

from mitraa.assistant.voice import Endpointer, Speaker, Transcriber, pick_voice, speakable

QUIET, SPEECH = 0.002, 0.05


def run(endpointer: Endpointer, levels: list[float]) -> tuple[str | None, int]:
    for index, level in enumerate(levels):
        result = endpointer.feed(level)
        if result:
            return result, index + 1
    return None, len(levels)


def frames(seconds: float, level: float) -> list[float]:
    return [level] * round(seconds / 0.03)


def test_speech_then_silence_ends():
    ep = Endpointer()
    result, used = run(ep, frames(0.3, QUIET) + frames(2.0, SPEECH) + frames(2.0, QUIET))
    assert result == "end" and ep.speech_started
    assert used * 0.03 == pytest.approx(0.3 + 2.0 + 1.1, abs=0.1)


def test_short_pauses_do_not_end_speech():
    ep = Endpointer()
    levels = frames(0.3, QUIET)
    for _ in range(4):  # words with 0.5s gaps
        levels += frames(0.6, SPEECH) + frames(0.5, QUIET)
    result, _ = run(ep, levels)
    assert result is None and ep.speech_started


def test_no_speech_times_out():
    result, used = run(Endpointer(start_timeout=3.0), frames(5, QUIET))
    assert result == "no_speech" and used * 0.03 == pytest.approx(3.0, abs=0.1)


def test_click_is_not_speech():
    ep = Endpointer(start_timeout=2.0)
    result, _ = run(ep, frames(0.3, QUIET) + [SPEECH, SPEECH] + frames(3, QUIET))  # 2 loud frames only
    assert result == "no_speech"


def test_max_length():
    result, used = run(Endpointer(max_seconds=5), frames(0.3, QUIET) + frames(10, SPEECH))
    assert result == "max" and used * 0.03 == pytest.approx(5, abs=0.1)


def test_threshold_adapts_to_noise_but_is_capped():
    noisy = Endpointer()
    run(noisy, frames(0.3, 0.01))
    assert noisy.threshold == pytest.approx(0.025)
    very_noisy = Endpointer()
    run(very_noisy, frames(0.3, 0.2))
    assert very_noisy.threshold == very_noisy.max_threshold


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
