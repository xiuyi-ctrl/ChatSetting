const $ = id => document.getElementById(id);
const docs = [];
let activeDoc = 0;
let selected = 0;
let saveQueue = Promise.resolve();

function openStore() {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open('dialogue-atlas', 1);
    request.onupgradeneeded = () => request.result.createObjectStore('state');
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

async function writeState() {
  const db = await openStore();
  try {
    await new Promise((resolve, reject) => {
      const transaction = db.transaction('state', 'readwrite');
      transaction.objectStore('state').put({docs, activeDoc}, 'current');
      transaction.oncomplete = resolve;
      transaction.onerror = () => reject(transaction.error);
    });
  } finally {
    db.close();
  }
}

function persist() {
  saveQueue = saveQueue.catch(() => {}).then(writeState);
  return saveQueue;
}

async function restore() {
  const db = await openStore();
  try {
    return await new Promise((resolve, reject) => {
      const request = db.transaction('state').objectStore('state').get('current');
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
  } finally {
    db.close();
  }
}

function savePosition() {
  persist().catch(() => { $('parseStatus').textContent = '浏览器未能保存阅读位置'; });
}

function textOf(blocks) {
  return blocks.filter(b => b.type === 'text').map(b => b.value).join(' ');
}

function escapeHTML(value) {
  return value.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;');
}

function renderMarkdown(source) {
  const math = [];
  const protectedSource = source.split(/(```[\s\S]*?```)/g).map((part, index) => {
    if (index % 2) return part;
    return part.replace(/\\\[([\s\S]*?)\\\]|\$\$([\s\S]*?)\$\$|\\\(([\s\S]*?)\\\)|(?<!\\)\$([^\s$](?:[^$\n]*?[^\s$])?)\$/g, (_, bracket, dollars, inline, dollarInline) => {
      const token = `MATHTOKEN${math.length}END`;
      math.push({token, tex: bracket ?? dollars ?? inline ?? dollarInline, display: bracket !== undefined || dollars !== undefined});
      return token;
    });
  }).join('');
  const parsed = new DOMParser().parseFromString(`<div>${marked.parse(protectedSource, {gfm: true, breaks: true})}</div>`, 'text/html');
  const root = parsed.body.firstElementChild;
  const allowed = new Set(['P', 'BR', 'H1', 'H2', 'H3', 'H4', 'H5', 'H6', 'UL', 'OL', 'LI', 'BLOCKQUOTE', 'PRE', 'CODE', 'STRONG', 'EM', 'DEL', 'HR', 'TABLE', 'THEAD', 'TBODY', 'TR', 'TH', 'TD', 'A', 'IMG']);
  for (const element of [...root.querySelectorAll('*')]) {
    if (!allowed.has(element.tagName)) {
      element.replaceWith(parsed.createTextNode(element.textContent || ''));
      continue;
    }
    const href = element.getAttribute('href');
    const src = element.getAttribute('src');
    const alt = element.getAttribute('alt');
    for (const attribute of [...element.attributes]) element.removeAttribute(attribute.name);
    if (element.tagName === 'A' && /^https?:\/\//i.test(href || '')) {
      element.setAttribute('href', href);
      element.setAttribute('target', '_blank');
      element.setAttribute('rel', 'noopener noreferrer');
    }
    if (element.tagName === 'IMG' && /^(https?:\/\/|data:image\/(png|jpeg|webp|gif);base64,)/i.test(src || '')) {
      element.setAttribute('src', src);
      element.setAttribute('alt', alt || '图片');
    }
  }
  let html = root.innerHTML;
  for (const item of math) {
    html = html.replaceAll(item.token, katex.renderToString(item.tex.trim(), {
      displayMode: item.display, throwOnError: false, trust: false
    }));
  }
  return html;
}

function renderBlocks(blocks, label, markdown = false) {
  return blocks.map(block => block.type === 'image'
    ? `<div class="image-wrap"><img src="${block.value}" alt="${label}中的图片" tabindex="0"></div><div class="image-caption">${markdown ? '分享链接图片' : 'Word 文档内嵌图片'} · 点击查看大图</div>`
    : block.type === 'missing-image'
      ? `<div class="missing-image">这张图片暂时无法从 ChatGPT 分享链接读取。<a href="${escapeHTML(block.value)}" target="_blank" rel="noopener noreferrer">在原分享页查看图片 ↗</a></div>`
    : markdown ? `<div class="markdown">${renderMarkdown(block.value)}</div>`
      : `<p>${escapeHTML(block.value).replaceAll('\n', '<br>')}</p>`).join('') || '<p class="empty">没有文字或图片</p>';
}

function renderList() {
  const doc = docs[activeDoc];
  if (!doc) return;
  const query = $('search').value.trim().toLowerCase();
  const matches = doc.turns.map((turn, index) => ({turn, index}))
    .filter(({turn}) => (textOf(turn.question) + ' ' + textOf(turn.answer)).toLowerCase().includes(query));
  $('count').textContent = query ? `找到 ${matches.length} 轮` : `共 ${doc.turns.length} 轮`;
  $('turns').innerHTML = matches.length ? matches.map(({turn, index}) => {
    const summary = textOf(turn.question).slice(0, 27) || '图片提问';
    const hasImage = turn.question.some(b => b.type === 'image' || b.type === 'missing-image') || turn.answer.some(b => b.type === 'image' || b.type === 'missing-image');
    return `<button class="turn ${selected === index ? 'active' : ''}" data-index="${index}"><span class="num">${String(index + 1).padStart(2, '0')}</span><b>${escapeHTML(summary)}${hasImage ? '<span class="pill">图片</span>' : ''}</b><small>${turn.warnings.length ? escapeHTML(turn.warnings.join('、')) : '完整问答'}</small></button>`;
  }).join('') : '<div class="empty">没有找到匹配的轮次</div>';
  document.querySelectorAll('.turn').forEach(button => button.addEventListener('click', () => {
    selected = Number(button.dataset.index);
    doc.selected = selected;
    render();
    setMobileView('read');
    savePosition();
  }));
}

function setMobileView(view) {
  document.querySelector('.app').dataset.mobileView = view;
  document.querySelectorAll('.mobile-tabs button').forEach(button => {
    button.setAttribute('aria-selected', String(button.dataset.view === view));
  });
}

document.querySelectorAll('.mobile-tabs button').forEach(button => button.addEventListener('click', () => setMobileView(button.dataset.view)));

function render() {
  const doc = docs[activeDoc];
  if (!doc) { renderEmpty(); return; }
  document.querySelector('.thread').hidden = false;
  document.querySelector('.footer-nav').hidden = false;
  const turn = doc.turns[selected];
  $('heading').textContent = '问答详情';
  $('counter').textContent = `${String(selected + 1).padStart(2, '0')} / ${String(doc.turns.length).padStart(2, '0')}`;
  $('crumb').textContent = `第 ${selected + 1} 轮`;
  $('docName').textContent = doc.title;
  $('docMeta').textContent = `${doc.type === 'link' ? (doc.platform || '分享') + '链接' : 'Word 文档'} · ${doc.turns.length} 轮问答`;
  $('docCrumb').textContent = doc.title;
  $('description').textContent = turn.warnings.length ? turn.warnings.join('；') : '按原始文档顺序展示，图片保留在所属消息中。';
  $('question').innerHTML = renderBlocks(turn.question, '提问', doc.type === 'link');
  $('answer').innerHTML = renderBlocks(turn.answer, '回答', doc.type === 'link');
  $('qmeta').textContent = turn.question.some(b => b.type === 'image' || b.type === 'missing-image') ? '图文内容' : '文字内容';
  $('ameta').textContent = turn.answer.some(b => b.type === 'image' || b.type === 'missing-image') ? '图文内容' : '文字内容';
  $('prev').disabled = selected === 0;
  $('next').disabled = selected === doc.turns.length - 1;
  $('parseStatus').textContent = '已完成解析';
  document.querySelectorAll('#question img, #answer img').forEach(img => {
    img.tabIndex = 0;
    img.addEventListener('click', () => openImage(img));
    img.addEventListener('keydown', e => { if (e.key === 'Enter') openImage(img); });
  });
  renderList();
}

function openImage(img) {
  $('previewImage').src = img.currentSrc || img.src;
  $('previewImage').alt = img.alt || '放大的对话图片';
  $('imageDialog').showModal();
  $('closeImage').focus();
}

function renderEmpty() {
  $('docName').textContent = '暂无文档';
  $('docMeta').textContent = '导入 Word 文档开始';
  $('docCrumb').textContent = '暂无文档';
  $('crumb').textContent = '';
  $('heading').textContent = '从导入文档开始';
  $('counter').textContent = '0 / 0';
  $('description').textContent = '点击左侧“导入新文档”，添加对话记录。';
  $('count').textContent = '共 0 轮';
  $('turns').innerHTML = '';
  $('parseStatus').textContent = '暂无对话记录';
  document.querySelector('.thread').hidden = true;
  document.querySelector('.footer-nav').hidden = true;
}

function confirmDocumentDelete(doc) {
  const dialog = $('deleteDialog');
  $('deleteFilename').textContent = doc.filename || doc.title;
  dialog.returnValue = '';
  dialog.showModal();
  $('cancelDelete').focus();
  return new Promise(resolve => dialog.addEventListener('close', () => resolve(dialog.returnValue === 'delete'), {once: true}));
}

async function importFiles(files) {
  for (const file of files) {
    if (!file.name.toLowerCase().endsWith('.docx')) {
      alert(`暂只支持 .docx 文件：${file.name}`);
      continue;
    }
    $('parseStatus').textContent = `正在解析 ${file.name}…`;
    try {
      if (window.AndroidBridge) {
        const encoded = await new Promise((resolve, reject) => {
          const reader = new FileReader();
          reader.onload = () => resolve(reader.result.split(',')[1]);
          reader.onerror = () => reject(new Error('无法读取文档'));
          reader.readAsDataURL(file);
        });
        await addDocument(await nativeCall('parseDocx', encoded, file.name));
        continue;
      }
      const response = await fetch('/api/parse', {
        method: 'POST',
        headers: {'Content-Type': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', 'X-Filename': encodeURIComponent(file.name)},
        body: file
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || '解析失败');
      await addDocument(result);
    } catch (error) {
      $('parseStatus').textContent = `导入失败：${error.message}`;
      alert(`${file.name}：${error.message}`);
    }
  }
  $('fileInput').value = '';
}

async function addDocument(result, replaceSource = false) {
  result.selected = 0;
  const existing = replaceSource && result.sourceUrl
    ? docs.findIndex(doc => doc.sourceUrl?.replace(/\/$/, '') === result.sourceUrl.replace(/\/$/, '')) : -1;
  if (existing >= 0) {
    if (docs[existing].renamed) {
      result.title = docs[existing].title;
      result.renamed = true;
    }
    docs[existing] = result;
  }
  else docs.push(result);
  activeDoc = existing >= 0 ? existing : docs.length - 1;
  selected = 0;
  $('search').value = '';
  updateWorkspace();
  render();
  setMobileView('list');
  closeMobileImport();
  try { await persist(); }
  catch (_) { $('parseStatus').textContent = '导入成功，但浏览器未能保存；刷新后可能丢失'; }
}

function closeMobileImport() {
  document.querySelector('.app').classList.remove('import-open');
  $('mobileImportTrigger').setAttribute('aria-expanded', 'false');
}
$('mobileImportTrigger').addEventListener('click', () => {
  closeMobileDocMenu();
  const open = document.querySelector('.app').classList.toggle('import-open');
  $('mobileImportTrigger').setAttribute('aria-expanded', String(open));
});
$('mobileImportBackdrop').addEventListener('click', closeMobileImport);
$('closeImport').addEventListener('click', closeMobileImport);
document.addEventListener('keydown', e => { if (e.key === 'Escape') { closeMobileImport(); closeMobileDocMenu(); } });

async function importLink(url) {
  $('parseStatus').textContent = '正在读取分享链接…';
  try {
    if (window.AndroidBridge) {
      await addDocument(await nativeCall('parseLink', url));
      $('linkInput').value = '';
      return;
    }
    const response = await fetch('/api/parse-link', {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({url})
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || '链接导入失败');
    await addDocument(result);
    $('linkInput').value = '';
  } catch (error) {
    $('parseStatus').textContent = `链接导入失败：${error.message}`;
    alert(error.message);
  }
}

let nativeRequestId = 0;
const nativeRequests = new Map();
function nativeCall(method, ...args) {
  return new Promise((resolve, reject) => {
    const id = ++nativeRequestId;
    nativeRequests.set(id, {resolve, reject});
    window.AndroidBridge[method](...args, id);
  });
}
window.nativeResolve = (id, value, error) => {
  const request = nativeRequests.get(id);
  if (!request) return;
  nativeRequests.delete(id);
  if (error) request.reject(new Error(error));
  else request.resolve(JSON.parse(value));
};

function updateWorkspace() {
  let container = $('workspaceDocs');
  if (!container) {
    container = document.createElement('div');
    container.id = 'workspaceDocs';
    container.className = 'workspace-docs';
    const rail = document.querySelector('.rail');
    const workspace = rail.querySelector('.workspace') || rail;
    workspace.append(container);
  }
  container.innerHTML = docs.map((doc, index) =>
    `<div class="workspace-entry"><button class="workspace-doc ${index === activeDoc ? 'active' : ''}" type="button" data-index="${index}" ${index === activeDoc ? 'aria-current="true"' : ''} title="${escapeHTML(doc.title || doc.filename)}"><span class="workspace-doc-icon">${String(index + 1).padStart(2, '0')}</span><span class="workspace-doc-text"><b>${escapeHTML(doc.title || doc.filename)}</b><small>${doc.turns.length} 轮问答 · ${doc.imageCount} 张可查看图片${doc.turns.some(turn => [...turn.question, ...turn.answer].some(block => block.type === 'missing-image')) ? ' · 部分图片需在原页查看' : ''}</small></span></button><button class="workspace-rename" type="button" data-index="${index}" aria-label="重命名 ${escapeHTML(doc.title || doc.filename)}" title="重命名标题">✎</button><button class="workspace-delete" type="button" data-index="${index}" aria-label="删除 ${escapeHTML(doc.title || doc.filename)}" title="删除这份文档">×</button></div>`
  ).join('');
  $('mobileDocLabel').textContent = docs.length ? `${String(activeDoc + 1).padStart(2, '0')} · ${docs[activeDoc].title || docs[activeDoc].filename}` : '暂无对话记录';
  $('mobileDocTrigger').disabled = !docs.length;
  $('mobileDocRename').disabled = !docs.length;
  $('mobileDocMenu').innerHTML = `<div class="mobile-doc-menu-title">选择对话记录 · ${docs.length} 份</div>` + docs.map((doc, index) =>
    `<button class="mobile-doc-option ${index === activeDoc ? 'active' : ''}" type="button" role="option" aria-selected="${index === activeDoc}" data-index="${index}"><span class="mobile-doc-number">${String(index + 1).padStart(2, '0')}</span><span><b>${escapeHTML(doc.title || doc.filename)}</b><small>${doc.turns.length} 轮问答</small></span>${index === activeDoc ? '<span class="check" aria-hidden="true">✓</span>' : ''}</button>`
  ).join('');
  $('mobileDocDelete').disabled = !docs.length;
  document.querySelectorAll('.workspace-doc').forEach(button => button.addEventListener('click', () => {
    activeDoc = Number(button.dataset.index);
    selected = docs[activeDoc].selected || 0;
    $('search').value = '';
    updateWorkspace();
    render();
    setMobileView('list');
    savePosition();
  }));
  document.querySelectorAll('.workspace-delete').forEach(button => button.addEventListener('click', async () => {
    const index = Number(button.dataset.index);
    if (!await confirmDocumentDelete(docs[index])) return;
    docs.splice(index, 1);
    if (index < activeDoc) activeDoc--;
    else if (index === activeDoc) activeDoc = Math.min(index, docs.length - 1);
    selected = docs[activeDoc]?.selected || 0;
    $('search').value = '';
    updateWorkspace();
    render();
    try { await persist(); }
    catch (_) { $('parseStatus').textContent = '删除未能保存，刷新后记录可能重新出现'; }
  }));
  document.querySelectorAll('.workspace-rename').forEach(button => button.addEventListener('click', () => openRename(Number(button.dataset.index))));
}

function openRename(index) {
  $('renameDialog').dataset.index = index;
  $('renameInput').value = docs[index].title || docs[index].filename;
  $('renameDialog').showModal();
  $('renameInput').focus();
  $('renameInput').select();
}

$('mobileDocTrigger').addEventListener('click', () => {
  closeMobileImport();
  const open = document.querySelector('.app').classList.toggle('doc-menu-open');
  $('mobileDocTrigger').setAttribute('aria-expanded', String(open));
});
function closeMobileDocMenu() {
  document.querySelector('.app').classList.remove('doc-menu-open');
  $('mobileDocTrigger').setAttribute('aria-expanded', 'false');
}
$('mobileDocBackdrop').addEventListener('click', closeMobileDocMenu);
$('mobileDocMenu').addEventListener('click', e => {
  const option = e.target.closest('.mobile-doc-option');
  if (!option) return;
  closeMobileDocMenu();
  document.querySelector(`.workspace-doc[data-index="${option.dataset.index}"]`)?.click();
});
$('mobileDocDelete').addEventListener('click', () => {
  closeMobileDocMenu();
  document.querySelector(`.workspace-delete[data-index="${activeDoc}"]`)?.click();
});
$('mobileDocRename').addEventListener('click', () => {
  closeMobileDocMenu();
  openRename(activeDoc);
});
$('renameForm').addEventListener('submit', async e => {
  e.preventDefault();
  const title = $('renameInput').value.trim();
  if (!title) { $('renameInput').value = ''; $('renameInput').focus(); return; }
  const doc = docs[Number($('renameDialog').dataset.index)];
  doc.title = title;
  doc.renamed = true;
  $('renameDialog').close();
  updateWorkspace();
  render();
  try { await persist(); }
  catch (_) { $('parseStatus').textContent = '重命名未能保存，刷新后可能恢复原标题'; }
});
$('cancelRename').addEventListener('click', () => $('renameDialog').close());

$('importButton').addEventListener('click', () => $('fileInput').click());
document.querySelector('a[download]').addEventListener('click', e => {
  if (window.AndroidBridge) { e.preventDefault(); window.AndroidBridge.saveTemplate(); }
});
$('linkForm').addEventListener('submit', e => { e.preventDefault(); importLink($('linkInput').value.trim()); });
$('cancelDelete').addEventListener('click', () => $('deleteDialog').close('cancel'));
$('confirmDelete').addEventListener('click', () => $('deleteDialog').close('delete'));
$('deleteDialog').addEventListener('click', e => { if (e.target === $('deleteDialog')) $('deleteDialog').close('cancel'); });
$('closeImage').addEventListener('click', () => $('imageDialog').close());
$('imageDialog').addEventListener('click', e => { if (e.target === $('imageDialog')) $('imageDialog').close(); });
$('imageDialog').addEventListener('close', () => $('previewImage').removeAttribute('src'));
$('fileInput').addEventListener('change', e => importFiles(e.target.files));
$('search').addEventListener('input', renderList);
$('prev').addEventListener('click', () => { if (selected > 0) { docs[activeDoc].selected = --selected; render(); savePosition(); } });
$('next').addEventListener('click', () => { if (selected < docs[activeDoc].turns.length - 1) { docs[activeDoc].selected = ++selected; render(); document.querySelector('.main').scrollTop = 0; savePosition(); } });
document.addEventListener('keydown', e => {
  if (!docs.length || ['INPUT', 'SELECT'].includes(document.activeElement.tagName)) return;
  if (e.key === 'ArrowUp' && selected > 0) { docs[activeDoc].selected = --selected; render(); savePosition(); }
  if (e.key === 'ArrowDown' && selected < docs[activeDoc].turns.length - 1) { docs[activeDoc].selected = ++selected; render(); savePosition(); }
});

async function initialize() {
  try {
    const state = await restore();
    if (state && Array.isArray(state.docs) && !docs.length) {
      docs.push(...state.docs);
      activeDoc = docs.length ? Math.min(state.activeDoc || 0, docs.length - 1) : 0;
      selected = docs[activeDoc]?.selected || 0;
      updateWorkspace();
      render();
      return;
    }
  } catch (_) {
    $('parseStatus').textContent = '浏览器本地存储不可用';
  }
  if (window.AndroidBridge) { updateWorkspace(); render(); return; }
  try {
    const response = await fetch('/api/sample');
    if (!response.ok) { updateWorkspace(); render(); return; }
    const result = await response.json();
    if (!docs.length) {
      result.selected = 3;
      docs.push(result);
      selected = 3;
      updateWorkspace();
      render();
    }
  } catch (_) { updateWorkspace(); render(); }
}
initialize();
if (!window.AndroidBridge) {
  let checkingImport = false;
  setInterval(async () => {
    if (checkingImport) return;
    checkingImport = true;
    try {
      const response = await fetch('/api/pending-import');
      if (response.ok) {
        const result = await response.json();
        if (result) await addDocument(result, true);
      }
    } catch (_) {
      // Local server may be restarting.
    } finally {
      checkingImport = false;
    }
  }, 1500);
}
