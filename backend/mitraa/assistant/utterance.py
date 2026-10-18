"""One local speech-aware utterance detector and bounded microphone pipeline."""
from __future__ import annotations
from collections import deque
from contextlib import ExitStack
from dataclasses import dataclass, field
import math
import queue
import threading
import time
from typing import Callable
import numpy as np

SAMPLE_RATE = 16000
FRAME_SECONDS = .03
FRAME_SAMPLES = 480

@dataclass
class Endpointer:
    frame_seconds: float = FRAME_SECONDS
    onset_seconds: float = .15
    min_speech_seconds: float = .27
    end_silence: float = 1.0
    start_timeout: float = 8.0
    max_seconds: float = 30.0
    preroll_seconds: float = .3
    trailing_seconds: float = .2
    speech_started: bool = False
    noise_floor: float = .001
    voiced_seconds: float = 0.0
    elapsed: float = 0.0
    quiet_seconds: float = 0.0
    result: str | None = None
    _window: deque = field(default_factory=deque)

    @classmethod
    def from_settings(cls, settings):
        return cls(onset_seconds=settings.mitraa_speech_onset_ms / 1000,
                   min_speech_seconds=settings.mitraa_min_speech_ms / 1000,
                   end_silence=settings.mitraa_end_silence,
                   start_timeout=settings.mitraa_no_speech_timeout,
                   max_seconds=settings.mitraa_max_utterance,
                   preroll_seconds=settings.mitraa_preroll_ms / 1000,
                   trailing_seconds=settings.mitraa_trailing_ms / 1000)

    @property
    def short_speech(self):
        return self.voiced_seconds < self.min_speech_seconds

    def feed(self, rms: float, speech: bool) -> str | None:
        """Requires a speech classifier result: never silently falls back to energy."""
        if self.result is not None:
            return self.result
        self.elapsed += self.frame_seconds
        # VAD is primary; energy rejects numerical silence, not quiet speech.
        positive = speech and rms >= max(.0005, min(.006, self.noise_floor * .8))
        if not speech:
            rate = .002 if self.speech_started else .02
            self.noise_floor = min(.03, (1-rate) * self.noise_floor + rate * rms)
        self._window.append(positive)
        window = math.ceil(self.onset_seconds / self.frame_seconds) + 1
        while len(self._window) > window:
            self._window.popleft()
        if not self.speech_started:
            required = math.ceil(self.onset_seconds / self.frame_seconds)
            if sum(self._window) >= required:
                self.speech_started = True
                self.voiced_seconds = sum(self._window) * self.frame_seconds
            elif self.elapsed >= self.start_timeout:
                self.result = 'no_speech'
        else:
            if positive:
                self.voiced_seconds += self.frame_seconds
                self.quiet_seconds = 0
            else:
                self.quiet_seconds += self.frame_seconds
            if self.quiet_seconds + 1e-9 >= self.end_silence:
                self.result = 'end'
        if self.result is None and self.elapsed + 1e-9 >= self.max_seconds:
            self.result = 'max' if self.speech_started else 'no_speech'
        return self.result

class FrameResampler:
    """Native 30-ms blocks -> 16-kHz frames, with preceding FIR context."""
    def __init__(self, source_rate):
        self.source_rate = source_rate
        self.previous = np.empty(0, dtype=np.float32)
        self.resample = None
        if source_rate != SAMPLE_RATE:
            from scipy.signal import resample_poly
            self.resample = resample_poly

    def convert(self, frame):
        if self.source_rate == SAMPLE_RATE:
            return np.asarray(frame, dtype=np.float32)
        divisor = math.gcd(self.source_rate, SAMPLE_RATE)
        context = np.concatenate((self.previous, frame))
        result = self.resample(context, SAMPLE_RATE // divisor, self.source_rate // divisor)
        self.previous = frame.copy()
        return np.asarray(result[-FRAME_SAMPLES:], dtype=np.float32)


def record_until_silence(endpointer: Endpointer, on_level: Callable[[float], None],
                         cancel: threading.Event, *, on_state=lambda state: None,
                         vad_mode=2, sd_module=None, vad=None):
    """A stream context guarantees close on endpoint, cancellation, and errors.

    Device callback only copies into a bounded queue. VAD, resampling and signal
    delivery run here, on VoiceInput's worker thread, never in the device callback.
    """
    if sd_module is None:
        import sounddevice as sd_module
    if vad is None:
        try:
            import webrtcvad
            vad = webrtcvad.Vad(vad_mode)
        except Exception as exc:
            raise RuntimeError('Speech detection unavailable. Install webrtcvad-wheels; no energy-only fallback is used.') from exc
    rate = int(round(sd_module.query_devices(kind='input')['default_samplerate']))
    block = round(rate * FRAME_SECONDS)
    if abs(block / rate - FRAME_SECONDS) > 1e-7:
        raise RuntimeError(f'Microphone sample rate {rate} cannot supply exact 30-ms frames.')
    chunks = queue.Queue(maxsize=32)
    overflow = threading.Event()
    def callback(indata, frames, time_info, status):
        if status:
            overflow.set()
        try:
            chunks.put_nowait(indata[:, 0].copy())
        except queue.Full:
            overflow.set()
    resampler = FrameResampler(rate)
    preroll = deque(maxlen=math.ceil((endpointer.preroll_seconds + endpointer.onset_seconds) / FRAME_SECONDS))
    captured = []
    last_voice = 0
    last_data = time.monotonic()
    with ExitStack() as resources:
        stream = sd_module.InputStream(samplerate=rate, channels=1, dtype='float32', blocksize=block, callback=callback)
        if hasattr(stream, 'close'):
            resources.callback(stream.close)  # also closes if stream startup fails
        resources.enter_context(stream)
        on_state('waiting_for_speech')
        while not cancel.is_set():
            if overflow.is_set():
                raise RuntimeError('Microphone audio buffer overflowed. Please retry or close busy audio apps.')
            try:
                chunk = chunks.get(timeout=.1)
            except queue.Empty:
                if time.monotonic() - last_data > 2:
                    raise RuntimeError('The microphone stopped supplying audio. Please retry.')
                continue
            last_data = time.monotonic()
            frame = resampler.convert(chunk)
            if len(frame) != FRAME_SAMPLES:
                raise RuntimeError('Microphone supplied an incomplete audio frame. Please retry.')
            rms = float(np.sqrt(np.mean(frame * frame)))
            pcm = (np.clip(frame, -1, 1) * 32767).astype('<i2').tobytes()
            voiced = vad.is_speech(pcm, SAMPLE_RATE)
            was_started = endpointer.speech_started
            result = endpointer.feed(rms, voiced)
            on_level(rms)
            if not was_started:
                preroll.append(frame)
                if endpointer.speech_started:
                    captured.extend(preroll)
                    preroll.clear()
                    on_state('recording_speech')
            else:
                captured.append(frame)
            if voiced and captured:
                last_voice = len(captured)
            if result is not None:
                if cancel.is_set():
                    return 'cancelled', None
                if result in ('end', 'max') and captured:
                    keep = min(len(captured), last_voice + math.ceil(endpointer.trailing_seconds / FRAME_SECONDS))
                    return result, np.concatenate(captured[:keep])
                return 'no_speech', None
    return 'cancelled', None
