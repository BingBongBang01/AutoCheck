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
  const runnings = Object.entries(jobs).filter(([, j]) => j.status === 'running');
  if (runnings.length === 0) {
    wrap.style.display = 'none';
    return;
  }
  wrap.style.display = 'flex';
  
  for (const [kind, job] of runnings) {
    let el = document.getElementById(`sb-job-${kind}`);
    if (!el) {
      el = document.createElement('div');
      el.id = `sb-job-${kind}`;
      el.style.cssText = 'display:flex;align-items:center;gap:6px;min-width:0;margin-right:12px;';
      el.innerHTML = `
        <span class="job-label" style="font-size:11px;color:var(--sub);white-space:nowrap;"></span>
        <div style="width:80px;height:6px;background:var(--border);border-radius:3px;overflow:hidden;">
          <div class="job-bar" style="height:100%;width:0%;background:var(--primary);transition:width .25s;"></div>
        </div>
      `;
      wrap.appendChild(el);
    }
    const pct = job.total > 0 ? Math.min(100, Math.round((job.current / job.total) * 100)) : 0;
    el.querySelector('.job-label').textContent = `${ANALYSIS_KIND_LABEL[kind]} ${pct}%`;
    el.querySelector('.job-bar').style.width = pct + '%';
  }
  // 완료되거나 사라진 작업 제거
  Array.from(wrap.children).forEach(child => {
    const kind = child.id.replace('sb-job-', '');
    if (!jobs[kind] || jobs[kind].status !== 'running') {
      child.remove();
    }
  });
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
           ${ANALYSIS_KIND_LABEL[kind]} 진행 중 — ${job.total ? `${job.current}/${job.total}` : job.message || ''} (경과 ${formatDuration(job.elapsed_sec)}, 남은 ${formatDuration(job.eta_sec)})
         </div>`
      ).join('');
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
