// ===== 실시간 감시 탭 (사이드바 독립 페이지) =====
// 예전에는 이 패널이 '세션 터미널' 탭 아래에 붙어 있었는데, 터미널 카드와 장비 목록 행을
// 같은 화면에서 나눠 쓰다 보니 3분할 패널에 남는 높이가 거의 없었다. 그래서 자체 장비 목록 행을
// 가진 독립 탭으로 분리했다 — 감시 대상 선택도 터미널 접속용 체크박스(selectedDeviceNames)와
// 분리되어, 접속하지 않은 장비의 CRT 로그도 감시할 수 있다.
//
// 3분할 구성:
//   좌  : 장비별 실시간 CLI 캡처 (탭 보기 / 분할 보기)
//   우상: 실시간 오류 분석 (아래 체크리스트 결과를 규칙 기반으로 요약)
//   우하: 장비 체크리스트 (정상 / 이상 항목)
// 좌우는 세로 구분선, 우측 상하는 가로 구분선으로 드래그해 비율을 바꾸고, 그 비율은
// save_realtime_watch_layout()으로 파일에 저장돼 프로그램을 다시 켜도 유지된다.
//
// 데이터는 0.8초 폴링(get_realtime_monitor_state)으로 가져온다. 백엔드는 0.3초마다 차분을 읽지만
// 화면 전체를 그때마다 evaluate_js로 밀어 넣으면 대량 로그에서 UI가 밀리므로, '놓치면 안 되는
// 경고 토스트'만 push(realtime-baseline-alerts.js)하고 패널은 폴링으로 분리했다.

let rtmViewMode = 'split';        // 'tabs' | 'split' — 좌측 로그 보기 방식
let rtmActiveDevice = null;       // 탭 보기에서 선택된 장비
let rtmSplitRatio = 0.52;         // 좌우 구분선 위치(좌측 비율)
let rtmRightRatio = 0.45;         // 우측 상단(분석) 비율
let rtmPollTimer = null;
let rtmLastState = null;
let rtmTargets = [];              // [{name, ip, port}] — 장비 목록의 활성 장비
let rtmSelectedDevices = new Set();
let rtmLayoutLoaded = false;

// 분할 보기의 장비 로그 박스 — 스크롤 없이 고정 높이로 최신 줄만 보여주고,
// 박스 여러 개를 담은 바깥 컨테이너만 위아래로 스크롤한다.
const RTM_BOX_HEIGHT = 176;       // px
const RTM_LINE_HEIGHT = 17;       // px — style.css의 .rtm-log-line과 맞춰야 클리핑이 정확하다
const RTM_BOX_LINES = Math.floor((RTM_BOX_HEIGHT - 30) / RTM_LINE_HEIGHT);

// ===== 페이지 렌더러 (core-navigation.js의 navigate('realtimewatch')) =====
async function renderRealtimeWatch() {
  const content = document.getElementById('content');
  const [targets, layout] = await Promise.all([
    call('get_terminal_targets'),
    call('get_realtime_watch_layout'),
  ]);
  rtmTargets = targets || [];
  applyRtmLayout(layout);

  content.innerHTML = `
   <div class="rtm-page">
    <h1 class="page-title">실시간 감시</h1>
    <p class="page-sub">SecureCRT 세션 로그(<code>Documents/AutoCheck/CRTlog</code>)에 기록되는 입·출력을 0.3초 간격으로 따라가며,
      사전 점검 결과(Baseline)와 달라진 설정·상태를 즉시 알립니다.</p>
    ${realtimeMonitorMarkup()}
   </div>`;

  await initRealtimeMonitorPanel();
}

function realtimeMonitorMarkup() {
  return `
    <div class="card rtm-card" id="rtm-card">
      <div class="rtm-tabbar">
        <div class="rtm-tab active"><span class="material-symbols-rounded">radar</span>자동 실시간 감시</div>
        <span style="flex:1"></span>
        <span class="rtm-status" id="rtm-status">감시 중지됨</span>
        <button class="btn btn-outlined" id="rtm-btn-toggle"><span class="material-symbols-rounded">sensors_off</span>실시간 감시 시작</button>
        <button class="btn btn-outlined" id="rtm-btn-clear" title="경고 이력과 체크리스트 판정을 초기화합니다"><span class="material-symbols-rounded">restart_alt</span>초기화</button>
        <button class="btn btn-outlined" id="rtm-btn-probe" title="CRTlog의 각 로그 파일이 어느 장비로 인식되는지 확인합니다"><span class="material-symbols-rounded">find_in_page</span>파일 진단</button>
        <label class="rtm-auto" title="프로그램을 실행하면 이 감시를 자동으로 시작합니다.">
          <input type="checkbox" id="rtm-autostart">프로그램 실행 시 자동 시작
        </label>
      </div>

      <div class="rtm-target-row">
        <span class="rtm-target-label">감시 대상 장비</span>
        <button class="btn btn-outlined rtm-mini" id="rtm-select-all">전체선택</button>
        <div class="rtm-target-chips" id="rtm-target-chips"></div>
      </div>
      <div class="rtm-warn-row" id="rtm-warn-row" style="display:none;"></div>

      <div class="rtm-body" id="rtm-body">
        <div class="rtm-left" id="rtm-left">
          <div class="rtm-pane-head">
            <span class="rtm-pane-title">실시간 입력 캡처</span>
            <span style="flex:1"></span>
            <button class="btn btn-outlined rtm-mini" id="rtm-view-tabs">탭 보기</button>
            <button class="btn btn-outlined rtm-mini" id="rtm-view-split">분할 보기</button>
          </div>
          <div class="rtm-device-tabs" id="rtm-device-tabs"></div>
          <div class="rtm-left-body" id="rtm-left-body"></div>
        </div>

        <div class="rtm-vsplit" id="rtm-vsplit" title="드래그해서 좌우 비율 조절"></div>

        <div class="rtm-right" id="rtm-right">
          <div class="rtm-analysis" id="rtm-analysis"></div>
          <div class="rtm-hsplit" id="rtm-hsplit" title="드래그해서 상하 비율 조절"></div>
          <div class="rtm-checklist" id="rtm-checklist"></div>
        </div>
      </div>
    </div>`;
}

function applyRtmLayout(layout) {
  if (!layout) return;
  if (typeof layout.split_ratio === 'number') rtmSplitRatio = layout.split_ratio;
  if (typeof layout.right_ratio === 'number') rtmRightRatio = layout.right_ratio;
  if (layout.view_mode) rtmViewMode = layout.view_mode;
  // 저장된 선택 장비 중 지금도 장비 목록에 있는 것만 복원하고, 저장된 게 없으면 전체 선택.
  const known = new Set(rtmTargets.map(t => t.name));
  const saved = (layout.selected_devices || []).filter(n => known.has(n));
  if (!rtmLayoutLoaded) {
    rtmSelectedDevices = new Set(saved.length ? saved : rtmTargets.map(t => t.name));
    rtmLayoutLoaded = true;
  }
}

function persistRtmLayout() {
  // 저장 실패(브릿지 없음 등)로 화면 조작이 막히면 안 되므로 결과를 기다리지 않는다.
  Promise.resolve(call('save_realtime_watch_layout', rtmSplitRatio, rtmRightRatio,
                       rtmViewMode, Array.from(rtmSelectedDevices)))
    .catch(() => {});
}

async function initRealtimeMonitorPanel() {
  const card = document.getElementById('rtm-card');
  if (!card) return;

  applyRtmRatios();
  wireRtmSplitters();
  renderRtmTargetChips();

  const toggleBtn = document.getElementById('rtm-btn-toggle');
  toggleBtn.addEventListener('click', async () => {
    toggleBtn.disabled = true;
    const picked = Array.from(rtmSelectedDevices);
    await toggleRealtimeBaselineWatch(picked);
    toggleBtn.disabled = false;
    await refreshRealtimeMonitor();
  });

  document.getElementById('rtm-btn-clear').addEventListener('click', async () => {
    await call('clear_realtime_alerts');
    await refreshRealtimeMonitor();
  });

  document.getElementById('rtm-btn-probe').addEventListener('click', openRtmProbeModal);

  document.getElementById('rtm-select-all').addEventListener('click', () => {
    const allOn = rtmTargets.every(t => rtmSelectedDevices.has(t.name));
    rtmSelectedDevices = new Set(allOn ? [] : rtmTargets.map(t => t.name));
    renderRtmTargetChips();
    persistRtmLayout();
  });

  const autoBox = document.getElementById('rtm-autostart');
  autoBox.addEventListener('change', async () => {
    const result = await call('set_realtime_watch_autostart', autoBox.checked);
    if (result && result.error) { showToast(result.error, 'error'); autoBox.checked = !autoBox.checked; return; }
    showToast(autoBox.checked ? '프로그램 실행 시 실시간 감시를 자동으로 시작합니다.'
                              : '자동 시작을 해제했습니다.');
  });

  const setView = (mode) => { rtmViewMode = mode; persistRtmLayout(); renderRtmState(rtmLastState); };
  document.getElementById('rtm-view-tabs').addEventListener('click', () => setView('tabs'));
  document.getElementById('rtm-view-split').addEventListener('click', () => setView('split'));

  const status = await call('get_realtime_baseline_status');
  if (status) autoBox.checked = !!status.autostart;

  await refreshRealtimeMonitor();
  startRtmPolling();
}

// ===== 감시 대상 장비 행 =====
function renderRtmTargetChips() {
  const wrap = document.getElementById('rtm-target-chips');
  if (!wrap) return;
  if (!rtmTargets.length) {
    wrap.innerHTML = `<span class="rtm-target-empty">장비 목록에 활성 장비가 없습니다 — '장비 목록' 탭에서 먼저 등록하세요.</span>`;
    return;
  }
  wrap.innerHTML = rtmTargets.map(t => `
    <label class="rtm-target-chip ${rtmSelectedDevices.has(t.name) ? 'on' : ''}">
      <input type="checkbox" data-rtm-target="${rtEscape(t.name)}" ${rtmSelectedDevices.has(t.name) ? 'checked' : ''}>
      <span>${rtEscape(t.name)}</span><em>${rtEscape(t.ip || '')}</em>
    </label>`).join('');
  wrap.querySelectorAll('[data-rtm-target]').forEach(cb => {
    cb.addEventListener('change', () => {
      if (cb.checked) rtmSelectedDevices.add(cb.dataset.rtmTarget);
      else rtmSelectedDevices.delete(cb.dataset.rtmTarget);
      cb.closest('.rtm-target-chip').classList.toggle('on', cb.checked);
      persistRtmLayout();
    });
  });
}

// ===== 폴링 =====
function startRtmPolling() {
  if (rtmPollTimer) clearInterval(rtmPollTimer);
  rtmPollTimer = setInterval(async () => {
    // 다른 탭으로 이동하면 카드가 사라진다 — 그때 폴링을 멈춘다(백엔드 감시는 계속 돈다).
    if (!document.getElementById('rtm-card')) {
      clearInterval(rtmPollTimer);
      rtmPollTimer = null;
      return;
    }
    await refreshRealtimeMonitor();
  }, 800);
}

async function refreshRealtimeMonitor() {
  const state = await call('get_realtime_monitor_state', 160);
  if (!state) return;
  rtmLastState = state;
  renderRtmState(state);
}

function renderRtmState(state) {
  if (!state || !document.getElementById('rtm-card')) return;
  renderRtmToolbar(state);
  renderRtmWarnRow(state);
  renderRtmDeviceTabs(state);
  renderRtmLogs(state);
  renderRtmAnalysis(state.analysis || {});
  renderRtmChecklist(state);
}

function renderRtmToolbar(state) {
  const btn = document.getElementById('rtm-btn-toggle');
  const statusEl = document.getElementById('rtm-status');
  const running = !!state.running;
  btn.classList.toggle('btn-primary', running);
  btn.classList.toggle('btn-outlined', !running);
  btn.innerHTML = `<span class="material-symbols-rounded">${running ? 'sensors' : 'sensors_off'}</span>` +
    (running ? '감시 중지' : '실시간 감시 시작');
  const devices = state.devices || [];
  const fails = devices.filter(d => d.status === 'fail').length;
  statusEl.textContent = running
    ? `감시 중 · 장비 ${devices.length}대 · 로그파일 ${state.tracked_files || 0}개${fails ? ` · 이상 ${fails}대` : ' · 이상 없음'}`
    : '감시 중지됨';
  statusEl.classList.toggle('rtm-status-on', running && !fails);
  statusEl.classList.toggle('rtm-status-bad', running && !!fails);
}

// 감시는 도는데 화면이 비어 있는 상황의 원인을 바로 알려준다 — 대부분 파일-장비 매칭 실패다.
function renderRtmWarnRow(state) {
  const row = document.getElementById('rtm-warn-row');
  const unmatched = state.unmatched_files || [];
  if (!state.running || !unmatched.length) { row.style.display = 'none'; return; }
  row.style.display = 'flex';
  row.innerHTML = `
    <span class="material-symbols-rounded">help</span>
    <span>장비를 알아내지 못한 로그 파일 ${unmatched.length}개 — 파일명·프롬프트가 장비 목록과 일치하지 않습니다.
      <b>파일 진단</b>으로 확인하세요. (${unmatched.slice(0, 3).map(rtEscape).join(', ')}${unmatched.length > 3 ? ' …' : ''})</span>`;
}

// ===== 좌측: 장비별 실시간 로그 =====
function renderRtmDeviceTabs(state) {
  const wrap = document.getElementById('rtm-device-tabs');
  const devices = state.devices || [];
  if (rtmViewMode !== 'tabs' || !devices.length) { wrap.style.display = 'none'; wrap.innerHTML = ''; return; }
  if (!rtmActiveDevice || !devices.some(d => d.device === rtmActiveDevice)) rtmActiveDevice = devices[0].device;
  wrap.style.display = 'flex';
  wrap.innerHTML = devices.map(d => `
    <button class="rtm-device-tab ${d.device === rtmActiveDevice ? 'active' : ''} rtm-${d.status}" data-rtm-device="${rtEscape(d.device)}">
      ${rtEscape(d.device)}${d.fail_count ? ` <b>${d.fail_count}</b>` : ''}
    </button>`).join('');
  wrap.querySelectorAll('[data-rtm-device]').forEach(el => {
    el.addEventListener('click', () => { rtmActiveDevice = el.dataset.rtmDevice; renderRtmState(rtmLastState); });
  });
}

function renderRtmLogs(state) {
  const body = document.getElementById('rtm-left-body');
  const scrollTop = body.scrollTop;
  // 첫 렌더에서는 clientHeight가 0이라 '맨 아래에 있다'로 오판해 목록이 끝으로 튄다 — 명시적으로 제외.
  const firstPaint = !body.firstElementChild;
  const atBottom = !firstPaint && body.scrollHeight - body.scrollTop - body.clientHeight < 24;
  const devices = state.devices || [];

  if (!devices.length) {
    body.innerHTML = `<p class="rtm-empty">감시 대상 장비가 없습니다 — 위 '감시 대상 장비'에서 장비를 체크하고 감시를 시작하세요.</p>`;
    return;
  }

  if (rtmViewMode === 'tabs') {
    const device = devices.find(d => d.device === rtmActiveDevice) || devices[0];
    // 탭 보기는 박스 하나가 패널 높이를 채운다 — 여기도 박스 내부 스크롤 없이 최신 줄만 보인다.
    const lines = Math.max(4, Math.floor((body.clientHeight - 40) / RTM_LINE_HEIGHT));
    body.innerHTML = rtmLogBox(device, lines, true);
    return;
  }

  body.innerHTML = devices.map(d => rtmLogBox(d, RTM_BOX_LINES, false)).join('');
  // 분할 보기에서는 사용자가 위쪽 장비를 보고 있을 수 있으므로, 맨 아래에 있었을 때만 따라 내린다.
  body.scrollTop = atBottom ? body.scrollHeight : scrollTop;
}

function rtmLogBox(device, maxLines, fill) {
  const lines = (device.lines || []).slice(-maxLines);
  const badge = device.status === 'fail' ? '이상' : (device.status === 'warn' ? '주의' : '정상');
  return `
    <div class="rtm-log-box rtm-${device.status} ${fill ? 'rtm-log-box-fill' : ''}"
         style="${fill ? '' : `height:${RTM_BOX_HEIGHT}px`}">
      <div class="rtm-log-head">
        <strong>${rtEscape(device.device)}</strong>
        <span class="rtm-chip rtm-chip-${device.status}">${badge}</span>
        ${device.has_baseline ? '' : '<span class="rtm-chip rtm-chip-unknown">Baseline 없음</span>'}
        <span style="flex:1"></span>
        <span class="rtm-log-count">${device.line_count}줄</span>
      </div>
      <div class="rtm-log-lines">${lines.length
        ? lines.map(l => `<div class="rtm-log-line${l.history ? ' rtm-log-history' : ''}"><span class="rtm-log-ts">${rtEscape(l.ts)}</span>${rtEscape(l.text)}</div>`).join('')
        : '<div class="rtm-log-line rtm-log-idle">입력 대기 중…</div>'}</div>
    </div>`;
}

// ===== 우측 상단: 실시간 오류 분석 =====
function renderRtmAnalysis(analysis) {
  const el = document.getElementById('rtm-analysis');
  const scrollTop = el.scrollTop;
  const counts = analysis.counts || {};
  const verdict = analysis.verdict || 'ok';
  el.innerHTML = `
    <div class="rtm-pane-head rtm-pane-head-sticky">
      <span class="rtm-pane-title">실시간 오류 분석</span>
      <span style="flex:1"></span>
      <span class="rtm-chip rtm-chip-fail">CRITICAL ${counts.CRITICAL || 0}</span>
      <span class="rtm-chip rtm-chip-warn">MAJOR ${counts.MAJOR || 0}</span>
      <span class="rtm-chip rtm-chip-unknown">WARNING ${counts.WARNING || 0}</span>
    </div>
    <div class="rtm-verdict rtm-${verdict}">
      <div class="rtm-verdict-headline">${rtEscape(analysis.headline || '')}</div>
      <div class="rtm-verdict-summary">${rtEscape(analysis.summary || '')}</div>
    </div>
    ${(analysis.findings || []).map(f => `
      <div class="rtm-finding rtm-sev-${(f.severity || 'WARNING').toLowerCase()}">
        <div class="rtm-finding-head">
          <span class="rtm-chip rtm-chip-${f.severity === 'CRITICAL' ? 'fail' : 'warn'}">${rtEscape(f.severity || '')}</span>
          <strong>${rtEscape(f.device || '')}</strong>
          <span class="rtm-finding-title">${rtEscape(f.title || '')}</span>
        </div>
        <div class="rtm-finding-line"><span class="rtm-label">원인 추정</span>${rtEscape(f.cause || '')}</div>
        <div class="rtm-finding-line"><span class="rtm-label">권고 조치</span>${rtEscape(f.action || '')}</div>
        ${(f.evidence || []).filter(Boolean).length
          ? `<div class="rtm-finding-evidence">${f.evidence.filter(Boolean).map(e => `<code>${rtEscape(e)}</code>`).join('')}</div>`
          : ''}
      </div>`).join('')}`;
  el.scrollTop = scrollTop;
}

// ===== 우측 하단: 장비 체크리스트 =====
function renderRtmChecklist(state) {
  const el = document.getElementById('rtm-checklist');
  const scrollTop = el.scrollTop;
  const devices = state.devices || [];
  const totalFail = devices.reduce((n, d) => n + d.fail_count, 0);

  el.innerHTML = `
    <div class="rtm-pane-head rtm-pane-head-sticky">
      <span class="rtm-pane-title">실시간 체크리스트</span>
      <span style="flex:1"></span>
      <span class="rtm-chip rtm-chip-fail">이상 ${totalFail}</span>
      <span class="rtm-chip rtm-chip-ok">정상 ${devices.reduce((n, d) => n + d.checklist.filter(c => c.status === 'pending').length, 0)}</span>
    </div>
    ${devices.length ? devices.map(rtmChecklistGroup).join('')
      : '<p class="rtm-empty">감시를 시작하면 장비별 점검 항목이 표시됩니다.</p>'}`;
  el.scrollTop = scrollTop;

  el.querySelectorAll('[data-rtm-detail]').forEach(row => {
    row.addEventListener('click', () => openRealtimeAlertDetail(row.dataset.rtmDetail));
  });
}

// 이상 항목을 위로 올린다 — 스크롤을 내리지 않고도 문제부터 보이게.
const RTM_STATUS_ORDER = { fail: 0, warn: 1, recovered: 2, pending: 3, unknown: 4 };
const RTM_STATUS_LABEL = { fail: '이상', warn: '주의', recovered: '복구', pending: '정상', unknown: '기준없음' };
const RTM_STATUS_ICON = { fail: 'cancel', warn: 'warning', recovered: 'restart_alt', pending: 'check_circle', unknown: 'help' };

function rtmChecklistGroup(device) {
  const items = [...device.checklist].sort(
    (a, b) => (RTM_STATUS_ORDER[a.status] ?? 9) - (RTM_STATUS_ORDER[b.status] ?? 9));
  return `
    <div class="rtm-check-group" data-rtm-detail="${rtEscape(device.device)}">
      <div class="rtm-check-group-head">
        <span class="rtm-chip rtm-chip-${device.status}">${RTM_STATUS_LABEL[device.status] || ''}</span>
        <strong>${rtEscape(device.device)}</strong>
        <span style="flex:1"></span>
        <span class="rtm-check-hint">클릭 → 세부 이력</span>
      </div>
      ${items.map(c => `
        <div class="rtm-check-row rtm-check-${c.status}">
          <span class="material-symbols-rounded">${RTM_STATUS_ICON[c.status] || 'help'}</span>
          <span class="rtm-check-label">${rtEscape(c.label)}</span>
          <span class="rtm-check-detail">${rtEscape(c.detail || '')}</span>
          ${c.count ? `<span class="rtm-check-count">${c.count}</span>` : ''}
          ${c.last_ts ? `<span class="rtm-check-ts">${rtEscape(c.last_ts)}</span>` : ''}
        </div>`).join('')}
    </div>`;
}

// ===== 파일 진단 모달 =====
async function openRtmProbeModal() {
  const result = await call('probe_realtime_log_files');
  if (!result) return;
  document.getElementById('rtm-probe-modal')?.remove();
  const files = result.files || [];
  const overlay = document.createElement('div');
  overlay.id = 'rtm-probe-modal';
  overlay.className = 'modal-overlay';
  overlay.innerHTML = `
    <div class="card rtm-probe-modal">
      <div class="rtm-alert-modal-head">
        <div>
          <h3 class="card-title">CRT 로그 파일 진단</h3>
          <p class="card-desc">${rtEscape(result.watch_dir || '')} · 파일 ${files.length}개 · 장비 목록 ${(result.known_devices || []).length}대</p>
        </div>
        <button class="btn btn-outlined" type="button" data-rtm-close>닫기</button>
      </div>
      <div class="rtm-probe-body">
        <table class="dtable"><thead><tr>
          <th>로그 파일</th><th>파일명 매칭</th><th>내용 매칭</th><th>최종 장비</th><th>크기</th>
        </tr></thead><tbody>
        ${files.length ? files.map(f => `
          <tr class="${f.resolved ? '' : 'rtm-probe-bad'}">
            <td>${rtEscape(f.file)}</td>
            <td>${f.from_filename ? rtEscape(f.from_filename) : '—'}</td>
            <td>${f.from_content ? rtEscape(f.from_content) : '—'}</td>
            <td><strong>${f.resolved ? rtEscape(f.resolved) : '식별 실패'}</strong></td>
            <td>${(f.size / 1024).toFixed(1)} KB</td>
          </tr>`).join('')
          : '<tr><td colspan="5">CRTlog 폴더에 .txt / .log 파일이 없습니다.</td></tr>'}
        </tbody></table>
        <p class="rtm-probe-note">파일명이 접속 IP여도 장비 목록의 IP와 대조해 매칭합니다. 그래도 실패하면
          로그 안의 <code>! device: X</code> 헤더나 프롬프트(<code>X#</code>)로 판정합니다.
          '식별 실패'인 파일은 장비 목록에 없는 장비이거나, 로그에 프롬프트가 아직 기록되지 않은 경우입니다.</p>
      </div>
    </div>`;
  overlay.addEventListener('click', (e) => { if (e.target === overlay) overlay.remove(); });
  overlay.querySelector('[data-rtm-close]').addEventListener('click', () => overlay.remove());
  document.body.appendChild(overlay);
}

// ===== 구분선 드래그 =====
function applyRtmRatios() {
  const left = document.getElementById('rtm-left');
  const analysis = document.getElementById('rtm-analysis');
  if (left) left.style.flex = `0 0 ${(rtmSplitRatio * 100).toFixed(2)}%`;
  if (analysis) analysis.style.flex = `0 0 ${(rtmRightRatio * 100).toFixed(2)}%`;
}

function wireRtmSplitters() {
  const body = document.getElementById('rtm-body');
  const right = document.getElementById('rtm-right');

  makeRtmDragger(document.getElementById('rtm-vsplit'), 'col-resize', (e) => {
    const rect = body.getBoundingClientRect();
    rtmSplitRatio = clampRatio((e.clientX - rect.left) / rect.width);
    applyRtmRatios();
  });

  makeRtmDragger(document.getElementById('rtm-hsplit'), 'row-resize', (e) => {
    const rect = right.getBoundingClientRect();
    rtmRightRatio = clampRatio((e.clientY - rect.top) / rect.height);
    applyRtmRatios();
    // 탭 보기 로그 박스는 패널 높이로 줄 수를 계산하므로 비율이 바뀌면 다시 그린다.
    if (rtmViewMode === 'tabs') renderRtmLogs(rtmLastState || {});
  });
}

function clampRatio(value) {
  return Math.min(0.82, Math.max(0.18, value));
}

function makeRtmDragger(handle, cursor, onMove) {
  if (!handle) return;
  handle.addEventListener('mousedown', (e) => {
    e.preventDefault();
    handle.classList.add('rtm-split-active');
    // 드래그 중 텍스트 선택과 커서 깜빡임을 막는다 — 패널 안에 로그 텍스트가 가득해서 특히 거슬린다.
    const prevCursor = document.body.style.cursor;
    document.body.style.cursor = cursor;
    document.body.style.userSelect = 'none';
    const move = (ev) => onMove(ev);
    const up = () => {
      document.removeEventListener('mousemove', move);
      document.removeEventListener('mouseup', up);
      document.body.style.cursor = prevCursor;
      document.body.style.userSelect = '';
      handle.classList.remove('rtm-split-active');
      // 드래그가 끝날 때만 저장한다 — mousemove마다 브릿지를 때리면 파일 쓰기가 폭주한다.
      persistRtmLayout();
    };
    document.addEventListener('mousemove', move);
    document.addEventListener('mouseup', up);
  });
}
