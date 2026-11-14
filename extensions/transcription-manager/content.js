(async () => {
  const dashboard = location.protocol === 'chrome-extension:';
  if (!dashboard) {
    const {absUrl} = await chrome.storage.local.get('absUrl');
    if (!absUrl || location.origin !== new URL(absUrl).origin) return;
  }
  if (document.getElementById('abs-book-tools')) return;
  const root = document.createElement('div'); root.id = 'abs-book-tools'; document.documentElement.append(root);
  const dom = root.attachShadow({mode:'open'});
  const style = document.createElement('style');
  style.textContent = `:host{font:14px/1.5 system-ui;color:#e6eef5}*{box-sizing:border-box}button,input{font:inherit}button{cursor:pointer;padding:7px 10px;border:1px solid #54718c;background:#29445e;color:white;border-radius:7px}button:disabled{opacity:.5;cursor:default}input{padding:8px;background:#142538;color:white;border:1px solid #54718c;border-radius:5px}#toggle{position:fixed;right:190px;bottom:22px;z-index:2147483645}#panel{position:fixed;right:14px;top:65px;width:min(820px,calc(100vw - 28px));height:calc(100vh - 85px);z-index:2147483646;background:#101f30;border:1px solid #54718c;border-radius:12px;display:flex;flex-direction:column;box-shadow:0 15px 70px #0009}#panel[hidden],[hidden]{display:none!important}header{padding:15px;background:#1b324a;display:flex;gap:8px;align-items:center;flex-wrap:wrap}main{overflow:auto;padding:15px;flex:1}article{padding:13px;border-bottom:1px solid #36516a}p{margin:7px 0}.error{color:#ffb6ac;white-space:pre-wrap}.muted{color:#9db4ca}progress{width:100%;height:12px}.cue{display:flex;gap:9px;padding:9px;border-bottom:1px solid #29445e}.cue input[type=checkbox]{width:18px;flex-shrink:0}.cue span{white-space:pre-wrap}.selected{background:#193f47}#selected input{width:min(500px,65%)}footer{padding:12px;background:#1b324a;display:flex;gap:8px;flex-wrap:wrap}strong{font-size:16px}#search{flex:1}#notice{padding:0 15px}label{cursor:pointer}.time{font-variant-numeric:tabular-nums;white-space:nowrap}a{color:#a7d9ff}`;
  dom.append(style);
  function el(tag,text='',cls=''){const e=document.createElement(tag);e.textContent=text;e.className=cls;return e;}
  function button(text,fn){const b=el('button',text);b.onclick=fn;return b;}
  async function api(action,value={}){const reply=await chrome.runtime.sendMessage({action,value});if(reply?.error)throw new Error(reply.error);return reply;}
  const time=s=>s==null?'估算中':[Math.floor(Math.max(0,s)/3600),Math.floor(Math.max(0,s)/60)%60,Math.floor(Math.max(0,s))%60].map(v=>String(v).padStart(2,'0')).join(':');
  const toggle=button('文稿与章节',()=>{panel.hidden=!panel.hidden;if(!panel.hidden)refresh();});toggle.id='toggle';dom.append(toggle);
  const panel=el('section');panel.id='panel';panel.hidden=true;
  const head=el('header');const title=el('strong','书架转写');head.append(title);
  const back=button('返回书架',()=>{view=null;selected.clear();render();});head.append(back,button('刷新',()=>refresh(true)),button('关闭',()=>panel.hidden=true));
  const search=el('input');search.id='search';search.placeholder='搜索书名';head.append(search);
  const notice=el('p');notice.id='notice';notice.className='error';const main=el('main');const footer=el('footer');panel.append(head,notice,main,footer);dom.append(panel);
  let books=[],view=null,selected=new Map(),query='',currentId=pageId(),loading=false;
  function pageId(){const match=location.pathname.match(/\/item\/([^/]+)/);return match?decodeURIComponent(match[1]):'';}
  function error(e){notice.textContent=e.message||String(e);}
  function download(){const text=view.srt;const a=el('a');a.href=URL.createObjectURL(new Blob([text],{type:'application/x-subrip;charset=utf-8'}));a.download=view.document||'transcript.srt';a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000);}
  async function openBook(id){try{notice.textContent='正在读取整本文稿…';const result=await api('bookCues',{path:id});view={...result,id};selected.clear();query='';search.value='';notice.textContent='';render();}catch(e){error(e);}}
  async function queueBook(id,b,retranscribe=false){if(retranscribe&&!confirm('将使用高精度模型重新识别本书全部音轨。继续？'))return;b.disabled=true;try{const result=await api('queueCurrentBook',{item_id:id,retranscribe});notice.textContent=`已加入队列：${result.queued} 个音轨${retranscribe?'（全部重新识别）':'（已有文稿的部分会复用）'}。`;await refresh();}catch(e){error(e);}finally{b.disabled=false;}}
  async function resetBook(id,b){
    if(!confirm('确定清除本书现有文稿并从头重新识别吗？全部音轨的旧 SRT、整本文稿及识别缓存会先备份，然后重新排队。此操作可能需要较长时间。'))return;
    b.disabled=true;
    try{
      const result=await api('resetTranscript',{item_id:id});
      view=null;selected.clear();query='';search.value='';
      notice.textContent=`已清除旧文稿并重新排队 ${result.queued} 个音轨。备份：${result.backup}`;
      await refresh(true);
    }catch(e){error(e);}finally{b.disabled=false;}
  }
  function render(){
    main.replaceChildren();footer.replaceChildren();back.hidden=!view;title.textContent=view?'选择文稿时间点':'书架转写';search.placeholder=view?'搜索文稿中的句子':'搜索书名';
    if(view){
      footer.append(el('span',`已选 ${selected.size} 个时间点 · ${view.complete?'整本文稿已完成':'部分文稿，等待识别完成'}`));
      const save=button('用所选时间点替换章节',async()=>{
        if(!selected.size)return;
        if(!confirm(`将按 ${selected.size} 个选中时间点替换本书章节。首个时间点大于零时自动添加“开篇”。旧章节会备份。继续？`))return;
        save.disabled=true;
        try{const result=await api('replaceChapters',{item_id:view.id,revision:view.revision,chapters_revision:view.chapters_revision,selections:[...selected].map(([id,title])=>({id,title}))});notice.textContent=`已替换为 ${result.count} 个章节；刷新书籍可查看。`;view= {...await api('bookCues',{path:view.id}),id:view.id};selected.clear();render();}catch(e){error(e);}finally{save.disabled=false;}
      });save.disabled=!view.complete||!selected.size;footer.append(save,button('下载 SRT',download));
      const reset=button('清除文稿并重新识别',()=>resetBook(view.id,reset));footer.append(reset);
      const selection=el('div');selection.id='selected';
      [...selected].sort((a,b)=>view.cues[a[0]].start-view.cues[b[0]].start).forEach(([id,text])=>{const line=el('p');const input=el('input');input.value=text;input.maxLength=500;input.oninput=()=>selected.set(id,input.value);line.append(el('span',time(view.cues[id].start)+' '),input,button('移除',()=>{selected.delete(id);render();}));selection.append(line);});main.append(selection);
      if(!view.cues.length)main.append(el('p','暂无文稿，请在书架列表点击“开始转写”。'));
      const filtered=view.cues.filter(c=>c.text.toLocaleLowerCase().includes(query.toLocaleLowerCase()));
      let shown=0;
      const more=button('继续显示更多句子',append);
      function append(){more.remove();filtered.slice(shown,shown+300).forEach(c=>{const row=el('label','','cue'+(selected.has(c.id)?' selected':''));const check=el('input');check.type='checkbox';check.checked=selected.has(c.id);check.onchange=()=>{if(check.checked)selected.set(c.id,c.text.replace(/\s+/g,' ').slice(0,500));else selected.delete(c.id);render();};row.append(check,el('span',time(c.start),'time'),el('span',c.text));main.append(row);});shown+=300;if(shown<filtered.length)main.append(more);}append();
      return;
    }
    const id=pageId();const visible=books.filter(b=>(dashboard||!id||b.id===id)&&b.name.toLocaleLowerCase().includes(query.toLocaleLowerCase()));
    if(!visible.length)main.append(el('p','未找到书籍；可点击刷新重新扫描书架。'));
    for(const b of visible){const card=el('article');card.append(el('strong',b.name),el('p',b.library,'muted'));const labels={done:'已有文稿',running:'正在转写',queued:'等待转写',missing:'缺少文稿',failed:'转写失败',cancelled:'已取消'};card.append(el('p',`${labels[b.status]||b.status} · ${b.progress}%${['running','queued'].includes(b.status)?' · 预计剩余 '+time(b.eta_seconds):''}`));if(b.stage)card.append(el('p',b.stage,'muted'));const progress=el('progress');progress.max=100;progress.value=b.progress;card.append(progress);
      const start=button(b.status==='failed'?'重试未完成部分':'开始转写',()=>queueBook(b.id,start));start.disabled=b.source_missing||b.complete||['running','queued'].includes(b.status);card.append(start,button('查看文稿 / 选择章节',()=>openBook(b.id)));const redo=button('高精度重转写',()=>queueBook(b.id,redo,true));redo.disabled=b.source_missing||['running','queued'].includes(b.status);card.append(redo);const reset=button('清除文稿并重新识别',()=>resetBook(b.id,reset));reset.disabled=b.source_missing||['running','queued'].includes(b.status);card.append(reset);if(b.errors.length)card.append(el('p',b.errors.join('\n'),'error'));main.append(card);}
    footer.append(el('span','自动扫描书架；点击后转写。数字统一为阿拉伯数字；按句意和停顿分组。旧字幕可选择高精度重转写，修改前会备份。','muted'));
  }
  async function refresh(force=false){if(loading||panel.hidden)return;loading=true;try{if(view){if(!force&&view.complete)return;const result=await api('bookCues',{path:view.id});if(result.revision!==view.revision){selected.clear();notice.textContent='文稿已更新，请重新选择章节时间点。';}view={...result,id:view.id};render();return;}const result=await api('books');books=result.books;if(result.model_error)notice.textContent=result.model_error;render();}catch(e){error(e);}finally{loading=false;}}
  search.oninput=()=>{query=search.value;render();};
  document.addEventListener('abs-open-book-tools', async event=>{
    panel.hidden=false;view=null;selected.clear();query='';search.value='';
    await refresh(true);
    const paths=event.detail?.trackPaths;
    const matched=Array.isArray(paths)?books.find(book=>book.tracks?.some(track=>paths.includes(track.path))):null;
    if(matched)await openBook(matched.id);
  });
  setInterval(()=>{const id=pageId();if(id!==currentId){currentId=id;view=null;selected.clear();query='';search.value='';render();}toggle.hidden=dashboard||!id;refresh();},3000);
  toggle.hidden=dashboard||!pageId();
})();
