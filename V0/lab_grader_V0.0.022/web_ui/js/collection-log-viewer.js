// ===== 점검 로그 뷰어 (00_orignal_log 원본 보기) — collection.js의 wireSelectableFileList와는 별도 구현(멀티탭 지원) =====
let collectionLogFiles = [];
let collectionOpenTabs = []; // [path]
let collectionActiveTab = null;
let collectionSelectedPaths = new Set(); // 로그 목록 다중 선택(삭제 대상)
let collectionLastClickedIdx = null;

async function refreshLogViewer(openLatest) {
  collectionLogFiles = await call('list_log_files') || [];
  if (openLatest && collectionLogFiles.length) {
    const latest = collectionLogFiles[0].path;
    if (!collectionOpenTabs.includes(latest)) collectionOpenTabs.push(latest);
    collectionActiveTab = latest;
  }
  renderLogViewer();
}

function renderLogViewer() {
  const el = document.getElementById('log-viewer');
  if (!el) return;
  if (!collectionLogFiles.length) {
    el.innerHTML = `<p style="font-size:12px;color:var(--sub);">생성된 로그 파일이 없습니다.</p>`;
    return;
  }
  el.innerHTML = `
    <div style="display:flex;gap:8px;align-items:center;margin-bottom:8px;">
      <span style="font-size:11px;color:var(--sub);">Shift+클릭/드래그로 범위 선택, Ctrl(Cmd)+A로 전체 선택</span>
      <span style="flex:1"></span>
      <span style="font-size:11px;color:var(--sub);" id="log-viewer-selected-count">${collectionSelectedPaths.size}개 선택됨</span>
      <button class="btn btn-danger" id="btn-delete-log-files" style="height:26px;padding:0 10px;font-size:11px;" ${collectionSelectedPaths.size ? '' : 'disabled'}>
        <span class="material-symbols-rounded" style="font-size:14px;">delete</span>삭제
      </button>
    </div>
    <div style="display:flex;gap:12px;height:360px;">
      <div style="width:260px;flex:0 0 260px;overflow:auto;background:var(--card);border:1px solid var(--border);border-radius:var(--radius-sm);padding:6px;user-select:none;" id="log-file-list" tabindex="0">
        ${collectionLogFiles.map(f => `
          <div class="connection-device log-file-row ${collectionOpenTabs.includes(f.path) ? 'session-active' : ''} ${collectionSelectedPaths.has(f.path) ? 'selected' : ''}" data-path="${f.path}" style="cursor:pointer;">
            <div style="display:flex;flex-direction:column;">
              <span class="device-name">${f.device}</span>
              <span style="font-size:10px;color:var(--sub);">${f.source} · ${f.mtime_str}</span>
            </div>
          </div>`).join('')}
      </div>
      <div style="flex:1;min-width:0;display:flex;flex-direction:column;">
        <div class="term-tabs" id="log-viewer-tabs" style="display:${collectionOpenTabs.length ? 'flex' : 'none'};"></div>
        <pre class="mono terminal" id="log-viewer-content" style="flex:1;height:auto;margin-top:${collectionOpenTabs.length ? '0' : '0'};white-space:pre-wrap;"></pre>
      </div>
    </div>
  `;

  wireLogFileList();

  document.getElementById('btn-delete-log-files').addEventListener('click', deleteSelectedLogFiles);

  const tabbar = document.getElementById('log-viewer-tabs');
  if (tabbar) {
    tabbar.innerHTML = collectionOpenTabs.map(path => {
      const f = collectionLogFiles.find(x => x.path === path);
      const label = f ? f.device : path.split(/[\\/]/).pop();
      return `<div class="term-tab ${path === collectionActiveTab ? 'active' : ''}" data-tab-path="${path}">
        <span class="material-symbols-rounded" style="font-size:14px;">description</span>${label}
        <span class="material-symbols-rounded term-tab-close" data-close-path="${path}">close</span>
      </div>`;
    }).join('');
    tabbar.querySelectorAll('[data-tab-path]').forEach(tabEl => {
      tabEl.addEventListener('click', (e) => {
        if (e.target.closest('[data-close-path]')) return;
        collectionActiveTab = tabEl.dataset.tabPath;
        renderLogViewer();
      });
    });
    tabbar.querySelectorAll('[data-close-path]').forEach(closeEl => {
      closeEl.addEventListener('click', (e) => {
        e.stopPropagation();
        const path = closeEl.dataset.closePath;
        collectionOpenTabs = collectionOpenTabs.filter(p => p !== path);
        if (collectionActiveTab === path) collectionActiveTab = collectionOpenTabs[0] || null;
        renderLogViewer();
      });
    });
  }

  const contentEl = document.getElementById('log-viewer-content');
  if (contentEl && collectionActiveTab) {
    contentEl.textContent = '불러오는 중...';
    call('read_log_file', collectionActiveTab).then(result => {
      contentEl.textContent = result && result.text !== undefined ? result.text : (result && result.error) || '읽을 수 없습니다.';
    });
  } else if (contentEl) {
    contentEl.textContent = '왼쪽 목록에서 로그 파일을 선택하세요.';
  }
}

function wireLogFileList() {
  const listEl = document.getElementById('log-file-list');
  if (!listEl) return;
  const rows = [...listEl.querySelectorAll('.log-file-row')];
  let dragActive = false;
  let dragStartIdx = null;
  let dragSelecting = true;

  const applyDrag = (idx) => {
    const start = Math.min(dragStartIdx, idx);
    const end = Math.max(dragStartIdx, idx);
    for (let i = start; i <= end; i++) {
      const path = rows[i].dataset.path;
      if (dragSelecting) collectionSelectedPaths.add(path);
      else collectionSelectedPaths.delete(path);
      rows[i].classList.toggle('selected', collectionSelectedPaths.has(path));
    }
    updateLogSelectionUi();
  };

  rows.forEach((row, idx) => {
    row.addEventListener('mousedown', (e) => {
      if (e.button !== 0) return;
      e.preventDefault();
      dragActive = false;
      dragStartIdx = idx;
      dragSelecting = !collectionSelectedPaths.has(row.dataset.path);
    });
    row.addEventListener('click', (e) => {
      if (dragActive) { dragActive = false; return; }
      const path = row.dataset.path;
      if (e.shiftKey && collectionLastClickedIdx !== null) {
        const start = Math.min(collectionLastClickedIdx, idx);
        const end = Math.max(collectionLastClickedIdx, idx);
        for (let i = start; i <= end; i++) collectionSelectedPaths.add(rows[i].dataset.path);
      } else if (e.ctrlKey || e.metaKey) {
        if (collectionSelectedPaths.has(path)) collectionSelectedPaths.delete(path);
        else collectionSelectedPaths.add(path);
        collectionLastClickedIdx = idx;
      } else {
        collectionSelectedPaths = new Set([path]);
        collectionLastClickedIdx = idx;
        if (!collectionOpenTabs.includes(path)) collectionOpenTabs.push(path);
        collectionActiveTab = path;
      }
      renderLogViewer();
    });
  });

  document.addEventListener('mousemove', (e) => {
    if (dragStartIdx === null || !e.buttons) return;
    const overRow = e.target.closest('#log-file-list .log-file-row');
    if (!overRow) return;
    const idx = rows.indexOf(overRow);
    if (idx === -1) return;
    dragActive = true;
    applyDrag(idx);
  });
  document.addEventListener('mouseup', () => {
    if (dragActive) collectionLastClickedIdx = dragStartIdx;
    dragStartIdx = null;
    setTimeout(() => { dragActive = false; }, 0);
  });

  listEl.addEventListener('keydown', (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'a') {
      e.preventDefault();
      collectionSelectedPaths = new Set(collectionLogFiles.map(f => f.path));
      renderLogViewer();
    }
  });
}

function updateLogSelectionUi() {
  const countEl = document.getElementById('log-viewer-selected-count');
  const btn = document.getElementById('btn-delete-log-files');
  if (countEl) countEl.textContent = `${collectionSelectedPaths.size}개 선택됨`;
  if (btn) btn.disabled = collectionSelectedPaths.size === 0;
}

async function deleteSelectedLogFiles() {
  const paths = [...collectionSelectedPaths];
  if (!paths.length) return;
  if (!confirm(`선택한 로그 파일 ${paths.length}개를 삭제하시겠습니까? 되돌릴 수 없습니다.`)) return;
  const result = await call('delete_log_files', paths) || { deleted: [], errors: {} };
  collectionOpenTabs = collectionOpenTabs.filter(p => !result.deleted.includes(p));
  if (collectionActiveTab && result.deleted.includes(collectionActiveTab)) {
    collectionActiveTab = collectionOpenTabs[0] || null;
  }
  collectionSelectedPaths = new Set();
  collectionLastClickedIdx = null;
  if (result.errors && Object.keys(result.errors).length) {
    alert(`일부 파일을 삭제하지 못했습니다:\n${Object.entries(result.errors).map(([p, msg]) => `${p}: ${msg}`).join('\n')}`);
  }
  await refreshLogViewer(false);
}
