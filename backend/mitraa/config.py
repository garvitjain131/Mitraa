"""Settings loaded from backend/.env (and the process environment)."""

from __future__ import annotations

import os
import sys
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent

# GUIDs for SHGetKnownFolderPath, used when a folder is redirected (e.g. OneDrive).
_KNOWN_FOLDER_GUIDS = {
    "documents": "FDD39AD0-238F-46AF-ADB4-6C85480369C7",
    "downloads": "374DE290-123F-4565-9164-39C4925E467B",
    "desktop": "B4BFCC3A-DB2C-424C-B029-7FE99A87C641",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    nebius_api_key: str = ""
    nebius_base_url: str = ""
    nemotron_model: str = ""

    mitraa_allowed_roots: str = "~/Documents;~/Downloads;~/Desktop"
    mitraa_max_steps: int = 8
    mitraa_bulk_threshold: int = 5
    mitraa_db_url: str = f"sqlite:///{(BACKEND_DIR / 'mitraa.db').as_posix()}"
    mitraa_history_limit: int = 40
    mitraa_llm_timeout: float = 60.0
    mitraa_llm_retries: int = 2
    # Desktop assistant: hold any of these keys to summon it (comma-separated).
    mitraa_hotkey: str = "ctrl"
    mitraa_hold_seconds: float = 3.0
    mitraa_log_level: str = "INFO"  # DEBUG also logs Ctrl hold start/release (never other keys)
    # Voice: speak after summoning; Mitraa answers out loud.
    mitraa_voice_input: bool = True
    mitraa_voice_output: bool = True
    mitraa_stt_model: str = "small"  # Whisper size: tiny | base | small | medium (multilingual)
    mitraa_stt_device: str = "auto"  # auto (GPU if CUDA works, else CPU) | cuda | cpu
    mitraa_stt_language: str = ""  # empty = auto-detect (English, Hindi, Hinglish, ...)
    mitraa_tts_voice: str = "Heera"  # part of a Windows voice name, e.g. Heera, Ravi, Zira
    mitraa_tts_rate: int = 1  # -10 (slow) .. 10 (fast)

    @property
    def allowed_roots(self) -> list[Path]:
        roots: list[Path] = []
        for raw in self.mitraa_allowed_roots.split(";"):
            raw = raw.strip().strip('"')
            if raw:
                roots.append(_locate_root(raw))
        return roots


def _locate_root(raw: str) -> Path:
    path = Path(os.path.expanduser(raw))
    if not path.exists() and sys.platform == "win32":
        known = _windows_known_folder(path.name)
        if known is not None:
            return known
    return path


def _windows_known_folder(name: str) -> Path | None:
    guid_text = _KNOWN_FOLDER_GUIDS.get(name.lower())
    if guid_text is None:
        return None
    try:
        import ctypes
        import uuid

        guid = (ctypes.c_byte * 16).from_buffer_copy(uuid.UUID(guid_text).bytes_le)
        out = ctypes.c_wchar_p()
        if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(out)) != 0:
            return None
        value = out.value
        ctypes.windll.ole32.CoTaskMemFree(out)
        return Path(value) if value else None
    except (OSError, AttributeError, ValueError):
        return None


@lru_cache
def get_settings() -> Settings:
    return Settings()
