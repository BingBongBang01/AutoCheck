// ===== Log Analysis (점검 회차의 raw -> FSM 이상탐지 -> 같은 회차의 problem) =====
let problemLogFiles = [];
let problemActiveTab = null;
let problemSelectedPaths = new Set();
let problemLastClickedIdx = null;
let problemExpandedRuns = new Set(); // 펼쳐진 점검 회차(run_id) — js/log-run-groups.js

let logAnalysisPollTimer = null;

function startLogAnalysisPolling() {
  if (logAnalysisPollTimer) clearInterval(logAnalysisPollTimer);
  onPageLeave(() => { clearInterval(logAnalysisPollTimer); logAnalysisPollTimer = null; });
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
  await loadLogRuns();
  problemLogFiles = await call('list_problem_log_files') || [];
  renderLogAnalysisPane();
  startLogAnalysisPolling();
}

function renderLogAnalysisPane() {
  const el = document.getElementById('log-analysis');
  if (!el) return;
  el.innerHTML = `
    <div style="display:flex;align-items:center;gap:10px;margin-bottom:10px;flex-wrap:wrap;">
      ${logRunSelectMarkup('log-analysis-run')}
      <button class="btn btn-primary" id="btn-run-log-analysis"><span class="material-symbols-rounded">play_arrow</span>분석 실행</button>
      <button class="btn btn-outlined" id="btn-analyze-local-ai"><span class="material-symbols-rounded">memory</span>Run Local AI Analysis</button>
      <button class="btn btn-outlined" id="btn-analyze-cloud-ai"><span class="material-symbols-rounded">cloud</span>Run Cloud AI Analysis</button>
      <button class="btn btn-outlined" id="btn-realtime-baseline"><span class="material-symbols-rounded">radar</span>실시간 감시</button>
      <button class="btn btn-outlined" id="btn-goto-report"><span class="material-symbols-rounded">summarize</span>보고서 만들기</button>
      <button class="btn btn-outlined" id="btn-open-problem-folder" title="분석 결과 폴더 열기"><span class="material-symbols-rounded">folder_open</span>폴더보기</button>
      <span style="font-size:11px;color:var(--sub);">고른 점검 회차의 원본 로그를 훑어 에러 구간을 찾고, 커맨드/카테고리 문맥을 포함해 같은 회차의 분석 결과 폴더에 저장합니다.</span>
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
          <div class="${logFileRowClass(f.path === problemActiveTab, problemSelectedPaths.has(f.path))}" data-select-path="${esc(f.path)}" style="cursor:pointer;">
            <div style="display:flex;flex-direction:column;min-width:0;">
              <span class="device-name">${esc(f.name)}</span>
              <span style="font-size:10px;color:var(--sub);">${esc(f.mtime_str)}</span>
            </div>
            ${logFileActiveBadge(f.path === problemActiveTab)}
          </div>`) : `<p style="font-size:12px;color:var(--sub);padding:8px;">아직 분석 결과가 없습니다 — '분석 실행'을 눌러 점검 원본 로그를 분석하세요.</p>`}
      </div>
      ${logViewMarkup('problem-log-content', problemActiveTab ? '불러오는 중...' : '왼쪽 목록에서 결과 파일을 선택하세요.')}
    </div>
  `;

  // 분석은 백엔드 스레드에서 백그라운드로 진행된다 — 여기서는 시작만 요청하고 바로 반환하며,
  // 진행률/완료 처리는 js/analysis-progress.js의 폴링(pollAnalysisJobs)이 어느 탭에서든 담당한다.
  document.getElementById('btn-run-log-analysis').addEventListener('click', async (e) => {
    const btn = e.currentTarget;
    btn.classList.add('loading');
    if (typeof analysisJobsPrev !== 'undefined') analysisJobsPrev['program'] = {status: 'running'};
    const result = await call('start_log_analysis', effectiveLogRunId());
    if (result && result.error) { btn.classList.remove('loading'); alert(result.error); }
    // 폴러를 즉시 깨운다 — 유휴 상태였다면 대기 타이머가 5초에 걸려 있어서, 이걸 하지 않으면
    // 진행바가 한 번 늦게 뜬다. wake() 는 대기를 취소하고 지금 한 번 돌린 뒤 빠른 주기로 잇는다.
    if (typeof analysisPoller !== 'undefined') analysisPoller.wake();
    else if (typeof pollAnalysisJobs === 'function') pollAnalysisJobs();
  });

  const runAiAnalysis = async (mode, btn, previewId, runId) => {
    btn.classList.add('loading');
    if (typeof analysisJobsPrev !== 'undefined') analysisJobsPrev[mode] = {status: 'running'};
    const result = await call('start_ai_log_analysis', mode, previewId, runId);
    if (result && result.error) { btn.classList.remove('loading'); alert(result.error); }
    // 폴러를 즉시 깨운다 — 유휴 상태였다면 대기 타이머가 5초에 걸려 있어서, 이걸 하지 않으면
    // 진행바가 한 번 늦게 뜬다. wake() 는 대기를 취소하고 지금 한 번 돌린 뒤 빠른 주기로 잇는다.
    if (typeof analysisPoller !== 'undefined') analysisPoller.wake();
    else if (typeof pollAnalysisJobs === 'function') pollAnalysisJobs();
  };

  // 이 PC 밖으로 나가는 분석(클라우드, 다른 PC 의 로컬 AI)은 보낼 내용을 먼저 보여 주고 승인을 받는다 —
  // 서버가 만들어 맡겨 둔 미리보기를 승인하면 바로 그 내용만 보낸다(IMPROVEMENT_PLAN 4-2).
  // 미리보기와 실행은 같은 회차여야 한다 — 승인한 내용이 그 회차의 것이다(서버도 확인한다).
  const startAiAnalysis = async (mode, btn) => {
    btn.classList.add('loading');
    const runId = effectiveLogRunId();
    const preview = await call('preview_ai_transmission', mode, runId);
    if (!preview || preview.error) {
      btn.classList.remove('loading');
      alert((preview && preview.error) || '보낼 내용을 준비하지 못했습니다.');
      return;
    }
    if (preview.needs_approval === false) { await runAiAnalysis(mode, btn, null, runId); return; }
    btn.classList.remove('loading');
    openAiTransmissionModal(preview, () => runAiAnalysis(mode, btn, preview.preview_id, runId));
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
    const res = await call('open_inspection_log_folder', 'problem', effectiveLogRunId());
    if (res && res.error) alert(res.error);
  });

  wireLogRunSelect('log-analysis-run');

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
  if (contentEl && problemActiveTab) showLogFile('problem-log-content', problemActiveTab);
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

// ===== 외부 AI 전송 미리보기·승인 (IMPROVEMENT_PLAN 4-2) =====
// 화면에는 서버가 만든 요약만 온다. 파일별 실제 내용(가린 뒤의 프롬프트 전체)은 '내용 보기'를 누를 때 받아
// textContent 로 넣는다 — 수 MB 가 될 수 있고, 로그가 HTML 로 해석되면 안 된다. 승인 버튼은 미리보기 id 만
// 돌려보낸다: 보낼 내용은 서버가 맡아 두고 있어서 화면이 고칠 수 없다.
function aiTransmissionHtml(p) {
  const dest = p.destination || {};
  const keys = dest.keys || [];
  const files = p.files || [];
  const skipped = p.skipped || [];
  const totals = p.totals || {};
  const flagged = files.filter(f => (f.unmasked || []).length);
  const num = (n) => Number(n || 0).toLocaleString();
  const title = p.mode === 'local' ? '다른 PC의 로컬 AI로 보내기 전 확인' : '클라우드 AI로 보내기 전 확인';
  return `
    <div class="customer-modal card" style="max-width:820px;height:auto;max-height:90vh;display:flex;flex-direction:column;">
      <div class="customer-modal-header">
        <div>
          <p class="card-title">${title}</p>
          <p class="card-desc">아래 내용이 이 PC 밖으로 나갑니다. 승인하면 이 내용만 보냅니다.</p>
        </div>
        <button class="btn btn-outlined" data-modal-close>취소</button>
      </div>
      <div style="padding:12px 16px;overflow:auto;flex:1;min-height:0;display:flex;flex-direction:column;gap:8px;font-size:12px;">
        <div><b>받는 곳</b> ${esc(dest.label)} · ${esc(dest.host)} · 모델 ${esc(dest.model)}</div>
        ${keys.length > 1 ? `<div><b>키 순서</b> ${keys.map((k, i) => `${i + 1}. ${esc(k.name)}(${esc(k.model)})`).join(' → ')}
          <span style="color:var(--sub);">— 앞의 키가 거부되거나 요청 한도에 걸리면 다음 키로 보냅니다(같은 제공자 안에서만).</span></div>` : ''}
        ${dest.fallback ? `<div style="color:var(--sub);">Anthropic 안전 분류기가 거절하면 Anthropic 이 권장하는 다른 Claude 모델이 같은 요청을 처리할 수 있습니다(같은 Anthropic API 안).</div>` : ''}
        <div><b>항상 가리는 항목</b> ${(p.masking || []).map(esc).join(', ')}</div>
        <div><b>보낼 양</b> 파일 ${num(totals.files)}개 · 요청 ${num(totals.requests)}건 · ${num(totals.chars)}자</div>
        ${skipped.length ? `<div style="color:var(--sub);">이상 징후가 없어 보내지 않는 파일 ${skipped.length}개: ${skipped.map(esc).join(', ')}</div>` : ''}
        ${flagged.length ? `<div style="color:var(--warning);">가린 뒤에도 비밀값처럼 보이는 것이 남은 파일이 ${flagged.length}개 있습니다 — '내용 보기'로 확인하고, 보내면 안 되면 취소하세요.</div>` : ''}
        ${(p.warnings || []).map(w => `<div style="color:var(--warning);">${esc(w)}</div>`).join('')}
        <div style="display:flex;flex-direction:column;gap:6px;">
          ${files.map(f => `
            <div style="border:var(--line);border-radius:var(--radius-sm);padding:6px 8px;">
              <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;">
                <span style="flex:1;min-width:160px;word-break:break-all;">${esc(f.source)}</span>
                <span style="color:var(--sub);">요청 ${num(f.requests)}건 · ${num(f.chars)}자</span>
                ${(f.unmasked || []).length ? `<span style="color:var(--warning);">${esc(f.unmasked.join(', '))}</span>` : ''}
                <button class="btn btn-outlined" data-ai-tx-part="${esc(f.index)}" style="height:24px;padding:0 8px;font-size:11px;">내용 보기</button>
              </div>
              <pre class="mono selectable-log" data-ai-tx-text="${esc(f.index)}" style="display:none;white-space:pre-wrap;max-height:260px;overflow:auto;margin:6px 0 0;"></pre>
            </div>`).join('')}
        </div>
        <div style="color:var(--sub);">미리보기는 ${Math.round((p.expires_in_sec || 600) / 60)}분 동안 유효합니다. 승인하면 승인 이력(시각·사용자·받는 곳·요청 수·내용의 SHA-256 — 내용 자체는 남기지 않음)이 남습니다.</div>
      </div>
      <div style="display:flex;justify-content:flex-end;gap:8px;padding:10px 14px;border-top:var(--line);">
        <button class="btn btn-outlined" data-modal-close>취소</button>
        <button class="btn btn-primary" data-ai-tx-approve>승인하고 보내기</button>
      </div>
    </div>`;
}

function openAiTransmissionModal(preview, onApprove) {
  const { overlay, close } = openModal({ id: 'ai-tx-modal', html: aiTransmissionHtml(preview) });
  overlay.querySelectorAll('[data-ai-tx-part]').forEach(btn => btn.addEventListener('click', async () => {
    const pre = overlay.querySelector(`[data-ai-tx-text="${CSS.escape(btn.dataset.aiTxPart)}"]`);
    if (!pre) return;
    if (pre.style.display !== 'none') { pre.style.display = 'none'; return; }
    pre.style.display = '';
    if (pre.dataset.loaded) return;
    pre.textContent = '불러오는 중...';
    const part = await call('get_ai_transmission_part', preview.preview_id, Number(btn.dataset.aiTxPart));
    if (part && typeof part.text === 'string') {
      pre.textContent = part.text;
      pre.dataset.loaded = '1';
    } else {
      pre.textContent = (part && part.error) || '불러오지 못했습니다.';
    }
  }));
  overlay.querySelector('[data-ai-tx-approve]').addEventListener('click', () => { close(); onApprove(); });
}
