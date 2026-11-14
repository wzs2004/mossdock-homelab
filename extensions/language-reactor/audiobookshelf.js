(() => {
  if (document.getElementById('abs-lr-button')) return;
  const button = document.createElement('button');
  button.id = 'abs-lr-button';
  button.textContent = '↗ Language Reactor';
  Object.assign(button.style, {
    position: 'fixed', right: '18px', top: '105px', zIndex: '2147483500',
    background: '#167557', color: 'white', border: '1px solid #65d5ac', borderRadius: '12px',
    padding: '11px 15px', font: '600 14px system-ui', cursor: 'pointer', boxShadow: '0 5px 22px #0006'
  });
  document.body.appendChild(button);

  function make(tag, text, parent, css = {}) {
    const element = document.createElement(tag);
    if (text != null) element.textContent = text;
    Object.assign(element.style, css);
    parent.appendChild(element);
    return element;
  }

  function sendMessage(message, timeout = 45000) {
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error(`插件后台 ${Math.round(timeout / 1000)} 秒内没有响应`)), timeout);
      try {
        chrome.runtime.sendMessage(message, response => {
          clearTimeout(timer);
          const error = chrome.runtime.lastError;
          if (error) reject(new Error(error.message));
          else if (!response) reject(new Error('插件后台没有返回数据（请刷新页面或重新加载扩展）'));
          else resolve(response);
        });
      } catch (error) {
        clearTimeout(timer);
        reject(error);
      }
    });
  }

  async function sendWithRetry(message, attempts = 2) {
    let last;
    for (let attempt = 1; attempt <= attempts; attempt++) {
      try {
        return await sendMessage(message);
      } catch (error) {
        last = error;
        // "Extension context invalidated" cannot recover without a page reload.
        if (/context invalidated/i.test(error.message)) break;
        if (attempt < attempts) await new Promise(resolve => setTimeout(resolve, 500));
      }
    }
    throw last;
  }

  button.onclick = async () => {
    const curtain = make('div', null, document.body, {position:'fixed', inset:'0', zIndex:'2147483600', background:'#000a', display:'grid', placeItems:'center', padding:'16px'});
    const card = make('div', null, curtain, {width:'min(680px, 100%)', maxHeight:'min(85vh, 760px)', overflow:'auto', background:'#182027', color:'white', padding:'22px', borderRadius:'16px', font:'14px system-ui', boxShadow:'0 20px 60px #0009'});
    const header = make('div', null, card, {display:'flex', justifyContent:'space-between', alignItems:'center'});
    make('strong', '选择要在 Language Reactor 学习的书籍或期刊', header, {fontSize:'18px'});
    let timer, poll;
    const close = () => {
      clearInterval(timer);
      clearInterval(poll);
      window.removeEventListener('focus', onFocus);
      document.removeEventListener('visibilitychange', onVisibility);
      curtain.remove();
    };
    make('button', '✕', header, {color:'white', background:'transparent', border:'0', cursor:'pointer', fontSize:'20px'}).onclick = close;
    curtain.addEventListener('click', event => { if (event.target === curtain) close(); });
    const note = make('p', '正在读取已转好的音频和字幕…', card, {color:'#afc4ce'});
    const detail = make('p', '', card, {color:'#6f8a97', fontSize:'12px', margin:'-6px 0 10px'});
    const selector = make('select', null, card, {width:'100%', padding:'11px', marginBottom:'12px', background:'#273743', color:'white', border:'1px solid #50616d', borderRadius:'9px'});
    const search = make('input', null, card, {boxSizing:'border-box', width:'100%', padding:'11px', marginBottom:'12px', background:'#273743', color:'white', border:'1px solid #50616d', borderRadius:'9px'});
    search.placeholder = '搜索音轨标题或编号';
    const list = make('div', null, card, {display:'grid', gap:'5px', maxHeight:'48vh', overflowY:'auto'});
    let issues = [], loading = false, lastError = '';

    const render = () => {
      const issue = issues.find(entry => entry.id === selector.value);
      const scroll = list.scrollTop;
      list.replaceChildren();
      if (!issue) { note.textContent = '书库暂无可用音轨，正在自动监测新书…'; return; }
      const query = search.value.trim().toLowerCase();
      note.textContent = `选中 ${issue.name}，${issue.tracks.length} 篇。点击音轨即打开 LR 并加载对应的音频 + SRT。`;
      issue.tracks.filter(track => track.name.toLowerCase().includes(query)).forEach(track => {
        const item = make('button', `${track.hasSubtitles ? '▶' : '⚠'}  ${track.name}`, list, {
          textAlign:'left', padding:'11px', borderRadius:'8px', border:'1px solid #354853', background:'#24313a', color:'white', cursor:'pointer'
        });
        item.title = track.hasSubtitles ? '打开 Language Reactor' : '这篇没有找到有效字幕';
        item.disabled = !track.hasSubtitles;
        if (item.disabled) item.style.opacity = '.5';
        item.onclick = async () => {
          item.disabled = true;
          item.textContent = '正在打开 Language Reactor…';
          try {
            const result = await sendWithRetry({type:'open', issueId:issue.id, index:track.index}, 1);
            if (!result?.ok) throw new Error(result?.error || '未知错误');
            close();
          } catch (error) {
            note.textContent = `打开失败：${error.message}`;
            item.disabled = false;
            item.textContent = `${track.hasSubtitles ? '▶' : '⚠'}  ${track.name}`;
          }
        };
      });
      list.scrollTop = scroll;
    };

    const showRetry = error => {
      lastError = error;
      detail.textContent = '';
      note.replaceChildren(document.createTextNode(`书库读取失败：${error}。`));
      const retry = document.createElement('button');
      retry.textContent = '立即重试';
      Object.assign(retry.style, {
        marginLeft: '8px', background: '#2a6b5d', color: '#fff', border: '0',
        borderRadius: '6px', padding: '3px 9px', font: '12px system-ui', cursor: 'pointer'
      });
      retry.onclick = () => { retry.disabled = true; refresh(); };
      note.appendChild(retry);
      note.appendChild(document.createTextNode('（每 10 秒自动重试）'));
    };

    const refresh = async () => {
      if (loading || !curtain.isConnected) return;
      loading = true;
      const started = Date.now();
      note.textContent = '正在读取已转好的音频和字幕…';
      note.style.color = '#afc4ce';
      timer = setInterval(() => {
        if (!loading) return;
        const seconds = Math.round((Date.now() - started) / 1000);
        note.textContent = `正在读取已转好的音频和字幕…（已等待 ${seconds} 秒）`;
      }, 1000);
      try {
        const response = await sendWithRetry({type:'catalog'});
        if (!response?.ok) throw new Error(response?.error || '没有收到书库列表');
        const oldId = selector.value;
        issues = response.data.issues || [];
        if (!issues.length) throw new Error('音频书库中没有文章');
        selector.replaceChildren();
        issues.forEach(issue => {
          const option = make('option', `${issue.library === 'audiobooks115' ? '115 有声书 · ' : '期刊 · '}${issue.name.split('/').pop()}（${issue.tracks.length} 音轨${issue.hasSubtitles ? ' · 已有字幕' : ''}）`, selector);
          option.value = issue.id;
        });
        const title = document.title + ' ' + (document.querySelector('h1')?.textContent || '');
        const itemId = location.pathname.match(/\/item\/([^/?#]+)/)?.[1];
        let selected = issues.find(issue => issue.id === oldId);
        if (!selected && itemId) selected = issues.find(issue => issue.itemIds?.includes(itemId));
        if (!selected) selected = issues.find(issue => issue.name.match(/\d{4}-\d\d-\d\d/) && title.includes(issue.name.match(/\d{4}-\d\d-\d\d/)[0]));
        selector.value = (selected || issues[0])?.id || '';
        const via = response.base ? ` · ${new URL(response.base).host}` : '';
        const latency = Number.isFinite(response.ms) ? `（${(response.ms / 1000).toFixed(1)} 秒）` : '';
        lastError = '';
        detail.textContent = `已连接${via}${latency} · 自动同步 · ${issues.length} 部作品`;
        note.style.color = '#afc4ce';
        render();
      } catch (error) {
        showRetry(error.message);
      } finally {
        clearInterval(timer);
        loading = false;
      }
    };

    selector.onchange = render;
    search.oninput = render;
    const onFocus = () => refresh();
    const onVisibility = () => { if (!document.hidden) refresh(); };
    window.addEventListener('focus', onFocus);
    document.addEventListener('visibilitychange', onVisibility);
    poll = setInterval(() => { if (!document.hidden) refresh(); }, 10000);
    refresh();
  };
})();
