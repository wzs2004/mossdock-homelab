/* 转写管理扩展 —— 只做三件事：看队列、按书开始转写、跟随书库变化。
   密钥只存在浏览器里，不写进仓库。 */
importScripts('config.js');

const SCRIPT_ID = 'abs-transcription-panel';
const API = {
  books: ['GET', '/api/books/catalog?lite=1'],
  queueCurrentBook: ['POST', '/api/books/queue']
};

function originPattern(value) {
  const url = new URL(value);
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password ||
      url.pathname !== '/' || url.search || url.hash) {
    throw new Error('请填写仅含主机和端口的 HTTP(S) 地址');
  }
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
  await chrome.scripting.registerContentScripts([{
    id: SCRIPT_ID, matches: [pattern], js: ['content.js'], runAt: 'document_idle'
  }]);
}

let registration = Promise.resolve();
function queueRegistration() {
  registration = registration.catch(() => {}).then(registerPanel)
    .catch(error => console.error('面板注册失败:', error));
}

async function initialize(openSettings = true) {
  const existing = await readConfig();
  const changes = {};
  if (existing.absUrl !== ABS_URL) changes.absUrl = ABS_URL;
  if (existing.managerUrl !== MANAGER_URL) changes.managerUrl = MANAGER_URL;
  if (Object.keys(changes).length) await chrome.storage.local.set(changes);
  const config = {...existing, ...changes};
  queueRegistration();
  if (!openSettings) return;
  let hasAccess = false;
  try {
    if (config.absUrl) hasAccess = await chrome.permissions.contains({origins: [originPattern(config.absUrl)]});
  } catch (_) { /* 设置页会提示地址无效 */ }
  if (!config.absUrl || !config.managerKey || !hasAccess) await chrome.runtime.openOptionsPage();
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
    if (!config.absUrl || !config.managerUrl) throw new Error('请先在扩展设置里填写服务器地址');
    let source;
    try { source = new URL(sender.url); } catch { throw new Error('无效请求来源'); }
    if (!sender.tab || source.origin !== new URL(config.absUrl).origin) {
      throw new Error('只允许在 Audiobookshelf 页面上操作');
    }
    const spec = API[message?.action];
    if (!spec) throw new Error('无效操作');
    if (!config.managerKey) throw new Error('请先在扩展设置里填写转写服务密钥');
    const [method, endpoint] = spec;
    const options = {method, cache: 'no-store', headers: {'X-ASR-Extension-Token': config.managerKey}};
    if (method === 'POST') {
      options.headers['Content-Type'] = 'application/json';
      options.body = JSON.stringify(message.value || {});
    }
    const response = await fetch(config.managerUrl.replace(/\/$/, '') + endpoint, options);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || `接口错误 ${response.status}`);
    return data;
  })().then(sendResponse).catch(error => sendResponse({error: error.message || '连接转写服务失败'}));
  return true;
});

chrome.action.onClicked.addListener(() => chrome.runtime.openOptionsPage());
