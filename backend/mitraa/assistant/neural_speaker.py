"""Local Kokoro neural playback with the same lifecycle as Windows speech."""
import logging
from pathlib import Path
import queue
import threading
import numpy as np
from mitraa.assistant.voice import Speaker

logger=logging.getLogger(__name__)

def load_engine(folder):
    from kokoro_onnx import Kokoro
    import onnxruntime as ort
    options=ort.SessionOptions()
    options.intra_op_num_threads=2
    options.inter_op_num_threads=1
    session=ort.InferenceSession(str(Path(folder)/'kokoro-v1.0.onnx'),sess_options=options,providers=['CPUExecutionProvider'])
    return Kokoro.from_session(session,str(Path(folder)/'voices-v1.0.bin'))

class NeuralSpeaker(Speaker):
    def __init__(self, folder, voice='bm_george', speed=.96):
        self.folder=folder; self.neural_voice=voice; self.speed=speed
        self._closing=threading.Event()
        super().__init__(voice,0)

    def close(self):
        self._closing.set(); self.stop()
        super().close()

    def _run(self):
        try:
            engine=load_engine(self.folder)
            self.voice_description=f'Kokoro {self.neural_voice} (local neural)'
            logger.info('Speaking with voice: %s',self.voice_description)
        except Exception:
            logger.exception('Local neural voice initialization failed')
            self.available=False; self.error='Local neural voice unavailable. Check the downloaded model and neural voice dependencies.'
            self.idle.set()
            while True:
                item=self._queue.get()
                if item is None: return
                item[1](('speech_error',self.error))
        while not self._closing.is_set():
            item=self._queue.get()
            if item is None: return
            while not self._queue.empty():
                item=self._queue.get()
                if item is None: self.idle.set(); return
            text,post,generation=item
            with self._state_lock:
                if generation!=self._generation: self.idle.set(); continue
                self._interrupt.clear()
            try:
                samples,rate=engine.create(text,voice=self.neural_voice,speed=self.speed,lang='en-gb')
                if self._closing.is_set() or self._interrupt.is_set():
                    self.idle.set(); post(('speech_stopped',None)); continue
                self._play(np.asarray(samples,dtype=np.float32),rate,post,generation)
            except Exception:
                self.idle.set()
                logger.exception('Neural speech failed')
                post(('speech_error','Could not play the local neural voice. Please retry.'))

    def _play(self,samples,rate,post,generation):
        import sounddevice as sd
        started=threading.Event(); cursor=0; level=0.
        def callback(out,frames,timing,status):
            nonlocal cursor,level
            out.fill(0)
            if self._interrupt.is_set() or self._closing.is_set(): raise sd.CallbackAbort
            count=min(frames,len(samples)-cursor)
            if count:
                block=samples[cursor:cursor+count]
                out[:count,0]=block; cursor+=count
                level=float(np.sqrt(np.mean(block*block)))
                started.set()
            if cursor>=len(samples): raise sd.CallbackStop
        stream=sd.OutputStream(samplerate=rate,channels=1,dtype='float32',blocksize=1024,callback=callback)
        announced=False
        try:
            with self._state_lock:
                if generation!=self._generation or self._interrupt.is_set():
                    post(('speech_stopped',None)); return
                stream.start()
            while stream.active:
                if started.is_set() and not announced:
                    post(('speech_started',None)); announced=True
                if announced: post(('speech_level',level))
                if self._interrupt.wait(.03) or self._closing.is_set():
                    stream.abort(); break
            if started.is_set() and not announced: post(('speech_started',None))
        finally:
            stream.close(); self.idle.set()
        post(('speech_stopped' if self._interrupt.is_set() or self._closing.is_set() else 'speech_finished',None))
