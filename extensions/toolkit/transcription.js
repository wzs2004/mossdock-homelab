/* 转写管理面板 —— 主页看队列，书页看这一本。
   没有搜索、没有文稿浏览、没有高级选项，只保留队列与进度。 */
(async () => {
  const {absUrl} = await chrome.storage.local.get('absUrl');
  if (!absUrl || location.origin !== new URL(absUrl).origin) return;
  if (document.getElementById('abs-transcription-root')) return;

  const root = document.createElement('div');
  root.id = 'abs-transcription-root';
  document.documentElement.append(root);
  const dom = root.attachShadow({mode: 'open'});
  const style = document.createElement('style');
  style.textContent = `
    :host{font:14px/1.55 system-ui;color:#e6eef5}
    *{box-sizing:border-box}button{font:inherit;cursor:pointer;padding:7px 12px;border:1px solid #54718c;
      background:#29445e;color:#fff;border-radius:7px}button:disabled{opacity:.45;cursor:default}
    #toggle{position:fixed;right:20px;bottom:20px;z-index:2147483645;box-shadow:0 6px 20px #0006}
    #panel{position:fixed;right:20px;bottom:72px;width:min(460px,calc(100vw - 40px));max-height:70vh;
      z-index:2147483646;background:#101f30;border:1px solid #54718c;border-radius:12px;
      display:flex;flex-direction:column;box-shadow:0 15px 50px #0009}
    #panel[hidden]{display:none}
    header{display:flex;align-items:center;gap:8px;padding:12px 14px;background:#1b324a;border-radius:12px 12px 0 0}
    header strong{flex:1;font-size:15px}header .count{color:#9db4ca;font-size:12px}
    main{overflow:auto;padding:6px 0}
    .row{padding:10px 14px;border-top:1px solid #23374d}
    .row:first-child{border-top:0}
    .title{display:flex;gap:8px;align-items:baseline}
    .title b{flex:1;font-size:13.5px;font-weight:600}
    .tag{font-size:11px;padding:1px 7px;border-radius:10px;background:#2d4a66;color:#cfe6f7;white-space:nowrap}
    .tag.running{background:#1f6f5c;color:#c8ffe9}.tag.done{background:#2b5a3a;color:#c8f5d5}
    .tag.failed{background:#6b2b2b;color:#ffd0c8}.tag.missing{background:#4a4a5e;color:#d6d6e6}
    progress{width:100%;height:6px;margin-top:6px}
    .meta{font-size:11.5px;color:#9db4ca;margin-top:4px;white-space:pre-wrap}
    .error{font-size:11.5px;color:#ffb6ac;margin-top:4px;white-space:pre-wrap}
    .row button{margin-top:8px}
    footer{padding:10px 14px;color:#9db4ca;font-size:11.5px;border-top:1px solid #23374d}
  `;
  dom.append(style);

  const el = (tag, text = '', cls = '') => {
    const node = document.createElement(tag);
    node.textContent = text;
    if (cls) node.className = cls;
    return node;
  };

  const toggle = el('button');
  toggle.id = 'toggle';
  dom.append(toggle);

  const panel = el('section');
  panel.id = 'panel';
  panel.hidden = true;
  const head = el('header');
  const title = el('strong', '转写队列');
  const count = el('span', '', 'count');
  const refreshButton = el('button', '刷新');
  const closeButton = el('button', '关闭');
  head.append(title, count, refreshButton, closeButton);
  const main = el('main');
  const footer = el('footer', '自动跟随书库变化，每 3 秒刷新');
  panel.append(head, main, footer);
  dom.append(panel);

  const LABELS = {running: '转写中', queued: '排队中', done: '已完成',
                  missing: '未转写', failed: '失败', cancelled: '已取消'};
  const RANK = ['running', 'queued', 'failed', 'missing', 'done', 'cancelled'];

  let books = [];
  let loading = false;
  let currentId = itemId();

  function itemId() {
    const match = location.pathname.match(/\/item\/([^/]+)/);
    return match ? decodeURIComponent(match[1]) : '';
  }

  async function api(action, value) {
    const reply = await chrome.runtime.sendMessage({action, value});
    if (reply?.error) throw new Error(reply.error);
    return reply;
  }

  function syncToggle() {
    const id = itemId();
    const book = books.find(b => b.id === id);
    if (!id) { toggle.textContent = '📝 转写队列'; return; }
    if (book && ['running', 'queued'].includes(book.status)) {
      toggle.textContent = `转写中 ${Math.round(book.progress || 0)}%`;
      return;
    }
    if (book && book.status === 'failed') { toggle.textContent = '转写失败 · 查看'; return; }
    toggle.textContent = '转写这本书';
  }

  function row(book) {
    const box = el('div', '', 'row');
    const line = el('div', '', 'title');
    line.append(el('b', book.name));
    line.append(el('span', LABELS[book.status] || book.status, `tag ${book.status || ''}`));
    box.append(line);

    const bar = document.createElement('progress');
    bar.max = 100;
    bar.value = Math.max(0, Math.min(100, book.progress || 0));
    box.append(bar);

    const bits = [`${Math.round(book.progress || 0)}%`];
    if (book.tracks?.length) bits.push(`${book.tracks.length} 条音轨`);
    if (['running', 'queued'].includes(book.status) && book.eta_seconds) {
      bits.push(`预计剩余 ${Math.round(book.eta_seconds / 60)} 分钟`);
    }
    if (book.stage) bits.push(book.stage);
    box.append(el('div', bits.join(' · '), 'meta'));

    if (book.errors?.length) box.append(el('div', book.errors.join('\n'), 'error'));

    const canStart = book.status !== 'done' && !['running', 'queued'].includes(book.status)
                     && !book.source_missing;
    if (canStart) {
      const start = el('button', book.status === 'failed' ? '重试' : '开始转写');
      start.onclick = async () => {
        start.disabled = true;
        start.textContent = '已提交…';
        try {
          await api('queueCurrentBook', {item_id: book.id, retranscribe: false});
          await load();
        } catch (error) {
          box.append(el('div', error.message, 'error'));
          start.disabled = false;
          start.textContent = '重试';
        }
      };
      box.append(start);
    }
    return box;
  }

  function render() {
    const id = itemId();
    const visible = books
      .filter(b => !id || b.id === id)
      .sort((a, b) => {
        const ai = RANK.indexOf(a.status), bi = RANK.indexOf(b.status);
        return (ai < 0 ? 99 : ai) - (bi < 0 ? 99 : bi) || a.name.localeCompare(b.name);
      });

    const pending = books.filter(b => b.status !== 'done').length;
    title.textContent = id ? '这本书的转写' : '转写队列';
    count.textContent = id ? '' : `未完成 ${pending} · 已完成 ${books.length - pending}`;

    main.replaceChildren();
    if (!visible.length) {
      main.append(el('div', '没有找到书籍', 'meta'));
    } else {
      for (const book of visible) main.append(row(book));
    }
    syncToggle();
  }

  async function load() {
    if (loading) return;
    loading = true;
    try {
      const data = await api('books');
      books = data.books || [];
      if (!books.length) notice.textContent = data.model_error || '';
      render();
    } catch (error) {
      main.replaceChildren(el('div', `读取失败：${error.message}`, 'error'));
    } finally {
      loading = false;
    }
  }

  toggle.onclick = async () => {
    panel.hidden = !panel.hidden;
    if (panel.hidden) return;
    if (!books.length) main.replaceChildren(el('div', '正在读取书籍列表…', 'meta'));
    await load();
  };
  closeButton.onclick = () => { panel.hidden = true; };
  refreshButton.onclick = () => load();

  setInterval(async () => {
    const id = itemId();
    if (id !== currentId) {
      currentId = id;
      panel.hidden = true;
    }
    if (panel.hidden) {
      if (!books.length) {
        try { books = (await api('books')).books || []; } catch (_) { /* 静默 */ }
      }
      syncToggle();
      return;
    }
    await load();
  }, 3000);

  syncToggle();
})();
