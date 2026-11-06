async function toDataUrl(blob) {
  if (blob.size > 8_000_000) throw new Error('图片过大');
  const bytes = new Uint8Array(await blob.slice(0, 12).arrayBuffer());
  const mime = bytes[0] === 0xff && bytes[1] === 0xd8 ? 'image/jpeg'
    : bytes[0] === 0x89 && bytes[1] === 0x50 && bytes[2] === 0x4e && bytes[3] === 0x47 ? 'image/png'
    : String.fromCharCode(...bytes.slice(0, 4)) === 'RIFF' && String.fromCharCode(...bytes.slice(8, 12)) === 'WEBP' ? 'image/webp'
    : String.fromCharCode(...bytes.slice(0, 3)) === 'GIF' ? 'image/gif' : null;
  if (!mime) throw new Error('图片格式不受支持');
  return await new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = reject;
    reader.readAsDataURL(new Blob([blob], {type: mime}));
  });
}

async function fetchImage(url) {
  const response = await fetch(url, {credentials: 'include', signal: AbortSignal.timeout(10000)});
  if (!response.ok) throw new Error(`图片 HTTP ${response.status}`);
  return toDataUrl(await response.blob());
}

function captureImage(img) {
  if (!img.complete || !img.naturalWidth) throw new Error('缩略图尚未加载');
  const canvas = document.createElement('canvas');
  canvas.width = img.naturalWidth;
  canvas.height = img.naturalHeight;
  canvas.getContext('2d').drawImage(img, 0, 0);
  const data = canvas.toDataURL('image/png');
  if (data.length > 12_000_000) throw new Error('缩略图过大');
  return data;
}

function previewRect(size) {
  const darkCorner = (x, y) => {
    for (let element = document.elementFromPoint(x, y); element; element = element.parentElement) {
      const color = getComputedStyle(element).backgroundColor.match(/^rgba?\((\d+),\s*(\d+),\s*(\d+)(?:,\s*([\d.]+))?/);
      if (color && Number(color[4] || 1) > 0.5) return (+color[1] + +color[2] + +color[3]) / 3 < 90;
    }
    return false;
  };
  const margin = 5;
  const dark = [[margin, margin], [innerWidth - margin, margin],
    [margin, innerHeight - margin], [innerWidth - margin, innerHeight - margin]]
    .filter(([x, y]) => darkCorner(x, y)).length;
  if (!document.querySelector('[aria-modal="true"]') && dark < 3) return null;
  const ratio = size?.width && size?.height ? size.width / size.height : null;
  let best = null;
  for (const element of document.querySelectorAll('img')) {
    const rect = element.getBoundingClientRect();
    if (rect.width < innerWidth * 0.35 || rect.height < innerHeight * 0.3 ||
        rect.left < 0 || rect.top < 0 || rect.right > innerWidth || rect.bottom > innerHeight ||
        (ratio && Math.abs(rect.width / rect.height - ratio) > 0.2)) continue;
    if (!best || rect.width * rect.height > best.width * best.height) {
      best = {x: rect.x, y: rect.y, width: rect.width, height: rect.height};
    }
  }
  return best;
}

const pause = ms => new Promise(resolve => setTimeout(resolve, ms));

function filenameElement(name) {
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    if (node.textContent.trim() === name && node.parentElement.getBoundingClientRect().width) return node.parentElement;
  }
  return null;
}

async function closePreview(size) {
  if (!previewRect(size)) return;
  document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', code: 'Escape', bubbles: true}));
  await pause(250);
  if (previewRect(size)) {
    document.elementFromPoint(5, 5)?.click();
    await pause(250);
  }
  if (previewRect(size)) throw new Error('无法关闭上一张大图预览');
}

async function openPreview(name, size) {
  await closePreview(size);
  let label = filenameElement(name);
  if (!label) {
    for (let y = 0; y < document.documentElement.scrollHeight && !label; y += innerHeight * 0.8) {
      window.scrollTo(0, y);
      await pause(100);
      label = filenameElement(name);
    }
  }
  if (!label) throw new Error(`页面中找不到 ${name}`);
  label.scrollIntoView({block: 'center'});
  await pause(150);
  const labelRect = label.getBoundingClientRect();
  const thumbnails = [...document.querySelectorAll('img')].filter(img => {
    const rect = img.getBoundingClientRect();
    return rect.width >= 70 && rect.width <= 300 && rect.height >= 70 && rect.height <= 300 &&
      rect.bottom <= labelRect.top + 40 && rect.bottom >= labelRect.top - 350 &&
      Math.abs(rect.x + rect.width / 2 - (labelRect.x + labelRect.width / 2)) < 350;
  }).sort((a, b) => Math.abs(a.getBoundingClientRect().bottom - labelRect.top) -
    Math.abs(b.getBoundingClientRect().bottom - labelRect.top)).slice(0, 8);
  const targets = [label.closest('button,[role="button"]'), ...thumbnails.flatMap(img => [img.closest('button,[role="button"]'), img]),
    label, label.parentElement, label.parentElement?.parentElement]
    .filter((item, index, all) => item && all.indexOf(item) === index);
  for (const target of targets) {
    target.click();
    for (let attempt = 0; attempt < 5; attempt++) {
      await pause(200);
      const rect = previewRect(size);
      if (!rect) continue;
      await pause(500);
      const settled = previewRect(size);
      if (!settled || Math.abs(settled.width - rect.width) > 2 || Math.abs(settled.height - rect.height) > 2) continue;
      const image = [...document.images].find(img => {
        const bounds = img.getBoundingClientRect();
        return Math.abs(bounds.x - settled.x) < 2 && Math.abs(bounds.y - settled.y) < 2 &&
          Math.abs(bounds.width - settled.width) < 2 && Math.abs(bounds.height - settled.height) < 2;
      });
      if (image) {
        try { return {image: await fetchImage(image.currentSrc || image.src)}; }
        catch (_) {
          try { return {image: captureImage(image)}; } catch (_) { /* Use a screenshot below. */ }
        }
      }
      return {rect: settled, viewport: {width: innerWidth, height: innerHeight}};
    }
  }
  throw new Error(`无法打开 ${name} 的大图预览`);
}

async function collect(pointers, sizes = {}, conversationId = '') {
  const match = location.pathname.match(/^\/share\/([0-9a-f-]{36})\/?$/i);
  if (!match) throw new Error('当前页面不是 ChatGPT 分享页。');
  const images = {};
  const candidates = [...document.images]
    .filter(img => img.getBoundingClientRect().width >= 80 && img.getBoundingClientRect().height >= 60);
  if (pointers.length === 1) candidates.sort((a, b) => b.naturalWidth * b.naturalHeight - a.naturalWidth * a.naturalHeight);
  let candidateIndex = 0;
  const failures = [];
  const screenshots = {};
  const direct = await Promise.allSettled(pointers.map(async id => {
    const base = `/backend-api/files/download/${encodeURIComponent(id)}`;
    const urls = conversationId ? [`${base}?conversation_id=${encodeURIComponent(conversationId)}&inline=false`] : [];
    urls.push(`${base}?shared_conversation_id=${match[1]}&inline=false`);
    let failure;
    for (const url of urls) {
      try { return await fetchImage(url); } catch (error) { failure = error; }
    }
    throw failure;
  }));
  pointers.forEach((id, index) => {
    if (direct[index].status === 'fulfilled') images[id] = direct[index].value;
    else failures.push(direct[index].reason.message);
  });
  for (const id of pointers) {
    if (!images[id] && pointers.length === 1) {
      while (candidateIndex < candidates.length && !images[id]) {
        const img = candidates[candidateIndex];
        try { images[id] = await fetchImage(img.currentSrc || img.src); }
        catch (fetchError) {
          try { images[id] = captureImage(img); }
          catch (captureError) { failures.push(`${fetchError.message} / ${captureError.message}`); }
        }
        candidateIndex++;
      }
    }
    if (!images[id] && pointers.length === 1) screenshots[id] = previewRect(sizes[id]);
  }
  return {url: location.origin + location.pathname, images,
    unresolved: pointers.length - Object.keys(images).length,
    screenshots, viewport: {width: innerWidth, height: innerHeight},
    diagnostics: {candidates: candidates.length, previewFound: Object.values(screenshots).some(Boolean), failures: failures.slice(0, 3)}};
}

chrome.runtime.onMessage.addListener((message, sender, respond) => {
  if (message?.action === 'collect') {
    collect(message.pointers || [], message.sizes || {}, message.conversationId || '')
      .then(respond, error => respond({error: error.message || '读取失败'}));
    return true;
  }
  if (message?.action === 'open-preview') {
    openPreview(message.name, message.size).then(respond, error => respond({error: error.message || '打开预览失败'}));
    return true;
  }
  if (message?.action === 'close-preview') {
    closePreview(message.size).then(() => respond({ok: true}), error => respond({error: error.message || '关闭预览失败'}));
    return true;
  }
});
