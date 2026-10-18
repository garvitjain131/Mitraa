"""Fresh normal-desktop launch benchmark; no recording, model or agent request.
Run from backend: python -m tests.startup_benchmark
Briefly registers the hotkey/tray, measures ready, then quits cleanly.
"""
import subprocess
import sys
import time
from pathlib import Path
CODE = '''
import time
start=time.perf_counter()
from mitraa.assistant.app import main
from mitraa.assistant.ui.controller import run_qt
from PySide6.QtWidgets import QApplication

def ready():
    import sys
    for name in ('numpy','sqlalchemy','openai','ortools','faster_whisper','mitraa.config'):
        assert name not in sys.modules, name
    print('READY',round(time.perf_counter()-start,3),flush=True)
    QApplication.instance().quit()
run_qt(False,on_ready=ready)
'''
if __name__=='__main__':
    start=time.perf_counter()
    child=subprocess.Popen([sys.executable,'-c',CODE],cwd=Path(__file__).resolve().parents[1],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    line=child.stdout.readline().strip()
    elapsed=time.perf_counter()-start
    stdout,stderr=child.communicate(timeout=30)
    if child.returncode or not line.startswith('READY'):
        raise SystemExit(stderr or stdout or 'Desktop did not reach ready.')
    print(f'Desktop ready: {elapsed:.3f}s from process launch ({line.split()[1]}s inside Python).')
    if stderr: print(stderr)
