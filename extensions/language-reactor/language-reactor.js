(() => {
  if (!location.pathname.startsWith("/video-file")) return;
  const match = location.hash.match(/(?:^#|&)abs-lr=([0-9a-f-]{36})/);
  if (!match) return;
  // A transient tab-specific job id; no network address or token is placed in the URL.
  history.replaceState(null, "", location.pathname + location.search);

  const status = document.createElement("div");
  Object.assign(status.style, {
    position: "fixed", right: "16px", bottom: "16px", zIndex: "2147483647", background: "#143a33",
    color: "#fff", padding: "14px 18px", maxWidth: "390px", borderRadius: "12px", font: "14px/1.5 system-ui", boxShadow: "0 5px 18px #0008"
  });
  status.textContent = "正在读取 Audiobookshelf 音频与现有字幕…";
  document.body.appendChild(status);

  const waitFor = (predicate, timeout = 60000) => new Promise((resolve, reject) => {
    const first = predicate(); if (first) return resolve(first);
    const observer = new MutationObserver(() => { const value = predicate(); if (value) { observer.disconnect(); clearTimeout(timer); resolve(value); } });
    observer.observe(document.documentElement, {childList: true, subtree: true, attributes: true, attributeFilter:["disabled"]});
    const timer = setTimeout(() => { observer.disconnect(); reject(new Error("Language Reactor 页面没有出现所需控件（可能页面已更新或尚未登录）")); }, timeout);
  });
  const setFile = (input, name, data, type) => {
    const transfer = new DataTransfer();
    transfer.items.add(new File([data], name, {type}));
    input.files = transfer.files;
    input.dispatchEvent(new Event("change", {bubbles:true}));
  };

  async function inject(audio, meta) {
    status.textContent = "音频已接收，正在导入 Language Reactor…";
    const mediaInput = await waitFor(() => document.querySelector('input[type="file"][accept*="audio/"]'));
    setFile(mediaInput, meta.filename, audio, meta.mimeType || "audio/mpeg");
    const radio = await waitFor(() => {
      const item = document.querySelector('input[type="radio"][value="external"]');
      return item && !item.disabled ? item : null;
    });
    radio.click();
    const subtitleInput = await waitFor(() => {
      const item = document.querySelector('input[type="file"][accept*=".srt"]');
      return item && !item.disabled ? item : null;
    });
    setFile(subtitleInput, meta.subtitleFilename, meta.srt, "application/x-subrip");
    status.textContent = `✓ 已自动加载 ${meta.filename} 和配套 SRT 字幕。正在启动播放器…`;
    // The LR media player has a Play button which validates all fields before starting.
    const play = await waitFor(() => {
      const candidates = [...document.querySelectorAll("button")];
      return candidates.find(node => !node.disabled && /^(play|播放|再生|播放视频)$/i.test(node.textContent.trim())) || null;
    }, 15000).catch(() => null);
    if (play) {
      play.click();
      status.textContent = "✓ 音频与字幕已导入 Language Reactor，正在打开学习播放器。";
    } else {
      status.textContent = "✓ 音频与字幕已导入。请点击页面底部的 Play 开始。";
    }
    setTimeout(() => status.remove(), 16000);
  }

  const port = chrome.runtime.connect({name:"LR_BRIDGE_TRANSFER"});
  let meta = null, chunks = [], length = 0;
  port.onMessage.addListener(async message => {
    try {
      if (message.type === "error") throw new Error(message.message);
      if (message.type === "meta") {
        meta = message; status.textContent = `正在接收 ${meta.filename}（${(meta.size/1048576).toFixed(1)} MB）…`;
      } else if (message.type === "chunk") {
        const decoded = atob(message.data);
        const bytes = Uint8Array.from(decoded, c => c.charCodeAt(0));
        chunks.push(bytes); length += bytes.length;
        if (meta) status.textContent = `正在接收音频… ${Math.round(length/meta.size*100)}%`;
        port.postMessage({type:"next"});
      } else if (message.type === "done") {
        if (!meta || length !== meta.size) throw new Error("音频数据不完整");
        const audio = new Blob(chunks, {type:meta.mimeType || "audio/mpeg"});
        chunks = [];
        port.disconnect();
        await inject(audio, meta);
      }
    } catch (error) {
      status.textContent = `Audiobookshelf 导入失败：${error.message}`;
      status.style.background = "#752d2d";
      try { port.disconnect(); } catch (_) { /* already closed */ }
    }
  });
  port.postMessage({type:"begin", jobId:match[1]});
})();
