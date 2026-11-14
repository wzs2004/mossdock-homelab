const fields = ['absUrl', 'managerUrl', 'managerKey'];
const status = document.querySelector('#status');
function normalizedURL(raw, allowPath = false) {
  const url = new URL(raw.trim());
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || (!allowPath && url.pathname !== '/') || url.search || url.hash) {
    throw new Error('只填写 HTTP(S) 主机和端口，不要包含路径或账号密码');
  }
  return allowPath ? url.origin + url.pathname.replace(/\/$/, '') : url.origin;
}
function permissionPattern(origin) {
  const url = new URL(origin);
  return `${url.protocol}//${url.hostname}/*`;
}
chrome.storage.local.get(fields).then(config => {
  for (const key of fields) {
    const input = document.getElementById(key);
    input.value = key === 'absUrl' ? ABS_URL : key === 'managerUrl' ? MANAGER_URL : (config[key] || '');
    if (key !== 'managerKey') input.readOnly = true;
  }
});
document.querySelector('#settings').addEventListener('submit', async event => {
  event.preventDefault();
  try {
    const absUrl = ABS_URL;
    const managerUrl = MANAGER_URL;
    const managerKey = document.getElementById('managerKey').value.trim();
    if (!managerKey) throw new Error('请输入密钥');
    // Request permissions directly from a user-initiated submit gesture.
    const origins = [...new Set([permissionPattern(absUrl), permissionPattern(managerUrl)])];
    const missing = [];
    for (const origin of origins) if (!await chrome.permissions.contains({origins: [origin]})) missing.push(origin);
    if (missing.length && !await chrome.permissions.request({origins: missing})) throw new Error('浏览器未授权访问这些地址');
    await chrome.storage.local.set({absUrl, managerUrl, managerKey});
    status.textContent = '配置已保存。请刷新 Audiobookshelf 页面。';
    try {
      const response = await fetch(`${managerUrl}/api/health`, {cache: 'no-store', headers: {'X-ASR-Extension-Token': managerKey}});
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      status.textContent += '\n✓ 转写服务连接成功。';
    } catch (error) {
      status.textContent += `\n⚠ 当前无法连接转写服务：${error.message}。检查域名与服务器。`;
    }
  } catch (error) { status.textContent = `保存失败：${error.message}`; }
});
document.getElementById('openAbs').addEventListener('click', () => {
  try { chrome.tabs.create({url: ABS_URL}); }
  catch (error) { status.textContent = error.message; }
});
