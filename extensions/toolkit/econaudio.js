/* 经济学人音频面板：注入到 monkfishare.gitlab.io/econaudio，
   选一期 → 交给家里的服务下载进 Audiobookshelf 书架。
   请求都走 background（有跨域权限），这里只负责 UI。 */
(() => {
  if (document.getElementById('abs-econ-panel-host')) return;

  const HOST = document.createElement('div');
  HOST.id = 'abs-econ-panel-host';
  const root = HOST.attachShadow({mode: 'open'});

  root.innerHTML = `
    <style>
      :host { all: initial; }
      .wrap {
        position: fixed; right: 20px; bottom: 20px; z-index: 2147483000;
        width: 320px; font: 13px/1.55 -apple-system, "PingFang SC", system-ui, sans-serif;
        color: #eaf4fa; background: #0e1929; border: 1px solid #2f5570; border-radius: 14px;
        box-shadow: 0 12px 32px rgba(0,0,0,.45); overflow: hidden;
      }
      .head {
        display: flex; align-items: center; gap: 8px; padding: 10px 14px;
        background: linear-gradient(180deg,#16304a,#122436); cursor: pointer;
        user-select: none;
      }
      .dot { width: 8px; height: 8px; border-radius: 50%; background: #6b7f8f; flex: none; }
      .dot.ok { background: #35c07a; } .dot.bad { background: #d9534f; }
      .title { flex: 1; font-weight: 600; font-size: 13px; letter-spacing: .2px; }
      .chev { opacity: .6; font-size: 11px; }
      .body { padding: 12px 14px 14px; display: grid; gap: 9px; }
      .body.hidden { display: none; }
      label { font-size: 12px; color: #9db6c6; }
      select, button { font: inherit; }
      select {
        width: 100%; padding: 8px 9px; border-radius: 8px; color: #eaf4fa;
        background: #172c41; border: 1px solid #3d5c74;
      }
      .row { display: flex; gap: 8px; }
      button {
        flex: 1; padding: 9px 10px; border: 0; border-radius: 8px; cursor: pointer;
        background: #13836b; color: #fff; font-weight: 600;
      }
      button.ghost { background: transparent; border: 1px solid #2f5570; color: #bfe7f5; flex: 0 0 auto; }
      button:disabled { opacity: .5; cursor: not-allowed; }
      .status { font-size: 12px; color: #9fe5bd; min-height: 17px; white-space: pre-wrap; }
      .status.err { color: #f0a6a3; }
      .hint { font-size: 11px; color: #7b93a5; }
      .bar { height: 4px; background: #172c41; border-radius: 3px; overflow: hidden; }
      .bar > i { display: block; height: 100%; width: 0; background: #13836b; transition: width .3s; }
    </style>
    <div class="wrap">
      <div class="head" id="head">
        <span class="dot" id="dot"></span>
        <span class="title">经济学人 · 送进书架</span>
        <span class="chev" id="chev">▾</span>
      </div>
      <div class="body" id="body">
        <label for="edition">选择一期</label>
        <select id="edition"><option>载入中…</option></select>
        <div class="row">
          <button id="go">下载到书架</button>
          <button class="ghost" id="refresh" title="重新载入列表">↻</button>
        </div>
        <div class="bar"><i id="bar"></i></div>
        <div class="status" id="status">正在连接家里的服务…</div>
        <div class="hint">下载在服务器上跑，关掉页面也不影响；完成后 Audiobookshelf 会自动扫描。</div>
      </div>
    </div>`;

  const $ = id => root.getElementById(id);
  const dot = $('dot'), select = $('edition'), go = $('go'), statusEl = $('status'), bar = $('bar');

  let editions = [];
  let polling = null;
  let busy = false;

  function setStatus(text, isError = false) {
    statusEl.textContent = text;
    statusEl.classList.toggle('err', isError);
  }
  function setDot(state) {
    dot.className = 'dot' + (state ? ' ' + state : '');
  }
  function setProgress(done, total) {
    bar.style.width = total ? Math.round((done / total) * 100) + '%' : '0';
  }

  async function send(action, payload = {}) {
    const response = await chrome.runtime.sendMessage({action, ...payload});
    if (!response) throw new Error('没有收到扩展的响应');
    if (response.error) throw new Error(response.error);
    return response;
  }

  function label(item) {
    const date = item.edition;
    const bits = [`${item.tracks} 篇`];
    if (item.inLibrary && item.completed >= item.tracks) bits.push('已在书架');
    else if (item.completed) bits.push(`已完成 ${item.completed}/${item.tracks}`);
    return `${date} · ${bits.join(' · ')}`;
  }

  async function loadEditions() {
    setStatus('正在获取可下载的期…');
    try {
      const data = await send('econEditions', {weeks: 20});
      editions = data.editions || [];
      select.innerHTML = '';
      if (!editions.length) {
        select.innerHTML = '<option value="">最近没有可下载的期</option>';
        setStatus('源站最近没有可用的期刊。', true);
        return;
      }
      for (const item of editions) {
        const option = document.createElement('option');
        option.value = item.edition;
        option.textContent = label(item);
        select.appendChild(option);
      }
      // 默认选最新一期；如果页面上已经选了某天，就跟着它
      const siteDate = document.querySelector('#editionDate');
      if (siteDate && siteDate.value && editions.some(e => e.edition === siteDate.value)) {
        select.value = siteDate.value;
      }
      const current = editions.find(e => e.edition === select.value);
      if (current && current.inLibrary && current.completed >= current.tracks) {
        setStatus('这一期已经在书架里了，可以换一期。');
      } else {
        setStatus('选好之后点「下载到书架」。');
      }
      setDot('ok');
    } catch (error) {
      setDot('bad');
      setStatus(`连不上服务：${error.message}`, true);
    }
  }

  async function poll(jobId, edition) {
    clearInterval(polling);
    polling = setInterval(async () => {
      try {
        const job = await send('econJob', {job: jobId});
        setProgress(job.completed || 0, job.total || 0);
        const failed = job.failed ? `（失败 ${job.failed}）` : '';
        setStatus(`${edition}：${job.completed || 0}/${job.total || 0}${failed}`);
        if (job.state === 'done' || job.state === 'partial' || job.state === 'error') {
          clearInterval(polling); polling = null; busy = false;
          go.disabled = false;
          if (job.state === 'done') {
            setProgress(job.total, job.total);
            setDot('ok');
            setStatus(`✓ ${edition} 已存进书架，Audiobookshelf 正在扫描。`);
            const item = editions.find(e => e.edition === edition);
            if (item) { item.inLibrary = true; item.completed = job.completed; }
            const index = editions.indexOf(item);
            if (index >= 0) select.options[index].textContent = label(item);
          } else {
            setDot('bad');
            setStatus(`${job.state === 'error' ? '失败' : '部分失败'}：${job.message || job.failed + ' 篇没下成功，可以再点一次重试'}`, true);
          }
        }
      } catch (error) {
        clearInterval(polling); polling = null; busy = false; go.disabled = false;
        setDot('bad');
        setStatus(`查询进度失败：${error.message}`, true);
      }
    }, 2000);
  }

  async function start() {
    const edition = select.value;
    if (!edition) return;
    if (busy) return;
    busy = true; go.disabled = true;
    setStatus(`正在让服务器下载 ${edition}…`);
    setProgress(0, 0);
    try {
      const job = await send('econSync', {edition});
      if (job.state === 'done') {
        busy = false; go.disabled = false;
        setProgress(1, 1);
        setStatus(`✓ ${edition} 已在书架里。`);
        return;
      }
      poll(job.job, edition);
    } catch (error) {
      busy = false; go.disabled = false;
      setDot('bad');
      setStatus(`启动失败：${error.message}`, true);
    }
  }

  $('go').addEventListener('click', start);
  $('refresh').addEventListener('click', () => { if (!busy) loadEditions(); });
  select.addEventListener('change', () => {
    const item = editions.find(e => e.edition === select.value);
    if (item && item.inLibrary && item.completed >= item.tracks) setStatus('这一期已经在书架里了。');
    else setStatus('选好之后点「下载到书架」。');
  });
  $('head').addEventListener('click', () => {
    const body = $('body');
    body.classList.toggle('hidden');
    $('chev').textContent = body.classList.contains('hidden') ? '▸' : '▾';
  });

  document.documentElement.appendChild(HOST);

  chrome.storage.onChanged.addListener((changes, area) => {
    if (area === 'local' && (changes.econUrl || changes.econKey || changes.absUrl)) {
      if (!busy) loadEditions();
    }
  });

  loadEditions();
})();
