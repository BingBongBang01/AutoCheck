// ===== Log Masking (점검 회차의 raw 또는 problem -> format-preserving 마스킹 -> masked) =====
let maskOptions = [];
let maskSelectedCategories = new Set();
let maskSource = 'original'; // 'original'(원본 로그 전체) | 'problem'(이상탐지 결과)
let maskingLogFiles = [];
let maskingActiveTab = null;
let maskingSelectedPaths = new Set();
let maskingLastClickedIdx = null;
let maskingExpandedRuns = new Set(); // 펼쳐진 점검 회차(run_id) — js/log-run-groups.js

let logMaskingPollTimer = null;

function startLogMaskingPolling() {
  if (logMaskingPollTimer) clearInterval(logMaskingPollTimer);
  logMaskingPollTimer = setInterval(async () => {
    if (typeof currentPage !== 'undefined' && currentPage !== 'logmasking') {
      clearInterval(logMaskingPollTimer);
      logMaskingPollTimer = null;
      return;
    }
    const newFiles = await call('list_masking_log_files') || [];
    if (JSON.stringify(newFiles) !== JSON.stringify(maskingLogFiles)) {
      const existingPaths = new Set(newFiles.map(f => f.path));
      if (maskingActiveTab && !existingPaths.has(maskingActiveTab)) {
        maskingActiveTab = null;
      }
      for (const p of maskingSelectedPaths) {
        if (!existingPaths.has(p)) maskingSelectedPaths.delete(p);
      }
      maskingLogFiles = newFiles;
      renderLogMaskingPane();
    }
  }, 1500);
}

async function refreshLogMasking() {
  if (!maskOptions.length) maskOptions = await call('get_mask_options') || [];
  maskingLogFiles = await call('list_masking_log_files') || [];
  renderLogMaskingPane();
  startLogMaskingPolling();
}

function renderLogMaskingPane() {
  const el = document.getElementById('log-masking');
  if (!el) return;
  el.innerHTML = `
    <div style="margin-bottom:12px;">
      <p style="font-size:12px;font-weight:600;margin-bottom:6px;">마스킹 소스</p>
      <label style="display:flex;align-items:center;gap:6px;font-size:13px;margin-bottom:4px;">
        <input type="radio" name="mask-source" value="original" ${maskSource === 'original' ? 'checked' : ''}>
        원본 로그 (전체)
      </label>
      <label style="display:flex;align-items:center;gap:6px;font-size:13px;">
        <input type="radio" name="mask-source" value="problem" ${maskSource === 'problem' ? 'checked' : ''}>
        분석 결과 (필터링된 이상탐지 결과)
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
      <button class="btn btn-outlined" id="btn-open-masking-folder" title="마스킹 결과 폴더 열기"><span class="material-symbols-rounded">folder_open</span>폴더보기</button>
      <span id="log-masking-summary" style="font-size:11px;color:var(--sub);"></span>
    </div>
    <div style="display:flex;gap:8px;align-items:center;margin-bottom:8px;">
      <span style="font-size:11px;color:var(--sub);">Shift+클릭/드래그로 범위 선택, Ctrl(Cmd)+A로 전체 선택</span>
      <span style="flex:1"></span>
      <span style="font-size:11px;color:var(--sub);">${maskingSelectedPaths.size}개 선택됨</span>
      <button class="btn btn-danger" id="btn-delete-masking-files" style="height:26px;padding:0 10px;font-size:11px;" ${maskingSelectedPaths.size ? '' : 'disabled'}>
        <span class="material-symbols-rounded" style="font-size:14px;">delete</span>삭제
      </button>
    </div>
    <div style="display:flex;gap:12px;flex:1;min-height:0;">
      <div style="width:260px;flex:0 0 260px;overflow:auto;background:var(--card);border:1px solid var(--border);border-radius:var(--radius-sm);padding:6px;user-select:none;" id="masking-file-list" tabindex="0">
        ${maskingLogFiles.length ? renderLogRunGroupsHtml(maskingLogFiles, maskingExpandedRuns, f => `
          <div class="connection-device log-file-row ${f.path === maskingActiveTab ? 'session-active' : ''} ${maskingSelectedPaths.has(f.path) ? 'selected' : ''}" data-select-path="${f.path}" style="cursor:pointer;">
            <div style="display:flex;flex-direction:column;">
              <span class="device-name">${f.name}</span>
              <span style="font-size:10px;color:var(--sub);">${f.mtime_str}</span>
            </div>
          </div>`) : `<p style="font-size:12px;color:var(--sub);padding:8px;">아직 마스킹 결과가 없습니다.</p>`}
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
      summaryEl.textContent = `마스킹 완료 — ${result.results.length}개 파일을 마스킹 결과 폴더에 저장함`;
    }
    await refreshLogMasking();
  });

  const openFolderBtn = document.getElementById('btn-open-masking-folder');
  if (openFolderBtn) openFolderBtn.addEventListener('click', async () => {
    const res = await call('open_inspection_log_folder', 'masking');
    if (res && res.error) alert(res.error);
  });

  wireSelectableFileList({
    listElId: 'masking-file-list',
    files: maskingLogFiles,
    selectedSet: maskingSelectedPaths,
    lastClickedRef: { get idx() { return maskingLastClickedIdx; }, set idx(v) { maskingLastClickedIdx = v; } },
    onClickSelect: (path) => { maskingActiveTab = path; },
    rerender: renderLogMaskingPane,
  });
  wireLogRunGroupToggles(document.getElementById('masking-file-list'), maskingExpandedRuns, renderLogMaskingPane);

  const delMaskingBtn = document.getElementById('btn-delete-masking-files');
  if (delMaskingBtn) delMaskingBtn.addEventListener('click', () => deleteSelectedPaths(maskingSelectedPaths, {
    onDeleted: (deleted) => { if (maskingActiveTab && deleted.includes(maskingActiveTab)) maskingActiveTab = null; },
    refresh: refreshLogMasking,
  }));

  const contentEl = document.getElementById('masking-log-content');
  if (contentEl && maskingActiveTab) {
    call('read_log_file', maskingActiveTab).then(result => {
      contentEl.textContent = result && result.text !== undefined ? result.text : (result && result.error) || '읽을 수 없습니다.';
    });
  }
}

async function renderLogMasking() {
  const content = document.getElementById('content');
  content.innerHTML = `
    <div class="term-page">
      <h1 class="page-title">마스킹</h1>
      <p class="page-sub">외부 공유용으로 로그 내의 민감정보를 가립니다.</p>
  
      <div class="card" style="display:flex; flex-direction:column; min-height:0; flex:1;">
        <div id="log-masking" style="flex:1; display:flex; flex-direction:column; min-height:0;"></div>
      </div>
    </div>
  `;
  await refreshLogMasking();
}
