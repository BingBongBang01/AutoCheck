// ===== 실시간 Baseline Diff 경고 (우측 하단 토스트) =====
// 백엔드(api/log_analysis_run_api.py)가 CRTlog 차분을 판정한 뒤 pywebview evaluate_js로
// window.onRealtimeDiffAlert(alerts)를 직접 호출한다. 즉 여기는 폴링이 없다 — 순수 push 수신부.
// core.js의 showToast()는 1.8초 뒤 사라지는 단문 알림이라, 심각도 색·장비명·원문 CLI를 함께
// 보여주고 클릭으로 세부 이력을 열어야 하는 이 요구사항에는 맞지 않아 별도 스택을 만든다.

const RT_SEVERITY_STYLE = {
  CRITICAL: { color: 'var(--critical)', icon: 'error', ttl: 12000, label: '심각' },
  MAJOR: { color: '#F97316', icon: 'warning', ttl: 9000, label: '주요' },
  WARNING: { color: 'var(--warning)', icon: 'info', ttl: 6000, label: '경고' },
};

function rtSeverityStyle(severity) {
  return RT_SEVERITY_STYLE[severity] || RT_SEVERITY_STYLE.WARNING;
}

function rtToastStack() {
  let stack = document.getElementById('rt-alert-stack');
  if (!stack) {
    stack = document.createElement('div');
    stack.id = 'rt-alert-stack';
    stack.className = 'rt-alert-stack';
    document.body.appendChild(stack);
  }
  return stack;
}

// alert_id -> 화면에 떠 있는 토스트 element. 백엔드가 '이 경고 해제됨'을 push하면 여기서 찾아 지운다.
// element에 data 속성만 두고 querySelector로 찾지 않는 이유: 토스트는 ttl로 스스로 사라지므로
// 해제 push가 도착했을 때 이미 없을 수 있고, Map이면 그 경우를 조용히 넘길 수 있다.
const rtLiveToasts = new Map();

// 백엔드는 배열로 push하지만, 단일 객체로 호출돼도 동작하게 둘 다 받는다.
window.onRealtimeDiffAlert = function (payload) {
  const alerts = Array.isArray(payload) ? payload : [payload];
  // 한 번에 몰려 들어오면 화면이 토스트로 덮이므로 최근 5건만 띄우고 나머지는 이력에서 본다.
  const shown = alerts.slice(-5);
  const hidden = alerts.length - shown.length;
  shown.forEach(showRealtimeAlertToast);
  if (hidden > 0) {
    showRealtimeAlertToast({
      device: shown[0] && shown[0].device,
      severity: 'WARNING',
      type: 'BATCH',
      message: `동일 시점 경고 ${hidden}건 더 — 클릭해 전체 보기`,
      raw_line: '',
    });
  }
};

function showRealtimeAlertToast(alert) {
  if (!alert) return;
  const style = rtSeverityStyle(alert.severity);
  const el = document.createElement('div');
  el.className = 'rt-alert-toast';
  el.style.setProperty('--rt-accent', style.color);
  if (alert.alert_id) el.dataset.alertId = alert.alert_id;
  // 근본 원인이 추정되면 그 문구를 같이 띄운다 — '무엇이 끊겼다'만으로는 무엇을 되돌려야
  // 하는지 알 수 없다(Module 3).
  const cause = alert.root_cause;
  el.innerHTML = `
    <span class="material-symbols-rounded rt-alert-icon">${style.icon}</span>
    <div class="rt-alert-body">
      <div class="rt-alert-head">
        <span class="rt-alert-badge">${style.label}</span>
        <span class="rt-alert-device">${rtEscape(alert.device || '알 수 없는 장비')}</span>
        <span class="rt-alert-time">${rtEscape(alert.ts || '')}</span>
      </div>
      <div class="rt-alert-msg">${rtEscape(alert.message || '')}</div>
      ${cause ? `<div class="rt-alert-cause"><span class="material-symbols-rounded">bolt</span>
        원인 추정: <code>${rtEscape(cause.raw_line || '')}</code>
        <em>(${rtEscape(String(cause.elapsed_sec ?? ''))}초 전)</em></div>` : ''}
      ${alert.raw_line ? `<code class="rt-alert-raw">${rtEscape(alert.raw_line)}</code>` : ''}
    </div>
    <button class="rt-alert-close" type="button" title="닫기">
      <span class="material-symbols-rounded" style="font-size:16px">close</span>
    </button>`;

  const remove = () => {
    if (alert.alert_id) rtLiveToasts.delete(alert.alert_id);
    el.classList.remove('rt-alert-in');
    setTimeout(() => el.remove(), 220);
  };
  el.querySelector('.rt-alert-close').addEventListener('click', (e) => {
    e.stopPropagation();
    remove();
  });
  el.addEventListener('click', () => {
    remove();
    openRealtimeAlertDetail(alert.device);
  });

  if (alert.alert_id) rtLiveToasts.set(alert.alert_id, { el, remove });
  rtToastStack().appendChild(el);
  requestAnimationFrame(() => el.classList.add('rt-alert-in'));
  setTimeout(remove, style.ttl);
}

// ===== 경고 자동 해제 (Module 2) =====
// 백엔드 StateTracker가 복구 이벤트(no shutdown / active-full / Established)를 감지하면
// alert_id 하나당 한 번 호출한다. 떠 있는 토스트는 '해제됨'으로 잠깐 바꿔 보여준 뒤 지운다 —
// 소리 없이 사라지면 작업자는 알림을 못 봤다고 생각하고 같은 확인을 반복한다.
window.onRealtimeDiffAlertResolved = function (alertId, detail) {
  const entry = rtLiveToasts.get(alertId);
  if (entry) {
    const { el, remove } = entry;
    el.classList.add('rt-alert-resolved');
    el.style.setProperty('--rt-accent', 'var(--success)');
    const msg = el.querySelector('.rt-alert-msg');
    if (msg) {
      const by = (detail && detail.resolved_by) || '';
      const secs = detail && detail.duration_sec != null ? `${detail.duration_sec}초 후 ` : '';
      msg.innerHTML = `<s>${msg.innerHTML}</s>
        <div class="rt-alert-fixed"><span class="material-symbols-rounded">task_alt</span>
        ${rtEscape(secs)}복구됨${by ? ` — <code>${rtEscape(by)}</code>` : ''}</div>`;
    }
    const icon = el.querySelector('.rt-alert-icon');
    if (icon) icon.textContent = 'task_alt';
    rtLiveToasts.delete(alertId);
    setTimeout(remove, 3200);
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
