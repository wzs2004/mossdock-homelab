"""Timestamp-preserving ASR cleanup inspired by stable-ts pause/punctuation grouping.

Recognition remains whisper.cpp; Silero runs separately, so no silence-compressed
word timestamps reach LR. No language model rewrites or invents transcript text.
"""
import array
import bisect
import math
import re
import subprocess
import wave
from pathlib import Path

RECOGNITION_PROFILE = 'turbo-beam5-pause-itn-v1'
PROFILE = 'turbo-beam5-pause-itn-v2'
NUMBERS = ('zero oh one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty thirty forty fifty sixty seventy eighty ninety hundred thousand million billion trillion first second third fourth fifth sixth seventh eighth ninth tenth eleventh twelfth thirteenth fourteenth fifteenth sixteenth seventeenth eighteenth nineteenth twentieth thirtieth fortieth fiftieth sixtieth seventieth eightieth ninetieth hundredth thousandth millionth billionth point minus negative').split()
NUMBER_RE = re.compile(r'(?<!\w)(?:' + '|'.join(sorted(NUMBERS,key=len,reverse=True)) + r')\b(?:[\s-]+(?:and[\s-]+)?(?:' + '|'.join(sorted(NUMBERS,key=len,reverse=True)) + r')\b)*',re.I)
ABBREVIATIONS = {'mr.','mrs.','ms.','dr.','prof.','st.','sr.','jr.','vs.','etc.','e.g.','i.e.','a.m.','p.m.'}
CJK = re.compile(r'[\u3400-\u9fff]')


def normalize_numbers(text):
    from text_to_num import alpha2digit
    import cn2an
    def convert(match):
        phrase=match.group();lower=phrase.lower().replace('-',' ')
        before=text[:match.start()].lower().split();after=text[match.end():].lstrip().lower()
        if lower=='first' and before and before[-1] in {'at','little'}:return phrase
        if lower=='one' and ((before and before[-1] in {'no','any'}) or (before and before[-1]=='the' and re.match(r'(?:that|who|with|which)\b',after)) or re.match(r'(?:should|must|can|could|would|might|another)\b',after)):
            return phrase
        # ASR often writes spoken years as nineteen-thirty-two / twenty twenty-four.
        parts=lower.split();small={'zero':0,'oh':0}
        small.update({v:i for i,v in enumerate('zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen'.split())})
        small.update(dict(zip('twenty thirty forty fifty sixty seventy eighty ninety'.split(),range(20,100,10))))
        if len(parts)>=2 and parts[0] in {'nineteen','twenty'} and all(x in small for x in parts):
            tail=sum(small[x] for x in parts[1:])
            if (len(parts)==2 and small[parts[1]]>=10) or (len(parts)==3 and small[parts[1]]>=20 and small[parts[2]]<10):
                return str(small[parts[0]]*100+tail)
        lower=re.sub(r'\band\b\s*','',lower)
        result=alpha2digit(lower,'en',ordinal_threshold=0)
        return result if re.search(r'\d',result) else phrase
    text=NUMBER_RE.sub(convert,text)
    # Chinese numerals use the same digit policy; protect common non-numeric idioms.
    protected={}
    for phrase in ('万一','一旦','一直','一起','一般','一致','统一','一些','一切','一边','一会儿'):
        if phrase in text:
            key='\ue000'+chr(0xe100+len(protected))+'\ue001';protected[key]=phrase;text=text.replace(phrase,key)
    def chinese(match):
        try:return str(cn2an.cn2an(match.group(),'smart'))
        except ValueError:return match.group()
    text=re.sub(r'[负零〇一二两三四五六七八九十百千万亿点]+',chinese,text)
    for key,value in protected.items():text=text.replace(key,value)
    def roman(match):
        chars=match[2].upper();values={'I':1,'V':5,'X':10,'L':50,'C':100};n=0;previous=0
        for char in reversed(chars):
            value=values[char];n += -value if value<previous else value;previous=max(previous,value)
        return match[1]+str(n)
    return re.sub(r'(\b(?:chapter|part|section)\s+)([IVXLC]+)\b',roman,text,flags=re.I)


def wav_info(path):
    with wave.open(str(path),'rb') as stream:
        if stream.getsampwidth()!=2 or stream.getnchannels()!=1 or stream.getframerate()!=16000:
            raise ValueError('识别需要 16kHz 单声道 PCM16 WAV')
        return stream.getnframes()/16000


def acoustic_silences(path):
    energies=[];step=.02
    with wave.open(str(path),'rb') as stream:
        while True:
            data=stream.readframes(320)
            if not data:break
            values=array.array('h',data)
            energies.append(math.sqrt(sum(v*v for v in values)/max(1,len(values))))
    if not energies:return [],0
    ordered=sorted(energies);noise=ordered[int(len(ordered)*.15)];speech=ordered[int(len(ordered)*.85)]
    threshold=min(max(35,noise*2.2),max(35,speech*.16))
    gaps=[];start=None
    for i,energy in enumerate(energies+[float('inf')]):
        if energy<threshold and start is None:start=i*step
        elif energy>=threshold and start is not None:
            if i*step-start>=.16:gaps.append((start,i*step))
            start=None
    return gaps,max(energies)


def speech_spans(path, binary=None, model=None):
    if not binary or not model or not Path(binary).is_file() or not Path(model).is_file():
        return None
    result=subprocess.run([str(binary),'-f',str(path),'-vm',str(model),'-vt','0.35','-vp','150','-np'],capture_output=True,text=True,timeout=120,check=True)
    # The upstream helper prints centiseconds, despite using a floating format.
    spans=[(float(a)/100,float(z)/100) for a,z in re.findall(r'Speech segment \d+: start = ([\d.]+), end = ([\d.]+)',result.stdout)]
    if 'Detected ' not in result.stdout:raise RuntimeError('Silero 未返回可验证的语音区间')
    return spans


def extract_words(payload):
    """whisper.cpp verbose_json contains BPE pieces, not pre-joined words."""
    words=[];warnings=[]
    for segment in payload.get('segments',[]):
        pieces=segment.get('words') or []
        chunk=[]
        for token in pieces:
            raw=token.get('word','')
            if not raw or raw.startswith('[_'):continue
            a=float(token.get('start',-1));z=float(token.get('end',-1))
            valid=math.isfinite(a) and math.isfinite(z) and 0<=a<z
            probability=float(token.get('probability',0))
            if not raw.strip():continue
            starts=raw[0].isspace() or not chunk or bool(CJK.search(raw))
            if not valid:
                if chunk and not starts:chunk[-1]['text']+=raw
                elif chunk:
                    chunk[-1]['text']+=' '+raw.strip();chunk[-1]['uncertain']=True
                else:warnings.append('缺少词级时间戳：'+raw.strip())
                continue
            if starts:
                chunk.append({'start':a,'end':z,'text':raw.strip(),'probability':probability})
            else:
                chunk[-1]['text']+=raw;chunk[-1]['end']=max(chunk[-1]['end'],z);chunk[-1]['probability']=min(chunk[-1]['probability'],probability)
        if not chunk and segment.get('text','').strip():
            a=float(segment.get('start',0));z=float(segment.get('end',a))
            if z>a:
                chunk=[{'start':a,'end':z,'text':segment['text'].strip(),'probability':0,'uncertain':True}]
                warnings.append('部分区间仅有片段时间戳，需要复核')
        words.extend(chunk)
    return words,warnings


def suppress_silence(words,silences,spans=None):
    output=[]
    for original in words:
        word=dict(original);a=word['start'];z=word['end']
        for start,end in silences:
            if end-start<.25:continue
            if start<=a<end and end<z:a=end
            if start<z<=end and start>a:z=start
        if spans:
            overlaps=[(max(a,s),min(z,e)) for s,e in spans if min(z,e)>max(a,s)]
            if overlaps:a=max(a,overlaps[0][0]);z=min(z,overlaps[-1][1])
            else:word['uncertain']=True
        if z>a:word.update(start=a,end=z);output.append(word)
    return output


def text_of(words):
    text=' '.join(w['text'] for w in words)
    text=re.sub(r'\s+([,.;:!?，。；：！？])',r'\1',text)
    text=re.sub(r'([\u3400-\u9fff])\s+(?=[\u3400-\u9fff])',r'\1',text)
    return text.strip()


def terminal(word):
    text=word['text'].rstrip('\"\'”’)]')
    if text.lower() in ABBREVIATIONS or re.fullmatch(r'(?:[A-Za-z]\.){1,5}',text):return False
    return bool(re.search(r'[.!?。！？]$',text))


def pause_between(left,right,silences):
    a=left['end'];z=right['start'];gap=max(0,z-a)
    for s,e in silences:
        # Word clocks can slightly straddle a genuine breath pause.
        if s>=a-.15 and e<=z+.15:gap=max(gap,e-s)
    return gap


def chapter_ranges(words,silences):
    ranges=[];i=0
    while i<len(words):
        key=words[i]['text'].lower().strip('"\'“”.,:')
        if key not in {'chapter','part','section','foreword','afterword','prologue','epilogue'}:i+=1;continue
        j=i+1
        if key in {'chapter','part','section'}:
            while j<min(len(words),i+6):
                if j>i+1 and (terminal(words[j-1]) or pause_between(words[j-1],words[j],silences)>=.3):break
                value=words[j]['text'].strip('"\'“”.,:')
                if not (re.fullmatch(r'\d+(?:st|nd|rd|th)?|[IVXLC]+',value,re.I) or value.lower() in NUMBERS):break
                j+=1
            if j==i+1:i+=1;continue
        before=pause_between(words[i-1],words[i],silences) if i else 1
        after=pause_between(words[j-1],words[j],silences) if j<len(words) else 1
        if before>=.3 or after>=.3 or (i and terminal(words[i-1])):
            ranges.append((i,j));i=j
        else:i+=1
    return ranges


def regroup(words,silences=(),max_chars=150,max_seconds=12):
    words=[dict(w) for w in words if w.get('text','').strip() and w['end']>w['start']>=0]
    if not words:return []
    words.sort(key=lambda w:(w['start'],w['end']))
    headings=chapter_ranges(words,silences);forced={0,len(words)}
    for a,z in headings:forced.update((a,z))
    blocks=sorted(forced);output=[]
    for begin,end in zip(blocks,blocks[1:]):
        if (begin,end) in headings:
            output.append((words[begin]['start'],words[end-1]['end'],normalize_numbers(text_of(words[begin:end]))));continue
        size=end-begin;cost=[float('inf')]*(size+1);previous=[None]*(size+1);cost[0]=0
        for local_end in range(1,size+1):
            j=begin+local_end
            for i in range(j-1,max(begin-1,j-46),-1):
                duration=words[j-1]['end']-words[i]['start'];text=text_of(words[i:j]);length=len(text)
                if i<j-1 and (duration>max_seconds or length>max_chars):break
                if j<end:
                    last=words[j-1]['text'].strip('"\'“”.,:').lower();nextword=words[j]['text'].strip('"\'“”.,:').lower()
                    if last in NUMBERS and nextword in NUMBERS:continue
                    pause=pause_between(words[j-1],words[j],silences)
                    boundary=-3 if terminal(words[j-1]) else (-1.5 if pause>=1.2 else (-.2 if pause>=.45 else (1 if pause>=.22 else 5)))
                    if last in {'a','an','the','to','of','for','from','with','by','in','on','at','and','or','because','if','than','that','which','who','is','are','was','were','be','been','has','had','will','would','can','could','not'} and not terminal(words[j-1]):boundary+=10
                    if words[j-1]['text'][:1].isupper() and words[j]['text'][:1].isupper() and not terminal(words[j-1]):boundary+=5
                    if re.search(r'[,;:，；：]$',words[j-1]['text']) and duration>=3:boundary-=1.5
                    if nextword in {'but','however','although','because','yet','while'} and duration>=5:boundary-=.8
                else:boundary=0
                short=3 if duration<1 and not terminal(words[j-1]) else 0
                score=cost[i-begin]+1+boundary+short+.005*length
                if score<cost[local_end]:cost[local_end]=score;previous[local_end]=i-begin
        segments=[];j=size
        while j:
            i=previous[j]
            if i is None:i=j-1
            segment=words[begin+i:begin+j];segments.append((segment[0]['start'],segment[-1]['end'],normalize_numbers(text_of(segment))))
            j=i
        output.extend(reversed(segments))
    cleaned=[]
    for a,z,text in output:
        adjusted=suppress_silence([{'start':a,'end':z,'text':text}],silences)
        if adjusted:a,z=adjusted[0]['start'],adjusted[0]['end']
        if cleaned and a<cleaned[-1][1]:
            a=max(a,cleaned[-1][1])
        if z>a and text:cleaned.append((a,z,text))
    return cleaned


def quality_report(words,spans):
    lows=[{'start':round(w['start'],3),'end':round(w['end'],3),'text':w['text']} for w in words if w.get('probability',1)<.35 or w.get('uncertain')]
    uncovered=[]
    if spans is not None:
        intervals=sorted((w['start'],w['end']) for w in words)
        for a,z in spans:
            cursor=a
            for s,e in intervals:
                if e<=cursor:continue
                if s>=z:break
                if s-cursor>=1.2:uncovered.append([round(cursor,3),round(min(s,z),3)])
                cursor=max(cursor,e)
            if z-cursor>=1.2:uncovered.append([round(cursor,3),round(z,3)])
    return {'profile':PROFILE,'low_confidence':lows,'uncovered_speech':uncovered,'review_count':len(lows)+len(uncovered)}


def srt_time(value):
    total=max(0,round(value*1000));hours,total=divmod(total,3600000);minutes,total=divmod(total,60000);seconds,millis=divmod(total,1000)
    return f'{hours:02}:{minutes:02}:{seconds:02},{millis:03}'


def render_srt(cues):
    return '\n'.join(f'{i}\n{srt_time(a)} --> {srt_time(z)}\n{text}\n' for i,(a,z,text) in enumerate(cues,1))


def cli_to_verbose(value):
    segments=[]
    for entry in value.get('transcription',[]):
        span=entry.get('offsets') or {}
        segment={'text':entry.get('text',''),'start':span.get('from',0)/1000,'end':span.get('to',0)/1000,'words':[]}
        for token in entry.get('tokens') or []:
            bounds=token.get('offsets') or {}
            segment['words'].append({'word':token.get('text',''),'start':bounds.get('from',-1000)/1000,'end':bounds.get('to',-1000)/1000,'probability':token.get('p',0)})
        segments.append(segment)
    return {'segments':segments}


def core_words(words,offset,begin,end,chunk):
    output=[]
    for word in words:
        a=word['start']+offset;z=word['end']+offset
        if begin<=(a+z)/2<end:
            output.append(dict(word,start=max(0,a),end=min(end+1.5,z),chunk=chunk))
    return output


def deduplicate_overlap(words):
    output=[]
    for word in sorted(words,key=lambda w:(w['start'],w['end'])):
        if output:
            last=output[-1];overlap=min(last['end'],word['end'])-max(last['start'],word['start'])
            if last.get('chunk')!=word.get('chunk') and last['text'].casefold()==word['text'].casefold() and overlap>.35*min(last['end']-last['start'],word['end']-word['start']):
                if word.get('probability',0)>last.get('probability',0):output[-1]=word
                continue
        output.append(word)
    return output
