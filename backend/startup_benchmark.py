import subprocess
import sys
import time
code = '''
import time
start=time.perf_counter()
from mitraa.assistant.app import main
print('app imports',round(time.perf_counter()-start,3),flush=True)
from mitraa.assistant.ui.controller import run_qt
print('Qt imports',round(time.perf_counter()-start,3),flush=True)
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer
app=QApplication([])
def ready():
    print('READY',round(time.perf_counter()-start,3),flush=True)
    app.quit()
QTimer.singleShot(0,ready)
run_qt(False)
'''
start=time.perf_counter()
result=subprocess.run([sys.executable,'-c',code],capture_output=True,text=True,timeout=90,cwd=__import__('pathlib').Path(__file__).parent)
print(result.stdout)
print('Total including shutdown:',round(time.perf_counter()-start,3))
print(result.stderr[-1500:])
raise SystemExit(result.returncode)

