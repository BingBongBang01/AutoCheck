// ===== Log Analysis (00_orignal_log -> FSM 이상탐지 -> 01_problem_log) =====
let problemLogFiles = [];
let problemActiveTab = null;
let problemSelectedPaths = new Set();
let problemLastClickedIdx = null;

async function refreshLogAnalysis() {
  problemLogFiles = await call('list_problem_log_files') || [];
  renderLogAnalysisPane();
}

function renderLogAnalysisPane() {
  const el = document.getElementById('log-analysis');
  if (!el) return;
  el.innerHTML = `
    <div style="display:flex;align-items:center;gap:10px;margin-bottom:10px;flex-wrap:wrap;">
      <button class="btn btn-primary" id="btn-run-log-analysis"><span class="material-symbols-rounded">play_arrow</span>분석 실행</button>
      <button class="btn btn-outlined" id="btn-analyze-local-ai"><span class="material-symbols-rounded">memory</span>Run Local AI Analysis</button>
      <button class="btn btn-outlined" id="btn-analyze-cloud-ai"><span class="material-symbols-rounded">cloud</span>Run Cloud AI Analysis</button>
      <span style="font-size:11px;color:var(--sub);">00_orignal_log의 원본을 훑어 에러 구간을 찾고, 커맨드/카테고리 문맥을 포함해 01_problem_log에 저장합니다.</span>
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
        ${problemLogFiles.length ? problemLogFiles.map(f => `
          <div class="connection-device log-file-row ${f.path === problemActiveTab ? 'session-active' : ''} ${problemSelectedPaths.has(f.path) ? 'selected' : ''}" data-select-path="${f.path}" style="cursor:pointer;">
            <div style="display:flex;flex-direction:column;">
              <span class="device-name">${f.name}</span>
              <span style="font-size:10px;color:var(--sub);">${f.mtime_str}</span>
            </div>
          </div>`).join('') : `<p style="font-size:12px;color:var(--sub);padding:8px;">아직 분석 결과가 없습니다 — '분석 실행'을 눌러 00_orignal_log를 분석하세요.</p>`}
      </div>
      <pre class="mono terminal" id="problem-log-content" style="flex:1;height:auto;white-space:pre-wrap;">${problemActiveTab ? '불러오는 중...' : '왼쪽 목록에서 결과 파일을 선택하세요.'}</pre>
    </div>
  `;

  // 분석은 백엔드 스레드에서 백그라운드로 진행된다 — 여기서는 시작만 요청하고 바로 반환하며,
  // 진행률/완료 처리는 js/analysis-progress.js의 폴링(pollAnalysisJobs)이 어느 탭에서든 담당한다.
  document.getElementById('btn-run-log-analysis').addEventListener('click', async (e) => {
    const btn = e.currentTarget;
    btn.classList.add('loading');
    const result = await call('start_log_analysis');
    if (result && result.error) { btn.classList.remove('loading'); alert(result.error); }
  });

  const startAiAnalysis = async (mode, btn) => {
    btn.classList.add('loading');
    const result = await call('start_ai_log_analysis', mode);
    if (result && result.error) { btn.classList.remove('loading'); alert(result.error); }
  };

  document.getElementById('btn-analyze-local-ai').addEventListener('click', (e) => startAiAnalysis('local', e.currentTarget));
  document.getElementById('btn-analyze-cloud-ai').addEventListener('click', (e) => startAiAnalysis('cloud', e.currentTarget));

  wireSelectableFileList({
    listElId: 'problem-file-list',
    files: problemLogFiles,
    selectedSet: problemSelectedPaths,
    lastClickedRef: { get idx() { return problemLastClickedIdx; }, set idx(v) { problemLastClickedIdx = v; } },
    onClickSelect: (path) => { problemActiveTab = path; },
    rerender: renderLogAnalysisPane,
  });

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
