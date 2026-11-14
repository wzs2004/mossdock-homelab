(async () => {
  const {absUrl} = await chrome.storage.local.get('absUrl');
  if (!absUrl || location.origin !== new URL(absUrl).origin || document.getElementById('abs-matching')) return;
  const host = document.createElement('div'); host.id = 'abs-matching'; document.documentElement.append(host);
  const dom = host.attachShadow({mode: 'open'});
  const style = document.createElement('style');
  style.textContent = `:host{font:14px/1.5 system-ui;color:#eef6ff}*{box-sizing:border-box}#match-toggle{position:fixed;left:20px;bottom:22px;z-index:2147483645}#match-toggle[hidden],section[hidden]{display:none!important}section{position:fixed;right:20px;bottom:80px;z-index:2147483646;padding:14px;background:#18354b;border:1px solid #53738b;border-radius:10px;width:min(420px,calc(100vw - 40px));max-height:calc(100vh - 120px);overflow:auto;box-shadow:0 8px 30px #0006}button,select,input{font:inherit}button{cursor:pointer;padding:8px 11px;background:#306c81;color:white;border:0;border-radius:6px}button:disabled{opacity:.5;cursor:default}select,input{width:100%;padding:7px;color:white;background:#102b3f;border:1px solid #68889b;border-radius:5px}label{display:block;margin:7px 0}label span{display:block;margin-bottom:3px}p{margin:7px 0;white-space:pre-wrap}progress{width:100%}.error{color:#ffb7a8;white-space:pre-wrap}.muted{color:#bbccda}article{border-top:1px solid #53738b;padding:9px 0}article strong{display:block;font-size:14px}article button{margin-top:6px}.actions{display:flex;gap:8px;align-items:center}.candidate{font-size:12px;max-height:90px;overflow:auto;white-space:pre-wrap}`;
  dom.append(style);
  const node = (tag, text='', className='') => {const e=document.createElement(tag);e.textContent=text;e.className=className;return e;};
  const box=node('section'), heading=node('strong','书籍元数据匹配');
  const toggle=node('button','元数据匹配');toggle.id='match-toggle';toggle.hidden=true;box.hidden=true;
  const close=node('button','关闭');close.style.float='right';close.onclick=()=>{visible=false;box.hidden=true;};
  const providerLabel=node('label'), provider=node('select');providerLabel.append(node('span','匹配来源'),provider);
  const titleLabel=node('label'),title=node('input');title.placeholder='留空使用当前书名';title.maxLength=300;titleLabel.append(node('span','搜索书名'),title);
  const authorLabel=node('label'),author=node('input');author.placeholder='留空使用当前作者';author.maxLength=300;authorLabel.append(node('span','搜索作者（可选）'),author);
  const actions=node('div','','actions'),start=node('button','搜索候选书籍'),reload=node('button','刷新匹配源');actions.append(start,reload);
  const status=node('p','选择匹配来源后搜索'),progress=node('progress'),err=node('p','','error');progress.max=100;
  const matches=node('div');box.append(close,heading,providerLabel,titleLabel,authorLabel,actions,status,progress,err,matches);dom.append(toggle,box);
  let id='',busy=false,searching=false,applying=false,loadingProviders=false,generation=0,visible=false;
  toggle.onclick=()=>{visible=!visible;box.hidden=!visible||!id;};
  async function api(action,value={}) {const r=await chrome.runtime.sendMessage({action,value});if(r?.error)throw Error(r.error);return r;}
  function updateBusy(){start.disabled=searching||applying||loadingProviders||!id||!provider.value;provider.disabled=searching||applying||loadingProviders;reload.disabled=searching||applying||loadingProviders;}
  function clearMatches(){matches.replaceChildren();}
  function showProgress(result){
    progress.value=result.progress||0;
    if(result.status==='running'){
      status.textContent=(result.stage||'应用所选元数据中')+`\n已等待 ${result.elapsed_seconds||0} 秒`;
      applying=true;
    }else if(result.status==='done'){
      status.textContent=result.stage||'匹配完成';applying=false;
    }else if(result.status==='failed'){
      status.textContent=result.stage||'匹配失败';err.textContent=result.error||'';applying=false;
    }else if(!searching&&!applying&&!matches.childElementCount){
      status.textContent='选择匹配来源并搜索，结果会在下方列出';
    }
    updateBusy();
  }
  function populateProviders(list){
    if(!Array.isArray(list)||!list.length)throw Error('服务端未返回可用的匹配来源');
    const previous=provider.value||'audible';provider.replaceChildren();
    for(const source of list){
      if(typeof source.value!=='string'||!source.value)continue;
      const option=node('option',source.text||source.value);option.value=source.value;provider.append(option);
    }
    if([...provider.options].some(x=>x.value===previous))provider.value=previous;
    else if([...provider.options].some(x=>x.value==='audible'))provider.value='audible';
    if(!provider.options.length)throw Error('服务端未返回有效的匹配来源');
    updateBusy();
  }
  async function loadProviders(){
    loadingProviders=true;err.textContent='';updateBusy();
    try{const result=await api('matchProviders');populateProviders(result.providers);}
    catch(e){provider.replaceChildren();err.textContent='获取匹配来源失败，请点击“刷新匹配源”：'+e.message;}
    finally{loadingProviders=false;updateBusy();}
  }
  reload.onclick=loadProviders;
  function displayMatches(result,target,searchGeneration){
    clearMatches();
    const list=result.results||[];
    matches.append(node('p',`来源：${provider.selectedOptions[0]?.textContent||result.provider} · 共找到 ${list.length} 个候选，选择一个应用。`,'muted'));
    if(!list.length){matches.append(node('p','此来源没有找到候选，可换来源或修改搜索关键词。'));return;}
    for(const candidate of list){
      const row=node('article'), name=node('strong',candidate.title||'(无书名)');
      const authors=candidate.author||candidate.authorName||(Array.isArray(candidate.authors)?candidate.authors.map(a=>a.name||a).join('、'):'');
      const year=candidate.publishedYear||candidate.publishedDate||'';
      const details=[authors,year,candidate.publisher].filter(Boolean).map(String).join(' · ');
      row.append(name,node('p',details,'muted'));
      if(candidate.subtitle)row.append(node('p',candidate.subtitle,'muted'));
      if(candidate.description)row.append(node('p',String(candidate.description).replace(/<[^>]*>/g,'').slice(0,400),'candidate'));
      const choose=node('button','选择此结果并应用');
      choose.onclick=async()=>{
        if(target!==id||searchGeneration!==generation||applying||searching)return;
        if(!confirm(`将“${candidate.title}”的信息应用到当前书籍？原元数据会备份。`))return;
        if(target!==id||searchGeneration!==generation||applying)return;
        applying=true;err.textContent='';updateBusy();
        try{
          const response=await api('matchBook',{item_id:target,token:result.token,index:candidate.index});
          if(target===id&&searchGeneration===generation)showProgress(response);
        }catch(e){if(target===id&&searchGeneration===generation){err.textContent=e.message;applying=false;updateBusy();}}
      };
      row.append(choose);matches.append(row);
    }
  }
  start.onclick=async()=>{
    const target=id,searchGeneration=++generation;
    if(!target||!provider.value)return;
    searching=true;err.textContent='';clearMatches();status.textContent='正在搜索候选书籍…';updateBusy();
    try{
      const result=await api('matchCandidates',{item_id:target,provider:provider.value,title:title.value.trim(),author:author.value.trim()});
      if(target===id&&searchGeneration===generation){status.textContent='请选择下方的一条匹配结果';displayMatches(result,target,searchGeneration);}
    }catch(e){if(target===id&&searchGeneration===generation){err.textContent=e.message;status.textContent='搜索失败';}}
    finally{if(target===id&&searchGeneration===generation){searching=false;updateBusy();}}
  };
  for(const input of [title,author])input.addEventListener('keydown',e=>{if(e.key==='Enter')start.click();});
  function invalidateSearch(message){generation++;searching=false;clearMatches();err.textContent='';status.textContent=message;updateBusy();}
  for(const input of [title,author])input.addEventListener('input',()=>invalidateSearch('关键词已更改，请重新搜索'));
  provider.onchange=()=>invalidateSearch('匹配来源已更改，请重新搜索');
  async function tick(){
    const raw=location.pathname.match(/\/item\/([^/]+)/)?.[1]||'';
    const next=raw?decodeURIComponent(raw):'';
    if(next!==id){id=next;visible=false;generation++;applying=false;searching=false;clearMatches();title.value='';author.value='';err.textContent='';progress.value=0;status.textContent='选择匹配来源并搜索';updateBusy();}
    toggle.hidden=!next;box.hidden=!next||!visible;
    if(!id||busy)return;
    busy=true;const target=id;
    try{
      const result=await api('matchStatus',{item_id:target});
      if(id===target)showProgress(result);
    }catch(e){if(id===target)err.textContent=e.message;}
    finally{busy=false;}
  }
  await loadProviders();
  await tick();setInterval(tick,2000);
})();
