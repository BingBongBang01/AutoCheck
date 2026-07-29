// ===== Collection (Pipeline 채점 실행 요약 + 공용 파일 목록 선택 헬퍼) =====
// 점검 로그 뷰어/Log Analysis/Log Masking 탭은 collection-log-viewer.js / collection-log-analysis.js / collection-log-masking.js 참고.

// ===== 범용 파일 목록 다중선택(Shift 범위선택/드래그/Ctrl+A) + 삭제 헬퍼 =====
// listElId: 목록 컨테이너 id, rowSelector: 행 클래스 셀렉터, files: 현재 목록 배열,
// selectedSet: 선택 상태 Set(경로), getPath: row -> path, onClickSelect: 단일 클릭 시 실행할 콜백(탭 열기 등), rerender: 다시 그리는 함수
function wireSelectableFileList({ listElId, files, selectedSet, lastClickedRef, onClickSelect, rerender }) {
  const listEl = document.getElementById(listElId);
  if (!listEl) return;
  const rows = [...listEl.querySelectorAll('[data-select-path]')];
  let dragStartIdx = null;
  let dragSelecting = true;

  const applyDrag = (idx) => {
    const start = Math.min(dragStartIdx, idx);
    const end = Math.max(dragStartIdx, idx);
    for (let i = start; i <= end; i++) {
      const path = rows[i].dataset.selectPath;
      if (dragSelecting) selectedSet.add(path);
      else selectedSet.delete(path);
      rows[i].classList.toggle('selected', selectedSet.has(path));
    }
  };

  // 드래그 범위선택 + 목록 가장자리 자동 스크롤(core.js 공용)
  const dragger = createDragRangeSelect({
    container: listEl,
    rowSelector: '[data-select-path]',
    rows,
    applyTo: applyDrag,
    onEnd: (dragged) => {
      if (dragged) { lastClickedRef.idx = dragStartIdx; rerender(); }
      dragStartIdx = null;
    },
  });

  rows.forEach((row, idx) => {
    row.addEventListener('mousedown', (e) => {
      if (e.button !== 0) return;
      e.preventDefault();
      dragStartIdx = idx;
      dragSelecting = !selectedSet.has(row.dataset.selectPath);
      dragger.begin(idx);
    });
    row.addEventListener('click', (e) => {
      if (dragger.isDragging()) return;
      const path = row.dataset.selectPath;
      if (e.shiftKey && lastClickedRef.idx !== null) {
        const start = Math.min(lastClickedRef.idx, idx);
        const end = Math.max(lastClickedRef.idx, idx);
        for (let i = start; i <= end; i++) selectedSet.add(rows[i].dataset.selectPath);
      } else if (e.ctrlKey || e.metaKey) {
        if (selectedSet.has(path)) selectedSet.delete(path);
        else selectedSet.add(path);
        lastClickedRef.idx = idx;
      } else {
        selectedSet.clear();
        selectedSet.add(path);
        lastClickedRef.idx = idx;
        if (onClickSelect) onClickSelect(path);
      }
      rerender();
    });
  });

  listEl.addEventListener('keydown', (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'a') {
      e.preventDefault();
      files.forEach(f => selectedSet.add(f.path));
      rerender();
    }
  });
}

async function deleteSelectedPaths(selectedSet, { onDeleted, refresh }) {
  const paths = [...selectedSet];
  if (!paths.length) return;
  if (!confirm(`선택한 파일 ${paths.length}개를 삭제하시겠습니까? 되돌릴 수 없습니다.`)) return;
  const result = await call('delete_log_files', paths) || { deleted: [], errors: {} };
  if (onDeleted) onDeleted(result.deleted || []);
  selectedSet.clear();
  if (result.errors && Object.keys(result.errors).length) {
    alert(`일부 파일을 삭제하지 못했습니다:\n${Object.entries(result.errors).map(([p, msg]) => `${p}: ${msg}`).join('\n')}`);
  }
  await refresh();
}

async function renderCollection() {
  const content = document.getElementById('content');
  content.innerHTML = `
    <h1 class="page-title">수집 / 채점</h1>
    <p class="page-sub">Pipeline(Collector→Parser→Rule Engine→Scorer→AI→Report) 실행 — 수집한 장비 상태를 target_state/stages 기준과 비교해 채점하고 이력에 저장합니다. 채점 이력이 있어야 '보고서' 탭에서 보고서를 생성할 수 있습니다. 수집된 원본 로그는 '점검 로그' 탭에서 확인합니다.</p>
    <div style="display:flex;align-items:center;gap:10px;margin-bottom:14px;">
      <button class="btn btn-primary" id="btn-run-collection"><span class="material-symbols-rounded">play_circle</span>채점 실행</button>
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

  `;
  document.getElementById('btn-clear-collection').addEventListener('click', () => { document.getElementById('collection-output').textContent = ''; });
  document.getElementById('btn-run-collection').addEventListener('click', async (e) => {
    const btn = e.currentTarget;
    btn.classList.add('loading');
    const output = await call('run_grade');
    document.getElementById('collection-output').textContent = output;
    btn.classList.remove('loading');
    await refreshCollectionSummary();
  });

  await refreshCollectionSummary();
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
