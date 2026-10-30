// ===== 화면 공용 헬퍼 =====
// 여러 탭이 같이 쓰는 함수를 둔다. 예전엔 도달할 수 없는 채점 화면 파일(collection.js)에 있어서,
// 그 화면을 지우면 점검 로그·로그 분석·마스킹 탭의 목록 선택이 함께 깨질 구조였다(IMPROVEMENT_PLAN 3-1·3-7).

// ===== 모달 =====
// 앱 공용 모양(.modal-overlay 안에 .card). 같은 id 의 모달이 이미 열려 있으면 바꿔 끼운다. 바깥(어두운
// 부분)을 누르거나 [data-modal-close] 를 단 버튼을 누르면 닫힌다. 반환: {overlay, close}.
// 예전엔 모달마다 오버레이를 만들고 닫기를 연결하는 코드를 따로 들고 있었다(IMPROVEMENT_PLAN 3-7).
function openModal({ id = null, html = '', onClose = null } = {}) {
  if (id) document.getElementById(id)?.remove();
  const overlay = document.createElement('div');
  overlay.className = 'modal-overlay';
  if (id) overlay.id = id;
  overlay.innerHTML = html;
  let closed = false;
  const close = () => {
    if (closed) return;
    closed = true;
    overlay.remove();
    if (onClose) onClose();
  };
  overlay.addEventListener('click', (e) => { if (e.target === overlay) close(); });
  overlay.querySelectorAll('[data-modal-close]').forEach(btn => btn.addEventListener('click', close));
  document.body.appendChild(overlay);
  return { overlay, close };
}

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

// ===== 점검 회차 고르기 — 원본로그분석·마스킹·보고서가 '언제 점검한 로그'를 쓸지 =====
// 세 화면이 같은 선택을 쓴다(한 회차를 분석 → 마스킹 → 보고서로 이어 가는 흐름). '' 는 기본값이다 — 원본로그분석·마스킹은
// 가장 최근 회차, 보고서는 모든 회차에서 장비마다 가장 최근 로그(장비 몇 대만 다시 점검한 회차를 합쳐 쓰는 경우).
let selectedLogRunId = '';
let logRuns = [];      // [{run_id, label, log_count}] — 원본 로그가 있는 회차, 최신순(api list_log_runs)

async function loadLogRuns() {
  logRuns = await call('list_log_runs') || [];
  if (selectedLogRunId && !logRuns.some(run => run.run_id === selectedLogRunId)) selectedLogRunId = '';
  return logRuns;
}

// 고른 회차 — '' 인데 '모든 회차' 항목이 없는 화면(원본로그분석·마스킹)이면 가장 최근 회차.
function effectiveLogRunId(allowAll = false) {
  if (selectedLogRunId || allowAll) return selectedLogRunId;
  return logRuns.length ? logRuns[0].run_id : '';
}

// allLatest: '모든 회차에서 장비마다 가장 최근 로그'를 고를 수 있는 화면(보고서)이면 그 항목의 이름.
function logRunSelectMarkup(id, allLatest = '') {
  const current = effectiveLogRunId(!!allLatest);
  const options = (allLatest ? [`<option value="">${esc(allLatest)}</option>`] : []).concat(logRuns.map((run, i) =>
    `<option value="${esc(run.run_id)}" ${run.run_id === current ? 'selected' : ''}>${esc(run.label)}${i === 0 ? ' (최근)' : ''} · 로그 ${run.log_count}개</option>`));
  if (!logRuns.length && !allLatest) options.push('<option value="">점검 이력 없음</option>');
  return `<label class="log-run-picker" title="어느 점검 회차의 원본 로그를 쓸지 고릅니다">점검 회차
      <select class="field" id="${id}">${options.join('')}</select></label>`;
}

function wireLogRunSelect(id, onChange) {
  const select = document.getElementById(id);
  if (!select) return;
  select.addEventListener('change', () => {
    selectedLogRunId = select.value;
    if (onChange) onChange(select.value);
  });
}

// ===== 로그 파일 보기 — 쪽 나누기·찾기 (IMPROVEMENT_PLAN 4-10) =====
// 예전엔 read_log_file 이 파일 전체를 넘겨 <pre> 에 넣어서, 수십 MB 로그를 열면 화면이 멈췄다. 이제 5,000줄씩 넘기고,
// 찾기는 서버가 파일 전체에서 한다(search_log_file). 찾은 줄을 누르면 그 줄이 든 쪽을 열고 줄을 칠한다.
// 로그 보기(원본 로그)·분석 결과·마스킹 결과 세 화면이 같은 것을 쓴다 — logViewMarkup 으로 자리를 만들고 showLogFile 로 연다.
const LOG_PAGE_LINES = 5000;
const logViewState = {};    // {pre id: {path, query, regex, hits}} — 쪽을 넘겨도 찾던 말이 남게

function logViewMarkup(id, placeholder) {
  return `<div class="logview" id="${id}-wrap">
      <div class="logview-bar" id="${id}-bar" hidden></div>
      <div class="logview-hits" id="${id}-hits" hidden></div>
      <pre class="mono terminal logview-pre selectable-log" id="${id}">${esc(placeholder || '')}</pre>
    </div>`;
}

async function showLogFile(id, path, { startLine = 0, highlightLine = null } = {}) {
  const pre = document.getElementById(id);
  if (!pre) return;
  const state = logViewState[id] = logViewState[id] && logViewState[id].path === path
    ? logViewState[id] : { path, query: '', regex: false, hits: null };
  pre.textContent = '불러오는 중...';
  const result = await call('read_log_file', path, startLine, LOG_PAGE_LINES);
  if (document.getElementById(id) !== pre) return;               // 그사이 화면이 바뀌었다
  const bar = document.getElementById(`${id}-bar`);
  if (!result || result.error || result.text === undefined) {
    pre.textContent = (result && result.error) || '읽을 수 없습니다.';
    if (bar) bar.hidden = true;
    return;
  }
  if (highlightLine == null) {
    pre.textContent = result.text;
  } else {
    const at = highlightLine - result.start_line;
    pre.innerHTML = result.text.split('\n').map((line, i) => (i === at
      ? `<mark class="logview-mark" id="${id}-mark">${esc(line)}</mark>` : esc(line))).join('\n');
    document.getElementById(`${id}-mark`)?.scrollIntoView({ block: 'center' });
  }
  if (highlightLine == null) pre.scrollTop = 0;
  renderLogViewBar(id, state, result);
}

function renderLogViewBar(id, state, result) {
  const bar = document.getElementById(`${id}-bar`);
  if (!bar) return;
  const first = result.total_lines ? result.start_line + 1 : 0;
  const last = result.start_line + result.lines;
  const hasPrev = result.start_line > 0;
  const hasNext = last < result.total_lines;
  bar.hidden = false;
  bar.innerHTML = `
    <span class="logview-range">${result.truncated
      ? `줄 ${first.toLocaleString()}–${last.toLocaleString()} / ${result.total_lines.toLocaleString()}`
      : `${result.total_lines.toLocaleString()}줄`}</span>
    ${result.truncated ? `<button class="btn btn-outlined rtm-mini" type="button" data-logview-page="prev" ${hasPrev ? '' : 'disabled'}>이전</button>
      <button class="btn btn-outlined rtm-mini" type="button" data-logview-page="next" ${hasNext ? '' : 'disabled'}>다음</button>` : ''}
    <span style="flex:1"></span>
    <input class="field logview-query" type="search" placeholder="파일 전체에서 찾기" value="${esc(state.query)}">
    <label class="logview-regex"><input type="checkbox" ${state.regex ? 'checked' : ''}>정규식</label>
    <button class="btn btn-outlined rtm-mini" type="button" data-logview-search>찾기</button>`;
  bar.querySelectorAll('[data-logview-page]').forEach(btn => btn.addEventListener('click', () => {
    const step = btn.dataset.logviewPage === 'next' ? LOG_PAGE_LINES : -LOG_PAGE_LINES;
    showLogFile(id, state.path, { startLine: Math.max(0, result.start_line + step) });
  }));
  const input = bar.querySelector('.logview-query');
  const regexBox = bar.querySelector('.logview-regex input');
  const search = async () => {
    state.query = input.value;
    state.regex = regexBox.checked;
    if (!state.query.trim()) { state.hits = null; renderLogViewHits(id, state); return; }
    const found = await call('search_log_file', state.path, state.query, state.regex);
    if (!found) return;
    if (found.error) { showToast(found.error, 'error'); return; }
    state.hits = found;
    renderLogViewHits(id, state);
  };
  bar.querySelector('[data-logview-search]').addEventListener('click', search);
  input.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); search(); } });
  renderLogViewHits(id, state);
}

function renderLogViewHits(id, state) {
  const box = document.getElementById(`${id}-hits`);
  if (!box) return;
  const found = state.hits;
  if (!found) { box.hidden = true; box.innerHTML = ''; return; }
  box.hidden = false;
  box.innerHTML = `<div class="logview-hits-head">${found.total
      ? `${found.total.toLocaleString()}줄에서 찾음${found.truncated ? ` — 앞 ${found.hits.length}줄만 보입니다` : ''}`
      : '찾지 못했습니다'}<span style="flex:1"></span>
      <button class="btn btn-outlined rtm-mini" type="button" data-logview-hits-close>닫기</button></div>
    ${found.hits.map((h, i) => `<div class="logview-hit" data-logview-hit="${i}">
        <span class="logview-hit-no">${(h.line + 1).toLocaleString()}</span><span class="mono">${esc(h.text)}</span></div>`).join('')}`;
  box.querySelector('[data-logview-hits-close]').addEventListener('click', () => {
    state.hits = null;
    renderLogViewHits(id, state);
  });
  box.querySelectorAll('[data-logview-hit]').forEach(row => row.addEventListener('click', () => {
    const line = found.hits[Number(row.dataset.logviewHit)].line;
    showLogFile(id, state.path, { startLine: Math.floor(line / LOG_PAGE_LINES) * LOG_PAGE_LINES, highlightLine: line });
  }));
}
