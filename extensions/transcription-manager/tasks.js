(async () => {
  const dashboardMode=location.protocol==='chrome-extension:' && location.pathname==='/dashboard.html';
  if (!dashboardMode) {
    const {absUrl} = await chrome.storage.local.get('absUrl');
    if (!absUrl || location.origin !== new URL(absUrl).origin) return;
  }
  if (document.getElementById('abs-asr-extension-root')) return;

  const root = document.createElement('div');
  root.id = 'abs-asr-extension-root';
  document.documentElement.append(root);
  const dom = root.attachShadow({mode: 'open'});
  const style = document.createElement('style');
  style.textContent = `
    :host { all:initial; font:13px/1.5 -apple-system,BlinkMacSystemFont,'PingFang SC',system-ui,sans-serif; color:#e2e8f0; }
    * { box-sizing:border-box; }
    button,input,select { font:inherit; }
    button { cursor:pointer; }
    button:disabled { opacity:.4; cursor:default; }
    #toggle { position:fixed;right:21px;bottom:22px;z-index:2147483645;display:flex;align-items:center;gap:9px;border:1px solid #596e80;
      background:#192e43;color:white;border-radius:99px;padding:12px 17px;box-shadow:0 10px 34px #0005;font-size:13px;font-weight:700; }
    #toggle .dot { width:8px;height:8px;background:#90e4a9;border-radius:50%; }
    #toggle.busy .dot { background:#ffc66b; animation:pulse 1s infinite alternate; }
    @keyframes pulse { to {opacity:.35;} }
    #panel { position:fixed; z-index:2147483646; right:16px;top:74px;width:min(485px,calc(100vw - 24px)); height:min(790px,calc(100vh - 96px));
      display:flex;flex-direction:column; border:1px solid #354658;background:#0f1a29;color:#e2e8f0;
      box-shadow:0 22px 70px #0009;border-radius:14px;overflow:hidden; }
    #panel[hidden],#logbox[hidden] { display:none; }
    header { background:#17273a;padding:17px 17px 15px;border-bottom:1px solid #31435a; }
    .title { display:flex;justify-content:space-between;align-items:center;gap:10px; }
    .title strong {font-size:17px;color:white;} .meta {font-size:11px;color:#93a7bb;margin-top:4px;}
    .round {background:#2a3a4e;color:#e5ecf4;border:1px solid #3f536b;border-radius:7px;padding:5px 9px;}
    .metrics {display:flex;gap:6px;margin-top:13px;}
    .metric {flex:1;min-width:0;background:#22334a;border-radius:7px;padding:8px;text-align:center;color:#b9cbd9;font-size:10px;}
    .metric b {display:block;font-size:20px;color:#f2f7ff;}
    .filters {display:flex;gap:7px;flex-wrap:wrap;padding:12px 14px;background:#101e2d;border-bottom:1px solid #293c50;}
    input,select {background:#223347;color:#eaf1f8;border:1px solid #3d5269;border-radius:6px;padding:8px;outline:none;min-width:0;}
    select {flex:1;} input {flex:2;min-width:110px;}
    .main-tabs,.status-tabs {display:flex;gap:6px;flex-wrap:wrap;margin-top:10px;}
    .main-tabs button,.status-tabs button {flex:1;}
    .status-tabs button[aria-pressed="true"],.main-tabs button[aria-pressed="true"] {background:#1c775e;border-color:#48c9ac;}
    .automation[hidden],.status-tabs[hidden] {display:none;}
    .viewbar {display:flex;gap:6px;align-items:center;flex-wrap:wrap;padding:8px 12px;border-bottom:1px solid #293c50;}
    .viewbar[hidden] {display:none;}
    .page-info {font-size:11px;color:#9fb6c9;margin-left:auto;}
    #list.tasks-list {display:grid;grid-template-columns:1fr;gap:8px;align-content:start;}
    .tasks-list>.track {background:#17283a;border:1px solid #34485c;border-radius:9px;min-width:0;}
    .chapter-evidence {font-size:11px;margin:8px 0;color:#c1d9cc;}
    .chapter-evidence summary {cursor:pointer;}
    .automation {padding:10px 14px;background:#172c32;border-bottom:1px solid #37515a;flex-shrink:0;max-height:48%;overflow:auto;}
    .automation.expanded {flex:1;min-height:0;max-height:none;overflow-y:auto;display:block;overscroll-behavior:contain;}
    .automation.expanded details {display:block;}
    .automation.expanded .auto-results {max-height:none;overflow:visible;}
    .automation.expanded details>input {flex:none;}
    .filters[hidden],#list[hidden] {display:none;}
    .auto-settings {display:flex;gap:14px;flex-wrap:wrap;margin:7px 0;font-size:12px;}
    .auto-settings label {display:flex;gap:6px;align-items:center;cursor:pointer;}
    .auto-settings input {flex:none;min-width:0;width:16px;height:16px;padding:0;accent-color:#48c9ac;}
    .automation summary {cursor:pointer;color:#b5dccc;margin:8px 0;font-size:12px;}
    .auto-results {max-height:190px;overflow:auto;}
    .auto-record {padding:9px 0;border-bottom:1px solid #37515a;overflow-wrap:anywhere;font-size:11px;}
    .auto-record strong {color:#e3f5ee;}
    .auto-stats,.auto-current {font-size:11px;color:#a8c6bf;margin:5px 0;overflow-wrap:anywhere;}
    .book-automation {padding:7px 10px;border-top:1px solid #34485c;}
    #list {overflow-y:auto;flex:1;overscroll-behavior:contain;padding:12px;}
    .info {color:#9eb2c4;font-size:11px;padding:8px 4px;}
    .warn {color:#ffd1a3;background:#513f2d;padding:9px 12px;border-radius:7px;white-space:pre-wrap;}
    .book {border:1px solid #34485c;background:#18283a;border-radius:9px;margin-bottom:8px;overflow:hidden;}
    .book-head {display:flex;gap:7px;align-items:center;padding:10px;}
    .expand {color:#e5edf5;background:#283e53;border:0;border-radius:6px;padding:6px 9px;}
    .book-name {flex:1;min-width:0;font-size:12px;font-weight:650;overflow-wrap:anywhere;}
    .sub {color:#90a7bb;font-size:10px;font-weight:400;margin-top:2px;}
    .btn {border-radius:6px;padding:6px 9px;background:#29465a;color:#ebf5ff;border:1px solid #46667c;font-size:11px;white-space:nowrap;}
    .btn.primary {background:#1c775e;border-color:#319379;}
    .btn.danger {background:#543540;border-color:#805364;}
    .tracks {border-top:1px solid #34485c;background:#122032;}
    .track {padding:10px 12px;border-bottom:1px solid #24394d;}
    .track:last-child {border:0;}
    .track-name {overflow-wrap:anywhere;word-break:break-word;font-size:12px;color:#e9f2fc;}
    .track-detail {display:flex;gap:8px;align-items:center;margin:5px 0;font-size:10px;color:#aac2d5;}
    .status {border-radius:15px;padding:2px 6px;background:#304356;color:#dde7ee;white-space:nowrap;}
    .status.running {background:#1c5363;color:#b7edfa;} .status.failed {background:#663d43;color:#ffb6ae;}
    .status.done {background:#205342;color:#a8f0c5;} .status.queued {background:#5a4b2c;color:#f9d88b;}
    .error {color:#ff9a95;overflow-wrap:anywhere;font-size:11px;margin:5px 0;}
    .progress {height:6px;border-radius:8px;overflow:hidden;background:#30455a;margin:6px 0;}
    .progress i {display:block;height:100%;background:#48c9ac;}
    .actions {display:flex;flex-wrap:wrap;gap:6px;margin-top:7px;}
    #logbox {border-top:1px solid #405775;background:#0b1320;max-height:38%;display:flex;flex-direction:column;}
    #logbox>.loghead {display:flex;gap:8px;justify-content:space-between;padding:8px 12px;background:#1c3143;}
    #logbox pre {font:11px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace;margin:0;padding:10px;white-space:pre-wrap;overflow:auto;flex:1;color:#b5d5c5;}
    #message {display:none;position:absolute;bottom:15px;left:15px;right:15px;padding:10px;border-radius:7px;background:#326c5a;color:white;box-shadow:0 3px 20px #0008;}
    #message.show {display:block;} .footer {padding:8px 14px;font-size:10px;color:#7995aa;border-top:1px solid #2e4359;}
    @media(max-width:600px){#panel {top:10px;right:8px;height:calc(100vh - 22px);width:calc(100vw - 16px);}#toggle{bottom:12px;right:12px;}}
  `;
  if(dashboardMode) style.textContent+=`
    #toggle {display:none;}
    #panel {position:relative;top:auto;right:auto;width:calc(100% - 32px);max-width:1680px;height:calc(100vh - 32px);margin:16px auto;border-radius:14px;}
    #panel>.automation {max-height:35%;}
    #panel>.automation.expanded {max-height:none;}
    #panel header .title strong {font-size:20px;}
    #list.tasks-list {grid-template-columns:repeat(auto-fit,minmax(340px,1fr));}
    .automation.expanded .auto-results {display:grid;grid-template-columns:repeat(auto-fit,minmax(350px,1fr));gap:14px;align-content:start;}
    .filters input {max-width:700px;}
    @media(max-width:600px){#panel{width:calc(100% - 12px);height:calc(100vh - 12px);margin:6px;}}
  `;
  dom.append(style);
  const elt = (tag, cls = '', value = '') => {
    const e = document.createElement(tag); if (cls) e.className = cls;
    if (value !== '') e.textContent = value;
    return e;
  };
  const toggle = elt('button', '', '📝 转写管理'); toggle.id = 'toggle'; toggle.type = 'button';
  const dot = elt('span', 'dot'); toggle.prepend(dot);
  const panel = elt('section'); panel.id = 'panel'; panel.hidden = !dashboardMode;
  const head = elt('header');
  const title = elt('div', 'title');
  const heading = elt('strong', '', '🎧 Audiobookshelf · 自动整理');
  const tools = elt('div');
  const refresh = elt('button', 'round', '↻'); refresh.title = '刷新';
  const close = elt('button', 'round', '✕'); close.title = '关闭';
  const fullscreen=elt('button','round','⛶ 全屏任务中心'); fullscreen.title='在新页面查看所有任务';
  const chapterTools=elt('button','round','文稿与章节');
  chapterTools.title='查看文稿、清除并重新识别或手动选择章节';
  chapterTools.addEventListener('click',()=>document.dispatchEvent(new CustomEvent('abs-open-book-tools')));
  fullscreen.addEventListener('click',async()=>{
    try {await api('openDashboard');}
    catch(error){toast(`全屏页面未打开：${error.message}。请重新加载扩展后再试。`,true);}
  });
  fullscreen.hidden=dashboardMode;
  tools.append(chapterTools,fullscreen, refresh, close); title.append(heading, tools); head.append(title);
  const meta = elt('div', 'meta', '正在连接转写服务器…'); head.append(meta);
  const metrics = elt('div', 'metrics'); head.append(metrics);
  const mainTabs=elt('div','main-tabs'),statusTabs=elt('div','status-tabs');head.append(mainTabs,statusTabs);
  const automation = elt('section', 'automation');
  automation.append(elt('strong', '', '历史匹配与分章记录'));
  automation.append(elt('p','info','自动整理操作已停用。手动匹配请打开书籍详情页的「当前书籍匹配」扩展；文稿和章节请使用「文稿与章节」。'));
  const settingsRow = elt('div', 'auto-settings');
  const matchLabel = elt('label'), chapterLabel = elt('label');
  const matchToggle = elt('input'), chapterToggle = elt('input');
  matchToggle.type = chapterToggle.type = 'checkbox';
  matchToggle.setAttribute('aria-label','自动匹配书籍资料');
  chapterToggle.setAttribute('aria-label','自动识别朗读章标题');
  matchLabel.append(matchToggle, document.createTextNode('自动匹配书籍资料'));
  chapterLabel.append(chapterToggle, document.createTextNode('自动识别朗读章标题'));
  settingsRow.append(matchLabel, chapterLabel);
  const autoStats = elt('div','auto-stats'), autoCurrent = elt('div','auto-current');
  const autoActions = elt('div','actions');
  const autoDetails = elt('details'); autoDetails.append(elt('summary','','逐本结果与操作'));
  const autoSearch = elt('input'); autoSearch.type='search'; autoSearch.placeholder='搜索匹配或分章结果'; autoSearch.style.width='100%';
  const autoResults = elt('div','auto-results'); autoDetails.append(autoSearch,autoResults);
  automation.append(autoStats,autoCurrent,autoDetails);
  const filters = elt('div', 'filters');
  const search = elt('input'); search.type = 'search'; search.placeholder = '搜索书名';
  const libraries = elt('select'); const statuses = elt('select');
  for (const [value, label] of [['all','全部状态'],['unfinished','未完成（含排队和失败）'],['missing','待识别'],['done','已识别'],['running','识别中'],['queued','队列中'],['failed','失败'],['cancelled','已取消'],['source_missing','源文件缺失（历史任务）']]) {
    const option = elt('option', '', label); option.value = value; statuses.append(option);
  }
  filters.append(search, libraries, statuses);
  const viewbar=elt('div','viewbar');
  const list = elt('div'); list.id = 'list';
  const logbox = elt('div'); logbox.id = 'logbox'; logbox.hidden = true;
  const loghead = elt('div', 'loghead'); const logtitle = elt('span', '', '转写日志');
  const logclose = elt('button', 'round', '✕');
  const logpre = elt('pre'); loghead.append(logtitle, logclose); logbox.append(loghead, logpre);
  const foot = elt('div', 'footer', 'Docker 本地 Whisper · 仅手动选择的音轨进入队列');
  const message = elt('div'); message.id = 'message';
  panel.append(head, automation, filters, viewbar, list, logbox, foot, message); dom.append(toggle, panel);

  let catalog = null, busy = false, expanded = new Set(), maxBooks = 32, logPath = '';
  let taskView='books',taskPage=0,taskPages=1,currentSection='tasks';const pageSize=50;const taskNodes=new Map();
  const labels = {done:'已识别',missing:'未识别',queued:'排队中',running:'正在识别',failed:'失败',cancelled:'已取消'};
  const api = async (action, value = {}) => {
    const result = await chrome.runtime.sendMessage({action, value});
    if (chrome.runtime.lastError) throw new Error(chrome.runtime.lastError.message);
    if (!result) throw new Error('扩展后台无响应');
    if (result.error) throw new Error(result.error);
    return result;
  };
  let toastTimer;
  function toast(text, error = false) {
    message.textContent = text; message.style.background = error ? '#8b3e43' : '#326c5a';
    message.classList.add('show'); clearTimeout(toastTimer);
    toastTimer = setTimeout(() => message.classList.remove('show'), 4000);
  }
  const seconds = value => {
    if (!Number.isFinite(value)) return '计算中';
    if (value < 60) return `${Math.round(value)}秒`;
    if (value < 3600) return `${Math.round(value / 60)}分钟`;
    return `${Math.floor(value / 3600)}小时${Math.round(value % 3600 / 60)}分`;
  };
  function btn(text, kind, handler) {
    const el = elt('button', 'btn ' + kind, text);
    el.type = 'button'; el.addEventListener('click', handler); return el;
  }
  async function act(action, payload) {
    try {
      const result = await api(action, payload);
      toast(result.message || (result.queued !== undefined ? `已加入 ${Number(result.queued)} 条任务` : '操作成功'));
      await load();
    } catch (error) {toast(error.message, true);}
  }
  async function showLog(track) {
    logPath = track.path; logbox.hidden = false;
    logtitle.textContent = `日志 · ${track.name}`;
    logpre.textContent = '读取日志中…';
    try { const data = await api('log', {path: track.path}); logpre.textContent = data.log; logpre.scrollTop = logpre.scrollHeight; }
    catch(error) {logpre.textContent = error.message;}
  }
  async function transcript(track, download = false) {
    try {
      const response = await api('transcript', {path: track.path, download});
      const url = URL.createObjectURL(new Blob([response.text], {type:'text/plain;charset=utf-8'}));
      const link = elt('a'); link.href = url; link.target = '_blank'; link.rel = 'noopener';
      if (download) link.download = `${track.name.replace(/\.[^.]+$/, '')}.srt`;
      link.click(); setTimeout(() => URL.revokeObjectURL(url), 120000);
    } catch(error) {toast(error.message, true);}
  }
  async function saveAutoSetting(key, value, input) {
    input.disabled=true;
    try { await api('automationSettings',{[key]:value}); await load(); }
    catch(error) { input.checked=!value; toast(error.message,true); }
    finally {input.disabled=false;}
  }
  matchToggle.addEventListener('change',()=>saveAutoSetting('auto_match',matchToggle.checked,matchToggle));
  chapterToggle.addEventListener('change',()=>saveAutoSetting('auto_chapters',chapterToggle.checked,chapterToggle));
  autoActions.append(
    btn('立即检查全部书库','',()=>act('automationAction',{action:'scan'})),
    btn('重试失败项','',()=>act('automationAction',{action:'retry_failed'})),
    btn('查看整理日志','',async()=>{
      logPath='__automation__';logtitle.textContent='自动匹配与分章日志';logbox.hidden=false;
      try {logpre.textContent=(await api('automationAction',{action:'log'})).log;}
      catch(error){toast(error.message,true);}
    })
  );
  const chapterNames={waiting:'等待全书字幕',done:'朗读章标题已应用',preserved:'保留正式章节',no_boundaries:'未识别到章标题',no_headings:'未识别到章标题',preview:'仅预览，未写入',skipped:'暂不需要分章',failed:'分章失败'};
  function recordText(record) {
    const match=record.match_status==='done' ? (record.match_updated?'已补充书籍资料':'匹配完成，资料完整') : record.match_status==='failed'?'资料未匹配':'等待匹配';
    return `${match} · ${record.chapter_message || chapterNames[record.chapter_status] || '等待分章检查'}`;
  }
  function recordEvidence(record) {
    const details=elt('details','chapter-evidence');
    const accepted=record.chapter_preview || [], review=record.chapter_review || [];
    details.append(elt('summary','',`朗读章标题证据：${accepted.length} 个确认，${review.length} 个待复核`));
    for(const heading of accepted) {
      const at=new Date(heading.start*1000).toISOString().slice(11,19);
      details.append(elt('div','',`${at} · ${heading.title}`),elt('div','sub',`原文：${heading.spoken_text}`));
    }
    for(const heading of review)details.append(elt('div','sub',`${Math.round(heading.start)}秒 · ${heading.spoken_text} · ${heading.review_reason}`));
    return details;
  }
  function recordActions(record) {
    const actions=elt('div','actions');
    for(const [kind,text] of [['match','立即匹配'],['preview_chapters','预览朗读章标题'],['chapters','识别并应用章节']]) {
      const queued=record.pending_actions?.includes(kind);
      const button=btn(queued?'操作排队中':text,'',()=>act('automationAction',{action:kind,item_id:record.item_id}));
      button.disabled=queued;actions.append(button);
    }
    if(record.chapter_legacy)actions.append(btn('恢复分章前目录','',()=>act('automationAction',{action:'restore_chapters',item_id:record.item_id})));
    return actions;
  }
  let autoResultsSignature = '';
  function renderAutoResults() {
    const query=autoSearch.value.trim().toLowerCase();
    const records=(catalog?.library_automation?.records || []).filter(record=>!query || `${record.title} ${record.path} ${record.match_error || ''}`.toLowerCase().includes(query));
    const signature=JSON.stringify(records);
    if(signature===autoResultsSignature)return;
    autoResultsSignature=signature;autoResults.replaceChildren();
    if(!records.length) autoResults.append(elt('div','info','暂无历史匹配结果'));
    records.forEach(record=>{
      const row=elt('div','auto-record'); row.append(elt('strong','',record.title),elt('div','auto-stats',recordText(record)));
      const error=record.match_error || record.chapter_error || record.operation_error;
      if(error) row.append(elt('div','error',error));
      if(record.chapter_preview?.length || record.chapter_review?.length)row.append(recordEvidence(record));
      autoResults.append(row);
    });
    autoResults.append(elt('div','info',`共 ${records.length} 本，已全部列出`));
  }
  function renderAutomation(value) {
    if(!value?.settings) {autoCurrent.textContent='正在读取自动整理设置…';return;}
    if(!matchToggle.disabled) matchToggle.checked=value.settings.auto_match;
    if(!chapterToggle.disabled) chapterToggle.checked=value.settings.auto_chapters;
    const count=value.totals || {};
    autoStats.textContent=`匹配完成 ${count.matched || 0} · 未匹配 ${count.unmatched || 0} · 内容分章 ${count.chaptered || 0} · 等待字幕 ${count.waiting || 0}`;
    const last=value.last_scan?new Date(value.last_scan*1000).toLocaleTimeString():'尚未完成首次检查';
    autoCurrent.textContent=`${value.current || (value.running?'正在检查书库':(!value.settings.auto_match && !value.settings.auto_chapters?'自动整理已暂停':'等待下一次检查'))} · 待执行 ${value.pending_commands || 0} · 最近检查：${last}`;
    if(value.error)autoCurrent.textContent+=` · ${value.error}`;
    if(autoDetails.open)renderAutoResults();
  }
  function showSection(section) {
    currentSection=section;const matching=section==='automation';
    automation.hidden=!matching;filters.hidden=matching;list.hidden=matching;viewbar.hidden=matching;statusTabs.hidden=matching;
    autoDetails.open=matching;automation.classList.toggle('expanded',matching);
    tasksNav.setAttribute('aria-pressed',String(!matching));automationNav.setAttribute('aria-pressed',String(matching));
    if(matching)renderAutoResults();else render();
  }
  const tasksNav=btn('任务列表','',()=>showSection('tasks'));
  const automationNav=btn('历史整理结果','',()=>showSection('automation'));
  mainTabs.append(tasksNav,automationNav);
  mainTabs.append(btn('同步字幕到媒体库','',()=>act('mediaSync',{})));
  const statusButtons={};
  for(const [value,label] of [['all','全部'],['unfinished','未完成'],['running','进行中'],['done','已完成']]) {
    const button=btn(label,'',()=>{statuses.value=value;taskView='books';taskPage=0;showSection('tasks');});
    statusButtons[value]=button;statusTabs.append(button);
  }
  autoDetails.addEventListener('toggle',()=>{if(currentSection==='automation' && autoDetails.open)renderAutoResults();});
  autoSearch.addEventListener('input',renderAutoResults);
  function drawTrack(track) {
    const row = elt('div', 'track'); row.append(elt('div', 'track-name', track.name));
    if(taskView==='tasks')row.append(elt('div','sub',`${track.library_name} · ${track.book.split('/').slice(1).join('/')}`));
    const detail = elt('div', 'track-detail');
    detail.append(elt('span', 'status ' + track.status, labels[track.status] || track.status));
    if (track.stage) detail.append(elt('span', '', track.stage));
    row.append(detail);
    if (track.status === 'running' || track.status === 'queued') {
      const progress = elt('div', 'progress');
      const bar = elt('i'); bar.style.width = `${track.progress}%`; progress.append(bar); row.append(progress);
      row.append(elt('div', 'sub', `${track.progress}% · 已运行 ${seconds(track.elapsed_seconds)} · 预计剩余 ${seconds(track.eta_seconds)}`));
    }
    if (track.error) row.append(elt('div', 'error', track.error));
    const actions = elt('div', 'actions');
    if (!track.source_missing && ['missing', 'failed', 'cancelled'].includes(track.status))
      actions.append(btn(track.status === 'failed' ? '↻ 重试' : '▶ 开始识别', 'primary', () => act('queue', {path:track.path})));
    if (!track.source_missing && ['running','queued'].includes(track.status))
      actions.append(btn('取消任务', 'danger', () => act('cancel', {path:track.path})));
    if (track.has_log) actions.append(btn('📋 日志', '', () => showLog(track)));
    if (track.has_subtitle) {
      actions.append(btn('查看文字', '', () => transcript(track)));
      actions.append(btn('下载 SRT', '', () => transcript(track, true)));
      if (!track.published && !track.source_missing) actions.append(btn('保存到书库', '', () => act('publish', {path:track.path})));
    }
    row.append(actions); return row;
  }
  function drawBook(book) {
    const item = elt('div', 'book'); const header = elt('div', 'book-head');
    const opened = expanded.has(book.id);
    const name = elt('div', 'book-name', book.name);
    name.append(elt('div', 'sub', `${labels[book.status]} · 进度 ${book.done}/${book.total} · ${book.library_name}`));
    header.append(name);
    header.append(btn(opened?'收起音轨':'展开音轨','',()=>{
      if(opened)expanded.delete(book.id);else expanded.add(book.id);
      render();
    }));
    const waiting = book.tracks.filter(t => ['missing','failed','cancelled'].includes(t.status));
    const start = btn('识别整本书', 'primary', () => {
      if (confirm(`开始或继续识别「${book.name}」？已完成部分会复用。`)) act('queueBook', {book:book.id});
    });
    start.disabled = waiting.length === 0; header.append(start); item.append(header);
    const toolActions=elt('div','actions');
    toolActions.append(btn('查看文稿 / 清除重识别 / 选择章节','',()=>{
      document.dispatchEvent(new CustomEvent('abs-open-book-tools',{
        detail:{trackPaths:book.tracks.map(track=>track.path)}
      }));
    }));
    item.append(toolActions);
    const problems=book.tracks.filter(t=>t.error || t.source_missing);if(problems.length)item.append(elt('div','error',`${problems.length} 个部分需要处理：${problems[0].source_missing?'源文件已移走':problems[0].error}`));
    const record=catalog.library_automation?.records?.find(record=>record.path===`/${book.id}` || record.path.startsWith(`/${book.id}/`));
    if(record) {
      const row=elt('div','book-automation');row.append(elt('div','auto-stats',recordText(record)));
      if(record.match_error || record.chapter_error)row.append(elt('div','error',record.match_error || record.chapter_error));
      item.append(row);
    }
    if (opened) {const tracks = elt('div', 'tracks'); book.tracks.forEach(t => tracks.append(drawTrack(t))); item.append(tracks);}
    return item;
  }
  const tasksButton=btn('所有音轨任务','primary',()=>{taskView='tasks';taskPage=0;render();});
  const booksButton=btn('按书籍查看','',()=>{taskView='books';render();});
  const firstPage=btn('首页','',()=>{taskPage=0;render();list.scrollTop=0;});
  const lastPage=btn('末页','',()=>{taskPage=taskPages-1;render();list.scrollTop=0;});
  const previousPage=btn('上一页','',()=>{if(taskPage>0){taskPage--;render();list.scrollTop=0;}});
  const nextPage=btn('下一页','',()=>{taskPage++;render();list.scrollTop=0;});
  const pageInfo=elt('span','page-info');viewbar.append(booksButton,tasksButton,firstPage,previousPage,nextPage,lastPage,pageInfo);
  function renderFlat(books) {
    const tracks=books.flatMap(book=>book.tracks);
    const order={running:0,queued:1,failed:2,missing:3,cancelled:4,done:5};
    tracks.sort((a,b)=>(order[a.status]-order[b.status]) || a.path.localeCompare(b.path));
    const pages=Math.max(1,Math.ceil(tracks.length/pageSize));taskPages=pages;taskPage=Math.max(0,Math.min(taskPage,pages-1));
    const visible=tracks.slice(taskPage*pageSize,(taskPage+1)*pageSize);
    firstPage.disabled=previousPage.disabled=taskPage===0;lastPage.disabled=nextPage.disabled=taskPage>=pages-1;
    pageInfo.textContent=`第 ${taskPage+1}/${pages} 页 · 共 ${tracks.length} 条任务`;
    firstPage.hidden=lastPage.hidden=previousPage.hidden=nextPage.hidden=false;
    list.classList.add('tasks-list');
    const children=[];
    visible.forEach(track=>{
      const signature=JSON.stringify(track);let cached=taskNodes.get(track.path);
      if(!cached || cached.signature!==signature){cached={signature,node:drawTrack(track)};taskNodes.set(track.path,cached);}
      children.push(cached.node);
    });
    if(!children.length)children.push(elt('div','info','没有匹配的任务'));
    if(list.children.length!==children.length || children.some((child,index)=>list.children[index]!==child))list.replaceChildren(...children);
    // Bound cached DOM nodes while preserving currently visible controls during refresh.
    if(taskNodes.size>300){const keep=new Set(visible.map(t=>t.path));for(const key of taskNodes.keys())if(!keep.has(key))taskNodes.delete(key);}
  }
  function render() {
    if (!catalog) return;
    const selectedLib = libraries.value || 'all', status = statuses.value || 'all';
    const query = search.value.toLowerCase().trim();
    const books = catalog.books.map(book => ({...book,status:book.tracks.some(t=>t.status==='running')?'running':book.done===book.total?'done':book.tracks.some(t=>t.status==='queued')?'queued':book.tracks.some(t=>t.status==='failed')?'failed':'missing'})).filter(book=>
      (selectedLib==='all'||book.library===selectedLib) &&
      (status==='all'||(status==='unfinished'?!['done','running'].includes(book.status):status==='source_missing'?book.tracks.some(t=>t.source_missing):status===book.status)) &&
      (!query||`${book.name} ${book.library_name}`.toLowerCase().includes(query)));
    for(const [value,button] of Object.entries(statusButtons))button.setAttribute('aria-pressed',String(status===value));
    booksButton.classList.toggle('primary',taskView==='books');
    tasksButton.classList.toggle('primary',taskView==='tasks');
    if(taskView==='tasks')return renderFlat(books);
    list.classList.remove('tasks-list');
    taskPages=Math.max(1,Math.ceil(books.length/pageSize));taskPage=Math.max(0,Math.min(taskPage,taskPages-1));
    firstPage.hidden=lastPage.hidden=previousPage.hidden=nextPage.hidden=false;
    firstPage.disabled=previousPage.disabled=taskPage===0;lastPage.disabled=nextPage.disabled=taskPage===taskPages-1;
    pageInfo.textContent=`第 ${taskPage+1}/${taskPages} 页 · 共 ${books.length} 本书任务`;
    const scroll=list.scrollTop;list.replaceChildren();
    if(!books.length)list.append(elt('div','info','没有匹配的书籍任务'));
    books.slice(taskPage*pageSize,(taskPage+1)*pageSize).forEach(book=>list.append(drawBook(book)));
    list.scrollTop=scroll;
  }

  async function load() {
    if (busy) return;
    busy = true;
    try {
      const result = await api('catalog');
      catalog = result;
      renderAutomation(result.library_automation);
      const bookStates=result.books.map(book=>book.tracks.some(t=>t.status==='running')?'running':book.done===book.total?'done':book.tracks.some(t=>t.status==='queued')?'queued':book.tracks.some(t=>t.status==='failed')?'failed':'missing');
      result.count=result.books.length;
      const state={done:0,running:0,queued:0,missing:0,failed:0};bookStates.forEach(status=>state[status]++);
      for(const [value,button] of Object.entries(statusButtons)){
        const count=value==='all'?result.count:value==='unfinished'?result.count-state.done-state.running:state[value];
        button.textContent=`${{all:'全部',unfinished:'未完成',running:'进行中',done:'已完成'}[value]} ${count}`;
      }
      toggle.classList.toggle('busy', state.running + state.queued > 0);
      const short = state.running ? `识别中 ${state.running}` : state.queued ? `排队 ${state.queued}` : '转写管理';
      toggle.lastChild.textContent = `📝 ${short}`;
      meta.textContent = `${result.engine || "Whisper"} · ${result.model} · ${result.language} · ${result.libraries.length} 个书库 · 当前 ${result.source_count ?? result.count} 音轨${result.history_count ? ' · 历史 '+result.history_count+' 条' : ''}`;
      const auto = result.automation;
      foot.textContent = auto?.enabled
        ? `自动识别已开启 · 文件稳定 ${Math.round(auto.settle_seconds/60)} 分钟后排队 · 失败最多尝试 ${auto.max_attempts} 次 · 手动取消后不自动重排`
        : 'Docker 本地 Whisper · 自动识别已关闭';
      const mediaSync=result.media_sync;
      if(mediaSync)foot.textContent+=` · 已索引 ${mediaSync.subtitle_files} 个字幕（${mediaSync.synced_books} 本）${mediaSync.current?' · '+mediaSync.current:''}${mediaSync.error?' · 同步异常：'+mediaSync.error:''}`;
      const pipeline = result.library_automation;
      if (pipeline?.settings) foot.textContent += ` · 自动匹配${pipeline.settings.auto_match?'开启':'关闭'} · 自动分章${pipeline.settings.auto_chapters?'开启':'关闭'}`;
      if (pipeline?.metadata_errors) foot.textContent += ` · ${pipeline.metadata_errors} 本资料暂未匹配，将稍后重试`;
      if (pipeline?.error) foot.textContent += ` · 分章/匹配异常：${pipeline.error}`;
      if (auto?.error) foot.textContent += ` · 扫描异常：${auto.error}`;
      metrics.replaceChildren();
      for (const [label, count] of [['书籍任务',result.count],['已识别',state.done],['待识别',state.missing],['进行中',state.running+state.queued],['失败',state.failed]]) {
        const metric = elt('div', 'metric'); metric.append(elt('b', '', String(count)), elt('span', '', label)); metrics.append(metric);
      }
      const previous = libraries.value;
      libraries.replaceChildren();
      const all = elt('option', '', '全部书库'); all.value = 'all'; libraries.append(all);
      result.libraries.forEach(lib => {const opt=elt('option', '', lib.name); opt.value=lib.id; libraries.append(opt);});
      libraries.value = Array.from(libraries.options).some(o => o.value === previous) ? previous : 'all';
      if (!panel.hidden) render();
      if (logPath && !logbox.hidden) {
        try {const text = logPath==='__automation__' ? await api('automationAction',{action:'log'}) : await api('log', {path:logPath}); logpre.textContent = text.log;} catch {}
      }
    } catch(error) {
      meta.textContent = '无法连接转写服务器';
      if (!panel.hidden) {list.replaceChildren(elt('div', 'warn', `${error.message}\n请检查 Docker 的 transcription-manager 容器和扩展配置。`));}
    } finally {busy = false;}
  }
  toggle.addEventListener('click', () => {panel.hidden = !panel.hidden; if (!panel.hidden) {showSection('tasks'); load();}});
  close.addEventListener('click', () => {if(dashboardMode)window.close();else panel.hidden = true;});
  refresh.addEventListener('click', load);
  logclose.addEventListener('click', () => {logPath = ''; logbox.hidden = true;});
  search.addEventListener('input', () => {maxBooks=32; taskPage=0; render();});
  statuses.addEventListener('change', () => {maxBooks=32; taskPage=0; render();});
  libraries.addEventListener('change', () => {maxBooks=32; taskPage=0; render();});
  showSection('tasks');
  load();
  let ticks = 0;
  setInterval(() => { if (!document.hidden && (!panel.hidden || (++ticks % 4 === 0))) load(); }, 3000);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) load(); });
  window.addEventListener('focus', load);
})();
