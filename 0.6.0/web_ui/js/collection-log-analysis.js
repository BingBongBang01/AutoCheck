// ===== Log Analysis (점검 회차의 raw -> FSM 이상탐지 -> 같은 회차의 problem) =====
let problemLogFiles = [];
let problemActiveTab = null;
let problemSelectedPaths = new Set();
let problemLastClickedIdx = null;
let problemExpandedRuns = new Set(); // 펼쳐진 점검 회차(run_id) — js/log-run-groups.js

let logAnalysisPollTimer = null;

function startLogAnalysisPolling() {
  if (logAnalysisPollTimer) clearInterval(logAnalysisPollTimer);
  logAnalysisPollTimer = setInterval(async () => {
    if (typeof currentPage !== 'undefined' && currentPage !== 'loganalysis') {
      clearInterval(logAnalysisPollTimer);
      logAnalysisPollTimer = null;
      return;
    }
    const newFiles = await call('list_problem_log_files') || [];
    if (JSON.stringify(newFiles) !== JSON.stringify(problemLogFiles)) {
      const existingPaths = new Set(newFiles.map(f => f.path));
      if (problemActiveTab && !existingPaths.has(problemActiveTab)) {
        problemActiveTab = null;
      }
      for (const p of problemSelectedPaths) {
        if (!existingPaths.has(p)) problemSelectedPaths.delete(p);
      }
      problemLogFiles = newFiles;
      renderLogAnalysisPane();
    }
  }, 1500);
}

async function refreshLogAnalysis() {
  problemLogFiles = await call('list_problem_log_files') || [];
  renderLogAnalysisPane();
  startLogAnalysisPolling();
}

function renderLogAnalysisPane() {
  const el = document.getElementById('log-analysis');
  if (!el) return;
  el.innerHTML = `
    <div style="display:flex;align-items:center;gap:10px;margin-bottom:10px;flex-wrap:wrap;">
      <button class="btn btn-primary" id="btn-run-log-analysis"><span class="material-symbols-rounded">play_arrow</span>분석 실행</button>
      <button class="btn btn-outlined" id="btn-analyze-local-ai"><span class="material-symbols-rounded">memory</span>Run Local AI Analysis</button>
      <button class="btn btn-outlined" id="btn-analyze-cloud-ai"><span class="material-symbols-rounded">cloud</span>Run Cloud AI Analysis</button>
      <button class="btn btn-outlined" id="btn-realtime-baseline"><span class="material-symbols-rounded">radar</span>실시간 감시</button>
      <button class="btn btn-outlined" id="btn-goto-report"><span class="material-symbols-rounded">summarize</span>보고서 만들기</button>
      <button class="btn btn-outlined" id="btn-open-problem-folder" title="분석 결과 폴더 열기"><span class="material-symbols-rounded">folder_open</span>폴더보기</button>
      <span style="font-size:11px;color:var(--sub);">최신 점검 회차의 원본 로그를 훑어 에러 구간을 찾고, 커맨드/카테고리 문맥을 포함해 같은 회차의 분석 결과 폴더에 저장합니다.</span>
    </div>
    <div id="log-analysis-summary" style="margin-bottom:10px;"></div>
    <div style="display:flex;gap:8px;align-items:center;margin-bottom:8px;">
      <span style="font-size:11px;color:var(--sub);">Shift+클릭/드래그로 범위 선택, Ctrl(Cmd)+A로 전체 선택</span>
      <span style="flex:1"></span>
      <span style="font-size:11px;color:var(--sub);">${problemSelectedPaths.size}개 선택됨</span>
      <button class="btn btn-danger" id="btn-delete-problem-files" style="height:26px;padding:0 10px;font-size:11px;" ${problemSelectedPaths.size ? '' : 'disabled'}>
        <span class="material-symbols-rounded" style="font-size:14px;">delete</span>삭제
      </button>
    </div>
    <div style="display:flex;gap:12px;flex:1;min-height:0;">
      <div style="width:260px;flex:0 0 260px;overflow:auto;background:var(--card);border:1px solid var(--border);border-radius:var(--radius-sm);padding:6px;user-select:none;" id="problem-file-list" tabindex="0">
        ${problemLogFiles.length ? renderLogRunGroupsHtml(problemLogFiles, problemExpandedRuns, f => `
          <div class="${logFileRowClass(f.path === problemActiveTab, problemSelectedPaths.has(f.path))}" data-select-path="${f.path}" style="cursor:pointer;">
            <div style="display:flex;flex-direction:column;min-width:0;">
              <span class="device-name">${f.name}</span>
              <span style="font-size:10px;color:var(--sub);">${f.mtime_str}</span>
            </div>
            ${logFileActiveBadge(f.path === problemActiveTab)}
          </div>`) : `<p style="font-size:12px;color:var(--sub);padding:8px;">아직 분석 결과가 없습니다 — '분석 실행'을 눌러 점검 원본 로그를 분석하세요.</p>`}
      </div>
      <pre class="mono terminal" id="problem-log-content" style="flex:1;height:auto;white-space:pre-wrap;">${problemActiveTab ? '불러오는 중...' : '왼쪽 목록에서 결과 파일을 선택하세요.'}</pre>
    </div>
  `;

  // 분석은 백엔드 스레드에서 백그라운드로 진행된다 — 여기서는 시작만 요청하고 바로 반환하며,
  // 진행률/완료 처리는 js/analysis-progress.js의 폴링(pollAnalysisJobs)이 어느 탭에서든 담당한다.
  document.getElementById('btn-run-log-analysis').addEventListener('click', async (e) => {
    const btn = e.currentTarget;
    btn.classList.add('loading');
    if (typeof analysisJobsPrev !== 'undefined') analysisJobsPrev['program'] = {status: 'running'};
    const result = await call('start_log_analysis');
    if (result && result.error) { btn.classList.remove('loading'); alert(result.error); }
    // 폴러를 즉시 깨운다 — 유휴 상태였다면 대기 타이머가 5초에 걸려 있어서, 이걸 하지 않으면
    // 진행바가 한 번 늦게 뜬다. wake() 는 대기를 취소하고 지금 한 번 돌린 뒤 빠른 주기로 잇는다.
    if (typeof analysisPoller !== 'undefined') analysisPoller.wake();
    else if (typeof pollAnalysisJobs === 'function') pollAnalysisJobs();
  });

  const startAiAnalysis = async (mode, btn) => {
    btn.classList.add('loading');
    if (typeof analysisJobsPrev !== 'undefined') analysisJobsPrev[mode] = {status: 'running'};
    const result = await call('start_ai_log_analysis', mode);
    if (result && result.error) { btn.classList.remove('loading'); alert(result.error); }
    // 폴러를 즉시 깨운다 — 유휴 상태였다면 대기 타이머가 5초에 걸려 있어서, 이걸 하지 않으면
    // 진행바가 한 번 늦게 뜬다. wake() 는 대기를 취소하고 지금 한 번 돌린 뒤 빠른 주기로 잇는다.
    if (typeof analysisPoller !== 'undefined') analysisPoller.wake();
    else if (typeof pollAnalysisJobs === 'function') pollAnalysisJobs();
  };

  // 실시간 감시는 전용 탭(js/realtime-monitor-panel.js)에서 조작한다 — 여기서는 바로가기만 둔다.
  const realtimeBtn = document.getElementById('btn-realtime-baseline');
  if (realtimeBtn) {
    syncRealtimeWatchButton(realtimeBtn);
    realtimeBtn.addEventListener('click', () => navigate('realtimewatch'));
  }

  document.getElementById('btn-analyze-local-ai').addEventListener('click', (e) => startAiAnalysis('local', e.currentTarget));
  document.getElementById('btn-analyze-cloud-ai').addEventListener('click', (e) => startAiAnalysis('cloud', e.currentTarget));
  // 분석까지 끝낸 뒤의 자연스러운 다음 단계 — 같은 원본로그로 정기점검 보고서 엑셀을 만든다.
  document.getElementById('btn-goto-report').addEventListener('click', () => navigate('report'));

  const openFolderBtn = document.getElementById('btn-open-problem-folder');
  if (openFolderBtn) openFolderBtn.addEventListener('click', async () => {
    const res = await call('open_inspection_log_folder', 'problem');
    if (res && res.error) alert(res.error);
  });

  wireSelectableFileList({
    listElId: 'problem-file-list',
    files: problemLogFiles,
    selectedSet: problemSelectedPaths,
    lastClickedRef: { get idx() { return problemLastClickedIdx; }, set idx(v) { problemLastClickedIdx = v; } },
    onClickSelect: (path) => { problemActiveTab = path; },
    rerender: renderLogAnalysisPane,
  });
  wireLogRunGroupToggles(document.getElementById('problem-file-list'), problemExpandedRuns, renderLogAnalysisPane);

  const delProblemBtn = document.getElementById('btn-delete-problem-files');
  if (delProblemBtn) delProblemBtn.addEventListener('click', () => deleteSelectedPaths(problemSelectedPaths, {
    onDeleted: (deleted) => { if (problemActiveTab && deleted.includes(problemActiveTab)) problemActiveTab = null; },
    refresh: refreshLogAnalysis,
  }));

  const contentEl = document.getElementById('problem-log-content');
  if (contentEl && problemActiveTab) {
    call('read_log_file', problemActiveTab).then(result => {
      contentEl.textContent = result && result.text !== undefined ? result.text : (result && result.error) || '읽을 수 없습니다.';
    });
  }
}

async function renderLogAnalysis() {
  const content = document.getElementById('content');
  content.innerHTML = `
    <div class="term-page">
      <h1 class="page-title">원본로그분석</h1>
      <p class="page-sub">원본 로그를 훑어 에러 구간을 찾고 이상 징후를 분석합니다.</p>
  
      <div class="card" style="display:flex; flex-direction:column; min-height:0; flex:1;">
        <div id="log-analysis" style="flex:1; display:flex; flex-direction:column; min-height:0;"></div>
      </div>
    </div>
  `;
  await refreshLogAnalysis();
}
