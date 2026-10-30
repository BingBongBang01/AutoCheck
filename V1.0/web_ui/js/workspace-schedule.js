// ===== 워크스페이스 — 무인 정기점검 (IMPROVEMENT_PLAN 4-3) =====
// 활성 프로파일의 일정을 저장하면 Windows 작업 스케줄러에 등록된다(꺼 두면 지운다). 정해진 시각에 창 없이
// 수집 → 규칙 분석 → 보고서를 만들고, 결과는 아래 실행 이력과 사이드바 '워크스페이스'의 실패 배지로 알린다.
// 카드는 바뀐 것이 있을 때만 다시 그린다 — 주기적으로 통째로 그리면 고치던 입력이 지워진다.

const WS_SCHEDULE_STATUS = {
  success: ['성공', 'badge-pass'], partial: ['일부 실패', 'badge-warn'],
  failed: ['실패', 'badge-fail'], skipped: ['건너뜀', 'badge-neutral'],
};
const WS_WEEKDAYS = ['월', '화', '수', '목', '금', '토', '일'];
const WS_FREQUENCIES = [['daily', '매일'], ['weekly', '매주'], ['monthly', '매월']];

let wsSchedulePoller = null;
let wsScheduleSig = null;
let wsScheduleWasRunning = false;

function wsScheduleStatusLine(info) {
  const s = info.schedule || {};
  if (!info.scheduler_available) {
    return `<span style="color:var(--warning);">이 PC 에서는 Windows 작업 스케줄러를 쓸 수 없어 등록되지 않습니다.</span>`;
  }
  if (!info.exists) return `<span style="color:var(--sub);">저장된 일정이 없습니다.</span>`;
  if (!s.enabled) return `<span style="color:var(--sub);">꺼져 있음 — 작업 스케줄러에 등록하지 않습니다.</span>`;
  if (info.registered) {
    return `<span style="color:var(--success);">작업 스케줄러에 등록됨 · 다음 실행 ${esc(info.next_run || '-')}</span>`;
  }
  return `<span style="color:var(--critical);">작업 스케줄러에 등록되지 않음 — '저장'을 다시 눌러 등록하세요.</span>`;
}

function wsScheduleHtml(info, alerts) {
  const s = info.schedule || {};
  const weekdays = new Set(s.weekdays || []);
  const formats = new Set(s.formats || []);
  const history = info.history || [];
  const alertItems = (alerts && alerts.items) || [];
  const busy = info.running ? 'disabled' : '';
  const statusOf = (r) => WS_SCHEDULE_STATUS[r.status] || [r.status || '-', 'badge-neutral'];
  return `
    ${alertItems.length ? `
      <div style="border-left:3px solid var(--critical);background:var(--hover);padding:8px 10px;margin-bottom:10px;border-radius:var(--radius-sm);">
        <div style="display:flex;align-items:center;gap:8px;font-size:12.5px;">
          <b>확인하지 않은 무인 정기점검 실패 ${esc(alerts.count)}건</b><span style="flex:1"></span>
          <button class="btn btn-outlined" id="ws-sch-ack" style="height:26px;padding:0 10px;font-size:11px;">확인</button>
        </div>
        ${alertItems.map(a => `<div style="font-size:12px;color:var(--sub);margin-top:4px;">
          ${esc((a.finished_at || '').replace('T', ' '))} · ${esc(a.customer)} / ${esc(a.profile)} — ${esc(statusOf(a)[0])}: ${esc(a.message)}</div>`).join('')}
      </div>` : ''}
    <div style="display:flex;gap:12px;flex-wrap:wrap;align-items:center;font-size:12.5px;">
      <label style="display:inline-flex;align-items:center;gap:4px;"><input type="checkbox" id="ws-sch-enabled" ${s.enabled ? 'checked' : ''} ${busy}>사용</label>
      <select id="ws-sch-frequency" class="field" style="width:auto;" ${busy}>
        ${WS_FREQUENCIES.map(([value, label]) => `<option value="${esc(value)}" ${s.frequency === value ? 'selected' : ''}>${label}</option>`).join('')}
      </select>
      <span id="ws-sch-weekdays" style="display:${s.frequency === 'weekly' ? 'inline-flex' : 'none'};gap:6px;">
        ${WS_WEEKDAYS.map((day, i) => `<label style="display:inline-flex;align-items:center;gap:2px;"><input type="checkbox" data-sch-weekday="${i}" ${weekdays.has(i) ? 'checked' : ''} ${busy}>${day}</label>`).join('')}
      </span>
      <span id="ws-sch-monthday-wrap" style="display:${s.frequency === 'monthly' ? 'inline-flex' : 'none'};align-items:center;gap:4px;">
        <select id="ws-sch-monthday" class="field" style="width:auto;" ${busy}>
          ${Array.from({ length: 28 }, (_, i) => i + 1).map(day => `<option value="${esc(day)}" ${Number(s.monthday) === day ? 'selected' : ''}>${day}</option>`).join('')}
        </select>일
      </span>
      <input type="time" id="ws-sch-time" class="field" style="width:auto;" value="${esc(s.time || '02:00')}" ${busy}>
      <span style="display:inline-flex;gap:8px;">
        ${(info.formats || []).map(f => `<label style="display:inline-flex;align-items:center;gap:2px;"><input type="checkbox" data-sch-format="${esc(f.id)}" ${formats.has(f.id) ? 'checked' : ''} ${busy}>${esc(f.label)}</label>`).join('')}
      </span>
    </div>
    <div style="margin-top:8px;font-size:12px;">${wsScheduleStatusLine(info)}${info.description ? ` <span style="color:var(--sub);">(${esc(info.description)})</span>` : ''}</div>
    ${info.running ? `<div style="margin-top:6px;font-size:12px;color:var(--primary);">실행 중 — ${esc((info.job && info.job.status === 'running' && info.job.message) || '작업 스케줄러가 실행한 점검이 진행 중입니다')}</div>` : ''}
    <div style="display:flex;gap:8px;margin-top:10px;flex-wrap:wrap;">
      <button class="btn btn-filled" id="ws-sch-save" ${busy}><span class="material-symbols-rounded">save</span>저장</button>
      <button class="btn btn-outlined" id="ws-sch-run" ${info.exists && !info.running ? '' : 'disabled'}><span class="material-symbols-rounded">play_arrow</span>지금 실행</button>
      <button class="btn btn-outlined" id="ws-sch-delete" ${info.exists && !info.running ? '' : 'disabled'}><span class="material-symbols-rounded">delete</span>일정 삭제</button>
    </div>
    <div style="overflow-x:auto;margin-top:12px;">
      <table class="dtable">
        <thead><tr><th>시작</th><th>실행</th><th>상태</th><th>내용</th><th>보고서</th></tr></thead>
        <tbody>${history.length ? history.map(r => `<tr>
          <td style="white-space:nowrap;">${esc((r.started_at || '').replace('T', ' '))}</td>
          <td>${r.trigger === 'manual' ? '지금 실행' : '예약'}</td>
          <td><span class="badge ${statusOf(r)[1]}">${esc(statusOf(r)[0])}</span></td>
          <td>${esc(r.message)}${Object.keys(r.devices_failed || {}).length ? `<div style="font-size:11px;color:var(--sub);">수집 실패: ${Object.entries(r.devices_failed).map(([name, error]) => `${esc(name)}(${esc(error)})`).join(', ')}</div>` : ''}</td>
          <td>${esc((r.reports || []).join(', '))}</td>
        </tr>`).join('') : `<tr><td colspan="5" style="color:var(--sub);">아직 실행 기록이 없습니다.</td></tr>`}</tbody>
      </table>
    </div>`;
}

function wsScheduleForm(root) {
  return {
    enabled: root.querySelector('#ws-sch-enabled').checked,
    frequency: root.querySelector('#ws-sch-frequency').value,
    time: root.querySelector('#ws-sch-time').value,
    weekdays: [...root.querySelectorAll('[data-sch-weekday]')].filter(x => x.checked).map(x => Number(x.dataset.schWeekday)),
    monthday: Number(root.querySelector('#ws-sch-monthday').value),
    formats: [...root.querySelectorAll('[data-sch-format]')].filter(x => x.checked).map(x => x.dataset.schFormat),
  };
}

// 다시 그릴지 가르는 값 — 입력 칸의 내용은 넣지 않는다(사용자가 고치던 것을 지우지 않게).
function wsScheduleSignature(info, alerts) {
  const job = info.job || {};
  return JSON.stringify([info.exists, info.registered, info.next_run, info.running, job.status, job.message,
    (info.history || []).map(r => r.id), alerts && alerts.count, info.schedule && info.schedule.updated_at]);
}

async function wsRefreshSchedule() {
  const el = document.getElementById('ws-schedule-body');
  if (!el) return null;
  const [info, alerts] = await Promise.all([call('get_inspection_schedule'), call('get_schedule_alerts')]);
  if (!info || info.error) {
    el.innerHTML = `<p style="font-size:12px;color:var(--sub);">${esc((info && info.error) || '무인 정기점검 정보를 불러오지 못했습니다.')}</p>`;
    return null;
  }
  updateScheduleBadge(alerts);
  if (wsScheduleWasRunning && !info.running) {
    const last = (info.history || [])[0];
    if (last) showToast(`무인 정기점검 ${statusLabelOf(last)} — ${last.message}`, last.status === 'success' ? 'success' : 'warn');
  }
  wsScheduleWasRunning = !!info.running;
  const sig = wsScheduleSignature(info, alerts);
  if (sig !== wsScheduleSig) {
    wsScheduleSig = sig;
    el.innerHTML = wsScheduleHtml(info, alerts);
    wsWireSchedule(el);
  }
  return info;
}

function statusLabelOf(record) {
  return (WS_SCHEDULE_STATUS[record.status] || [record.status])[0];
}

function wsWireSchedule(root) {
  const frequency = root.querySelector('#ws-sch-frequency');
  frequency.addEventListener('change', () => {
    root.querySelector('#ws-sch-weekdays').style.display = frequency.value === 'weekly' ? 'inline-flex' : 'none';
    root.querySelector('#ws-sch-monthday-wrap').style.display = frequency.value === 'monthly' ? 'inline-flex' : 'none';
  });
  root.querySelector('#ws-sch-save').addEventListener('click', wsSaveSchedule);
  root.querySelector('#ws-sch-run').addEventListener('click', wsRunScheduleNow);
  root.querySelector('#ws-sch-delete').addEventListener('click', wsDeleteSchedule);
  const ack = root.querySelector('#ws-sch-ack');
  if (ack) ack.addEventListener('click', wsAcknowledgeScheduleAlerts);
}

async function wsSaveSchedule() {
  const root = document.getElementById('ws-schedule-body');
  const result = await call('save_inspection_schedule', wsScheduleForm(root));
  if (!result || result.error) { showToast((result && result.error) || '일정을 저장하지 못했습니다.', 'error'); return; }
  if (result.warning) showToast(`일정은 저장했지만 ${result.warning}`, 'warn');
  else showToast(result.schedule.enabled ? '저장했습니다 — 작업 스케줄러에 등록됨' : '저장했습니다 — 꺼져 있어 등록하지 않음', 'success');
  wsScheduleSig = null;
  await wsRefreshSchedule();
}

async function wsRunScheduleNow() {
  if (!confirm('지금 장비에 접속해 무인 정기점검(수집 → 분석 → 보고서)을 한 번 실행합니다. 계속할까요?')) return;
  const result = await call('run_inspection_schedule_now');
  if (!result || result.error) { showToast((result && result.error) || '실행하지 못했습니다.', 'error'); return; }
  showToast('무인 정기점검을 시작했습니다.', 'success');
  if (wsSchedulePoller) wsSchedulePoller.wake();
}

async function wsDeleteSchedule() {
  if (!confirm('이 프로파일의 무인 정기점검 일정을 지우고 작업 스케줄러에서도 지웁니다(실행 이력은 남습니다). 계속할까요?')) return;
  const result = await call('delete_inspection_schedule');
  if (!result || result.error) { showToast((result && result.error) || '지우지 못했습니다.', 'error'); return; }
  showToast('일정을 지웠습니다.', 'success');
  wsScheduleSig = null;
  await wsRefreshSchedule();
}

async function wsAcknowledgeScheduleAlerts() {
  await call('acknowledge_schedule_alerts');
  updateScheduleBadge({ count: 0 });
  wsScheduleSig = null;
  await wsRefreshSchedule();
}

// 워크스페이스 화면을 그릴 때 부른다 — 실행 중이면 2초, 아니면 30초마다 살펴보고(작업 스케줄러가 띄운 점검도
// 보이게) 화면을 떠나면 멈춘다.
function wsInitSchedule() {
  if (wsSchedulePoller) wsSchedulePoller.stop();
  wsScheduleSig = null;
  wsScheduleWasRunning = false;
  wsSchedulePoller = createAdaptivePoller({
    tick: wsRefreshSchedule,
    isBusy: (info) => !!(info && info.running),
    activeMs: 2000,
    idleMs: 30000,
  });
  onPageLeave(() => { if (wsSchedulePoller) wsSchedulePoller.stop(); wsSchedulePoller = null; });
  wsSchedulePoller.start();
}

// ===== 사이드바 실패 배지 — 확인하지 않은 무인 점검 실패(모든 프로파일) =====
function updateScheduleBadge(alerts) {
  const badge = document.getElementById('nav-ws-badge');
  if (!badge || !alerts || alerts.error) return;
  const count = alerts.count || 0;
  badge.textContent = count > 99 ? '99+' : String(count);
  badge.style.display = count ? '' : 'none';
  badge.title = count ? `확인하지 않은 무인 정기점검 실패 ${count}건 — 워크스페이스에서 확인하세요` : '';
}

async function refreshScheduleBadge() {
  const alerts = await call('get_schedule_alerts');
  updateScheduleBadge(alerts);
  return alerts;
}

const scheduleAlertPoller = createAdaptivePoller({
  tick: refreshScheduleBadge,
  isBusy: () => false,
  activeMs: 60000,
  idleMs: 60000,
});
whenApiReady(() => scheduleAlertPoller.start());
