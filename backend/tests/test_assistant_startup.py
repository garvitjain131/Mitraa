"""Keep desktop launch independent of agent/database/model imports."""
import subprocess
import sys
from pathlib import Path
from mitraa.assistant.startup import load_startup_settings


def test_startup_preferences_preserve_env_priority(monkeypatch,tmp_path):
    import mitraa.assistant.startup as module
    monkeypatch.setattr(module,'BACKEND_DIR',tmp_path)
    (tmp_path/'.env').write_text('MITRAA_HOTKEY=f12\nMITRAA_HOLD_SECONDS=3\n',encoding='utf-8')
    monkeypatch.setenv('MITRAA_HOTKEY','ctrl_r')
    monkeypatch.setenv('MITRAA_VOICE_PRELOAD','false')
    monkeypatch.delenv('MITRAA_HOLD_SECONDS',raising=False)
    settings=load_startup_settings()
    assert settings.mitraa_hotkey=='ctrl_r' and settings.mitraa_hold_seconds==3
    assert not settings.mitraa_voice_preload


def test_desktop_and_worker_import_without_loading_backend():
    code='''
import sys
from mitraa.assistant.app import main
from mitraa.assistant.ui.controller import Controller
from mitraa.assistant.worker import AgentWorker
worker=AgentWorker(lambda event:None)
worker.shutdown()
for name in ('pydantic','openai','sqlalchemy','ortools','numpy','faster_whisper','mitraa.config'):
    assert name not in sys.modules, name
'''
    result=subprocess.run([sys.executable,'-c',code],cwd=Path(__file__).resolve().parents[1],capture_output=True,text=True,timeout=20)
    assert result.returncode==0,result.stderr
