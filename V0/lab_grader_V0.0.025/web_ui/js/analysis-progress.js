// ===== 분석(프로그램/로컬AI/클라우드AI) 백그라운드 진행 표시 =====
// 3개 분석은 백엔드에서 스레드로 돌기 때문에 어느 탭에 있든 계속 진행되며, 여기서는
// 1초 간격으로 상태를 폴링해 상단바(#sb-analysis-progress)에 진행바/경과·예상 시간을 그리고,
// '수집/채점' 탭이 열려 있으면 renderLogAnalysisPane()에도 최신 상태를 반영한다.

const ANALYSIS_KIND_LABEL = { program: '규칙기반 분석', local: 'Local AI 분석', cloud: 'Cloud AI 분석' };
let analysisJobsPrev = {};

function formatDuration(sec) {
  if (sec == null || !isFinite(sec)) return '--:--';
  const s = Math.max(0, Math.round(sec));
  const m = Math.floor(s / 60);
  const r = s % 60;
  return `${m}:${String(r).padStart(2, '0')}`;
}

function renderAnalysisStatusBar(jobs) {
  const wrap = document.getElementById('sb-analysis-progress');
  if (!wrap) return;
  const running = Object.entries(jobs).find(([, j]) => j.status === 'running');
  if (!running) {
    wrap.style.display = 'none';
    return;
  }
  const [kind, job] = running;
  const pct = job.total > 0 ? Math.min(100, Math.round((job.current / job.total) * 100)) : 0;
  wrap.style.display = 'flex';
  document.getElementById('sb-analysis-label').textContent =
    `${ANALYSIS_KIND_LABEL[kind] || kind} ${job.total ? `(${job.current}/${job.total})` : job.message || '진행 중'}`;
  document.getElementById('sb-analysis-bar').style.width = pct + '%';
  document.getElementById('sb-analysis-time').textContent =
    `경과 ${formatDuration(job.elapsed_sec)} / 남은 ${formatDuration(job.eta_sec)}`;
}

// '수집/채점' 탭이 현재 렌더되어 있을 때만 버튼/요약 영역을 갱신 — 다른 탭으로 이동해
// content DOM이 통째로 교체돼도 안전하도록 매번 getElementById로 존재를 확인한다.
function syncLogAnalysisPaneWithJobs(jobs) {
  const runBtn = document.getElementById('btn-run-log-analysis');
  const localBtn = document.getElementById('btn-analyze-local-ai');
  const cloudBtn = document.getElementById('btn-analyze-cloud-ai');
  if (!runBtn && !localBtn && !cloudBtn) return;

  const anyRunning = Object.values(jobs).some(j => j.status === 'running');
  const btnFor = { program: runBtn, local: localBtn, cloud: cloudBtn };
  for (const [kind, btn] of Object.entries(btnFor)) {
    if (!btn) continue;
    const job = jobs[kind];
    btn.disabled = anyRunning;
    btn.classList.toggle('loading', job.status === 'running');
  }

  const summaryEl = document.getElementById('log-analysis-summary');
  if (summaryEl) {
    const running = Object.entries(jobs).find(([, j]) => j.status === 'running');
    if (running) {
      const [kind, job] = running;
      summaryEl.innerHTML = `<span style="font-size:12px;color:var(--sub);">${ANALYSIS_KIND_LABEL[kind]} 진행 중 — ${job.total ? `${job.current}/${job.total}` : job.message || ''} (경과 ${formatDuration(job.elapsed_sec)}, 남은 ${formatDuration(job.eta_sec)})</span>`;
    }
  }
}

async function pollAnalysisJobs() {
  const jobs = await call('get_analysis_jobs_status');
  if (!jobs) return;

  renderAnalysisStatusBar(jobs);
  syncLogAnalysisPaneWithJobs(jobs);

  for (const [kind, job] of Object.entries(jobs)) {
    const prev = analysisJobsPrev[kind];
    if (prev && prev.status === 'running' && (job.status === 'done' || job.status === 'error')) {
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
        }
      }
    }
  }
  analysisJobsPrev = jobs;
}

setInterval(pollAnalysisJobs, 1000);
pollAnalysisJobs();
