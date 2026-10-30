// ===== Command Catalog — 메모장처럼 고친다 =====
// 장비에 입력하는 모양 그대로 보여 준다: 한 줄에 명령 하나, 명령 다음 줄에 '!'(세션 점검이 명령 사이에 넣어 보내는 것과
// 같다). 고치면 잠시 뒤 저장하고(자동 저장 — 글은 건드리지 않는다), '저장'·Ctrl+S 는 바로 저장하면서 빠진 '!' 를 채워
// 줄을 정리한다. 장비 설정을 바꿀 수 있는 명령이 있으면 서버가 저장하지 않고 그 줄을 알려 준다(api/catalog_api).
const CATALOG_AUTOSAVE_MS = 1000;
let catalogAutosaveTimer = null;
let catalogSaveSeq = 0;
let catalogDefaults = [];

function catalogCommandKey(command) {
  return String(command || '').trim().split(/\s+/).join(' ').toLowerCase();
}

// 메모장 글의 명령 — 빈 줄과 '!' 로 시작하는 줄은 명령이 아니다(engine/command_catalog.parse_catalog_text 와 같은 규칙).
function catalogTextCommands(text) {
  return String(text || '').split(/\r?\n/).map(line => line.trim()).filter(line => line && !line.startsWith('!'));
}

async function renderCatalog() {
  const content = document.getElementById('content');
  const data = await call('get_catalog_text') || { text: '', count: 0, hidden: [], defaults: [] };
  catalogDefaults = data.defaults || [];
  const hidden = data.hidden || [];

  content.innerHTML = `
    <h1 class="page-title">커맨드 카탈로그</h1>
    <p class="page-sub">점검할 때 장비에 이 순서대로 입력합니다. 메모장처럼 한 줄에 명령 하나씩 적고, 명령 다음 줄에 <code>!</code> 를 둡니다
      (<code>!</code> 로 시작하는 줄은 명령이 아닙니다). 고치면 자동으로 저장되고, '저장'(Ctrl+S)은 빠진 <code>!</code> 를 채워 줄을 정리합니다.
      장비 설정을 바꿀 수 있는 명령이 있으면 저장하지 않습니다.</p>
    <div class="catalog-editor-layout">
      <div class="card catalog-editor-card">
        <textarea id="catalog-text" class="field catalog-text" spellcheck="false" autocomplete="off" wrap="off"
                  placeholder="enable&#10;!&#10;terminal length 0&#10;!&#10;show version&#10;!" ${data.error ? 'disabled' : ''}>${esc(data.text || '')}</textarea>
        <div id="catalog-status" class="catalog-status"></div>
        <div id="catalog-problems" class="catalog-problems"></div>
        ${hidden.length ? `<div class="catalog-hidden-note">메모장에 없는 예전 항목 ${hidden.length}개 — 점검에서 실행되지 않던 명령입니다(${hidden.map(h => `${esc(h.command)}: ${esc(h.reason)}`).join(' · ')}). 다음에 저장하면 카탈로그에서 빠집니다.</div>` : ''}
      </div>
      <div class="card catalog-defaults-card">
        <div class="catalog-defaults-head">기본 카탈로그 명령<span>지금 글에 없는 것 — 누르면 맨 끝에 더합니다</span></div>
        <div id="catalog-defaults" class="catalog-defaults"></div>
      </div>
    </div>
    <div class="section-gap catalog-actions">
      <button class="btn btn-primary" id="btn-save-catalog" title="바로 저장하고 빠진 '!' 를 채워 줄을 정리합니다 (Ctrl+S)"><span class="material-symbols-rounded">save</span>저장</button>
      <button class="btn btn-outlined" id="btn-reset-catalog"><span class="material-symbols-rounded">restart_alt</span>기본값으로 초기화</button>
      <button class="btn btn-outlined" id="btn-export-catalog" title="지금 글을 .txt 로 저장합니다"><span class="material-symbols-rounded">file_download</span>내보내기(.txt)</button>
      <button class="btn btn-outlined" id="btn-import-catalog" title=".txt(예전 Excel 내보내기도 됨)의 명령으로 메모장을 바꾸고 저장합니다"><span class="material-symbols-rounded">file_upload</span>불러오기(.txt)</button>
      <button class="btn btn-outlined" id="btn-open-catalog-folder" title="내보내기·불러오기가 쓰는 이 정기점검의 commands 폴더"><span class="material-symbols-rounded">folder_open</span>폴더 열기</button>
    </div>
  `;

  const editor = document.getElementById('catalog-text');
  renderCatalogDefaults();
  if (data.error) setCatalogStatus(data.error, 'error');
  else setCatalogStatus(`명령 ${data.count || 0}개`);

  editor.addEventListener('input', scheduleCatalogAutosave);
  editor.addEventListener('keydown', e => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') {
      e.preventDefault();
      saveCatalogText('now');
    }
  });
  // 고친 뒤 자동 저장을 기다리는 사이에 다른 화면으로 가면 그 자리에서 저장한다.
  onPageLeave(() => { if (catalogAutosaveTimer) saveCatalogText('auto'); });

  document.getElementById('catalog-defaults').addEventListener('click', e => {
    const button = e.target.closest('[data-default-cmd]');
    if (!button || editor.disabled) return;
    const text = editor.value;
    editor.value = `${text}${text && !text.endsWith('\n') ? '\n' : ''}${button.dataset.defaultCmd}\n!\n`;
    editor.scrollTop = editor.scrollHeight;
    renderCatalogDefaults();
    scheduleCatalogAutosave();
  });
  document.getElementById('catalog-problems').addEventListener('click', e => {
    const row = e.target.closest('[data-problem-line]');
    if (row) selectCatalogLine(editor, Number(row.dataset.problemLine));
  });

  document.getElementById('btn-save-catalog').addEventListener('click', () => saveCatalogText('now'));
  document.getElementById('btn-reset-catalog').addEventListener('click', async () => {
    if (!confirm('메모장의 명령을 모두 지우고 기본 카탈로그로 되돌립니다. 계속할까요?')) return;
    const result = await call('reset_catalog_defaults');
    if (!result || result.error) { setCatalogStatus((result && result.error) || '되돌리지 못했습니다', 'error'); return; }
    clearTimeout(catalogAutosaveTimer);
    catalogAutosaveTimer = null;
    editor.value = result.text;
    showCatalogProblems([], []);
    renderCatalogDefaults();
    setCatalogStatus(`기본값으로 되돌림 · 명령 ${result.count}개`, 'ok');
  });
  document.getElementById('btn-export-catalog').addEventListener('click', async () => {
    const result = await call('export_catalog_text', editor.value);
    if (result && result.error) showToast(result.error, 'error');
    else if (result && result.path) showToast(`명령 ${result.count}개를 내보냈습니다: ${result.path}`);
  });
  document.getElementById('btn-import-catalog').addEventListener('click', async () => {
    if (!confirm('고른 파일의 명령으로 메모장 내용을 바꾸고 저장합니다. 계속할까요?')) return;
    const result = await call('import_catalog_text');
    if (!result) return;                                  // 파일을 고르지 않았다
    if (result.error) { showToast(result.error, 'error'); return; }
    editor.value = result.text;
    const saved = await saveCatalogText('now');
    if (saved && saved.ok) showToast(`명령 ${result.count}개를 불러와 저장했습니다`);
  });
  document.getElementById('btn-open-catalog-folder').addEventListener('click', async () => {
    const result = await call('open_catalog_folder');
    if (result && result.error) showToast(result.error, 'error');
  });
}

// 기본 카탈로그 명령 중 지금 글에 없는 것 — 누르면 맨 끝에 더한다(예전 화면의 '선택 명령 켜기' 대신).
function renderCatalogDefaults() {
  const box = document.getElementById('catalog-defaults');
  const editor = document.getElementById('catalog-text');
  if (!box || !editor) return;
  const present = new Set(catalogTextCommands(editor.value).map(catalogCommandKey));
  const missing = catalogDefaults.filter(d => !present.has(catalogCommandKey(d.command)));
  box.innerHTML = missing.length
    ? missing.map(d => `
        <button type="button" class="catalog-default" data-default-cmd="${esc(d.command)}" title="${esc(d.description || '')}">
          <span class="mono">${esc(d.command)}</span>${d.description ? `<small>${esc(d.description)}</small>` : ''}
        </button>`).join('')
    : '<p class="catalog-defaults-empty">기본 카탈로그 명령이 모두 들어 있습니다.</p>';
}

function setCatalogStatus(message, tone = '') {
  const el = document.getElementById('catalog-status');
  if (!el) return;
  el.textContent = message;
  el.className = `catalog-status ${tone}`;
}

// 저장하지 않은 줄(설정을 바꿀 수 있는 명령)과 확인할 줄(한글이 섞인 명령·같은 명령 두 번) — 누르면 그 줄을 고른다.
function showCatalogProblems(blocked, warnings) {
  const box = document.getElementById('catalog-problems');
  if (!box) return;
  const rows = blocked.map(p => ({ ...p, tone: 'blocked', tag: '저장 안 함' }))
    .concat(warnings.map(p => ({ ...p, tone: 'warn', tag: '확인' })))
    .map(p => ({ ...p, line: Number(p.line) || 1 }));
  box.innerHTML = rows.map(p => `
    <div class="catalog-problem ${p.tone}" data-problem-line="${p.line || 1}" title="눌러서 그 줄로 이동">
      <span class="catalog-problem-tag">${p.tag}</span>
      <span class="mono">${p.line}번째 줄 · ${esc(p.command)}</span>
      <span class="catalog-problem-reason">${esc(p.reason)}</span>
    </div>`).join('');
}

function selectCatalogLine(editor, lineNumber) {
  const lines = editor.value.split('\n');
  const index = Math.min(Math.max(lineNumber, 1), lines.length) - 1;
  const start = lines.slice(0, index).reduce((sum, line) => sum + line.length + 1, 0);
  editor.focus();
  editor.setSelectionRange(start, start + lines[index].length);
  const lineHeight = parseFloat(getComputedStyle(editor).lineHeight) || 20;
  editor.scrollTop = Math.max(0, (index - 3) * lineHeight);
}

function scheduleCatalogAutosave() {
  clearTimeout(catalogAutosaveTimer);
  setCatalogStatus('고치는 중 — 잠시 뒤 저장합니다');
  catalogAutosaveTimer = setTimeout(() => saveCatalogText('auto'), CATALOG_AUTOSAVE_MS);
}

// mode: 'auto'(자동 저장 — 글은 그대로) | 'now'(바로 저장하고 줄 정리 — 빠진 '!' 를 채운다)
async function saveCatalogText(mode) {
  clearTimeout(catalogAutosaveTimer);
  catalogAutosaveTimer = null;
  const editor = document.getElementById('catalog-text');
  if (!editor || editor.disabled) return null;
  const sent = editor.value;
  const seq = ++catalogSaveSeq;
  setCatalogStatus('저장 중…');
  const result = await call('save_catalog_text', sent);
  if (seq !== catalogSaveSeq) return result;             // 그 사이 다시 저장했다 — 이 응답은 지난 글의 것
  if (!result) { setCatalogStatus('저장하지 못했습니다 — 다시 시도하세요', 'error'); return null; }
  if (result.error) {
    showCatalogProblems(result.blocked || [], []);
    setCatalogStatus(result.error, 'error');
    return result;
  }
  let warnings = result.warnings || [];
  const current = document.getElementById('catalog-text');
  if (mode === 'now' && current && current.value === sent && result.text !== sent) {
    const top = current.scrollTop;
    current.value = result.text;
    current.scrollTop = top;
    warnings = warnings.map(w => ({ ...w, line: 2 * w.index + 1 }));   // 줄을 정리한 글에서 명령은 홀수 줄이다
  }
  showCatalogProblems([], warnings);
  renderCatalogDefaults();
  const time = new Date().toLocaleTimeString('ko-KR');
  setCatalogStatus(`명령 ${result.count}개 · 저장됨 ${time}` + (warnings.length ? ` · 확인할 줄 ${warnings.length}개` : ''),
    warnings.length ? 'warn' : 'ok');
  return result;
}
