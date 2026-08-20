// ===== 실시간 Baseline Diff 경고 (상단바 알림 버튼 + 우측 슬라이드 패널) =====
// 백엔드(api/log_analysis_run_api.py)가 CRTlog 차분을 판정한 뒤 pywebview evaluate_js로
// window.onRealtimeDiffAlert(alerts)를 직접 호출한다. 즉 여기는 폴링이 없다 — 순수 push 수신부.
//
// 예전에는 우측 하단에 토스트를 띄웠다. 작업 중 화면을 가리는 데다 몇 초 뒤 사라져서, 자리를
// 비운 사이에 지나간 경고는 되짚을 방법이 없었다. 지금은 전부 알림 목록에 쌓아 두고 상단바
// 알림 버튼에 개수만 표시한다 — 누르면 오른쪽에서 패널이 밀려 나오고 거기서 전부 읽는다.

const RT_SEVERITY_STYLE = {
  CRITICAL: { color: 'var(--critical)', icon: 'error', label: '심각' },
  MAJOR: { color: '#F97316', icon: 'warning', label: '주요' },
  WARNING: { color: 'var(--warning)', icon: 'info', label: '경고' },
};

function rtSeverityStyle(severity) {
  return RT_SEVERITY_STYLE[severity] || RT_SEVERITY_STYLE.WARNING;
}

// 받은 경고 전부(최신이 앞). 패널을 닫아 두어도 여기 쌓이므로 나중에 되짚을 수 있다.
// 화면 렌더는 앞 200건까지만 한다 — 대량 경고에서 DOM이 불어나면 패널 여는 것 자체가 느려진다.
let rtNotifications = [];
let rtUnreadCount = 0;
const RT_NOTIFY_MAX = 500;
const RT_NOTIFY_RENDER_MAX = 200;

// 백엔드는 배열로 push하지만, 단일 객체로 호출돼도 동작하게 둘 다 받는다.
window.onRealtimeDiffAlert = function (payload) {
  const alerts = (Array.isArray(payload) ? payload : [payload]).filter(Boolean);
  if (!alerts.length) return;
  // 최신이 위로. 같은 tick에 여러 건이 와도 순서를 유지한다.
  rtNotifications = alerts.slice().reverse().concat(rtNotifications).slice(0, RT_NOTIFY_MAX);
  rtUnreadCount += alerts.length;
  updateRtNotifyBadge();
  if (isRtNotifyPanelOpen()) renderRtNotifyList();
};

// ===== 상단바 알림 버튼 =====
function updateRtNotifyBadge() {
  const badge = document.getElementById('tb-notify-badge');
  const btn = document.getElementById('tb-notify');
  if (!badge || !btn) return;
  badge.style.display = rtUnreadCount ? 'flex' : 'none';
  badge.textContent = rtUnreadCount > 99 ? '99+' : String(rtUnreadCount);
  // 안 읽은 경고 중 가장 심각한 색으로 버튼을 물들인다 — 개수만으로는 급한지 알 수 없다.
  const worst = rtNotifications.slice(0, rtUnreadCount).reduce((acc, a) => {
    const rank = { WARNING: 1, MAJOR: 2, CRITICAL: 3 }[a.severity] || 0;
    return rank > acc.rank ? { rank, severity: a.severity } : acc;
  }, { rank: 0, severity: null });
  btn.style.color = worst.severity ? rtSeverityStyle(worst.severity).color : '';
  btn.title = rtUnreadCount ? `읽지 않은 실시간 경고 ${rtUnreadCount}건` : '실시간 경고 알림';
}

function isRtNotifyPanelOpen() {
  return !!document.getElementById('rt-notify-panel')?.classList.contains('open');
}

function toggleRtNotifyPanel() {
  const panel = rtNotifyPanel();
  if (panel.classList.contains('open')) { closeRtNotifyPanel(); return; }
  renderRtNotifyList();
  // 다음 프레임에 클래스를 붙여야 transform 전환이 실제로 재생된다(방금 만든 노드면 더욱).
  requestAnimationFrame(() => panel.classList.add('open'));
  rtUnreadCount = 0;
  updateRtNotifyBadge();
  setTimeout(() => document.addEventListener('click', rtNotifyOutsideClick), 0);
}

function closeRtNotifyPanel() {
  document.getElementById('rt-notify-panel')?.classList.remove('open');
  document.removeEventListener('click', rtNotifyOutsideClick);
}

function rtNotifyOutsideClick(e) {
  if (e.target.closest('#rt-notify-panel') || e.target.closest('#tb-notify')) return;
  closeRtNotifyPanel();
}

function rtNotifyPanel() {
  let panel = document.getElementById('rt-notify-panel');
  if (panel) return panel;
  panel = document.createElement('aside');
  panel.id = 'rt-notify-panel';
  panel.className = 'rt-notify-panel';
  panel.innerHTML = `
    <div class="rt-notify-head">
      <div>
        <p class="card-title">실시간 경고 알림</p>
        <p class="card-desc" id="rt-notify-sub">-</p>
      </div>
      <div style="display:flex;gap:6px">
        <button class="btn btn-outlined" type="button" id="rt-notify-history" title="전체 경고 이력을 모달로 봅니다">이력</button>
        <button class="btn btn-outlined" type="button" id="rt-notify-clear">비우기</button>
        <button class="btn btn-outlined" type="button" id="rt-notify-close">닫기</button>
      </div>
    </div>
    <div class="rt-notify-body" id="rt-notify-body"></div>`;
  document.body.appendChild(panel);
  panel.querySelector('#rt-notify-close').addEventListener('click', closeRtNotifyPanel);
  panel.querySelector('#rt-notify-history').addEventListener('click', () => openRealtimeAlertDetail(null));
  panel.querySelector('#rt-notify-clear').addEventListener('click', () => {
    // 화면 목록만 비운다 — 경고 이력 자체는 백엔드에 남아 '이력'과 감시 탭에서 그대로 보인다.
    rtNotifications = [];
    rtUnreadCount = 0;
    updateRtNotifyBadge();
    renderRtNotifyList();
  });
  return panel;
}

function renderRtNotifyList() {
  const body = document.getElementById('rt-notify-body');
  const sub = document.getElementById('rt-notify-sub');
  if (!body) return;
  const shown = rtNotifications.slice(0, RT_NOTIFY_RENDER_MAX);
  if (sub) {
    sub.textContent = rtNotifications.length
      ? `${rtNotifications.length}건${rtNotifications.length > shown.length ? ` (최근 ${shown.length}건 표시)` : ''}`
      : '아직 받은 경고가 없습니다.';
  }
  body.innerHTML = shown.length
    ? shown.map(rtNotifyRow).join('')
    : `<p class="card-desc" style="padding:12px">실시간 감시 중 경고가 발생하면 여기에 쌓입니다.</p>`;
  body.querySelectorAll('[data-rt-notify-device]').forEach(row => {
    row.addEventListener('click', () => openRealtimeAlertDetail(row.dataset.rtNotifyDevice || null));
  });
}

function rtNotifyRow(alert) {
  const style = rtSeverityStyle(alert.severity);
  const resolved = !!alert.resolved;
  const cause = alert.root_cause;
  return `
    <div class="rt-notify-row ${resolved ? 'rt-alert-row-resolved' : ''}"
         style="--rt-accent:${resolved ? 'var(--success)' : style.color}"
         data-rt-notify-device="${rtEscape(alert.device || '')}" title="클릭 → 이 장비의 세부 이력">
      <span class="material-symbols-rounded rt-alert-icon">${resolved ? 'task_alt' : style.icon}</span>
      <div class="rt-alert-body">
        <div class="rt-alert-head">
          <span class="rt-alert-badge">${resolved ? '해제' : style.label}</span>
          <span class="rt-alert-device">${rtEscape(alert.device || '알 수 없는 장비')}</span>
          <span class="rt-alert-time">${rtEscape(alert.ts || '')}</span>
        </div>
        <div class="rt-alert-msg">${rtEscape(alert.message || '')}</div>
        ${cause ? `<div class="rt-alert-cause"><span class="material-symbols-rounded">bolt</span>
          원인 추정: <code>${rtEscape(cause.raw_line || '')}</code>
          <em>(${rtEscape(String(cause.elapsed_sec ?? ''))}초 전)</em></div>` : ''}
        ${alert.raw_line ? `<code class="rt-alert-raw">${rtEscape(alert.raw_line)}</code>` : ''}
        ${resolved ? `<div class="rt-alert-fixed"><span class="material-symbols-rounded">task_alt</span>
          ${alert.resolved_detail ? rtEscape(alert.resolved_detail) : '복구됨'}</div>` : ''}
      </div>
    </div>`;
}

document.getElementById('tb-notify')?.addEventListener('click', (e) => {
  e.stopPropagation();
  toggleRtNotifyPanel();
});

// ===== 경고 자동 해제 (Module 2) =====
// 백엔드 StateTracker가 복구 이벤트(no shutdown / active-full / Established)를 감지하면
// alert_id 하나당 한 번 호출한다. 떠 있는 토스트는 '해제됨'으로 잠깐 바꿔 보여준 뒤 지운다 —
// 소리 없이 사라지면 작업자는 알림을 못 봤다고 생각하고 같은 확인을 반복한다.
window.onRealtimeDiffAlertResolved = function (alertId, detail) {
  // 목록에서 지우지 않고 '해제'로 바꿔 남긴다 — 소리 없이 사라지면 작업자는 알림을 못 봤다고
  // 생각하고 같은 확인을 반복한다. '내렸다가 올렸다'는 사실 자체가 점검 이력이기도 하다.
  const hit = rtNotifications.find(a => a.alert_id === alertId);
  if (hit) {
    hit.resolved = true;
    const by = (detail && detail.resolved_by) || '';
    const secs = detail && detail.duration_sec != null ? `${detail.duration_sec}초 후 ` : '';
    hit.resolved_detail = `${secs}복구됨${by ? ` — ${by}` : ''}`;
    if (isRtNotifyPanelOpen()) renderRtNotifyList();
    updateRtNotifyBadge();
  }
  // 감시 패널이 열려 있으면 다음 폴링(0.8초)까지 기다리지 않고 즉시 반영한다.
  if (typeof refreshRealtimeMonitor === 'function' && document.getElementById('rtm-card')) {
    refreshRealtimeMonitor();
  }
};

// ===== 점검 완료로 Baseline이 갱신됐을 때 (Module 1) =====
window.onRealtimeBaselineRefreshed = function (info) {
  const gained = (info && info.gained) || [];
  const loaded = (info && info.loaded) || 0;
  showToast(gained.length
    ? `점검 완료 — Baseline 갱신(장비 ${loaded}대). 새로 기준이 생긴 장비: ${gained.join(', ')}`
    : `점검 완료 — Baseline을 장비 ${loaded}대 기준으로 갱신했습니다.`);
  if (info && info.source_kind === 'mixed') {
    showToast('Baseline에 수동 CRT 세션 로그가 섞여 있습니다 — 점검을 1회 수행하면 기준이 분리됩니다.', 'warn');
  }
  if (typeof refreshRealtimeMonitor === 'function' && document.getElementById('rtm-card')) {
    refreshRealtimeMonitor();
  }
};

// ===== 세부 Diff 이력 모달 =====
async function openRealtimeAlertDetail(device) {
  const result = await call('get_realtime_alerts', device || null, 200);
  const alerts = (result && result.alerts) || [];
  document.getElementById('rt-alert-modal')?.remove();

  const overlay = document.createElement('div');
  overlay.id = 'rt-alert-modal';
  overlay.className = 'modal-overlay';
  overlay.innerHTML = `
    <div class="card rt-alert-modal">
      <div class="rt-alert-modal-head">
        <div>
          <h3 class="card-title">실시간 Baseline Diff 이력</h3>
          <p class="card-desc">${device ? rtEscape(device) : '전체 장비'} · ${alerts.length}건</p>
        </div>
        <div style="display:flex;gap:8px">
          <button class="btn btn-outlined" type="button" data-rt-action="all">전체 장비</button>
          <button class="btn btn-outlined" type="button" data-rt-action="close">닫기</button>
        </div>
      </div>
      <div class="rt-alert-modal-body">
        ${alerts.length ? alerts.map(rtAlertRow).join('') :
          '<p class="card-desc">기록된 경고가 없습니다.</p>'}
      </div>
    </div>`;

  overlay.addEventListener('click', (e) => { if (e.target === overlay) overlay.remove(); });
  overlay.querySelector('[data-rt-action="close"]').addEventListener('click', () => overlay.remove());
  overlay.querySelector('[data-rt-action="all"]').addEventListener('click', () => {
    overlay.remove();
    openRealtimeAlertDetail(null);
  });
  document.body.appendChild(overlay);
}

function rtAlertRow(alert) {
  const style = rtSeverityStyle(alert.severity);
  // 해제된 경고는 지우지 않고 취소선으로 남긴다 — '내렸다가 올렸다'는 사실 자체가 점검 이력이다.
  const resolved = !!alert.resolved;
  const cause = alert.root_cause;
  return `
    <div class="rt-alert-row ${resolved ? 'rt-alert-row-resolved' : ''}"
         style="--rt-accent:${resolved ? 'var(--success)' : style.color}">
      <div class="rt-alert-row-head">
        <span class="rt-alert-badge">${resolved ? '해제' : style.label}</span>
        <strong>${rtEscape(alert.device || '')}</strong>
        <span class="rt-alert-type">${rtEscape(alert.type || '')}</span>
        <span class="rt-alert-time">${rtEscape(alert.ts || '')}</span>
      </div>
      <div class="rt-alert-msg">${rtEscape(alert.message || '')}</div>
      ${cause ? `<div class="rt-alert-cause"><span class="material-symbols-rounded">bolt</span>
        원인 추정: <code>${rtEscape(cause.raw_line || '')}</code></div>` : ''}
      ${alert.raw_line ? `<code class="rt-alert-raw">${rtEscape(alert.raw_line)}</code>` : ''}
      ${resolved ? `<div class="rt-alert-fixed"><span class="material-symbols-rounded">task_alt</span>
        ${alert.duration_sec != null ? `${rtEscape(String(alert.duration_sec))}초 후 ` : ''}복구
        ${alert.resolved_by ? `— <code>${rtEscape(alert.resolved_by)}</code>` : ''}
        ${alert.resolved_ts ? `<span class="rt-alert-time">${rtEscape(alert.resolved_ts)}</span>` : ''}</div>` : ''}
      ${alert.source_file ? `<div class="rt-alert-src">${rtEscape(alert.source_file)}</div>` : ''}
    </div>`;
}

function rtEscape(text) {
  const div = document.createElement('div');
  div.textContent = text == null ? '' : String(text);
  return div.innerHTML;
}

// ===== 감시 시작/중지 =====
// 어느 탭에서든 호출할 수 있게 전역으로 둔다(Log Analysis 탭 버튼에서 연결).
// deviceNames: 감시할 장비명 배열(null이면 백엔드가 장비 목록의 활성 장비 전체를 쓴다).
async function toggleRealtimeBaselineWatch(deviceNames = null) {
  const status = await call('get_realtime_baseline_status');
  if (status && status.running) {
    await call('stop_realtime_baseline_watch');
    showToast('실시간 감시를 중지했습니다.', 'warn');
  } else {
    const result = await call('start_realtime_baseline_watch', 0.3, deviceNames && deviceNames.length ? deviceNames : null);
    if (result && result.error) { showToast(result.error, 'error'); return false; }
    showToast(`실시간 감시 시작 — 대상 장비 ${(result.devices || []).length}대`);
    // Baseline이 없어도 감시는 돌지만, 대조 항목은 판정할 수 없으므로 그 사실을 알린다.
    if (result && result.warning) showToast(result.warning, 'warn');
  }
  return true;
}

// '실시간 감시' 탭 바로가기 버튼의 라벨/색을 현재 감시 상태에 맞춘다.
async function syncRealtimeWatchButton(btn) {
  if (!btn) return;
  const status = await call('get_realtime_baseline_status');
  const running = !!(status && status.running);
  btn.classList.toggle('btn-primary', running);
  btn.classList.toggle('btn-outlined', !running);
  btn.innerHTML = '<span class="material-symbols-rounded">radar</span>' +
    (running ? `실시간 감시 중${status.alert_count ? ` (${status.alert_count})` : ''}` : '실시간 감시');
  btn.title = running
    ? 'CRTlog를 0.3초 간격으로 tail하며 Baseline과 대조 중 — 클릭하면 실시간 감시 탭으로 이동합니다'
    : 'SecureCRT 세션 로그 실시간 감시 화면으로 이동합니다';
}
