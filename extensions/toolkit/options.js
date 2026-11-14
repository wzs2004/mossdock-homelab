/* Audiobookshelf 工具箱设置页。
   absUrl 是唯一需要填写的地址，三个服务地址由它推导；三段密钥分别写入独立存储键。
   未填写的密钥不会写入，保持浏览器里的原值。 */
const status = document.querySelector('#status');
const absUrlInput = document.querySelector('#absUrl');
const KEY_INPUTS = ['asrKey', 'metaKey', 'bridgeKey'];
const SERVICE_SUFFIXES = {asrUrl: '/__ext/asr', metaUrl: '/__ext/meta', bridgeUrl: '/__ext/lr'};

function setStatus(text, isError = false) {
  status.textContent = text;
  status.classList.toggle('error', isError);
}

function normalizedOrigin(raw) {
  const url = new URL(String(raw || '').trim());
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password ||
      url.pathname !== '/' || url.search || url.hash) {
    throw new Error('只填写 HTTP(S) 主机和端口，不要包含路径或账号密码');
  }
  return url.origin;
}

function permissionPattern(origin) {
  const url = new URL(origin);
  return `${url.protocol}//${url.hostname}/*`;
}

function serviceUrls(origin) {
  const urls = {};
  for (const [key, suffix] of Object.entries(SERVICE_SUFFIXES)) urls[key] = origin + suffix;
  return urls;
}

function showPreview(origin) {
  const urls = serviceUrls(origin);
  document.querySelector('#asrUrlPreview').textContent = urls.asrUrl;
  document.querySelector('#metaUrlPreview').textContent = urls.metaUrl;
  document.querySelector('#bridgeUrlPreview').textContent = urls.bridgeUrl;
}

chrome.storage.local.get([
  'absUrl', ...KEY_INPUTS, 'customDomain'
]).then(config => {
  let origin = ABS_URL;
  try {
    origin = normalizedOrigin(config.absUrl || ABS_URL);
  } catch (_) {
    origin = ABS_URL;
  }
  absUrlInput.value = origin;
  showPreview(origin);
  for (const key of KEY_INPUTS) document.getElementById(key).value = config[key] || '';
  if (!config.customDomain) {
    setStatus('当前使用仓库内置地址（由 site.json 生成）。');
  }
});

absUrlInput.addEventListener('input', () => {
  try {
    showPreview(normalizedOrigin(absUrlInput.value));
  } catch (_) {
    /* 正在输入，地址还不完整 */
  }
});

let saving = false;
document.querySelector('#settings').addEventListener('submit', async event => {
  event.preventDefault();
  if (saving) return;
  saving = true;
  const submit = event.submitter;
  if (submit) submit.disabled = true;
  try {
    const absUrl = normalizedOrigin(absUrlInput.value);
    const urls = serviceUrls(absUrl);
    // 权限请求必须在用户点击（submit）手势里发起。
    const pattern = permissionPattern(absUrl);
    if (!await chrome.permissions.contains({origins: [pattern]})) {
      if (!await chrome.permissions.request({origins: [pattern]})) {
        throw new Error('浏览器未授权访问这个地址');
      }
    }
    const update = {absUrl, ...urls, customDomain: true};
    const written = [];
    for (const key of KEY_INPUTS) {
      const value = document.getElementById(key).value.trim();
      if (!value) continue; // 留空 = 保持原密钥，不清空
      update[key] = value;
      written.push(key);
    }
    await chrome.storage.local.set(update);
    absUrlInput.value = absUrl;
    showPreview(absUrl);
    const keys = written.length ? `已更新 ${written.length} 个密钥` : '密钥未变（留空保持原值）';
    setStatus(`✓ 配置已保存：${absUrl}；${keys}。请刷新 Audiobookshelf 页面，Language Reactor 标签页重新打开后生效。`);
  } catch (error) {
    setStatus(`保存失败：${error.message}`, true);
  } finally {
    saving = false;
    if (submit) submit.disabled = false;
  }
});

document.querySelector('#resetDefaults').addEventListener('click', async () => {
  await chrome.storage.local.set({
    absUrl: ABS_URL,
    asrUrl: ASR_URL,
    metaUrl: META_URL,
    bridgeUrl: BRIDGE_URL,
    customDomain: false
  });
  setStatus('已恢复仓库内置地址，正在重新加载…');
  location.reload();
});

document.querySelector('#openAbs').addEventListener('click', () => {
  let url = ABS_URL;
  try {
    url = normalizedOrigin(absUrlInput.value);
  } catch (_) { /* 输入不合法时退回内置地址 */ }
  try {
    chrome.tabs.create({url});
  } catch (error) {
    setStatus(error.message, true);
  }
});
