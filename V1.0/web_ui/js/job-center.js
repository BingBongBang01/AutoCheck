// ===== 작업 센터 (IMPROVEMENT_PLAN 4-9) =====
// 백그라운드 작업 — 세션 점검, 무인 정기점검 '지금 실행', 분석 세 가지(규칙·로컬 AI·클라우드 AI), 워크스페이스 보고서·
// 내보내기 — 의 진행·취소·완료 이력을 상태바 한곳에서 보인다(get_job_center, api/job_center_api.py). 작업을 시작하는
// 곳은 그대로 각 탭이다. 예전엔 상태바에 점검·분석 진행바가 따로 있었고, 보고서·내보내기는 워크스페이스 탭에서만
// 보였고, 분석은 멈출 방법이 없었다.
//
// 상태바 버튼: 도는 작업이 있으면 첫 작업의 이름·진행률(외 N), 방금 끝난 작업은 10초 동안 그 결과, 창을 열어 보지 않은
// 오류가 있으면 빨간 표시, 그 밖에는 '작업'. 누르면 위로 작업 센터 창이 열린다(진행 중 + 이번 실행에서 끝난 작업).
const JC_STATUS = {
  done: { label: '완료', tone: 'ok', icon: 'check_circle' },
  cancelled: { label: '취소됨', tone: 'warn', icon: 'cancel' },
  error: { label: '오류', tone: 'fail', icon: 'error' },
};
const JC_FLASH_MS = 10000;
const JC_CONFIRM_MS = 4000;
let jcState = { jobs: [], running: 0, history: [] };
let jcOpen = false;
let jcKnown = null;        // 직전 폴링까지 본 끝난 작업의 키 — 새로 끝난 것을 찾는다(첫 폴링은 기준만 세운다)
let jcFlash = null;        // {entry, until} — 방금 끝난 작업을 상태바에 잠깐 보인다
let jcErrorsSeenAt = 0;    // 창을 마지막으로 연 시각(초) — 그 뒤에 난 오류만 상태바에 남긴다
let jcConfirming = null;   // {id, until} — '취소'를 한 번 누른 작업(한 번 더 눌러야 취소한다)

function jcKey(entry) { return `${entry.id}#${entry.seq}`; }

function jcPercent(job) {
  return job.total > 0 ? Math.min(100, Math.round((job.current / job.total) * 100)) : null;
}

// 진행 중 작업의 한 줄 — '3/12 · 경과 1:20 · 남은 4:01 · 대기 1'
function jcProgressText(job) {
  const parts = [];
  if (job.total > 0) parts.push(`${job.current}/${job.total}`);
  parts.push(`경과 ${formatDuration(job.elapsed_sec)}`);
  if (job.eta_sec != null) parts.push(`남은 ${formatDuration(job.eta_sec)}`);
  if (job.queued) parts.push(`대기 ${job.queued}`);
  return parts.join(' · ');
}

// 끝난 시각 — 오늘이면 'HH:MM:SS', 다른 날이면 'MM-DD HH:MM'.
function jcClock(epoch) {
  if (!epoch) return '';
  const d = new Date(epoch * 1000);
  const pad = (n) => String(n).padStart(2, '0');
  const today = new Date();
  return d.toDateString() === today.toDateString()
    ? `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
    : `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

// 상태바 버튼에 무엇을 보일지 — 도는 작업 > 방금 끝난 작업 > 확인하지 않은 오류 > 쉼. now 는 ms.
function jcIndicator(state, flash, errorsSeenAt, now) {
  const running = state.jobs || [];
  if (running.length) {
    const first = running[0];
    const pct = jcPercent(first);
    return { tone: 'busy', icon: 'sync', pct,
             text: `${first.label}${pct != null ? ` ${pct}%` : ''}${running.length > 1 ? ` 외 ${running.length - 1}` : ''}` };
  }
  if (flash && flash.until > now) {
    const status = JC_STATUS[flash.entry.status] || JC_STATUS.done;
    return { tone: status.tone, icon: status.icon, pct: null, text: `${flash.entry.label} ${status.label}` };
  }
  const errors = (state.history || []).filter(e => e.status === 'error' && (e.end_ts || 0) > errorsSeenAt);
  if (errors.length) return { tone: 'fail', icon: 'error', pct: null, text: `작업 오류 ${errors.length}건` };
  return { tone: 'idle', icon: 'task_alt', pct: null, text: '작업' };
}

function renderJobCenterIndicator() {
  const btn = document.getElementById('sb-jobs');
  if (!btn) return;
  const view = jcIndicator(jcState, jcFlash, jcErrorsSeenAt, Date.now());
  btn.dataset.tone = view.tone;
  btn.innerHTML = `<span class="material-symbols-rounded">${view.icon}</span><span class="sb-jobs-text">${esc(view.text)}</span>`
    + (view.pct != null ? `<span class="sb-jobs-bar"><span style="width:${view.pct}%"></span></span>` : '');
}

function jcJobRow(job) {
  const pct = jcPercent(job);
  const confirming = jcConfirming && jcConfirming.id === job.id && jcConfirming.until > Date.now();
  const cancel = !job.cancellable ? '<span class="jc-note">멈출 수 없음</span>'
    : job.cancel_requested ? '<span class="jc-note">취소 요청됨</span>'
    : `<button class="btn btn-outlined rtm-mini${confirming ? ' jc-confirm' : ''}" type="button"
               data-jc-cancel="${esc(job.id)}">${confirming ? '한 번 더 누르면 취소' : '취소'}</button>`;
  const message = job.cancel_requested ? (job.last_log || '') : (job.message || job.last_log || '');
  return `<div class="jc-job">
      <div class="jc-row"><span class="jc-group">${esc(job.group)}</span><b>${esc(job.label)}</b>
        <span style="flex:1"></span>${cancel}</div>
      <div class="jc-bar${pct == null ? ' jc-bar-busy' : ''}"><span style="width:${pct == null ? 100 : pct}%"></span></div>
      <div class="jc-meta">${esc(jcProgressText(job))}</div>
      ${message ? `<div class="jc-msg" title="${esc(message)}">${esc(message)}</div>` : ''}
    </div>`;
}

function jcHistoryRow(entry) {
  const status = JC_STATUS[entry.status] || JC_STATUS.done;
  const took = entry.start_ts && entry.end_ts ? formatDuration(entry.end_ts - entry.start_ts) : '';
  const detail = entry.status === 'error' ? (entry.error || '') : (entry.summary || '');
  return `<div class="jc-hist">
      <span class="rtm-chip rtm-chip-${status.tone}">${status.label}</span>
      <div class="jc-hist-body">
        <div><b>${esc(entry.label)}</b> <span class="jc-meta">${esc(jcClock(entry.end_ts))}${took ? ` · ${esc(took)}` : ''}</span></div>
        ${detail ? `<div class="jc-msg" title="${esc(detail)}">${esc(detail)}</div>` : ''}
      </div>
    </div>`;
}

function renderJobCenterPanel() {
  const panel = document.getElementById('jc-panel');
  if (!panel) return;
  const jobs = jcState.jobs || [];
  const history = jcState.history || [];
  panel.innerHTML = `
    <div class="jc-row"><b class="jc-title">작업 센터</b><span style="flex:1"></span>
      <button class="btn btn-outlined rtm-mini" type="button" id="jc-close">닫기</button></div>
    <div class="jc-section">진행 중${jobs.length ? ` ${jobs.length}` : ''}</div>
    ${jobs.length ? jobs.map(jcJobRow).join('') : '<div class="jc-empty">진행 중인 작업이 없습니다.</div>'}
    <div class="jc-section">이번 실행에서 끝난 작업</div>
    ${history.length ? history.map(jcHistoryRow).join('') : '<div class="jc-empty">아직 끝난 작업이 없습니다.</div>'}`;
  panel.querySelector('#jc-close').addEventListener('click', closeJobCenter);
  panel.querySelectorAll('[data-jc-cancel]').forEach(btn => btn.addEventListener('click', () => jcCancel(btn)));
}

// '취소'는 두 번 눌러야 한다 — 긴 점검·분석을 실수로 멈추지 않게(멈춰도 그때까지의 결과는 남는다).
async function jcCancel(btn) {
  const id = btn.dataset.jcCancel;
  if (!(jcConfirming && jcConfirming.id === id && jcConfirming.until > Date.now())) {
    jcConfirming = { id, until: Date.now() + JC_CONFIRM_MS };
    renderJobCenterPanel();
    return;
  }
  jcConfirming = null;
  btn.disabled = true;
  const result = await call('cancel_job', id);
  if (result && result.error) showToast(result.error, 'error');
  else if (result) showToast('취소를 요청했습니다 — 하던 단계를 마치고 멈춥니다. 그때까지의 결과는 남습니다.');
  await pollJobCenter();
}

function openJobCenter() {
  const btn = document.getElementById('sb-jobs');
  if (!btn) return;
  let panel = document.getElementById('jc-panel');
  if (!panel) {
    panel = document.createElement('div');
    panel.id = 'jc-panel';
    panel.className = 'card jc-panel';
    document.body.appendChild(panel);
  }
  const rect = btn.getBoundingClientRect();
  panel.style.left = `${Math.max(8, Math.min(rect.left, window.innerWidth - 436))}px`;
  panel.style.bottom = `${Math.max(8, window.innerHeight - rect.top + 6)}px`;
  jcOpen = true;
  jcErrorsSeenAt = Date.now() / 1000;
  renderJobCenterPanel();
  renderJobCenterIndicator();
  document.addEventListener('mousedown', jcOutsideClick);
  document.addEventListener('keydown', jcEscapeKey);
  jobCenterPoller.wake();
}

function closeJobCenter() {
  jcOpen = false;
  jcConfirming = null;
  document.getElementById('jc-panel')?.remove();
  document.removeEventListener('mousedown', jcOutsideClick);
  document.removeEventListener('keydown', jcEscapeKey);
}

function toggleJobCenter() {
  if (jcOpen) closeJobCenter();
  else openJobCenter();
}

function jcOutsideClick(e) {
  if (e.target.closest('#jc-panel') || e.target.closest('#sb-jobs')) return;
  closeJobCenter();
}

function jcEscapeKey(e) {
  if (e.key === 'Escape') closeJobCenter();
}

async function pollJobCenter() {
  const state = await call('get_job_center');
  if (!state || state.error) return null;
  const history = state.history || [];
  if (jcKnown) {
    const fresh = history.filter(e => !jcKnown.has(jcKey(e)));
    if (fresh.length) jcFlash = { entry: fresh[0], until: Date.now() + JC_FLASH_MS };
  }
  jcKnown = new Set(history.map(jcKey));
  jcState = state;
  renderJobCenterIndicator();
  if (jcOpen) renderJobCenterPanel();
  return state;
}

// 도는 작업이 있거나, 창이 열려 있거나, 방금 끝난 결과를 보이는 동안은 1초. 아니면 2초 — 다른 탭에서 시작한 작업이
// 상태바에 늦게 나타나지 않게 짧게 둔다(상태 몇 개를 읽는 가벼운 호출이다).
const jobCenterPoller = createAdaptivePoller({
  tick: pollJobCenter,
  isBusy: (state) => state.running > 0 || jcOpen || !!(jcFlash && jcFlash.until > Date.now()),
  activeMs: 1000,
  idleMs: 2000,
});
document.getElementById('sb-jobs')?.addEventListener('click', toggleJobCenter);
whenApiReady(() => jobCenterPoller.start());
