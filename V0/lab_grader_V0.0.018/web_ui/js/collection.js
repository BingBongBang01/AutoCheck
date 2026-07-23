// ===== Collection (Pipeline 채점 실행 + 점검 로그 뷰어 + Log Analysis) =====
let collectionLogFiles = [];
let collectionOpenTabs = []; // [path]
let collectionActiveTab = null;
let collectionSelectedPaths = new Set(); // 로그 목록 다중 선택(삭제 대상)
let collectionLastClickedIdx = null;
let collectionLogSubTab = 'original'; // 'original' | 'analysis' | 'masking' — 점검 로그 뷰어 아래 중첩 탭
let problemLogFiles = [];
let problemActiveTab = null;
let maskOptions = [];
let maskSelectedCategories = new Set();
let maskSource = 'original'; // 'original'(00_orignal_log 전체) | 'problem'(01_problem_log)
let maskingLogFiles = [];
let maskingActiveTab = null;

async function renderCollection() {
  const content = document.getElementById('content');
  content.innerHTML = `
    <h1 class="page-title">수집</h1>
    <p class="page-sub">Pipeline(Collector→Parser→Rule Engine→Scorer→AI→Report) 실행 및 점검 로그 확인</p>
    <div style="display:flex;align-items:center;gap:10px;margin-bottom:14px;">
      <button class="btn btn-primary" id="btn-run-collection"><span class="material-symbols-rounded">play_circle</span>실행</button>
      <button class="btn btn-danger" id="btn-clear-collection"><span class="material-symbols-rounded">delete_sweep</span>로그 지우기</button>
    </div>
    <div class="card">
      <div class="terminal" id="collection-output" style="height:260px;">실행 결과가 여기 표시됩니다.</div>
    </div>

    <div class="card section-gap">
      <div class="card-header">
        <div class="card-icon"><span class="material-symbols-rounded">table_view</span></div>
        <div><p class="card-title">수집 범위 — 장비별 수집 데이터</p><p class="card-desc">세션 터미널/수집 파이프라인에서 각 장비별로 어떤 커맨드의 원본 출력을 수집했는지 요약합니다.</p></div>
      </div>
      <div id="collection-summary-table"></div>
    </div>

    <div class="card section-gap">
      <div class="card-header">
        <div class="card-icon"><span class="material-symbols-rounded">description</span></div>
        <div><p class="card-title">점검 로그</p><p class="card-desc">원본 로그 확인, 그 아래 중첩된 Log Analysis(이상탐지)·Log Masking(마스킹) 탭을 이용합니다.</p></div>
        <span style="flex:1"></span>
        <button class="btn btn-outlined" id="btn-open-log-folder"><span class="material-symbols-rounded">folder_open</span>Open Folder</button>
      </div>
      <div class="term-tabs" style="margin-bottom:10px;">
        <div class="term-tab ${collectionLogSubTab === 'original' ? 'active' : ''}" data-log-subtab="original">
          <span class="material-symbols-rounded" style="font-size:14px;">description</span>원본 로그
        </div>
        <div class="term-tab ${collectionLogSubTab === 'analysis' ? 'active' : ''}" data-log-subtab="analysis">
          <span class="material-symbols-rounded" style="font-size:14px;">troubleshoot</span>Log Analysis
        </div>
        <div class="term-tab ${collectionLogSubTab === 'masking' ? 'active' : ''}" data-log-subtab="masking">
          <span class="material-symbols-rounded" style="font-size:14px;">visibility_off</span>Log Masking
        </div>
      </div>
      <div class="log-viewer" id="log-viewer" style="display:${collectionLogSubTab === 'original' ? 'block' : 'none'};"></div>
      <div id="log-analysis" style="display:${collectionLogSubTab === 'analysis' ? 'block' : 'none'};"></div>
      <div id="log-masking" style="display:${collectionLogSubTab === 'masking' ? 'block' : 'none'};"></div>
    </div>
  `;
  document.getElementById('btn-clear-collection').addEventListener('click', () => { document.getElementById('collection-output').textContent = ''; });
  document.getElementById('btn-open-log-folder').addEventListener('click', async () => {
    const result = await call('open_inspection_log_folder');
    if (result && result.error) alert(result.error);
  });
  document.querySelectorAll('[data-log-subtab]').forEach(tabEl => {
    tabEl.addEventListener('click', async () => {
      collectionLogSubTab = tabEl.dataset.logSubtab;
      document.getElementById('log-viewer').style.display = collectionLogSubTab === 'original' ? 'block' : 'none';
      document.getElementById('log-analysis').style.display = collectionLogSubTab === 'analysis' ? 'block' : 'none';
      document.getElementById('log-masking').style.display = collectionLogSubTab === 'masking' ? 'block' : 'none';
      document.querySelectorAll('[data-log-subtab]').forEach(el => el.classList.toggle('active', el.dataset.logSubtab === collectionLogSubTab));
      if (collectionLogSubTab === 'analysis') await refreshLogAnalysis();
      if (collectionLogSubTab === 'masking') await refreshLogMasking();
    });
  });
  document.getElementById('btn-run-collection').addEventListener('click', async (e) => {
    const btn = e.currentTarget;
    btn.classList.add('loading');
    const output = await call('run_grade');
    document.getElementById('collection-output').textContent = output;
    btn.classList.remove('loading');
    await refreshCollectionSummary();
    await refreshLogViewer(true);
  });

  await refreshCollectionSummary();
  await refreshLogViewer(false);
  if (collectionLogSubTab === 'analysis') await refreshLogAnalysis();
  if (collectionLogSubTab === 'masking') await refreshLogMasking();
}

async function refreshCollectionSummary() {
  const summary = await call('get_collection_summary') || [];
  const el = document.getElementById('collection-summary-table');
  if (!el) return;
  if (!summary.length) {
    el.innerHTML = `<p style="font-size:12px;color:var(--sub);">아직 수집된 데이터가 없습니다 — 세션 터미널에서 점검을 실행하거나 위에서 Pipeline을 실행하세요.</p>`;
    return;
  }
  el.innerHTML = `
    <table class="mono" style="width:100%;border-collapse:collapse;font-size:12.5px;">
      <thead><tr style="text-align:left;color:var(--sub);"><th style="padding:6px 8px;">장비</th><th style="padding:6px 8px;">수집된 커맨드 수</th><th style="padding:6px 8px;">커맨드 목록</th></tr></thead>
      <tbody>
        ${summary.map(row => `
          <tr style="border-top:1px solid var(--border);">
            <td style="padding:6px 8px;font-weight:600;">${row.device}</td>
            <td style="padding:6px 8px;">${row.command_count}</td>
            <td style="padding:6px 8px;color:var(--sub);">${row.commands.join(', ') || '-'}</td>
          </tr>`).join('')}
      </tbody>
    </table>`;
}

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

// ===== Log Analysis (00_orignal_log -> FSM 이상탐지 -> 01_problem_log) =====
async function refreshLogAnalysis() {
  problemLogFiles = await call('list_problem_log_files') || [];
  renderLogAnalysisPane();
}

function renderLogAnalysisPane() {
  const el = document.getElementById('log-analysis');
  if (!el) return;
  el.innerHTML = `
    <div style="display:flex;align-items:center;gap:10px;margin-bottom:10px;">
      <button class="btn btn-primary" id="btn-run-log-analysis"><span class="material-symbols-rounded">play_arrow</span>분석 실행</button>
      <span style="font-size:11px;color:var(--sub);">00_orignal_log의 원본을 훑어 에러 구간을 찾고, 커맨드/카테고리 문맥을 포함해 01_problem_log에 저장합니다.</span>
    </div>
    <div id="log-analysis-summary" style="margin-bottom:10px;"></div>
    <div style="display:flex;gap:12px;height:360px;">
      <div style="width:260px;flex:0 0 260px;overflow:auto;background:var(--card);border:1px solid var(--border);border-radius:var(--radius-sm);padding:6px;" id="problem-file-list">
        ${problemLogFiles.length ? problemLogFiles.map(f => `
          <div class="connection-device log-file-row ${f.path === problemActiveTab ? 'session-active' : ''}" data-problem-path="${f.path}" style="cursor:pointer;">
            <div style="display:flex;flex-direction:column;">
              <span class="device-name">${f.name}</span>
              <span style="font-size:10px;color:var(--sub);">${f.mtime_str}</span>
            </div>
          </div>`).join('') : `<p style="font-size:12px;color:var(--sub);padding:8px;">아직 분석 결과가 없습니다 — '분석 실행'을 눌러 00_orignal_log를 분석하세요.</p>`}
      </div>
      <pre class="mono terminal" id="problem-log-content" style="flex:1;height:auto;white-space:pre-wrap;">${problemActiveTab ? '불러오는 중...' : '왼쪽 목록에서 결과 파일을 선택하세요.'}</pre>
    </div>
  `;

  document.getElementById('btn-run-log-analysis').addEventListener('click', async (e) => {
    const btn = e.currentTarget;
    btn.classList.add('loading');
    const result = await call('run_log_analysis');
    btn.classList.remove('loading');
    if (result && result.error) { alert(result.error); return; }
    const summaryEl = document.getElementById('log-analysis-summary');
    if (summaryEl && result && result.results) {
      const totalProblems = result.results.reduce((sum, r) => sum + r.problem_count, 0);
      summaryEl.innerHTML = `<span style="font-size:12px;color:var(--sub);">분석 완료 — 원본 ${result.results.length}개 파일 중 ${result.results.filter(r => r.problem_count > 0).length}개에서 총 ${totalProblems}건 발견</span>`;
    }
    await refreshLogAnalysis();
  });

  document.querySelectorAll('[data-problem-path]').forEach(row => {
    row.addEventListener('click', () => {
      problemActiveTab = row.dataset.problemPath;
      renderLogAnalysisPane();
    });
  });

  const contentEl = document.getElementById('problem-log-content');
  if (contentEl && problemActiveTab) {
    call('read_log_file', problemActiveTab).then(result => {
      contentEl.textContent = result && result.text !== undefined ? result.text : (result && result.error) || '읽을 수 없습니다.';
    });
  }
}

// ===== Log Masking (00_orignal_log 또는 01_problem_log -> format-preserving 마스킹 -> 02_masking_log) =====
async function refreshLogMasking() {
  if (!maskOptions.length) maskOptions = await call('get_mask_options') || [];
  maskingLogFiles = await call('list_masking_log_files') || [];
  renderLogMaskingPane();
}

function renderLogMaskingPane() {
  const el = document.getElementById('log-masking');
  if (!el) return;
  el.innerHTML = `
    <div style="margin-bottom:12px;">
      <p style="font-size:12px;font-weight:600;margin-bottom:6px;">마스킹 소스</p>
      <label style="display:flex;align-items:center;gap:6px;font-size:13px;margin-bottom:4px;">
        <input type="radio" name="mask-source" value="original" ${maskSource === 'original' ? 'checked' : ''}>
        00_orignal_log (원본 전체)
      </label>
      <label style="display:flex;align-items:center;gap:6px;font-size:13px;">
        <input type="radio" name="mask-source" value="problem" ${maskSource === 'problem' ? 'checked' : ''}>
        01_problem_log (필터링된 이상탐지 결과)
      </label>
    </div>
    <div style="margin-bottom:12px;">
      <p style="font-size:12px;font-weight:600;margin-bottom:6px;">마스킹 대상 선택</p>
      <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:6px;">
        ${maskOptions.map(o => `
          <label style="display:flex;align-items:center;gap:6px;font-size:13px;">
            <input type="checkbox" data-mask-key="${o.key}" ${maskSelectedCategories.has(o.key) ? 'checked' : ''}>
            ${o.label}
          </label>`).join('')}
      </div>
    </div>
    <div style="display:flex;align-items:center;gap:10px;margin-bottom:10px;">
      <button class="btn btn-primary" id="btn-run-log-masking"><span class="material-symbols-rounded">visibility_off</span>마스킹 실행</button>
      <span id="log-masking-summary" style="font-size:11px;color:var(--sub);"></span>
    </div>
    <div style="display:flex;gap:12px;height:360px;">
      <div style="width:260px;flex:0 0 260px;overflow:auto;background:var(--card);border:1px solid var(--border);border-radius:var(--radius-sm);padding:6px;" id="masking-file-list">
        ${maskingLogFiles.length ? maskingLogFiles.map(f => `
          <div class="connection-device log-file-row ${f.path === maskingActiveTab ? 'session-active' : ''}" data-masking-path="${f.path}" style="cursor:pointer;">
            <div style="display:flex;flex-direction:column;">
              <span class="device-name">${f.name}</span>
              <span style="font-size:10px;color:var(--sub);">${f.mtime_str}</span>
            </div>
          </div>`).join('') : `<p style="font-size:12px;color:var(--sub);padding:8px;">아직 마스킹 결과가 없습니다.</p>`}
      </div>
      <pre class="mono terminal" id="masking-log-content" style="flex:1;height:auto;white-space:pre-wrap;">${maskingActiveTab ? '불러오는 중...' : '왼쪽 목록에서 결과 파일을 선택하세요.'}</pre>
    </div>
  `;

  document.querySelectorAll('[name="mask-source"]').forEach(radio => {
    radio.addEventListener('change', () => { maskSource = radio.value; });
  });
  document.querySelectorAll('[data-mask-key]').forEach(cb => {
    cb.addEventListener('change', () => {
      if (cb.checked) maskSelectedCategories.add(cb.dataset.maskKey);
      else maskSelectedCategories.delete(cb.dataset.maskKey);
    });
  });

  document.getElementById('btn-run-log-masking').addEventListener('click', async (e) => {
    const btn = e.currentTarget;
    btn.classList.add('loading');
    const result = await call('run_log_masking', maskSource, [...maskSelectedCategories]);
    btn.classList.remove('loading');
    if (result && result.error) { alert(result.error); return; }
    const summaryEl = document.getElementById('log-masking-summary');
    if (summaryEl && result && result.results) {
      summaryEl.textContent = `마스킹 완료 — ${result.results.length}개 파일을 02_masking_log에 저장함`;
    }
    await refreshLogMasking();
  });

  document.querySelectorAll('[data-masking-path]').forEach(row => {
    row.addEventListener('click', () => {
      maskingActiveTab = row.dataset.maskingPath;
      renderLogMaskingPane();
    });
  });

  const contentEl = document.getElementById('masking-log-content');
  if (contentEl && maskingActiveTab) {
    call('read_log_file', maskingActiveTab).then(result => {
      contentEl.textContent = result && result.text !== undefined ? result.text : (result && result.error) || '읽을 수 없습니다.';
    });
  }
}
