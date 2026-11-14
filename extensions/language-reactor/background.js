/* Chrome settings are local to the user's profile. Private presets stay outside Git. */
try { importScripts('preset.js'); } catch (_) { /* optional private installer */ }
try { importScripts('config.js'); } catch (_) { /* compatibility with setup.py local installs */ }

const SCRIPT_ID = 'audiobookshelf-language-reactor-button';
const CATALOG_TIMEOUT = 30000;
const AUDIO_TIMEOUT = 45000;
const SUBTITLE_TIMEOUT = 25000;
// Last bridge that answered successfully. Retrying it first makes a transient
// DNS/TLS hiccup on one hostname recover without reopening the picker.
let workingBase = null;

function originPattern(value) {
  const url = new URL(value);
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.pathname !== '/' || url.search || url.hash) {
    throw new Error('请输入仅包含主机和端口的 HTTP(S) 地址');
  }
  return `${url.protocol}//${url.hostname}/*`;
}
function normalizeBase(value) {
  return String(value || '').trim().replace(/\/+$/, '');
}
async function readConfig() {
  return chrome.storage.local.get(['absUrl', 'bridgeUrl', 'bridgeKey']);
}
async function registerButton() {
  const config = await readConfig();
  await chrome.scripting.unregisterContentScripts({ids:[SCRIPT_ID]}).catch(() => {});
  if (!config.absUrl) return;
  const pattern = originPattern(config.absUrl);
  if (!await chrome.permissions.contains({origins: [pattern]})) return;
  await chrome.scripting.registerContentScripts([{id: SCRIPT_ID, matches: [pattern], js: ['audiobookshelf.js'], runAt: 'document_idle'}]);
}
let registration = Promise.resolve();
function queueRegistration() {
  registration = registration.catch(() => {}).then(registerButton).catch(error => console.error('Button registration failed:', error));
}
async function migrateConfig() {
  const existing = await readConfig();
  const changes = {};
  // 仓库内置的默认地址优先；只有用户在选项里手动改过（customDomain=true）才保留
  if (!existing.customDomain) {
    if (existing.absUrl !== ABS_URL) changes.absUrl = ABS_URL;
    if (existing.bridgeUrl !== BRIDGE_URL) changes.bridgeUrl = BRIDGE_URL;
  }
  const legacyKey = typeof BRIDGE_CONFIG !== 'undefined' ? BRIDGE_CONFIG.key : null;
  if (!existing.bridgeKey && legacyKey) changes.bridgeKey = legacyKey;
  if (Object.keys(changes).length) await chrome.storage.local.set(changes);
  return {...existing, ...changes};
}

async function initialize(openSettings = true) {
  const config = await migrateConfig();
  queueRegistration();
  if (!openSettings) return;
  let hasAccess = false;
  try {
    if (config.absUrl) hasAccess = await chrome.permissions.contains({origins: [originPattern(config.absUrl)]});
  } catch (_) { /* settings page will show invalid URL */ }
  if (!config.absUrl || !config.bridgeUrl || !config.bridgeKey || !hasAccess) {
    await chrome.runtime.openOptionsPage();
  }
}
chrome.runtime.onInstalled.addListener(() => { initialize().catch(console.error); });
chrome.runtime.onStartup.addListener(() => initialize(false).catch(console.error));
chrome.storage.onChanged.addListener((changes, area) => {
  if (area === 'local' && (changes.absUrl || changes.bridgeUrl || changes.bridgeKey)) {
    workingBase = null;
    queueRegistration();
  }
});
chrome.permissions.onAdded.addListener(queueRegistration);

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}
/** Every bridge base that is both configured and permitted, best candidate first. */
async function bridgeCandidates(config) {
  const ordered = [workingBase, config.bridgeUrl];
  if (typeof BRIDGE_FALLBACKS !== 'undefined' && Array.isArray(BRIDGE_FALLBACKS)) ordered.push(...BRIDGE_FALLBACKS);
  const bases = [];
  for (const raw of ordered) {
    const base = normalizeBase(raw);
    if (!base || bases.includes(base)) continue;
    let pattern;
    try { pattern = originPattern(new URL(base).origin); } catch (_) { continue; }
    if (await chrome.permissions.contains({origins: [pattern]})) bases.push(base);
  }
  return bases;
}

/** One request with a hard timeout. The AbortController is what stops an
 *  extension page from waiting forever on a stalled tunnel. */
async function requestBridge(base, path, key, timeout) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(new Error(`等待 ${Math.round(timeout / 1000)} 秒后仍未响应`)), timeout);
  let response;
  try {
    response = await fetch(base + path, {
      cache: 'no-store',
      signal: controller.signal,
      headers: {'X-LR-Bridge-Key': key}
    });
  } catch (error) {
    const reason = error?.name === 'AbortError'
      ? `等待 ${Math.round(timeout / 1000)} 秒后仍未响应`
      : (error?.message || String(error));
    const wrapped = new Error(reason);
    wrapped.network = true;
    throw wrapped;
  } finally {
    clearTimeout(timer);
  }
  if (!response.ok) {
    let reason = `HTTP ${response.status}`;
    try { reason = (await response.json()).error || reason; } catch (_) { /* non JSON error body */ }
    const wrapped = new Error(reason);
    wrapped.status = response.status;
    throw wrapped;
  }
  return response;
}

async function bridgeFetch(path, {timeout = CATALOG_TIMEOUT, attempts = 2} = {}) {
  const config = await readConfig();
  if (!config.bridgeUrl || !config.bridgeKey) throw new Error('请先配置字幕服务地址和密钥');
  const bases = await bridgeCandidates(config);
  if (!bases.length) {
    throw new Error('插件尚未获准访问服务器。请打开插件设置，点击“保存并授权连接”，然后刷新书库。');
  }
  const hosts = bases.map(base => new URL(base).host);
  let lastReason = '未知错误';
  for (let round = 0; round < attempts; round++) {
    for (const base of bases) {
      try {
        const response = await requestBridge(base, path, config.bridgeKey, timeout);
        workingBase = base;
        return response;
      } catch (error) {
        lastReason = error.message;
        // A valid HTTP answer (bad key / missing track) will not improve on retry.
        if (error.status) throw error;
      }
    }
    if (round + 1 < attempts) await sleep(600);
  }
  const where = hosts.length > 1 ? `${hosts.join('、')}，最后一次` : hosts[0];
  const tail = /[。.!?]$/.test(lastReason) ? '' : '。';
  throw new Error(`无法连接字幕服务器（${where}）：${lastReason}${tail}请检查网络，或在插件选项里重新保存并授权。`);
}

chrome.runtime.onMessage.addListener((message, sender, reply) => {
  (async () => {
    const config = await readConfig();
    if (!sender.tab || !config.absUrl || new URL(sender.url).origin !== new URL(config.absUrl).origin) {
      throw new Error('仅允许配置的 Audiobookshelf 页面');
    }
    if (message?.type === 'catalog') {
      const started = Date.now();
      const response = await bridgeFetch('/api/catalog', {timeout: CATALOG_TIMEOUT});
      const data = await response.json();
      reply({ok: true, data, base: workingBase, ms: Date.now() - started});
    } else if (message?.type === 'open') {
      if (!Number.isInteger(message.index) || !/^[a-f0-9]{20}$/.test(message.issueId)) throw new Error('无效的音轨选择');
      const id = crypto.randomUUID();
      await chrome.storage.session.set({['job_' + id]: {issueId: message.issueId, index: message.index, created: Date.now()}});
      await chrome.tabs.create({url: `https://www.languagereactor.com/video-file#abs-lr=${id}`, active: true});
      reply({ok: true});
    } else throw new Error('未知操作');
  })().catch(error => reply({ok: false, error: error.message}));
  return true;
});

function toBase64(bytes) {
  let binary = '';
  const step = 0x8000;
  for (let offset = 0; offset < bytes.length; offset += step) {
    binary += String.fromCharCode.apply(null, bytes.subarray(offset, offset + step));
  }
  return btoa(binary);
}

chrome.runtime.onConnect.addListener(port => {
  if (port.name !== 'LR_BRIDGE_TRANSFER') return;
  if (!port.sender?.tab || !port.sender?.url?.startsWith('https://www.languagereactor.com/video-file')) {
    port.disconnect();
    return;
  }
  let reader = null, pending = new Uint8Array(0), sending = false, closed = false;
  const stop = () => {
    closed = true;
    if (reader) { try { reader.cancel().catch(() => {}); } catch (_) {} reader = null; }
  };
  port.onDisconnect.addListener(stop);
  const sendNext = async () => {
    if (sending || closed || !reader) return;
    sending = true;
    try {
      if (!pending.length) {
        const result = await reader.read();
        if (result.done) {
          port.postMessage({type: 'done'});
          const done = reader;
          reader = null;
          try { done.cancel().catch(() => {}); } catch (_) {}
          return;
        }
        pending = result.value;
      }
      const slice = pending.subarray(0, 96 * 1024);
      pending = pending.subarray(slice.length);
      port.postMessage({type: 'chunk', data: toBase64(slice)});
    } finally {
      sending = false;
    }
  };
  port.onMessage.addListener(message => {
    (async () => {
      if (message.type === 'next') return sendNext();
      if (message.type !== 'begin') return;
      const id = message.jobId;
      if (!/^[a-f0-9-]{36}$/.test(id)) throw new Error('无效的导入任务');
      const jobKey = 'job_' + id;
      const record = (await chrome.storage.session.get(jobKey))[jobKey];
      if (!record || Date.now() - record.created > 5 * 60 * 1000) throw new Error('导入任务已失效，请从 Audiobookshelf 重新打开');
      // A failed transfer is retried by the user-facing button in the LR tab,
      // so one attempt per request keeps the error latency predictable.
      const [audioResponse, subResponse] = await Promise.all([
        bridgeFetch(`/api/audio/${record.issueId}/${record.index}`, {timeout: AUDIO_TIMEOUT, attempts: 1}),
        bridgeFetch(`/api/subtitle/${record.issueId}/${record.index}`, {timeout: SUBTITLE_TIMEOUT, attempts: 1})
      ]);
      const subs = await subResponse.json();
      if (!audioResponse.body) throw new Error('服务器没有返回音频数据');
      reader = audioResponse.body.getReader();
      pending = new Uint8Array(0);
      const size = Number(audioResponse.headers.get('Content-Length'));
      port.postMessage({type: 'meta',
        filename: subs.audioFilename || subs.filename.replace(/\.srt$/i, '.mp3'),
        mimeType: subs.mimeType || 'audio/mpeg',
        subtitleFilename: subs.filename, srt: subs.srt,
        size: Number.isFinite(size) ? size : 0
      });
      await sendNext();
    })().catch(error => {
      stop();
      try { port.postMessage({type: 'error', message: error.message}); } catch (_) { /* already closed */ }
    });
  });
});
