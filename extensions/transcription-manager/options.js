const status = document.querySelector('#status');
document.getElementById('absUrl').value = ABS_URL;

chrome.storage.local.get('managerKey').then(config => {
  document.getElementById('managerKey').value = config.managerKey || '';
});

function originPattern(origin) {
  const url = new URL(origin);
  return `${url.protocol}//${url.hostname}/*`;
}

document.querySelector('#settings').addEventListener('submit', async event => {
  event.preventDefault();
  try {
    const managerKey = document.getElementById('managerKey').value.trim();
    if (!managerKey) throw new Error('请输入密钥');
    const origins = [...new Set([originPattern(ABS_URL), originPattern(MANAGER_URL)])];
    const missing = [];
    for (const origin of origins) {
      if (!await chrome.permissions.contains({origins: [origin]})) missing.push(origin);
    }
    if (missing.length && !await chrome.permissions.request({origins: missing})) {
      throw new Error('浏览器未授权访问这些地址');
    }
    await chrome.storage.local.set({absUrl: ABS_URL, managerUrl: MANAGER_URL, managerKey});
    status.textContent = '已保存。请刷新 Audiobookshelf 页面。';
    try {
      const response = await fetch(`${MANAGER_URL}/api/health`, {
        cache: 'no-store', headers: {'X-ASR-Extension-Token': managerKey}
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      status.textContent += '\n✓ 转写服务连接成功。';
    } catch (error) {
      status.textContent += `\n⚠ 暂时连不上转写服务：${error.message}`;
    }
  } catch (error) {
    status.textContent = `保存失败：${error.message}`;
  }
});
