// ===== 워크스페이스(고객사/프로파일/Run 이력) =====
// 고객사/프로파일 자체의 선택/생성/이름변경/삭제는 이미 topbar의 #tb-context-selector +
// openCustomerProfileModal()(core-profile-modal.js)이 담당하므로 여기서는 그 버튼을 그대로
// 다시 트리거하기만 하고, 새로 추가되는 건 Run History/Workspace Information/Current Run
// Status/Recent Reports·Exports·Logs 조회 + 폴더 열기 + Archive Profile + 리포트 생성/
// 내보내기(백그라운드 실행)뿐이다.

const WORKSPACE_STATUS_BADGE = {
  READY: 'badge-neutral', RUNNING: 'badge-fail', PAUSED: 'badge-neutral',
  FAILED: 'badge-fail', ABORTED: 'badge-fail', COMPLETED: 'badge-pass',
};

async function renderWorkspace() {
  const content = document.getElementById('content');
  content.innerHTML = `
    <h1 class="page-title">워크스페이스</h1>
    <p class="page-sub">고객사/프로파일별 실행(Run) 이력과 산출물(리포트/내보내기/로그)을 한 곳에서 확인합니다.</p>

    <div class="card hoverable" style="margin-bottom:16px;">
      <div class="card-header">
        <div class="card-icon"><span class="material-symbols-rounded">business</span></div>
        <div>
          <p class="card-title" id="ws-customer-profile">-</p>
          <p class="card-desc">현재 활성 고객사 / 프로파일</p>
        </div>
      </div>
      <div style="display:flex;gap:8px;flex-wrap:wrap;">
        <button class="btn btn-outlined" id="ws-btn-switch"><span class="material-symbols-rounded">swap_horiz</span>고객사/프로파일 변경</button>
        <button class="btn btn-outlined" id="ws-btn-archive-profile"><span class="material-symbols-rounded">archive</span>프로파일 보관(Archive)</button>
        <button class="btn btn-outlined" id="ws-btn-open-workspace"><span class="material-symbols-rounded">folder_open</span>워크스페이스 폴더 열기</button>
      </div>
    </div>

    <div id="ws-progress" style="display:none;margin-bottom:16px;" class="card">
      <div style="display:flex;align-items:center;gap:10px;">
        <span class="material-symbols-rounded" style="animation:spin 800ms linear infinite;">progress_activity</span>
        <span id="ws-progress-label" style="font-size:13px;flex:1;"></span>
        <span id="ws-progress-time" style="font-size:12px;color:var(--sub);"></span>
      </div>
    </div>

    <div class="grid-cols-4" id="ws-kpi-row"></div>

    <div class="card hoverable section-gap">
      <div class="card-header">
        <div class="card-icon"><span class="material-symbols-rounded">bolt</span></div>
        <div><p class="card-title">현재 실행(Run) 작업</p><p class="card-desc">리포트 생성 / 내보내기 — 백그라운드로 실행되어 다른 탭으로 이동해도 계속 진행됩니다.</p></div>
      </div>
      <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;">
        <select id="ws-report-format" class="field" style="width:auto;">
          <option value="markdown">Markdown</option>
          <option value="docx">DOCX</option>
          <option value="pdf">PDF</option>
          <option value="html">HTML</option>
          <option value="excel">Excel</option>
          <option value="json_summary">JSON Summary</option>
        </select>
        <button class="btn btn-filled" id="ws-btn-gen-report"><span class="material-symbols-rounded">description</span>리포트 생성</button>
        <span style="width:1px;height:24px;background:var(--border);"></span>
        <select id="ws-export-kind" class="field" style="width:auto;">
          <option value="logs">로그(zip)</option>
          <option value="run">전체 Run(zip)</option>
          <option value="workspace">전체 워크스페이스(zip)</option>
        </select>
        <button class="btn btn-outlined" id="ws-btn-export"><span class="material-symbols-rounded">archive</span>내보내기</button>
      </div>
    </div>

    <div class="grid-cols-3 section-gap">
      ${wsRecentCard('reports', '최근 리포트', 'description', 'open_reports_folder')}
      ${wsRecentCard('exports', '최근 내보내기', 'archive', 'open_exports_folder')}
      ${wsRecentCard('logs', '최근 로그', 'receipt_long', 'open_logs_folder')}
    </div>

    <div class="card hoverable section-gap">
      <div class="card-header">
        <div class="card-icon"><span class="material-symbols-rounded">history</span></div>
        <div><p class="card-title">Run 이력</p><p class="card-desc">Customer / Profile / Run ID / 실행 시각 / 상태 / Health Score / 장비 수 / 커맨드 수 / 리포트 수</p></div>
      </div>
      <div style="overflow-x:auto;">
        <table class="dtable" id="ws-run-history-table">
          <thead><tr>
            <th>Customer</th><th>Profile</th><th>Run ID</th><th>실행 시각</th><th>상태</th>
            <th>Health</th><th>장비</th><th>커맨드</th><th>리포트</th><th></th>
          </tr></thead>
          <tbody id="ws-run-history-body"></tbody>
        </table>
      </div>
    </div>
  `;

  document.getElementById('ws-btn-switch').addEventListener('click', () => {
    if (typeof openCustomerProfileModal === 'function') openCustomerProfileModal();
  });
  document.getElementById('ws-btn-open-workspace').addEventListener('click', () => wsRunFolderAction('open_workspace_folder'));
  document.getElementById('ws-btn-archive-profile').addEventListener('click', wsArchiveProfile);
  document.getElementById('ws-btn-gen-report').addEventListener('click', wsGenerateReport);
  document.getElementById('ws-btn-export').addEventListener('click', wsExport);

  await refreshWorkspace();
}

function wsRecentCard(kind, title, icon, openFn) {
  return `
    <div class="card hoverable">
      <div class="card-header">
        <div class="card-icon"><span class="material-symbols-rounded">${icon}</span></div>
        <div><p class="card-title">${title}</p></div>
      </div>
      <ul id="ws-recent-${kind}" style="list-style:none;padding:0;margin:0;font-size:13px;color:var(--sub);max-height:160px;overflow-y:auto;"></ul>
      <button class="btn btn-outlined" style="margin-top:8px;width:100%;" data-open-fn="${openFn}">
        <span class="material-symbols-rounded">folder_open</span>폴더 열기
      </button>
    </div>`;
}

async function wsRunFolderAction(fn, ...args) {
  const result = await call(fn, ...args);
  if (result && result.error) showToast(result.error, 'error');
  else showToast('폴더를 열었습니다.', 'success');
}

async function wsArchiveProfile() {
  const overview = await call('get_workspace_overview');
  if (!overview || overview.error) { showToast(overview?.error || '활성 프로파일이 없습니다.', 'error'); return; }
  if (!confirm(`'${overview.customer} / ${overview.profile}' 프로파일을 보관(Archive)하시겠습니까?\n실행(Run) 기록은 보관 폴더로 이동되며, 목록에서는 사라집니다.`)) return;
  const btn = document.getElementById('ws-btn-archive-profile');
  btn.classList.add('loading');
  const result = await call('archive_profile', overview.customer, overview.profile);
  btn.classList.remove('loading');
  if (result && result.error) { showToast(result.error, 'error'); return; }
  showToast('프로파일을 보관했습니다.', 'success');
  await refreshWorkspace();
}

async function wsGenerateReport() {
  const format = document.getElementById('ws-report-format').value;
  const btn = document.getElementById('ws-btn-gen-report');
  btn.disabled = true;
  const result = await call('start_workspace_report', format);
  if (result && result.error) { showToast(result.error, 'error'); btn.disabled = false; return; }
  workspacePoller.wake();   // 유휴 주기(5초)에 걸려 진행 표시가 늦게 뜨는 것을 막는다
}

async function wsExport() {
  const kind = document.getElementById('ws-export-kind').value;
  const btn = document.getElementById('ws-btn-export');
  btn.disabled = true;
  const result = await call('start_workspace_export', kind, 'zip');
  if (result && result.error) { showToast(result.error, 'error'); btn.disabled = false; return; }
  workspacePoller.wake();
}

async function refreshWorkspace() {
  const overview = await call('get_workspace_overview');
  if (!document.getElementById('ws-customer-profile')) return; // 다른 탭으로 이동함
  if (!overview || overview.error) {
    document.getElementById('ws-customer-profile').textContent = overview?.error || '활성 고객사/프로파일이 없습니다.';
    document.getElementById('ws-kpi-row').innerHTML = '';
    document.getElementById('ws-run-history-body').innerHTML = '';
    return;
  }
  document.getElementById('ws-customer-profile').textContent = `${overview.customer} / ${overview.profile}`;

  const cur = overview.current_run;
  const kpiRow = document.getElementById('ws-kpi-row');
  if (cur) {
    kpiRow.innerHTML = `
      ${kpiCard('상태', cur.status, 'flag', cur.status === 'COMPLETED' ? 'success' : cur.status === 'FAILED' || cur.status === 'ABORTED' ? 'critical' : 'primary')}
      ${kpiCard('Health Score', cur.health_score ?? '-', 'monitor_heart', (cur.health_score ?? 100) >= 80 ? 'success' : (cur.health_score ?? 100) >= 50 ? 'warning' : 'critical')}
      ${kpiCard('장비 수', cur.device_count, 'dns', 'primary')}
      ${kpiCard('리포트 수', cur.report_count, 'description', 'primary')}
    `;
  } else {
    kpiRow.innerHTML = `<div class="card"><p class="card-desc">아직 실행(Run)이 없습니다.</p></div>`;
  }

  wsRenderRecentList('reports', overview.recent_reports);
  wsRenderRecentList('exports', overview.recent_exports);
  wsRenderRecentList('logs', overview.recent_logs);

  const openFnMap = { reports: 'open_reports_folder', exports: 'open_exports_folder', logs: 'open_logs_folder' };
  Object.entries(openFnMap).forEach(([kind, fn]) => {
    document.querySelectorAll(`[data-open-fn="${fn}"]`).forEach(btn => {
      btn.onclick = () => wsRunFolderAction(fn);
    });
  });

  const body = document.getElementById('ws-run-history-body');
  body.innerHTML = (overview.run_history || []).map(row => `
    <tr>
      <td>${row.customer}</td><td>${row.profile}</td><td>${row.run_id}</td>
      <td>${row.execution_time || '-'}</td>
      <td><span class="badge ${WORKSPACE_STATUS_BADGE[row.status] || 'badge-neutral'}">${row.status}</span></td>
      <td>${row.health_score ?? '-'}</td><td>${row.device_count}</td><td>${row.command_count}</td><td>${row.report_count}</td>
      <td><button class="btn btn-outlined ws-btn-open-run" data-run-id="${row.run_id}"><span class="material-symbols-rounded">folder_open</span></button></td>
    </tr>`).join('') || `<tr><td colspan="10" style="color:var(--sub);">실행(Run) 이력이 없습니다.</td></tr>`;
  body.querySelectorAll('.ws-btn-open-run').forEach(btn => {
    btn.addEventListener('click', () => wsRunFolderAction('open_current_run_folder'));
  });
}

function wsRenderRecentList(kind, items) {
  const el = document.getElementById(`ws-recent-${kind}`);
  if (!el) return;
  el.innerHTML = (items || []).map(name => `<li style="padding:3px 0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${name}</li>`).join('')
    || `<li style="padding:3px 0;">없음</li>`;
}

// ===== 워크스페이스 작업(리포트 생성/내보내기) 진행 표시 =====
// analysis-progress.js와 동일한 폴링 패턴(1초 간격) — 별도 job 슬롯(report/export)이라
// 상단바가 아니라 이 페이지 안의 #ws-progress 카드에 렌더링한다(다른 탭 이동 시 자동 정지 아님,
// 계속 폴링하되 DOM이 없으면 조용히 건너뜀 — syncLogAnalysisPaneWithJobs와 동일한 안전 패턴).
const WORKSPACE_JOB_LABEL = { inspection: '점검', parsing: '파싱', analysis: '분석', report: '리포트 생성', export: '내보내기' };
let workspaceJobsPrev = {};

let workspaceOverviewPrevStr = '';

// 한 번의 폴링: (워크스페이스 탭이 열려 있으면) 개요 갱신 -> 작업 상태 반영 -> 완료 감지.
// 주기 결정을 위해 jobs 를 반환한다.
//
// 개요 조회(get_workspace_overview)는 run 마다 session.json/metadata.json/health_score.json 을
// 읽고 리포트 목록까지 세는 무거운 호출이다(OPTIMIZATION_PLAN 3-3). 유휴 시 주기를 5초로
// 늘리면 이 호출 빈도가 그대로 1/5 이 된다 — 이 항목의 실질 이득 대부분이 여기서 나온다.
async function pollWorkspaceJobs() {
  if (typeof currentPage !== 'undefined' && currentPage === 'workspace') {
    const overview = await call('get_workspace_overview');
    const overviewStr = JSON.stringify(overview);
    if (overviewStr !== workspaceOverviewPrevStr) {
      workspaceOverviewPrevStr = overviewStr;
      refreshWorkspace();
    }
  }

  const jobs = await call('get_workspace_job_status');
  if (!jobs) return null;

  const wrap = document.getElementById('ws-progress');
  if (wrap) {
    const running = Object.entries(jobs).find(([, j]) => j.status === 'running');
    if (running) {
      const [kind, job] = running;
      wrap.style.display = 'block';
      document.getElementById('ws-progress-label').textContent =
        `${WORKSPACE_JOB_LABEL[kind] || kind} 진행 중 — ${job.message || ''}`;
      document.getElementById('ws-progress-time').textContent =
        job.elapsed_sec != null ? `경과 ${Math.round(job.elapsed_sec)}초` : '';
    } else {
      wrap.style.display = 'none';
    }
  }

  for (const [kind, job] of Object.entries(jobs)) {
    const prev = workspaceJobsPrev[kind];
    if (prev && prev.status === 'running' && (job.status === 'done' || job.status === 'error')) {
      if (job.status === 'error') {
        showToast(`${WORKSPACE_JOB_LABEL[kind] || kind} 실패: ${job.error}`, 'error');
      } else {
        showToast(`${WORKSPACE_JOB_LABEL[kind] || kind} 완료`, 'success');
      }
      ['ws-btn-gen-report', 'ws-btn-export'].forEach(id => {
        const btn = document.getElementById(id);
        if (btn) btn.disabled = false;
      });
      if (document.getElementById('ws-customer-profile')) await refreshWorkspace();
    }
  }
  workspaceJobsPrev = jobs;
  return jobs;
}

// 리포트 생성/내보내기가 돌지 않으면 5초 주기로 늘린다. 두 작업 모두 사용자가 버튼을
// 눌러야 시작되므로, 워크스페이스 탭을 열어 둔 채 아무것도 안 하는 시간이 대부분이다.
const workspacePoller = createAdaptivePoller({
  tick: pollWorkspaceJobs,
  isBusy: (jobs) => Object.values(jobs).some(j => j.status === 'running'),
  activeMs: 1000,
  idleMs: 5000,
}).start();
