// ===== 분석(프로그램/로컬AI/클라우드AI) 백그라운드 진행 표시 =====
// 3개 분석은 백엔드에서 스레드로 돌기 때문에 어느 탭에 있든 계속 진행되며, 여기서는 상태를 폴링해
// '원본로그분석' 탭이 열려 있으면 버튼·요약에 최신 상태를 반영하고 끝난 것을 알린다. 상태바의 진행 표시와
// 취소는 작업 센터(js/job-center.js — IMPROVEMENT_PLAN 4-9)가 다른 작업과 함께 맡는다.

const ANALYSIS_KIND_LABEL = { program: '규칙기반 분석', local: 'Local AI 분석', cloud: 'Cloud AI 분석' };
let analysisJobsPrev = {};

function formatDuration(sec) {
  if (sec == null || !isFinite(sec)) return '--:--';
  const s = Math.max(0, Math.round(sec));
  const m = Math.floor(s / 60);
  const r = s % 60;
  return `${m}:${String(r).padStart(2, '0')}`;
}

// '원본로그분석' 등 탭이 현재 렌더되어 있을 때만 버튼/요약 영역을 갱신
function syncLogAnalysisPaneWithJobs(jobs) {
  const runBtn = document.getElementById('btn-run-log-analysis');
  const localBtn = document.getElementById('btn-analyze-local-ai');
  const cloudBtn = document.getElementById('btn-analyze-cloud-ai');
  if (!runBtn && !localBtn && !cloudBtn) return;

  const btnFor = { program: runBtn, local: localBtn, cloud: cloudBtn };
  for (const [kind, btn] of Object.entries(btnFor)) {
    if (!btn) continue;
    const job = jobs[kind];
    const isRunning = job && job.status === 'running';
    btn.disabled = isRunning;
    btn.classList.toggle('loading', isRunning);
  }

  const summaryEl = document.getElementById('log-analysis-summary');
  if (summaryEl) {
    const runnings = Object.entries(jobs).filter(([, j]) => j.status === 'running');
    if (runnings.length > 0) {
      summaryEl.innerHTML = runnings.map(([kind, job]) => 
        `<div style="font-size:12px;color:var(--sub);margin-bottom:2px;">
           ${ANALYSIS_KIND_LABEL[kind]} 진행 중 — ${job.total ? `${job.current}/${job.total}` : esc(job.message || '')} (경과 ${formatDuration(job.elapsed_sec)}, 남은 ${formatDuration(job.eta_sec)})
         </div>`
      ).join('');
    }
  }
}

// 한 번의 폴링: 상태 조회 -> 탭 반영 -> 끝남 감지. 주기 결정을 위해 jobs 를 반환한다.
async function pollAnalysisJobs() {
  const jobs = await call('get_analysis_jobs_status');
  if (!jobs) return null;

  syncLogAnalysisPaneWithJobs(jobs);

  for (const [kind, job] of Object.entries(jobs)) {
    const prev = analysisJobsPrev[kind];
    if (prev && prev.status === 'running' && ['done', 'error', 'cancelled'].includes(job.status)) {
      if (job.status === 'error') {
        alert(`${ANALYSIS_KIND_LABEL[kind]} 실패: ${job.error}`);
      }
      // 완료 시점에 '수집/채점' 탭이 열려 있으면 파일 목록/요약을 새로고침.
      if (typeof refreshLogAnalysis === 'function' && document.getElementById('log-analysis')) {
        await refreshLogAnalysis();
        const summaryEl = document.getElementById('log-analysis-summary');
        if (summaryEl && job.status === 'done') {
          if (kind === 'program' && job.results) {
            const totalProblems = job.results.reduce((sum, r) => sum + r.problem_count, 0);
            summaryEl.innerHTML = `<span style="font-size:12px;color:var(--sub);">분석 완료 — 원본 ${job.results.length}개 파일 중 ${job.results.filter(r => r.problem_count > 0).length}개에서 총 ${totalProblems}건 발견</span>`;
          } else if (job.results) {
            summaryEl.innerHTML = `<span style="font-size:12px;color:var(--sub);">${ANALYSIS_KIND_LABEL[kind]} 완료 — ${job.results.length}개 파일 처리</span>`;
          }
        } else if (summaryEl && job.status === 'cancelled') {
          // 작업 센터에서 취소했다 — 그때까지 분석한 파일은 남는다.
          summaryEl.innerHTML = `<span style="font-size:12px;color:var(--sub);">${ANALYSIS_KIND_LABEL[kind]} 취소됨 — ${(job.results || []).length}개 파일까지 처리했습니다.</span>`;
        }
      }
    }
  }
  analysisJobsPrev = jobs;
  return jobs;
}

// 진행 중인 분석이 없으면 5초 주기로 늘린다 — 세 작업(program/local/cloud)은 사용자가
// 버튼을 눌러야 시작되므로 대부분의 시간은 유휴다. 분석 시작 버튼 핸들러가
// analysisPoller.wake() 를 부르면 즉시 다시 빠른 주기로 돌아온다(완료 토스트가 늦으면 안 된다).
const analysisPoller = createAdaptivePoller({
  tick: pollAnalysisJobs,
  isBusy: (jobs) => Object.values(jobs).some(j => j.status === 'running'),
  activeMs: 1000,
  idleMs: 5000,
});
whenApiReady(() => analysisPoller.start());
