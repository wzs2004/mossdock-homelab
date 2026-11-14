/* Settings are kept in the browser profile. No credentials are committed. */
try { importScripts('auth.js'); } catch (_) { /* 本地安装时预置密钥 */ }
importScripts('config.js');

const SCRIPT_ID = 'audiobookshelf-book-matching';
const API = {
  matchBook: ['POST', '/api/books/match'],
  matchStatus: ['GET', '/api/books/match-status'],
  matchProviders: ['GET', '/api/books/match-providers'],
  matchCandidates: ['GET', '/api/books/match-candidates']
};

function originPattern(value) {
  const url = new URL(value);
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.pathname !== '/' || url.search || url.hash) {
    throw new Error('请输入仅包含主机和端口的 HTTP(S) 地址');
  }
  // Chrome matches a host and scheme; check the exact origin before handling requests.
  return `${url.protocol}//${url.hostname}/*`;
}

async function readConfig() {
  return chrome.storage.local.get(['absUrl', 'managerUrl', 'managerKey']);
}

async function registerPanel() {
  const config = await readConfig();
  await chrome.scripting.unregisterContentScripts({ids: [SCRIPT_ID]}).catch(() => {});
  if (!config.absUrl) return;
  const pattern = originPattern(config.absUrl);
  if (!await chrome.permissions.contains({origins: [pattern]})) return;
  await chrome.scripting.registerContentScripts([{id: SCRIPT_ID, matches: [pattern], js: ['content.js'], runAt: 'document_idle'}]);
}

let registration = Promise.resolve();
function queueRegistration() {
  registration = registration.catch(() => {}).then(registerPanel).catch(error => console.error('Panel registration failed:', error));
}

async function migrateConfig() {
  const existing = await readConfig();
  const changes = {};
  // Existing Chrome profiles may still contain local-IP or Tailscale addresses.
  // 仓库内置的默认地址优先；只有用户在选项里手动改过（customDomain=true）才保留
  // 老版本指向转写服务 /__ext/asr，统一迁移到元数据服务 /__ext/meta
  if (typeof existing.managerUrl === 'string' && /\/__ext\/asr\/?$/.test(existing.managerUrl)) {
    changes.managerUrl = MANAGER_URL;
  }
  if (!existing.customDomain) {
    if (existing.absUrl !== ABS_URL) changes.absUrl = ABS_URL;
    if (existing.managerUrl !== MANAGER_URL) changes.managerUrl = MANAGER_URL;
  }
  if (!existing.managerKey && self.ASR_TOKEN) changes.managerKey = self.ASR_TOKEN;
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
  if (!config.absUrl || !config.managerUrl || !config.managerKey || !hasAccess) {
    await chrome.runtime.openOptionsPage();
  }
}
chrome.runtime.onInstalled.addListener(() => { initialize().catch(console.error); });
chrome.runtime.onStartup.addListener(() => initialize(false).catch(console.error));
chrome.storage.onChanged.addListener((changes, area) => {
  if (area === 'local' && (changes.absUrl || changes.managerUrl || changes.managerKey)) queueRegistration();
});
chrome.permissions.onAdded.addListener(queueRegistration);

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  (async () => {
    const config = await readConfig();
    if (!config.absUrl || !config.managerUrl) throw new Error('请先打开扩展设置，填写服务器地址');
    const dashboard = sender.id === chrome.runtime.id && sender.url === chrome.runtime.getURL('dashboard.html');
    let source;
    try { source = new URL(sender.url); } catch { throw new Error('无效请求来源'); }
    if (!dashboard && (!sender.tab || source.origin !== new URL(config.absUrl).origin)) {
      throw new Error('仅允许配置的 Audiobookshelf 页面操作');
    }
    if (message?.action === 'openDashboard') {
      await chrome.tabs.create({url: chrome.runtime.getURL('dashboard.html')});
      return {ok: true};
    }
    const spec = API[message?.action];
    if (!spec) throw new Error('无效操作');
    const [method, endpoint] = spec;
    const requestURL = new URL(config.managerUrl.replace(/\/$/, '') + endpoint);
    if (method === 'GET' && message.action !== 'matchProviders') {
      const itemId = message.value?.item_id || message.value?.path;
      if (typeof itemId !== 'string' || !itemId) throw new Error('缺少当前书籍');
      requestURL.searchParams.set('path', itemId);
      if (message.action === 'matchCandidates') {
        for (const field of ['provider', 'title', 'author']) {
          if (typeof message.value?.[field] === 'string') requestURL.searchParams.set(field, message.value[field]);
        }
      }
    }
    if (!config.managerKey) throw new Error('请在扩展设置中填写转写服务密钥');
    const options = {method, cache: 'no-store', headers: {'X-Meta-Token': config.managerKey}};
    if (method === 'POST') {
      options.headers['Content-Type'] = 'application/json';
      options.body = JSON.stringify(message.value || {});
    }
    const response = await fetch(requestURL, options);
    const data = ['transcript','bookTranscript'].includes(message.action) && response.ok ? {text: await response.text()} : await response.json();
    if (!response.ok) throw new Error(data.error || `接口错误 ${response.status}`);
    return data;
  })().then(sendResponse).catch(error => sendResponse({error: error.message || '连接转写服务失败'}));
  return true;
});

chrome.action.onClicked.addListener(() => chrome.runtime.openOptionsPage());
