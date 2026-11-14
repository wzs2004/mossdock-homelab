"""Authenticated local WAV queue backed by one resident whisper.cpp Metal model."""
import atexit
import hashlib
import json
import os
import re
import secrets
import signal
import sys
import urllib.parse
import subprocess
import tempfile
import threading
import time
import urllib.request
import uuid
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE=Path(__file__).resolve().parent
sys.path.insert(0,str(BASE/'deps'))
from asr_quality import (PROFILE, wav_info, acoustic_silences, speech_spans, extract_words, regroup, quality_report, render_srt, suppress_silence)
CONFIG=json.loads((BASE/'config.json').read_text())
MODEL_PATH=Path(CONFIG.get('model_path',str(BASE/'model.bin')))
MODEL_NAME=CONFIG.get('model_name',MODEL_PATH.name)
JOBS={};LOCK=threading.RLock();ENGINE_LOCK=threading.RLock()
ACTIVE=None;ENGINE_PROC=None;ENGINE_READY=False
ENGINE_PREFIX='/'+hashlib.sha256((CONFIG['key']+'resident-engine').encode()).hexdigest()
ENGINE_URL='http://127.0.0.1:13386'+ENGINE_PREFIX
LOCAL_HTTP=urllib.request.build_opener(urllib.request.ProxyHandler({}))


def stop_engine():
    global ENGINE_READY
    proc=ENGINE_PROC;ENGINE_READY=False
    if proc and proc.poll() is None:
        proc.terminate()
        try:proc.wait(timeout=5)
        except subprocess.TimeoutExpired:proc.kill();proc.wait()


def engine_output(proc):
    global ENGINE_READY
    for raw in proc.stdout:
        line=raw.decode(errors='replace').strip()
        with LOCK:
            job=JOBS.get(ACTIVE)
            if job:
                job['log']=(job['log']+'\n'+line)[-12000:]
                found=re.search(r'progress\s*=\s*(\d+)%',line)
                if found:job['progress']=min(99,int(found[1]))
    proc.stdout.close()
    if ENGINE_PROC is proc:ENGINE_READY=False


def ensure_engine():
    global ENGINE_PROC,ENGINE_READY
    with ENGINE_LOCK:
        if ENGINE_READY and ENGINE_PROC and ENGINE_PROC.poll() is None:return
        if ENGINE_PROC is None or ENGINE_PROC.poll() is not None:
            command=[str(BASE/'bin/whisper-server'),'-m',str(MODEL_PATH),'-l','en','-t','4','-bs','5','-bo','5','-ml','100000','-sow','-nlp','-pp',
                     '--host','127.0.0.1','--port','13386','--request-path',ENGINE_PREFIX]
            ENGINE_PROC=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
            threading.Thread(target=engine_output,args=(ENGINE_PROC,),daemon=True).start()
        started=time.monotonic()
        while time.monotonic()-started<120:
            if ENGINE_PROC.poll() is not None:raise RuntimeError('常驻 Metal 引擎启动失败')
            try:
                with LOCAL_HTTP.open(ENGINE_URL+'/health',timeout=2) as response:
                    if response.status==200:ENGINE_READY=True;return
            except OSError:pass
            time.sleep(.25)
        stop_engine();raise RuntimeError('常驻 Metal 引擎加载超时')


def infer(wav, job, beam=5):
    boundary='asr-'+uuid.uuid4().hex
    parts=[]
    parameters=[('response_format','verbose_json'),('language',job['language']),
                ('beam_size',str(beam)),('best_of',str(beam)),('temperature','0'),
                ('max_len','100000'),('split_on_word','true'),('vad','false'),
                ('no_language_probabilities','true'),('prompt',job.get('prompt',''))]
    for name,value in parameters:
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="segment.wav"\r\nContent-Type: audio/wav\r\n\r\n'.encode())
    parts.extend([wav.read_bytes(),f'\r\n--{boundary}--\r\n'.encode()])
    request=urllib.request.Request(ENGINE_URL+'/inference',data=b''.join(parts),headers={'Content-Type':'multipart/form-data; boundary='+boundary})
    with LOCAL_HTTP.open(request,timeout=1800) as response:
        result=json.load(response)
    if not isinstance(result,dict) or not isinstance(result.get('segments'),list):
        raise RuntimeError('识别引擎未返回有效的词级时间戳 JSON')
    return result


def recognize(job_id,wav,folder):
    global ACTIVE
    job=JOBS[job_id]
    try:
        duration=wav_info(wav)
        job.update(stage='检测语音、静音与气口',progress=0)
        silences,peak=acoustic_silences(wav)
        spans=speech_spans(wav,BASE/'bin/whisper-vad-speech-segments',CONFIG.get('vad_model',str(BASE/'vad.bin')))
        if spans is None:
            raise RuntimeError('Silero 语音检测模型或程序缺失，请重新安装本机转写服务')
        if job['status']=='cancelled':return
        started=time.monotonic();retried=False
        if not spans and peak<60:
            words=[];warnings=[]
        else:
            ensure_engine()
            job.update(stage='高精度识别与词级对齐',progress=0)
            payload=infer(wav,job)
            words,warnings=extract_words(payload)
            report=quality_report(words,spans)
            # Retry genuine uncovered speech once; do not accept an empty success.
            uncovered=sum(z-a for a,z in report['uncovered_speech'])
            if spans and (not words or uncovered>max(3,duration*.12)):
                job.update(stage='复核疑似漏识别语音',progress=0)
                alternative=infer(wav,job,beam=8)
                newer,notes=extract_words(alternative)
                newer_report=quality_report(newer,spans)
                if newer and (not words or sum(z-a for a,z in newer_report['uncovered_speech'])<uncovered):
                    words,warnings=newer,notes
                retried=True
            if spans and not words:raise RuntimeError('检测到语音但两次识别均为空，已保留为失败供重试')
        if job['status']=='cancelled':return
        words=suppress_silence(words,silences,spans)
        job.update(stage='按句意、停顿与章节标题整理 SRT',progress=99)
        content=render_srt(regroup(words,silences))
        report=quality_report(words,spans);report.update(warnings=warnings,retried=retried,vad='silero-v6.2.0')
        job.update(status='done',progress=100,stage='识别完成',srt=content,words=words,silences=silences,
                   speech_spans=spans,quality=report,profile=PROFILE,model=MODEL_NAME,
                   inference_seconds=round(time.monotonic()-started,3))
    except Exception as exc:
        if job['status']!='cancelled':job.update(status='failed',error=str(exc))
    finally:
        with LOCK:ACTIVE=None
        import shutil
        shutil.rmtree(folder,ignore_errors=True)


class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def reply(self,code,value):
        body=json.dumps(value).encode();self.send_response(code)
        self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)))
        self.end_headers();self.wfile.write(body)
    def allowed(self):
        if not secrets.compare_digest(self.headers.get('X-ASR-Key',''),CONFIG['key']):self.reply(403,{'error':'Unauthorized'});return False
        return True
    def do_GET(self):
        if not self.allowed():return
        if self.path=='/health':return self.reply(200,{'engine':MODEL_NAME+' · resident Metal','model':MODEL_NAME,'profile':PROFILE,'active':ACTIVE,'ready':ENGINE_READY})
        job=JOBS.get(self.path.removeprefix('/jobs/'))
        if job is None:return self.reply(404,{'error':'Job not found'})
        self.reply(200,{**job,'elapsed_seconds':round(time.monotonic()-job['started'],2)})
    def do_POST(self):
        global ACTIVE
        if not self.allowed():return
        if self.path!='/jobs':return self.reply(404,{'error':'Unknown endpoint'})
        length=int(self.headers.get('Content-Length','0'));language=self.headers.get('X-ASR-Language','en')
        if not 44<=length<=64*1024*1024 or language not in {'en','zh','auto'}:return self.reply(400,{'error':'Expected en/zh/auto PCM WAV, at most 64 MB'})
        with LOCK:
            if ACTIVE:return self.reply(409,{'error':'Worker busy'})
            for key,job in list(JOBS.items()):
                if time.monotonic()-job['started']>3600:JOBS.pop(key,None)
            job_id=uuid.uuid4().hex;folder=Path(tempfile.mkdtemp(prefix='segment-',dir=BASE));wav=folder/'segment.wav'
            wav.write_bytes(self.rfile.read(length))
            with wav.open('rb') as source:valid=source.read(4)==b'RIFF'
            if wav.stat().st_size!=length or not valid:
                wav.unlink();folder.rmdir();return self.reply(400,{'error':'Incomplete WAV'})
            JOBS[job_id]={'status':'running','progress':0,'log':'','started':time.monotonic(),'language':language,'prompt':urllib.parse.unquote(self.headers.get('X-ASR-Prompt',''))[:800]}
            ACTIVE=job_id;threading.Thread(target=recognize,args=(job_id,wav,folder),daemon=True).start()
        self.reply(202,{'id':job_id})
    def do_DELETE(self):
        if not self.allowed():return
        key=self.path.removeprefix('/jobs/')
        with LOCK:
            job=JOBS.get(key)
            running=job and job['status']=='running'
            if running:job['status']='cancelled'
        if running:stop_engine()
        self.reply(200,{'ok':True})


if __name__=='__main__':
    os.environ['DYLD_LIBRARY_PATH']=str(BASE/'lib')
    atexit.register(stop_engine)
    signal.signal(signal.SIGTERM,lambda *_:exit(0))
    threading.Thread(target=ensure_engine,daemon=True).start()
    ThreadingHTTPServer(('127.0.0.1',13385),Handler).serve_forever()
