"""Voice for the desktop assistant: microphone -> Whisper (local) -> text, and text -> Windows voice.

The microphone is opened only while listening (after the hotkey summons Mitraa) and
closed as soon as you stop talking. Audio never leaves the machine.
"""

from __future__ import annotations

import importlib.util
import logging
import os
import queue
import re
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from mitraa.config import Settings

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16_000
FRAME_SECONDS = 0.03
# Biases Whisper toward Roman-script Hinglish and file-style words instead of Devanagari/Urdu script.
INITIAL_PROMPT = "Hey Mitraa. Mera resume.pdf Downloads mein move karo. Move notes.txt from Documents to Desktop."

Post = Callable[[tuple[str, Any]], None]


# ---------------------------------------------------------------- end-of-speech detection

@dataclass
class Endpointer:
    """Decides, frame by frame, when the user has started and finished speaking.

    feed(rms) returns None to keep listening, or "end" / "no_speech" / "max".
    The noise floor is learned from the first calibration_seconds of audio.
    """

    frame_seconds: float = FRAME_SECONDS
    calibration_seconds: float = 0.25
    start_timeout: float = 7.0
    end_silence: float = 1.1
    max_seconds: float = 20.0
    min_threshold: float = 0.006
    max_threshold: float = 0.05
    ratio: float = 2.5
    frames_to_start: int = 3
    threshold: float | None = None
    speech_started: bool = False
    _levels: list[float] = field(default_factory=list)
    _frames: int = 0
    _loud_run: int = 0
    _quiet_run: int = 0

    def feed(self, rms: float) -> str | None:
        self._frames += 1
        elapsed = self._frames * self.frame_seconds
        if self.threshold is None:
            self._levels.append(rms)
            if elapsed >= self.calibration_seconds:
                floor = sorted(self._levels)[len(self._levels) // 5]
                self.threshold = min(self.max_threshold, max(self.min_threshold, floor * self.ratio))
            return None

        loud = rms >= self.threshold
        if not self.speech_started:
            self._loud_run = self._loud_run + 1 if loud else 0
            if self._loud_run >= self.frames_to_start:
                self.speech_started = True
            elif elapsed >= self.start_timeout:
                return "no_speech"
        else:
            self._quiet_run = 0 if loud else self._quiet_run + 1
            if self._quiet_run * self.frame_seconds >= self.end_silence:
                return "end"
        if elapsed >= self.max_seconds:
            return "max" if self.speech_started else "no_speech"
        return None


def record_until_silence(
    endpointer: Endpointer,
    on_level: Callable[[float], None],
    cancel: threading.Event,
) -> tuple[str, np.ndarray | None]:
    """Record from the default microphone until the endpointer says stop.

    Returns (outcome, audio) where outcome is "end", "max", "no_speech" or "cancelled".
    """
    import sounddevice as sd

    block = int(SAMPLE_RATE * endpointer.frame_seconds)
    frames: list[np.ndarray] = []
    outcome = "cancelled"
    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32", blocksize=block) as stream:
        while not cancel.is_set():
            data, _overflowed = stream.read(block)
            mono = data[:, 0].copy()
            frames.append(mono)
            rms = float(np.sqrt(np.mean(mono * mono)))
            on_level(rms)
            result = endpointer.feed(rms)
            if result:
                outcome = result
                break
    if outcome in ("end", "max"):
        return outcome, np.concatenate(frames)
    return outcome, None


# ---------------------------------------------------------------- speech-to-text

def _add_cuda_dll_dirs() -> None:
    """Make pip-installed CUDA libraries (nvidia-cublas-cu12, nvidia-cudnn-cu12) loadable on Windows."""
    if sys.platform != "win32":
        return
    for package in ("nvidia.cublas", "nvidia.cudnn"):
        try:
            spec = importlib.util.find_spec(package)
        except ModuleNotFoundError:
            continue
        if spec is None or not spec.submodule_search_locations:
            continue
        bin_dir = Path(list(spec.submodule_search_locations)[0]) / "bin"
        if bin_dir.is_dir():
            os.add_dll_directory(str(bin_dir))
            os.environ["PATH"] = f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"


class Transcriber:
    """Loads Whisper once in the background; transcribe() waits for it if needed."""

    def __init__(self, model: str, device: str, language: str) -> None:
        self.model_name = model
        self.device = device
        self.language = language or None
        self.ready = threading.Event()
        self.error: str | None = None
        self.description = ""
        self._model: Any = None
        self._lock = threading.Lock()

    def load_async(self) -> None:
        threading.Thread(target=self._load, name="mitraa-whisper-load", daemon=True).start()

    def _load(self) -> None:
        try:
            from faster_whisper import WhisperModel

            attempts = [("cuda", "float16"), ("cpu", "int8")] if self.device == "auto" else [
                (self.device, "float16" if self.device == "cuda" else "int8")
            ]
            if any(d == "cuda" for d, _ in attempts):
                _add_cuda_dll_dirs()
            last_error: Exception | None = None
            for device, compute_type in attempts:
                try:
                    model = WhisperModel(self.model_name, device=device, compute_type=compute_type)
                    model.transcribe(np.zeros(SAMPLE_RATE, dtype=np.float32), beam_size=1)  # warm-up / load check
                    self._model = model
                    self.description = f"Whisper {self.model_name} on {device.upper()}"
                    logger.info("Speech model ready: %s", self.description)
                    return
                except Exception as exc:  # CUDA missing, out of memory, ...
                    logger.warning("Whisper on %s unavailable: %s", device, exc)
                    last_error = exc
            self.error = f"Speech model failed to load: {last_error}"
        except Exception as exc:
            self.error = f"Speech recognition unavailable: {exc}"
            logger.exception("Whisper load failed")
        finally:
            self.ready.set()

    def transcribe(self, audio: np.ndarray, timeout: float = 300) -> tuple[str, str]:
        """Returns (text, language). Raises RuntimeError if the model isn't usable."""
        if not self.ready.wait(timeout):
            raise RuntimeError("The speech model is still loading.")
        if self._model is None:
            raise RuntimeError(self.error or "Speech model unavailable.")
        with self._lock:
            segments, info = self._model.transcribe(
                audio,
                language=self.language,
                beam_size=5,
                vad_filter=True,
                initial_prompt=INITIAL_PROMPT,
                condition_on_previous_text=False,
            )
            text = " ".join(segment.text.strip() for segment in segments).strip()
        return text, info.language


class VoiceInput:
    """One listen -> transcribe cycle at a time, reporting progress through post()."""

    def __init__(self, transcriber: Transcriber, post: Post) -> None:
        self.transcriber = transcriber
        self.post = post
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def active(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.active:
            return
        self._cancel = threading.Event()
        self._thread = threading.Thread(target=self._run, args=(self._cancel,), name="mitraa-listen", daemon=True)
        self._thread.start()

    def cancel(self) -> None:
        self._cancel.set()

    def _run(self, cancel: threading.Event) -> None:
        try:
            self.post(("voice_state", "listening"))
            outcome, audio = record_until_silence(Endpointer(), lambda rms: self.post(("voice_level", rms)), cancel)
            if outcome == "cancelled":
                self.post(("voice_state", "idle"))
                return
            if audio is None:
                self.post(("voice_none", "I didn't hear anything."))
                return
            self.post(("voice_state", "transcribing" if self.transcriber.ready.is_set() else "loading"))
            text, language = self.transcriber.transcribe(audio)
            if cancel.is_set():
                self.post(("voice_state", "idle"))
            elif text:
                logger.info("Heard %.1fs of speech (language: %s)", len(audio) / SAMPLE_RATE, language)
                self.post(("voice_text", text))
            else:
                self.post(("voice_none", "I couldn't make that out."))
        except Exception as exc:
            logger.exception("Voice input failed")
            self.post(("voice_error", _microphone_hint(exc)))


def _microphone_hint(exc: Exception) -> str:
    text = str(exc)
    if "PortAudio" in type(exc).__name__ or "device" in text.lower() or "-9996" in text:
        return (
            "I can't use the microphone. Check Settings → Privacy & security → Microphone and allow "
            f"desktop apps to use it. ({text})"
        )
    return f"Voice input failed: {text}"


# ---------------------------------------------------------------- text-to-speech

# Any token containing a slash or backslash: C:\Users\x\Downloads, Documents/resume.pdf
_PATH = re.compile(r"(?:[A-Za-z]:)?[^\s\\/,;]*(?:[\\/][^\s\\/,;]+)+[\\/]?")
_URL = re.compile(r"https?://\S+")
_MARKDOWN = re.compile(r"[*`#>|]+|(?<!\w)_+|_+(?!\w)")  # keeps snake_case file names intact


def speakable(text: str, max_chars: int = 320) -> str:
    """Turn a chat reply into something pleasant to hear: no markdown, paths shortened, kept brief."""
    text = _URL.sub("a link", text)
    text = _PATH.sub(lambda m: Path(m.group(0).rstrip("\\/")).name or m.group(0), text)
    text = _MARKDOWN.sub(" ", text)
    text = re.sub(r"^\s*(?:\d+[.)]|[-•])\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= max_chars:
        return text
    sentences = re.split(r"(?<=[.!?])\s+", text)
    spoken = ""
    for sentence in sentences:
        if len(spoken) + len(sentence) > max_chars:
            break
        spoken = f"{spoken} {sentence}".strip()
    return (spoken or text[:max_chars]) + " The rest is on screen."


def pick_voice(descriptions: list[str], preferred: str) -> int | None:
    """Index of the first voice whose description contains `preferred` (case-insensitive)."""
    wanted = preferred.strip().lower()
    if not wanted:
        return None
    for index, description in enumerate(descriptions):
        if wanted in description.lower():
            return index
    return None


class Speaker:
    """Speaks replies with Windows voices (SAPI, including the newer OneCore voices like Heera).

    All COM calls happen on one dedicated thread. say() replaces whatever is being said;
    stop() silences it immediately.
    """

    _ASYNC, _PURGE = 1, 2

    def __init__(self, voice_name: str, rate: int = 0) -> None:
        self.voice_name = voice_name
        self.rate = rate
        self.enabled = True
        self.available = sys.platform == "win32"
        self.voice_description = ""
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._interrupt = threading.Event()
        if self.available:
            threading.Thread(target=self._run, name="mitraa-speaker", daemon=True).start()

    def say(self, text: str) -> None:
        if not (self.enabled and self.available):
            return
        spoken = speakable(text)
        if spoken:
            self._interrupt.set()
            self._queue.put(spoken)

    def stop(self) -> None:
        self._interrupt.set()

    def close(self) -> None:
        self._interrupt.set()
        self._queue.put(None)

    def _run(self) -> None:
        try:
            import comtypes
            import comtypes.client

            comtypes.CoInitialize()
            voice = comtypes.client.CreateObject("SAPI.SpVoice")
            self._select_voice(voice, comtypes.client)
            voice.Rate = max(-10, min(10, self.rate))
        except Exception:
            logger.exception("Text-to-speech unavailable")
            self.available = False
            return

        while True:
            text = self._queue.get()
            if text is None:
                break
            while not self._queue.empty():  # only the newest reply matters
                newer = self._queue.get()
                if newer is None:
                    return
                text = newer
            self._interrupt.clear()
            try:
                voice.Speak(text, self._ASYNC | self._PURGE)
                while not voice.WaitUntilDone(100):
                    if self._interrupt.is_set():
                        voice.Speak("", self._ASYNC | self._PURGE)
                        break
            except Exception:
                logger.exception("Speaking failed")

    def _select_voice(self, voice: Any, client: Any) -> None:
        tokens: list[Any] = []
        for category in (
            r"HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Speech_OneCore\Voices",
            r"HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Speech\Voices",
        ):
            try:
                cat = client.CreateObject("SAPI.SpObjectTokenCategory")
                cat.SetId(category, False)
                found = cat.EnumerateTokens()
                tokens += [found.Item(i) for i in range(found.Count)]
            except Exception:
                continue
        descriptions = [token.GetDescription() for token in tokens]
        index = pick_voice(descriptions, self.voice_name)
        if index is not None:
            voice.Voice = tokens[index]
        self.voice_description = voice.Voice.GetDescription()
        logger.info("Speaking with voice: %s", self.voice_description)


# ---------------------------------------------------------------- factory

@dataclass
class Voice:
    transcriber: Transcriber | None
    speaker: Speaker | None


def build_voice(settings: Settings) -> Voice:
    transcriber = None
    if settings.mitraa_voice_input:
        transcriber = Transcriber(settings.mitraa_stt_model, settings.mitraa_stt_device, settings.mitraa_stt_language)
        transcriber.load_async()
    speaker = Speaker(settings.mitraa_tts_voice, settings.mitraa_tts_rate) if settings.mitraa_voice_output else None
    return Voice(transcriber, speaker)
