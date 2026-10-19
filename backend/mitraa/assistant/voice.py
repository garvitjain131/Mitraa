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
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from mitraa.config import Settings

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16_000
FRAME_SECONDS = 0.03
# Vocabulary context, not example sentences that can leak into short recordings.
# Hindi stays in its natural script; English names and paths retain their spelling.
INITIAL_PROMPT = "Hindi and English conversation, including mixed Hindi-English. हिंदी और अंग्रेज़ी। Mitraa, मित्रा।"

Post = Callable[[tuple[str, Any]], None]


# Public re-exports keep one recorder and detector for all desktop callers.
from mitraa.assistant.utterance import Endpointer, record_until_silence


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
        self.language = None if not language or language.strip().lower() == 'auto' else language.strip().lower()
        self.ready = threading.Event()
        self.error: str | None = None
        self.description = ""
        self._model: Any = None
        self._lock = threading.Lock()
        self._load_lock = threading.Lock()
        self._load_thread: threading.Thread | None = None
        self.load_seconds: float | None = None

    def load_async(self) -> None:
        with self._load_lock:
            if self.ready.is_set() or (self._load_thread and self._load_thread.is_alive()):
                return
            self._load_thread = threading.Thread(target=self._load, name="mitraa-whisper-load", daemon=True)
            self._load_thread.start()

    def _load(self) -> None:
        started = time.perf_counter()
        try:
            from faster_whisper import WhisperModel
            from faster_whisper.utils import download_model
            from huggingface_hub.errors import LocalEntryNotFoundError
            import ctranslate2

            # Resolve once, locally first. GPU/CPU attempts reuse the same path
            # instead of each asking the hub for the current remote revision.
            if Path(self.model_name).is_dir():
                source = self.model_name
            else:
                try:
                    source = download_model(self.model_name, local_files_only=True)
                    if not all((Path(source)/name).is_file() for name in ('model.bin','config.json','tokenizer.json')):
                        raise FileNotFoundError('Cached speech model is incomplete.')
                    logger.info('Using cached speech model: %s', self.model_name)
                except (LocalEntryNotFoundError, FileNotFoundError):
                    logger.info('Downloading speech model for first use: %s', self.model_name)
                    source = download_model(self.model_name)

            cuda_usable = False
            if self.device == 'auto':
                try:
                    cuda_usable = ctranslate2.get_cuda_device_count() > 0
                except Exception:
                    logger.info('CUDA is unavailable; using CPU speech recognition.')

            attempts = ([("cuda", "float16"), ("cpu", "int8")] if cuda_usable else [("cpu", "int8")]) if self.device == "auto" else [
                (self.device, "float16" if self.device == "cuda" else "int8")
            ]
            if any(d == "cuda" for d, _ in attempts):
                _add_cuda_dll_dirs()
            last_error: Exception | None = None
            for device, compute_type in attempts:
                try:
                    model = WhisperModel(source, device=device, compute_type=compute_type, local_files_only=True)
                    if device == 'cuda':
                        # Validate actual CUDA kernels without language detection or
                        # decoding fabricated silence. CPU constructors need no warm-up.
                        # FeatureExtractor does not retain its feature_size argument.
                        # The actual filter bank also handles 128-bin Whisper models.
                        bins = model.feature_extractor.mel_filters.shape[0]
                        model.encode(np.zeros((bins,3000),dtype=np.float32))
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
            self.load_seconds = time.perf_counter()-started
            logger.info("Speech model initialization finished in %.3fs", self.load_seconds)
            self.ready.set()

    def transcribe(self, audio: np.ndarray, timeout: float = 300, *, short: bool = False, min_logprob: float = -1.0, max_no_speech: float = .6) -> tuple[str, str]:
        """Returns (text, language). Raises RuntimeError if the model isn't usable."""
        if not self.ready.wait(timeout):
            raise RuntimeError("The speech model is still loading.")
        if self._model is None:
            raise RuntimeError(self.error or "Speech model unavailable.")
        with self._lock:
            language=self.language
            if language is None:
                # Hindi is often confused with Urdu on short utterances. Rank
                # only the two supported languages, then pin decoding so later
                # segments cannot switch to an unintended script.
                _, _, probabilities=self._model.detect_language(audio=audio,vad_filter=False)
                supported=[(code,score) for code,score in probabilities if code in ('hi','en')]
                if not supported:
                    raise RuntimeError('Hindi/English language detection failed. Please try again.')
                language=max(supported,key=lambda item:item[1])[0]
            segments, info = self._model.transcribe(
                audio,
                language=language,
                task='transcribe',
                multilingual=False,
                beam_size=5,
                temperature=0.0,
                # The recorder already performs VAD and retains pre-roll/tail.
                # A second VAD can remove quiet syllables and intra-sentence words.
                vad_filter=False,
                initial_prompt=INITIAL_PROMPT,
                condition_on_previous_text=False,
            )
            # Apply metadata before any text can reach the agent. A short command
            # is allowed when confidence is strong; text length alone is not a veto.
            accepted = []
            cutoff = max(min_logprob, -.65) if short else min_logprob
            for segment in segments:
                if (segment.no_speech_prob <= max_no_speech and segment.avg_logprob >= cutoff
                        and segment.compression_ratio <= 2.4):
                    text = segment.text.strip()
                    if any(character.isalnum() for character in text):
                        accepted.append(text)
            text = " ".join(accepted).strip()
        return text, info.language


class VoiceInput:
    """One listen -> transcribe cycle at a time, reporting progress through post()."""

    def __init__(self, transcriber: Transcriber, post: Post, settings: Settings | None = None, playback_idle: threading.Event | None = None, predecessor: threading.Event | None = None) -> None:
        self.transcriber = transcriber
        self.post = post
        self.settings = settings or Settings()
        self.playback_idle = playback_idle
        self.predecessor = predecessor
        self.capture_closed = threading.Event()
        self.capture_closed.set()
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def active(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.active:
            return
        self._cancel = threading.Event()
        self.capture_closed.clear()
        self._thread = threading.Thread(target=self._run, args=(self._cancel,), name="mitraa-listen", daemon=True)
        self._thread.start()

    def cancel(self) -> None:
        self._cancel.set()

    def close(self) -> None:
        self.cancel()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=2)

    def _run(self, cancel: threading.Event) -> None:
        try:
            self.post(('voice_state', 'loading_voice'))
            # No capture during loading or while the speaker is still purging.
            while not self.transcriber.ready.wait(.1):
                if cancel.is_set():
                    return
            if cancel.is_set():
                return
            if self.transcriber.error:
                raise RuntimeError(self.transcriber.error)
            if self.playback_idle:
                while not self.playback_idle.wait(.1):
                    if cancel.is_set():
                        return
            if cancel.is_set():
                return
            if self.predecessor:
                while not self.predecessor.wait(.1):
                    if cancel.is_set(): return
            if cancel.is_set(): return
            endpointer = Endpointer.from_settings(self.settings)
            outcome, audio = record_until_silence(
                endpointer, lambda rms: self.post(('voice_level', rms)), cancel,
                on_state=lambda state: self.post(('voice_state', state)),
                vad_mode=self.settings.mitraa_vad_mode)
            self.capture_closed.set()
            if cancel.is_set() or outcome == 'cancelled':
                return
            if audio is None or not len(audio):
                self.post(('voice_none', "I didn’t catch anything. Tap the microphone to try again."))
                return
            # Recorder has exited its stream context before this event is sent.
            self.post(('voice_endpoint', outcome))
            self.post(('voice_state', 'transcribing'))
            text, _ = self.transcriber.transcribe(
                audio, short=endpointer.short_speech,
                min_logprob=self.settings.mitraa_stt_min_logprob,
                max_no_speech=self.settings.mitraa_stt_max_no_speech)
            if not cancel.is_set():
                if text:
                    from mitraa.assistant.transcript_display import romanize_hindi
                    display=romanize_hindi(text)
                    if not cancel.is_set():
                        self.post(('voice_text', {'text':text,'display':display}))
                else:
                    self.post(('voice_none', "I couldn’t make that out. Tap the microphone to try again."))
        except Exception as exc:
            if not cancel.is_set():
                logger.exception('Voice input failed')
                self.post(('voice_error', _microphone_hint(exc)))
        finally:
            self.capture_closed.set()


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
        self.post: Post = lambda event: None
        self._generation = 0
        self._state_lock = threading.Lock()
        self.idle = threading.Event()
        self.idle.set()
        self._thread: threading.Thread | None = None
        self.voice_name = voice_name
        self.rate = rate
        self.enabled = True
        self.available = sys.platform == "win32"
        self.voice_description = ""
        self.error = "Speech output is unavailable on this platform." if not self.available else ""
        self._queue: queue.Queue[Any] = queue.Queue()
        self._interrupt = threading.Event()
        if self.available:
            self._thread = threading.Thread(target=self._run, name="mitraa-speaker", daemon=True)
            self._thread.start()

    def say(self, text: str) -> None:
        if not self.enabled:
            return
        if not self.available:
            self.post(("speech_error", self.error or "Speech output is unavailable."))
            return
        spoken = speakable(text)
        if spoken:
            with self._state_lock:
                self.idle.clear()
                self._interrupt.set()
                self._queue.put((spoken, self.post, self._generation))

    def stop(self) -> None:
        with self._state_lock:
            self._generation += 1
            self._interrupt.set()

    def close(self) -> None:
        self._interrupt.set()
        self._queue.put(None)
        if self._thread:
            self._thread.join(timeout=2)

    def _run(self) -> None:
        try:
            import comtypes
            import comtypes.client

            comtypes.CoInitialize()
            voice = comtypes.client.CreateObject("SAPI.SpVoice")
            self._select_voice(voice, comtypes.client)
            voice.Rate = max(-10, min(10, self.rate))
        except Exception as exc:
            self.error = f"Speech output unavailable: {exc}"
            logger.exception("Text-to-speech unavailable")
            self.available = False
            self.idle.set()
            while not self._queue.empty():
                pending = self._queue.get_nowait()
                if pending is not None:
                    pending[1](("speech_error", self.error))
            return

        while True:
            text = self._queue.get()
            if text is None:
                break
            while not self._queue.empty():  # only the newest reply matters
                newer = self._queue.get()
                if newer is None:
                    voice.Speak("", self._ASYNC | self._PURGE)
                    comtypes.CoUninitialize()
                    return
                text = newer
            text, post, generation = text
            try:
                with self._state_lock:
                    if generation != self._generation:
                        self.idle.set()
                        continue
                    self._interrupt.clear()
                    voice.Speak(text, self._ASYNC | self._PURGE)
                started = False
                stopped = False
                while True:
                    # SAPI's running state confirms playback, rather than estimating
                    # a duration or treating the queued Speak call as sound output.
                    if not started and voice.Status.RunningState == 2:
                        started = True
                        post(("speech_started", None))
                    if voice.WaitUntilDone(30):
                        break
                    if self._interrupt.is_set():
                        voice.Speak("", self._ASYNC | self._PURGE)
                        stopped = True
                        break
                self.idle.set()
                post(("speech_stopped" if stopped else "speech_finished", None))
            except Exception as exc:
                self.idle.set()
                post(("speech_error", str(exc)))
                logger.exception("Speaking failed")
        voice.Speak("", self._ASYNC | self._PURGE)
        comtypes.CoUninitialize()

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
    speaker = None
    if settings.mitraa_voice_output:
        folder=Path(settings.mitraa_tts_model_dir)
        neural=settings.mitraa_tts_backend=='kokoro' or (
            settings.mitraa_tts_backend=='auto' and all((folder/name).is_file() for name in ('kokoro-v1.0.onnx','voices-v1.0.bin')))
        if neural:
            from mitraa.assistant.neural_speaker import NeuralSpeaker
            speaker=NeuralSpeaker(folder,settings.mitraa_neural_voice,settings.mitraa_neural_speed)
        else:
            speaker=Speaker(settings.mitraa_tts_voice, settings.mitraa_tts_rate)
    return Voice(transcriber, speaker)
