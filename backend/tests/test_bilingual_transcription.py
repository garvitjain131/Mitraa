"""Decoder contracts only; real accent/noise accuracy requires speech samples."""
from types import SimpleNamespace as NS
import numpy as np
import pytest
from mitraa.assistant.voice import Transcriber, INITIAL_PROMPT

@pytest.mark.parametrize('language,expected,multilingual',[
    ('','hi',False),('auto','hi',False),('hi','hi',False),('en','en',False)])
def test_bilingual_decoder_retains_audio_and_original_script(language,expected,multilingual):
    transcriber=Transcriber('small','cpu',language)
    transcriber.ready.set()
    audio=np.ones(16000,dtype=np.float32)*.02
    words=['नमस्ते मित्रा।','Please open Downloads.','मेरी file कहाँ है?']
    class Model:
        def detect_language(self,**options):
            assert options['audio'] is audio and not options['vad_filter']
            # Urdu wins unrestricted detection, but must not win decoding.
            return 'ur',.8,[('ur',.8),('hi',.15),('en',.05)]
        def transcribe(self,received,**options):
            assert received is audio
            assert options['language']==expected
            assert options['multilingual']==multilingual
            assert options['task']=='transcribe'
            assert options['vad_filter'] is False
            assert options['temperature']==0.0
            assert options['beam_size']==5
            assert 'हिंदी' in options['initial_prompt']
            segments=[NS(text=text,no_speech_prob=.01,avg_logprob=-.2,compression_ratio=1.) for text in words]
            return iter(segments),NS(language='hi')
    transcriber._model=Model()
    assert transcriber.transcribe(audio)==(' '.join(words),'hi')


def test_english_wins_over_other_unsupported_languages():
    transcriber=Transcriber('small','cpu',''); transcriber.ready.set()
    class Model:
        def detect_language(self,**options):
            return 'fr',.6,[('fr',.6),('en',.35),('hi',.05)]
        def transcribe(self,audio,**options):
            assert options['language']=='en' and not options['multilingual']
            return iter([NS(text='Hello!',no_speech_prob=.01,avg_logprob=-.2,compression_ratio=1.)]),NS(language='en')
    transcriber._model=Model()
    assert transcriber.transcribe(np.ones(16000,dtype=np.float32))==('Hello!','en')
