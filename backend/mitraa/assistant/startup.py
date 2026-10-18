"""Lightweight launch preferences; full backend settings load only when needed."""
from dataclasses import dataclass
import os
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]

@dataclass(frozen=True)
class StartupSettings:
    mitraa_hotkey: str = 'ctrl'
    mitraa_hold_seconds: float = 3.0
    mitraa_log_level: str = 'INFO'
    mitraa_voice_preload: bool = True


def load_startup_settings():
    from dotenv import dotenv_values
    values = {key.lower(): value for key,value in dotenv_values(BACKEND_DIR / '.env').items() if value is not None}
    values.update({key.lower():value for key,value in os.environ.items()})
    hold = float(values.get('mitraa_hold_seconds', '3'))
    if hold <= 0:
        raise ValueError('MITRAA_HOLD_SECONDS must be positive.')
    return StartupSettings(values.get('mitraa_hotkey','ctrl'),hold,values.get('mitraa_log_level','INFO'),
                           values.get('mitraa_voice_preload','true').lower() in ('1','true','yes','on'))
