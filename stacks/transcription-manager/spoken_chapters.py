"""Recognize spoken chapter headings in timestamped text, never topic segments.

Design references (independently implemented, no upstream code copied):
https://github.com/Inguiotar/ABChapterize -- announcements, numbering and gaps
https://github.com/stickystyle/chapter_marker -- heading evidence from ASR segments
EPUB NCX/nav headings are optional corroborating evidence.
"""
import re
import unicodedata
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

ALGORITHM = 'spoken-headings-v2'
UNITS = dict(zip('zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen'.split(), range(20)))
TENS = dict(zip('twenty thirty forty fifty sixty seventy eighty ninety'.split(), range(20, 100, 10)))
ORDINALS = dict(zip('first second third fourth fifth sixth seventh eighth ninth tenth eleventh twelfth thirteenth fourteenth fifteenth sixteenth seventeenth eighteenth nineteenth twentieth thirtieth fortieth fiftieth sixtieth seventieth eightieth ninetieth'.split(), [*range(1,20),20,30,40,50,60,70,80,90]))
NUMBER_WORDS = {**UNITS, **TENS, **ORDINALS}
CN = {'零':0,'〇':0,'一':1,'二':2,'两':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9}
PROSE = {'day','days','of','more','thing','things','time','times','man','woman','was','were','is','are','had','has','have','would','could','will','can','said','says','looked','went','wanted','felt','knew'}
CONNECTORS = {'a','an','the','and','or','of','to','in','on','at','for','with'}


def normalized(text):
    return re.sub(r'[^\w\u3400-\u9fff]+', ' ', unicodedata.normalize('NFKC', text).casefold()).strip()


def number_prefix(text, homophones=False):
    text = text.lstrip()
    match = re.match(r'(\d{1,4})(?=\W|$)', text)
    if match: return int(match[1]), match.end()
    match = re.match(r'[IVXLCDM]+(?=\W|$)', text)
    if match:
        value=0; previous=0
        for char in reversed(match[0]):
            n={'I':1,'V':5,'X':10,'L':50,'C':100,'D':500,'M':1000}[char]
            value += -n if n<previous else n; previous=max(n,previous)
        if 0<value<=999:return value,match.end()
    value=0; consumed=0; seen=False
    for token in re.finditer(r'[A-Za-z]+|[-\s]+',text):
        word=token[0].lower()
        if re.fullmatch(r'[-\s]+',word): continue
        if word=='and' and seen:
            consumed=token.end();continue
        if word in NUMBER_WORDS:
            # "one two" is not the number twelve; only legal additive tens.
            n=NUMBER_WORDS[word]
            if seen and n<20 and value%100 not in range(20,100,10):break
            if seen and n>=20 and value%100:break
            value+=n;consumed=token.end();seen=True
        elif word=='hundred' and seen and 0<value<10:
            value*=100;consumed=token.end()
        elif not seen and homophones and word in {'to','too','for','won'}:
            return {'to':2,'too':2,'for':4,'won':1}[word],token.end()
        else:break
    return (value,consumed) if seen and 0<value<=999 else (None,0)


def chinese_number(text):
    if text.isdigit():return int(text)
    result=0;digit=0
    for char in text:
        if char in CN:digit=CN[char]
        elif char in {'十','百'}:result+=(digit or 1)*({'十':10,'百':100}[char]);digit=0
        else:return None
    return result+digit


def short_title(text, toc=(), relaxed=False):
    text=re.sub(r'\s+',' ',text).strip(' .:;!?—–-\"“”')
    if not text:return None
    if normalized(text) in {normalized(t) for t in toc}:return text
    words=re.findall(r"[A-Za-z][A-Za-z'’\-]*",text)
    if re.search(r'[,，;；\"“”]|[.!?。！？].*\S',text) or len(text)>75:return None
    if words:
        if not 1<=len(words)<=9:return None
        significant=[w for w in words if w.lower() not in CONNECTORS]
        if not significant or any(w.lower() in PROSE or w.lower().endswith(("n't","n’t")) for w in significant):return None
        if words[0].lower() in {'he','she','they','we','you','it','dear'}:return None
        capitals=sum(w[0].isupper() for w in significant)
        if capitals>=max(1,len(significant)*.65):return text
        if relaxed and len(words)<=3 and len(significant)>=2:return text
        return None
    return text if 2<=len(text)<=18 and re.fullmatch(r'[\u3400-\u9fff ·]+',text) else None


def read_toc(folder):
    titles=[]
    for book in sorted(Path(folder).glob('*.epub'))[:2]:
        try:
            with zipfile.ZipFile(book) as archive:
                for entry in archive.infolist():
                    if entry.file_size>2_000_000:continue
                    if not (entry.filename.lower().endswith('.ncx') or re.search(r'(?:^|/)(?:nav|toc)\.x?html$',entry.filename,re.I)):continue
                    root=ET.fromstring(archive.read(entry))
                    for node in root.iter():
                        tag=node.tag.rsplit('}',1)[-1]
                        if tag not in {'navLabel','a'}:continue
                        title=' '.join(''.join(node.itertext()).split())
                        if 0<len(title)<=120:titles.append(title)
        except (OSError,ValueError,ET.ParseError,zipfile.BadZipFile):continue
    return list(dict.fromkeys(titles))


def candidates(cues,toc=()):
    result=[]
    for index,(start,end,text) in enumerate(cues):
        text=' '.join(text.split()).strip()
        original=text; consumed=1
        if re.fullmatch(r'(?:chapter|part|section|book)\s*[.:]?',text,re.I) and index+1<len(cues) and cues[index+1][0]-end<=3:
            text+=' '+cues[index+1][2];end=cues[index+1][1];consumed=2
        prefix=re.match(r'^(?:part\s+(?:\d+|[a-z-]+)[.:,\s]+)?(chapter|section|part|book)\s+',text,re.I)
        cn=re.match(r'^第([零〇一二两三四五六七八九十百\d]+)(章|节|卷|部|回)[\s、.:：]*',text)
        number=None;tail='';kind='number';label='';strength='bare'
        if cn:
            number=chinese_number(cn[1]);tail=text[cn.end():];label=cn[0].strip(' 、.:：');kind='chapter';strength='explicit'
        elif prefix:
            number,used=number_prefix(text[prefix.end():],True)
            if number is not None:
                label=text[:prefix.end()+used].strip(' .:');tail=text[prefix.end()+used:];kind=prefix[1].lower();strength='explicit'
        else:
            number,used=number_prefix(text)
            if number is not None:label=text[:used];tail=text[used:]
        if number is None:
            known=next((title for title in toc if normalized(title)==normalized(text)),None)
            if known:result.append(dict(start=start,end=end,title=known,number=None,kind='toc',strength='toc',spoken_text=original,evidence=['电子书目录与朗读文字一致'],confidence=.99))
            continue
        tail=tail.strip(' .:：—–-,、')
        # Narrative use of chapter numbers is not a heading.
        if tail and re.match(r'^(?:was|were|is|are|had|has|says|said|of|in|would|will)\b',tail,re.I):continue
        if strength=='bare' and tail and not short_title(tail,toc,True):continue
        title=short_title(tail,toc,relaxed=False) if tail else None
        next_index=index+consumed
        if not title and tail:
            first_sentence=re.split(r'[.!?。！？]',tail,maxsplit=1)[0]
            title=short_title(first_sentence,toc)
        if not title and tail:
            prefix_tail=re.split(r'\b(?:This|That|He|She|They|It|We|When|As|I)\b',tail,maxsplit=1)[0].strip(' .:—–-')
            if prefix_tail and prefix_tail!=tail:title=short_title(prefix_tail,toc)
        if not tail and next_index<len(cues):
            a,b,line=cues[next_index]
            if 0<=a-end<=6 and b-a<=8:
                title=short_title(line,toc,relaxed=(strength=='bare'))
                if not title:
                    first_sentence=re.split(r'[.!?。！？]',line,maxsplit=1)[0]
                    title=short_title(first_sentence,toc)
                if not title:
                    prefix_line=re.split(r'\b(?:This|That|He|She|They|It|We|When|As|I)\b',line,maxsplit=1)[0].strip(' .:—–-')
                    if prefix_line and prefix_line!=line:title=short_title(prefix_line,toc)
        if strength=='bare' and not title and tail:title=short_title(tail,toc,True)
        gap=start-cues[index-1][1] if index else start
        bare_exact=not tail and not title
        evidence=['朗读中出现章节词和编号'] if strength=='explicit' else ['独立编号与短标题' if title else '独立编号']
        if gap>=.6:evidence.append('标题前存在停顿')
        if title:evidence.append('短标题符合标题形态')
        rendered=label if not title else label+' · '+title
        if cn and title:rendered=label+' · '+title
        result.append(dict(start=start,end=end,title=rendered,number=number,kind=kind,strength=strength,
                           spoken_text=original,evidence=evidence,confidence=.98 if strength=='explicit' else .75,
                           gap=gap,bare_exact=bare_exact))
    return result


def detect(cues,duration,toc=()):
    cues=sorted(cues); found=candidates(cues,toc); rejected=[]
    # Fast numbered lists / a read-out table of contents are not chapter breaks.
    crowded=set()
    for i in range(len(found)-2):
        group=found[i:i+3]
        if all(0<b['start']-a['start']<20 for a,b in zip(group,group[1:])):
            crowded.update(range(i,i+3))
    accepted=[dict(c) for i,c in enumerate(found) if c['strength'] in {'explicit','toc'} and i not in crowded]
    bare=[dict(c) for i,c in enumerate(found) if c['strength']=='bare' and i not in crowded]
    # Number-only headings need corroboration across the book, not a regex alone.
    chains=[]
    for item in bare:
        previous=next((chain for chain in reversed(chains) if chain[-1]['number']+1==item['number'] and item['start']-chain[-1]['start']>=25),None)
        if previous is None:chains.append([item])
        else:previous.append(item)
    for chain in chains:
        titled=sum(not x['bare_exact'] and '短标题符合标题形态' in x['evidence'] for x in chain)
        valid=chain[0]['number'] in {1,2} and ((len(chain)>=2 and titled==len(chain)) or (len(chain)>=3 and all(x['gap']>=.6 for x in chain)))
        if valid:
            for c in chain:
                c['confidence']=.94;c['evidence'].append('全书编号连续且时间顺序一致');accepted.append(c)
        else:
            for c in chain:c['review_reason']='裸编号缺少连续章节或标题证据';rejected.append(c)
    for i,c in enumerate(found):
        if i in crowded:rejected.append(dict(c,review_reason='连续短间隔编号，疑似目录朗读或正文列举'))
    accepted.sort(key=lambda c:c['start'])
    selected=[]
    for c in accepted:
        if selected and c['start']-selected[-1]['start']<20:
            if c['confidence']>selected[-1]['confidence']:selected[-1]=c
            else:rejected.append(dict(c,review_reason='与另一章节标记过近'))
            continue
        selected.append(c)
    result=[]
    if selected:
        if selected[0]['start']>5:result.append({'id':0,'start':0.0,'end':round(selected[0]['start'],3),'title':'开场（首个章标题之前）'})
        for index,c in enumerate(selected):
            start=0.0 if index==0 and c['start']<=5 else c['start']
            end=selected[index+1]['start'] if index+1<len(selected) else duration
            if end>start:result.append({'id':len(result),'start':round(start,3),'end':round(end,3),'title':c['title']})
    return {'algorithm':ALGORITHM,'chapters':result,'accepted':selected,'review':rejected,
            'message':f'识别到 {len(selected)} 个朗读章标题' if selected else '未找到可信的朗读章标题，未按时长或话题强行分章'}
