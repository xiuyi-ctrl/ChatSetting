const button = document.getElementById('import');
const status = document.getElementById('status');
const open = document.getElementById('open');
async function cropScreenshot(dataUrl, rect, viewport) {
  const image = new Image();
  image.src = dataUrl;
  await image.decode();
  const scaleX = image.naturalWidth / viewport.width;
  const scaleY = image.naturalHeight / viewport.height;
  const canvas = document.createElement('canvas');
  canvas.width = Math.round(rect.width * scaleX);
  canvas.height = Math.round(rect.height * scaleY);
  canvas.getContext('2d').drawImage(image, rect.x * scaleX, rect.y * scaleY,
    rect.width * scaleX, rect.height * scaleY, 0, 0, canvas.width, canvas.height);
  return canvas.toDataURL('image/png');
}
open.addEventListener('click', async () => {
  const tabs = await chrome.tabs.query({});
  const existing = tabs.find(tab => tab.url?.startsWith('http://127.0.0.1:8765/interface-sample.html'));
  if (existing) await chrome.tabs.update(existing.id, {active: true});
  else await chrome.tabs.create({url: 'http://127.0.0.1:8765/interface-sample.html'});
});
button.addEventListener('click', async () => {
  button.disabled = true;
  button.textContent = '正在导入…';
  open.hidden = true;
  status.textContent = '正在读取对话结构…';
  try {
    const [tab] = await chrome.tabs.query({active: true, currentWindow: true});
    if (!/^https:\/\/chatgpt\.com\/share\/[0-9a-f-]+\/?$/i.test(tab?.url || '')) {
      throw new Error('请先打开 ChatGPT 分享链接。');
    }
    const shareResponse = await fetch('http://127.0.0.1:8765/api/extension-share', {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({url: tab.url})
    }).catch(() => { throw new Error('无法连接本地服务，请先运行 python server.py。'); });
    const shared = await shareResponse.json();
    if (!shareResponse.ok) throw new Error(shared.error || '分享内容读取失败');
    const imageParts = (shared.linear_conversation || []).flatMap(node =>
      ((node.message || {}).content || {}).parts || []).filter(part =>
      part && typeof part === 'object' && part.content_type === 'image_asset_pointer');
    const pointers = [...new Set(imageParts.map(part => part.asset_pointer?.split('://')[1]?.split('?')[0]).filter(Boolean))];
    const sizes = Object.fromEntries(imageParts.map(part => [part.asset_pointer?.split('://')[1]?.split('?')[0],
      {width: part.width, height: part.height}]));
    const names = {};
    for (const node of shared.linear_conversation || []) {
      for (const attachment of node.message?.metadata?.attachments || []) {
        if (attachment.id && attachment.name) names[attachment.id] = attachment.name;
      }
    }
    status.textContent = `已读取对话，正在获取 ${pointers.length} 张图片…`;
    const data = await Promise.race([
      chrome.tabs.sendMessage(tab.id, {action: 'collect', pointers, sizes, conversationId: shared.backing_conversation_id}),
      new Promise((_, reject) => setTimeout(() => reject(new Error('读取超时，请刷新分享页后重试。')), 30000))
    ]).catch(error => {
      if (error.message?.includes('超时')) throw error;
      throw new Error('扩展尚未进入此页面，请刷新分享页后重试。');
    });
    if (data?.error) throw new Error(data.error);
    if (data.unresolved) {
      for (const [index, id] of pointers.entries()) {
        if (data.images[id]) continue;
        const name = names[id];
        if (!name) { data.diagnostics.failures.push(`第 ${index + 1} 张没有文件名，无法自动定位`); continue; }
        status.textContent = `正在抓取第 ${index + 1}/${pointers.length} 张：${name}`;
        try {
          const preview = await chrome.tabs.sendMessage(tab.id, {action: 'open-preview', name, size: sizes[id]});
          if (preview.error || (!preview.image && !preview.rect)) throw new Error(preview.error || '未找到大图预览');
          if (preview.image) data.images[id] = preview.image;
          else {
            const screenshot = await chrome.tabs.captureVisibleTab(tab.windowId, {format: 'png'});
            data.images[id] = await cropScreenshot(screenshot, preview.rect, preview.viewport);
          }
          await chrome.tabs.sendMessage(tab.id, {action: 'close-preview', size: sizes[id]});
          await new Promise(resolve => setTimeout(resolve, 550));
        } catch (error) {
          data.diagnostics.failures.push(`${name}：${error.message}`);
        }
      }
      data.unresolved = pointers.length - Object.keys(data.images).length;
    }
    data.shared = shared;
    const response = await fetch('http://127.0.0.1:8765/api/extension-import', {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)
    }).catch(() => { throw new Error('无法连接本地服务，请先运行 python server.py。'); });
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new Error(error.error || '本地服务未接收导入内容，请检查服务状态。');
    }
    const result = await response.json();
    status.textContent = `已导入 ${result.turns} 轮、${result.images} 张图片。` +
      (data.unresolved ? ` 另有 ${data.unresolved} 张图片未读到（页面候选 ${data.diagnostics?.candidates || 0} 张；预览定位 ${data.diagnostics?.previewFound ? '成功' : '失败'}；${data.diagnostics?.failures?.join('；') || '原因未知'}）。` : '');
    open.hidden = false;
  } catch (error) {
    status.textContent = error.message || '导入失败';
  } finally {
    button.disabled = false;
    button.textContent = '导入当前分享页';
  }
});
