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
  el.innerHTML = `
    <span class="material-symbols-rounded rt-alert-icon">${style.icon}</span>
    <div class="rt-alert-body">
      <div class="rt-alert-head">
        <span class="rt-alert-badge">${style.label}</span>
        <span class="rt-alert-device">${rtEscape(alert.device || '알 수 없는 장비')}</span>
        <span class="rt-alert-time">${rtEscape(alert.ts || '')}</span>
      </div>
      <div class="rt-alert-msg">${rtEscape(alert.message || '')}</div>
      ${alert.raw_line ? `<code class="rt-alert-raw">${rtEscape(alert.raw_line)}</code>` : ''}
    </div>
    <button class="rt-alert-close" type="button" title="닫기">
      <span class="material-symbols-rounded" style="font-size:16px">close</span>
    </button>`;

  const remove = () => {
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

  rtToastStack().appendChild(el);
  requestAnimationFrame(() => el.classList.add('rt-alert-in'));
  setTimeout(remove, style.ttl);
}

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
  return `
    <div class="rt-alert-row" style="--rt-accent:${style.color}">
      <div class="rt-alert-row-head">
        <span class="rt-alert-badge">${style.label}</span>
        <strong>${rtEscape(alert.device || '')}</strong>
        <span class="rt-alert-type">${rtEscape(alert.type || '')}</span>
        <span class="rt-alert-time">${rtEscape(alert.ts || '')}</span>
      </div>
      <div class="rt-alert-msg">${rtEscape(alert.message || '')}</div>
      ${alert.raw_line ? `<code class="rt-alert-raw">${rtEscape(alert.raw_line)}</code>` : ''}
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
