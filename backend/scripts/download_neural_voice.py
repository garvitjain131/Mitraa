"""Fetch Kokoro's published ONNX assets without executing downloaded code."""
import hashlib
import json
from pathlib import Path
import shutil
import urllib.request

ROOT=Path(__file__).resolve().parents[1]/'models'/'kokoro'
ROOT.mkdir(parents=True,exist_ok=True)
request=urllib.request.Request('https://api.github.com/repos/thewh1teagle/kokoro-onnx/releases/tags/model-files-v1.1',headers={'User-Agent':'Mitraa-voice-setup'})
with urllib.request.urlopen(request,timeout=60) as response:
    release=json.load(response)
records=[]
for name in ('kokoro-v1.0.onnx','voices-v1.0.bin'):
    asset=next(a for a in release['assets'] if a['name']==name)
    target=ROOT/name
    if not target.exists() or target.stat().st_size!=asset['size']:
        partial=target.with_suffix(target.suffix+'.part')
        print('Downloading',name,asset['size'],'bytes',flush=True)
        with urllib.request.urlopen(asset['browser_download_url'],timeout=120) as response, partial.open('wb') as output:
            shutil.copyfileobj(response,output)
        if partial.stat().st_size!=asset['size']: raise RuntimeError('Download size mismatch')
        partial.replace(target)
    digest=hashlib.file_digest(target.open('rb'),'sha256').hexdigest()
    expected=asset.get('digest')
    if expected and expected!='sha256:'+digest: raise RuntimeError('Published digest mismatch: '+name)
    records.append({'file':name,'url':asset['browser_download_url'],'sha256':digest,'publisher_digest':expected,'size':asset['size']})
    print('Verified',name,digest,flush=True)
(ROOT/'download-manifest.json').write_text(json.dumps(records,indent=2),encoding='utf8')
for name,url in {
    'MODEL_CARD.md':'https://huggingface.co/hexgrad/Kokoro-82M/raw/main/README.md',
    'VOICES.md':'https://huggingface.co/hexgrad/Kokoro-82M/raw/main/VOICES.md',
    'RUNTIME_LICENSE.txt':'https://raw.githubusercontent.com/thewh1teagle/kokoro-onnx/main/LICENSE',
}.items():
    with urllib.request.urlopen(url,timeout=60) as response:
        (ROOT/name).write_bytes(response.read())
