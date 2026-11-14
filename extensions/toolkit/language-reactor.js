(() => {
  if (!location.pathname.startsWith('/video-file')) return;
  const match = location.hash.match(/(?:^#|&)abs-lr=([0-9a-f-]{36})/);
  if (!match) return;
  // A transient tab-specific job id; no network address or token is placed in the URL.
  history.replaceState(null, '', location.pathname + location.search);

  const card = document.createElement('div');
  Object.assign(card.style, {
    position: 'fixed', right: '16px', bottom: '16px', zIndex: '2147483647', background: '#143a33',
    color: '#fff', padding: '14px 16px 12px', width: 'min(420px, calc(100vw - 32px))', borderRadius: '12px',
    font: '14px/1.5 system-ui', boxShadow: '0 5px 18px #0008'
  });
  const message = document.createElement('div');
  message.textContent = '正在连接 Audiobookshelf…';
  const footer = document.createElement('div');
  Object.assign(footer.style, {display: 'flex', gap: '8px', marginTop: '8px'});
  const retry = document.createElement('button');
  retry.textContent = '重试';
  retry.style.display = 'none';
  const close = document.createElement('button');
  close.textContent = '关闭';
  for (const button of [retry, close]) {
    Object.assign(button.style, {
      background: '#2a6b5d', color: '#fff', border: '0', borderRadius: '7px',
      padding: '5px 10px', font: '12px system-ui', cursor: 'pointer'
    });
    footer.appendChild(button);
  }
  card.append(message, footer);
  document.body.appendChild(card);

  try {
    if (!chrome.runtime?.id) throw new Error('no runtime');
  } catch (_) {
    message.textContent = '插件已被更新或停用，请刷新页面后重新打开。';
    card.style.background = '#752d2d';
    return;
  }

  const DONE = 'done';
  let port = null, chunks = [], length = 0, meta = null, state = 'idle', watchdog = null, lastAudio = null;

  const tone = color => { card.style.background = color; };
  const clearWatchdog = () => { if (watchdog) clearTimeout(watchdog); watchdog = null; };
  const watch = (ms, text) => {
    clearWatchdog();
    watchdog = setTimeout(() => fail(text), ms);
  };
  function setStatus(text, color = '#143a33') {
    message.textContent = text;
    tone(color);
  }
  function fail(text, retryable = true) {
    clearWatchdog();
    disconnect();
    state = 'failed';
    setStatus(text, '#752d2d');
    retry.style.display = retryable ? '' : 'none';
  }
  function disconnect() {
    const current = port;
    port = null;
    if (current) { try { current.disconnect(); } catch (_) { /* already gone */ } }
  }
  function connect() {
    disconnect();
    chunks = []; length = 0; meta = null; lastAudio = null; state = 'loading';
    retry.style.display = 'none';
    setStatus('正在读取 Audiobookshelf 音频与现有字幕…');
    watch(60000, '读取超时：60 秒内没有收到音频数据。请重试，或检查插件选项里的服务器地址和密钥。');
    let next;
    try {
      next = chrome.runtime.connect({name: 'LR_BRIDGE_TRANSFER'});
    } catch (error) {
      fail(`无法连接插件后台：${error.message}。请刷新页面后重试。`);
      return;
    }
    port = next;
    next.onDisconnect.addListener(() => {
      if (port !== next) return;
      if (state !== DONE) fail('与插件后台的连接已断开（扩展可能被更新或停用）。请重试，必要时刷新页面。');
    });
    next.onMessage.addListener(message => handle(message, next));
    try {
      next.postMessage({type: 'begin', jobId: match[1]});
    } catch (error) {
      fail(`无法启动导入：${error.message}`);
    }
  }

  async function handle(incoming, source) {
    if (state === DONE || source !== port) return;
    try {
      if (incoming.type === 'error') throw new Error(incoming.message);
      if (incoming.type === 'meta') {
        meta = incoming;
        watch(60000, '接收音频超时：服务器超过 60 秒没有发送数据，请重试。');
        const size = meta.size ? `（${(meta.size / 1048576).toFixed(1)} MB）` : '';
        setStatus(`正在接收 ${meta.filename}${size}…`);
      } else if (incoming.type === 'chunk') {
        const decoded = atob(incoming.data);
        const bytes = new Uint8Array(decoded.length);
        for (let i = 0; i < decoded.length; i++) bytes[i] = decoded.charCodeAt(i);
        chunks.push(bytes);
        length += bytes.length;
        watch(60000, '接收音频超时：服务器超过 60 秒没有发送数据，请重试。');
        if (meta) {
          setStatus(meta.size
            ? `正在接收音频… ${Math.min(100, Math.round(length / meta.size * 100))}%`
            : `正在接收音频… ${(length / 1048576).toFixed(1)} MB`);
        }
        source.postMessage({type: 'next'});
      } else if (incoming.type === 'done') {
        clearWatchdog();
        if (!meta) throw new Error('服务器没有返回音频信息');
        if (meta.size && length !== meta.size) throw new Error(`音频数据不完整（${length}/${meta.size} 字节）`);
        state = DONE;
        const audio = new Blob(chunks, {type: meta.mimeType || 'audio/mpeg'});
        chunks = [];
        disconnect();
        lastAudio = audio;
        await inject(audio, meta);
      }
    } catch (error) {
      disconnect();
      fail(`Audiobookshelf 导入失败：${error.message}`);
    }
  }

  const waitFor = (predicate, timeout, label) => new Promise((resolve, reject) => {
    const first = predicate();
    if (first) return resolve(first);
    const observer = new MutationObserver(() => {
      const value = predicate();
      if (value) { observer.disconnect(); clearTimeout(timer); resolve(value); }
    });
    observer.observe(document.documentElement, {childList: true, subtree: true, attributes: true, attributeFilter: ['disabled']});
    const timer = setTimeout(() => {
      observer.disconnect();
      reject(new Error(`Language Reactor 页面没有出现${label}（可能页面已更新或尚未登录）`));
    }, timeout);
  });
  const setFile = (input, name, data, type) => {
    const transfer = new DataTransfer();
    transfer.items.add(new File([data], name, {type}));
    input.files = transfer.files;
    input.dispatchEvent(new Event('input', {bubbles: true}));
    input.dispatchEvent(new Event('change', {bubbles: true}));
  };

  async function inject(audio, info) {
    if (state === 'injecting') return;
    state = 'injecting';
    try {
      setStatus('音频已接收，正在查找 Language Reactor 上传控件…');
      const mediaInput = await waitFor(() => document.querySelector('input[type="file"][accept*="audio/"]'), 45000, '音频上传控件');
      setFile(mediaInput, info.filename, audio, info.mimeType || 'audio/mpeg');
      const radio = await waitFor(() => {
        const item = document.querySelector('input[type="radio"][value="external"]');
        return item && !item.disabled ? item : null;
      }, 30000, '“外部字幕”选项');
      radio.click();
      const subtitleInput = await waitFor(() => {
        const item = document.querySelector('input[type="file"][accept*=".srt"]');
        return item && !item.disabled ? item : null;
      }, 30000, '字幕上传控件');
      setFile(subtitleInput, info.subtitleFilename, info.srt, 'application/x-subrip');
      setStatus(`✓ 已自动加载 ${info.filename} 和配套 SRT 字幕。正在启动播放器…`);
      // The LR media player has a Play button which validates all fields before starting.
      const play = await waitFor(() => {
        const candidates = [...document.querySelectorAll('button')];
        return candidates.find(node => !node.disabled && (/^(play|播放|再生|播放视频)$/i.test(node.textContent.trim())
          || /^(play|播放)$/i.test(node.getAttribute('aria-label') || ''))) || null;
      }, 15000, '播放按钮').catch(() => null);
      if (play) {
        play.click();
        setStatus('✓ 音频与字幕已导入 Language Reactor，正在打开学习播放器。');
      } else {
        setStatus('✓ 音频与字幕已导入。请点击页面底部的 Play 开始。');
      }
      state = 'injected';
      retry.style.display = 'none';
      setTimeout(() => card.remove(), 30000);
    } catch (error) {
      fail(`自动导入未完成：${error.message}`, false);
      if (lastAudio && info) {
        retry.textContent = '重试导入';
        retry.style.display = '';
        retry.onclick = () => { retry.style.display = 'none'; card.style.background = '#143a33'; inject(lastAudio, info); };
      }
    }
  }

  retry.onclick = () => {
    retry.style.display = 'none';
    if (lastAudio && meta) { state = 'idle'; inject(lastAudio, meta); return; }
    connect();
  };
  close.onclick = () => { clearWatchdog(); disconnect(); card.remove(); };
  connect();
})();
