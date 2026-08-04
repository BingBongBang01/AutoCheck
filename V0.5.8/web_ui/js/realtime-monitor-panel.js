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
// Module 4 — 서버(config/realtime_watch.yaml)가 소유하는 숨김/고정 설정의 최신 사본.
// 화면은 이걸 읽어 그리기만 하고, 바꿀 때는 항상 서버 API를 거쳐 응답으로 갱신한다
// (localStorage에 두면 재실행 시 사라지고, 프론트에서 목록을 조립하면 폴링 사이의 변경을 되살린다).
let rtmFilter = { hidden_rules: [], hidden_devices: [], hidden_keywords: [], pinned_items: [] };

// 실시간 오류 분석 좌우 2분할 선택 상태 — 모듈 전역이라 다른 탭에 갔다 와도, 0.8초 폴링으로
// 화면이 다시 그려져도 마지막에 고른 오류가 그대로 유지된다.
// findings는 폴링마다 새 객체로 오므로 인덱스로는 기억할 수 없다 — 오류 제목(그룹 키)을 쓴다.
let rtmAnalysisSelectedKey = null;
let rtmAnalysisRenderedKey = null; // 직전 렌더의 선택 키 — 바뀐 경우에만 우측을 맨 위로 스크롤
// 실시간 체크리스트 좌측 장비 그리드에서 선택된 장비 — 클릭 시 우측 목록에서 그 장비 그룹을 맨 위로.
let rtmChecklistSelectedDevice = null;

// 심각도 3단계 공용 테이블 — engine/realtime_monitor.py의 _SEVERITY_RANK와 맞춘다.
const RTM_SEV_RANK = { WARNING: 1, MAJOR: 2, CRITICAL: 3 };
const RTM_SEV_LABEL = { CRITICAL: '심각', MAJOR: '주요', WARNING: '경고' };
const RTM_SEV_CHIP = { CRITICAL: 'fail', MAJOR: 'major', WARNING: 'warn' };

// 장비별 최종 심각도 {장비명: 'CRITICAL'|'MAJOR'|'WARNING'}. renderRtmState가 폴링마다 새로
// 계산해서 세 패널(입력 캡처 / 오류 분석 / 체크리스트)이 같은 값을 쓰게 한다.
let rtmSeverityByDevice = {};

// 오류 분석에 CRITICAL/MAJOR가 떠 있는데도 좌측 입력 캡처와 체크리스트는 전부 '정상'(초록)으로
// 보이던 문제를 여기서 없앤다. 원인은 두 색이 서로 다른 근거를 봤다는 것이다:
//   - 오류 분석: 경고(alert) 전체를 그대로 집계
//   - 캡처/체크리스트: 체크리스트 항목의 status만 봄
// 그런데 규칙 엔진이 낸 경고(rule_id 기반)는 engine/realtime_monitor.py의 CHECK_ITEMS 어디에도
// 매핑되지 않아서 체크리스트가 'pending(정상)'으로 남는다 — 그래서 경고가 205건이어도 장비는
// 초록이었다. 이제 두 출처의 최고 심각도를 합쳐 한 값으로 쓴다.
function rtmComputeSeverityByDevice(state) {
  const worst = {};
  const bump = (device, severity) => {
    if (!device || !severity || !RTM_SEV_RANK[severity]) return;
    if (!worst[device] || RTM_SEV_RANK[severity] > RTM_SEV_RANK[worst[device]]) {
      worst[device] = severity;
    }
  };
  // 1) 체크리스트에서 아직 해결되지 않은(fail/warn) 항목 — recovered/pending/unknown은 제외.
  (state.devices || []).forEach(d => {
    (d.checklist || []).forEach(c => {
      if (c.status === 'fail' || c.status === 'warn') bump(d.device, c.severity);
    });
  });
  // 2) 실시간 오류 분석이 집계한 미해결 경고 — 체크리스트에 매핑되지 않는 규칙 경고까지 포함된다.
  ((state.analysis || {}).findings || []).forEach(f => bump(f.device, f.severity));
  return worst;
}

// 장비의 '지금' 심각도. 위에서 계산한 통합 표를 우선 보고, 아직 계산 전이면 체크리스트로 대체한다.
function rtmDeviceSeverity(device) {
  const merged = rtmSeverityByDevice[device.device];
  if (merged) return merged;
  let worst = null;
  (device.checklist || []).forEach(c => {
    if ((c.status === 'fail' || c.status === 'warn') && c.severity
        && (!worst || RTM_SEV_RANK[c.severity] > RTM_SEV_RANK[worst])) {
      worst = c.severity;
    }
  });
  return worst; // null | 'WARNING' | 'MAJOR' | 'CRITICAL'
}

// 고정 항목을 Set/데이터 속성에 담을 때 쓰는 (장비, 점검항목) 합성 키.
// 장비명에 공백이 들어갈 수 있어 ' '로 이으면 split이 갈라지는 위치를 못 정한다 —
// 장비명·항목키에 절대 안 들어가는 제어문자(US, 0x1F)를 구분자로 쓴다.
const RTM_PIN_SEP = String.fromCharCode(31);   // US (unit separator)
function rtmPinKey(device, checkId) { return `${device}${RTM_PIN_SEP}${checkId}`; }

// 분할 보기의 장비 로그 박스 — 스크롤 없이 고정 높이로 최신 줄만 보여주고,
// 박스 여러 개를 담은 바깥 컨테이너만 위아래로 스크롤한다.
const RTM_BOX_HEIGHT = 176;       // px
const RTM_LINE_HEIGHT = 17;       // px — style.css의 .rtm-log-line과 맞춰야 클리핑이 정확하다
const RTM_BOX_LINES = Math.floor((RTM_BOX_HEIGHT - 30) / RTM_LINE_HEIGHT);

// ===== 페이지 렌더러 (core-navigation.js의 navigate('realtimewatch')) =====
async function renderRealtimeWatch() {
  const content = document.getElementById('content');
  const [targets, layout, filter] = await Promise.all([
    call('get_terminal_targets'),
    call('get_realtime_watch_layout'),
    call('get_realtime_filter'),
  ]);
  rtmTargets = targets || [];
  applyRtmLayout(layout);
  if (filter) rtmFilter = filter;

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
        <button class="btn btn-outlined" id="rtm-btn-settings" title="장비·규칙별 표시 여부와 상단 고정 항목을 설정합니다"><span class="material-symbols-rounded">tune</span>표시 설정</button>
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
      <!-- 숨김이 걸려 있으면 반드시 알린다 — 모르고 숨겨 두면 '경고가 안 뜬다'는 오해가 된다. -->
      <div class="rtm-hidden-row" id="rtm-hidden-row" style="display:none;"></div>
      <!-- 상단 고정 카드: 스크롤과 무관하게 항상 보여야 하는 핵심 항목 -->
      <div class="rtm-pinned-row" id="rtm-pinned-row" style="display:none;"></div>

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

  // 탭을 다시 열면 카드 DOM이 새로 만들어진다 — 직전 커서를 그대로 쓰면 서버가 '안 바뀌었다'며
  // 섹션을 생략하고 화면은 빈 채로 남는다.
  rtmResetDeltaCursor();
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
  document.getElementById('rtm-btn-settings').addEventListener('click', openRtmSettingsModal);

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
// OPTIMIZATION_PLAN 3-1. 예전에는 0.8초마다 **전체 스냅샷**을 받아 세 패널의 innerHTML을
// 통째로 갈아치웠다. 장비 30대·tail 160줄이면 응답이 701.7 KB(= 877 KB/s)였고, 그 중 88%가
// 이미 화면에 있는 로그 줄이었다. 이제 '어디까지 받았다'는 커서를 함께 보내고 서버는
// 새 줄과 바뀐 섹션만 돌려준다 — 실측 8.5 KB/s(104배 감소).
//
// 설계상 중요한 두 가지:
//   1) 렌더 함수들은 **여전히 완전한 상태만** 본다. 델타는 rtmMergeState()가 직전 상태에
//      합쳐 다시 완전한 상태로 만든 다음 넘긴다. 그래서 세 패널이 같은 스냅샷에서 파생된다는
//      이 모듈의 불변식이 유지되고(서버도 한 락 블록에서 만든다), 렌더 쪽 분기가 늘지 않는다.
//   2) 안 바뀐 패널은 다시 그리지 않는다. 이게 두 번째 이득이다 — DOM이 살아남으므로
//      클릭으로 고정한 교차 강조를 매 렌더마다 복원할 필요가 없고, 로그 박스는 교체가 아니라
//      append로 바뀌어 사용자의 스크롤/선택이 흔들리지 않는다.
const RTM_TAIL = 160;               // 서버에 요청하는 장비별 최근 줄 수 = 클라이언트 사본의 상한
const RTM_DELTA_SECTIONS = ['analysis', 'alerts', 'pinned', 'filter'];

let rtmCursor = null;   // {epoch, versions, devices:{장비: seq}} — 서버에 알리는 마지막 수신 위치

// 커서와 누적 사본을 버려 다음 폴링이 전체를 받게 한다. 탭을 다시 열면 DOM이 새 것이므로
// (렌더를 건너뛸 근거가 사라졌다) 반드시 여기를 지나야 한다.
function rtmResetDeltaCursor() {
  rtmCursor = null;
  rtmLastState = null;
}

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
  // 커서가 없으면(첫 폴링·탭 재진입·직전 응답이 불완전) 인자를 빼고 부른다 — 서버는 그 경로에서
  // 예전과 똑같이 전체를 돌려준다.
  const payload = rtmCursor
    ? await call('get_realtime_monitor_state', RTM_TAIL, rtmCursor)
    : await call('get_realtime_monitor_state', RTM_TAIL);
  if (!payload) return;

  const merged = rtmMergeState(rtmLastState, payload);
  rtmLastState = merged.state;
  // 다음 폴링의 기준점은 **합친 뒤의** 상태에서 뽑는다. 응답에 없던 섹션은 직전 지문을 그대로
  // 유지해야 서버가 계속 생략할 수 있다.
  rtmCursor = merged.complete ? rtmBuildCursor(merged.state) : null;
  renderRtmState(merged.state, merged);
}

// 델타 응답을 직전 완전 상태에 합쳐 다시 완전한 상태로 만든다.
// 반환: {state, changed:Set, complete:boolean}
//   changed  — 다시 그려야 하는 것들. 'logs'는 장비별 새 줄 목록(append 대상)을 함께 담는다.
//   complete — 합친 결과가 완전한지. 아니면 커서를 버려 다음 폴링에서 전체를 받는다.
function rtmMergeState(prev, payload) {
  const changed = new Set();
  const state = Object.assign({}, payload);
  let complete = true;

  // 1) 패널 단위 섹션 — null은 '안 바뀌었다'는 뜻이므로 직전 값을 그대로 쓴다.
  RTM_DELTA_SECTIONS.forEach(key => {
    if (payload[key] === null || payload[key] === undefined) {
      if (!prev || prev[key] === null || prev[key] === undefined) {
        // 직전 값이 없는데 서버가 생략했다 — 커서가 어긋났다. 화면을 비우지 않고 다음 폴링에서
        // 전체를 받아 메운다(빈 값으로 그리면 '오류가 사라졌다'는 잘못된 화면이 된다).
        complete = false;
      } else {
        state[key] = prev[key];
      }
    } else {
      changed.add(key);
    }
  });

  // 2) 장비별 로그 줄 — resync면 교체, 아니면 직전 사본 뒤에 붙인다.
  const prevByDevice = {};
  (prev && prev.devices || []).forEach(d => { prevByDevice[d.device] = d; });
  const appended = {};
  state.devices = (payload.devices || []).map(entry => {
    const before = prevByDevice[entry.device];
    const incoming = entry.lines || [];
    const merged = Object.assign({}, entry);

    if (entry.resync || !before) {
      merged.lines = incoming;
      if (!before || incoming.length !== (before.lines || []).length) changed.add('logs');
      else if (incoming.some((l, i) => l.seq !== before.lines[i].seq)) changed.add('logs');
    } else {
      merged.lines = incoming.length ? (before.lines || []).concat(incoming).slice(-RTM_TAIL)
                                     : (before.lines || []);
      if (incoming.length) {
        changed.add('logs');
        appended[entry.device] = incoming;
      }
    }

    // checklist가 null이면 직전 것을 되살린다 — 없으면 이 장비만이 아니라 체크리스트 패널
    // 전체(정상 건수 합계)가 틀려지므로 불완전으로 표시한다.
    if (entry.checklist === null || entry.checklist === undefined) {
      if (before && before.checklist) merged.checklist = before.checklist;
      else { merged.checklist = []; complete = false; }
    } else {
      changed.add('checklist');
    }
    return merged;
  });
  // resync로 줄이 통째로 갈린 장비는 append로는 처리할 수 없다 — 로그 박스를 재구성한다.
  const resynced = state.devices.some(d => d.resync);

  // 3) 장비 목록 자체(추가/삭제/숨김)가 바뀌면 좌측 배치와 탭이 달라진다.
  const prevNames = (prev && prev.devices || []).map(d => d.device).join('\n');
  if (prevNames !== state.devices.map(d => d.device).join('\n')) changed.add('devices');
  if (!prev || prev.epoch !== payload.epoch) changed.add('devices');

  return { state, changed, complete, appended, resynced };
}

function rtmBuildCursor(state) {
  const devices = {};
  (state.devices || []).forEach(d => { devices[d.device] = d.line_seq || 0; });
  return { epoch: state.epoch, versions: state.versions || {}, devices };
}

// delta(rtmMergeState의 결과)를 넘기면 바뀐 패널만 다시 그린다. 인자를 빼면 전부 그린다 —
// 보기 모드 변경처럼 서버 응답과 무관하게 화면을 재구성해야 할 때 쓴다.
function renderRtmState(state, delta = null) {
  if (!state || !document.getElementById('rtm-card')) return;
  const dirty = (key) => delta === null || delta.changed.has(key);

  // 서버가 소유한 필터가 폴링마다 같이 온다 — 다른 창/YAML에서 바뀌어도 화면이 따라온다.
  if (state.filter) rtmFilter = state.filter;
  // 세 패널이 같은 색을 쓰도록 장비별 심각도를 먼저 한 번만 계산한다(패널마다 따로 계산하면
  // 근거가 갈려서 '분석은 CRITICAL인데 장비는 초록'인 화면이 나온다).
  const prevSeverity = rtmSeverityByDevice;
  rtmSeverityByDevice = rtmComputeSeverityByDevice(state);
  // 심각도는 alerts와 checklist 둘 다에서 나온다. 색이 바뀌면 세 패널의 칩·테두리·정렬(심각도
  // 버킷)이 전부 달라지므로, 지문만 보고 건너뛰면 색이 한 박자 늦게 따라온다.
  const severityChanged = delta === null
    || JSON.stringify(prevSeverity) !== JSON.stringify(rtmSeverityByDevice);

  renderRtmToolbar(state);       // 버튼 두 개 — 지문을 볼 만한 비용이 아니다.
  renderRtmWarnRow(state);
  if (dirty('filter') || dirty('devices')) renderRtmHiddenRow(state);
  if (dirty('pinned') || severityChanged) renderRtmPinnedRow(state);
  if (dirty('devices') || dirty('checklist') || severityChanged) renderRtmDeviceTabs(state);
  // 로그는 append 경로가 있다 — 배치를 바꾸는 요인(장비 목록·심각도 버킷·보기 모드)이 그대로면
  // 새 줄만 DOM 뒤에 붙인다. resync(버퍼 밀림)면 그 장비의 줄이 통째로 갈렸으니 재구성한다.
  if (dirty('logs') || dirty('devices') || severityChanged) {
    const canAppend = delta && !severityChanged && !delta.resynced && !delta.changed.has('devices');
    renderRtmLogs(state, canAppend ? delta.appended : null);
  }
  if (dirty('analysis') || dirty('devices')) renderRtmAnalysis(state);
  if (dirty('checklist') || dirty('devices') || severityChanged) renderRtmChecklist(state);
  // 다시 그린 패널에 클릭으로 고정해 둔 교차 강조를 입힌다. 이제는 DOM이 살아남으므로 매
  // 폴링마다 복원할 필요는 없지만, 한 패널이라도 갈렸으면 그 패널의 강조는 사라진 상태다.
  applyRtmCrossHighlight();
}

// ===== 3-Way 교차 강조 (장애 원인 분석 ↔ 좌측 CLI 로그 ↔ 우측 체크리스트) =====
// 오류 카드 하나가 가리키는 장비들을, 세 패널에서 같은 색·같은 리듬으로 동시에 깜빡인다.
// "이 오류는 어느 장비의 로그에서 났고, 체크리스트의 어느 항목에 걸렸나"를 눈으로 잇는 것이
// 목적이다. hover는 미리보기(손을 떼면 원래대로), click은 고정(폴링 재렌더에도 유지).
let rtmXhlPinned = null;   // {devices: [name], sev: 'CRITICAL'|...} — 클릭으로 고정된 강조
let rtmXhlHover = null;    // 마우스를 올린 동안만 유효한 미리보기

const RTM_XHL_CLASSES = ['rtm-xhl', 'rtm-xhl-critical', 'rtm-xhl-source', 'rtm-xhl-source-critical'];

// 지금 유효한 강조(hover 우선, 없으면 고정)를 세 패널에 입힌다. 인자 없이 부르면 상태만 반영.
function applyRtmCrossHighlight() {
  document.querySelectorAll('.rtm-xhl, .rtm-xhl-critical, .rtm-xhl-source, .rtm-xhl-source-critical')
    .forEach(el => el.classList.remove(...RTM_XHL_CLASSES));
  const target = rtmXhlHover || rtmXhlPinned;
  if (!target || !target.devices.length) return;
  const critical = target.sev === 'CRITICAL';
  const cls = critical ? 'rtm-xhl-critical' : 'rtm-xhl';
  const srcCls = critical ? 'rtm-xhl-source-critical' : 'rtm-xhl-source';
  const devices = new Set(target.devices);

  // 1) 원인 카드 자신(어느 줄에서 시작된 강조인지)
  document.querySelectorAll('#rtm-analysis [data-rtm-devices]').forEach(el => {
    if (el.dataset.rtmXhlKey === target.key) el.classList.add(srcCls);
  });
  // 2) 좌측 CLI 로그 박스
  document.querySelectorAll('.rtm-log-box[data-rtm-xhl-device]')
    .forEach(el => { if (devices.has(el.dataset.rtmXhlDevice)) el.classList.add(cls); });
  // 3) 우측 하단 체크리스트 — 장비 그룹과 그 안에서 지금 이상인 항목 행까지.
  document.querySelectorAll('#rtm-checklist [data-rtm-detail]').forEach(el => {
    if (!devices.has(el.dataset.rtmDetail)) return;
    el.classList.add(cls);
    el.querySelectorAll('.rtm-check-fail, .rtm-check-warn').forEach(row => row.classList.add(cls));
  });
  document.querySelectorAll('#rtm-checklist [data-rtm-devtile]').forEach(el => {
    if (devices.has(el.dataset.rtmDevtile)) el.classList.add(cls);
  });
}

// 오류 카드(.rtm-err-row / .rtm-finding) 한 줄에 hover/click 강조를 붙인다.
function wireRtmCrossHighlight(row) {
  const devices = (row.dataset.rtmDevices || '').split('\n').filter(Boolean);
  const sev = row.dataset.rtmSev || 'WARNING';
  const key = row.dataset.rtmXhlKey || '';
  row.addEventListener('mouseenter', () => { rtmXhlHover = { devices, sev, key }; applyRtmCrossHighlight(); });
  row.addEventListener('mouseleave', () => { rtmXhlHover = null; applyRtmCrossHighlight(); });
  row.addEventListener('click', () => {
    // 같은 줄을 다시 누르면 고정 해제 — 계속 깜빡이는 화면을 사용자가 끌 수 있어야 한다.
    const same = rtmXhlPinned && rtmXhlPinned.key === key;
    rtmXhlPinned = same ? null : { devices, sev, key };
    applyRtmCrossHighlight();
  });
}

// ===== 숨김 안내 줄 =====
function renderRtmHiddenRow(state) {
  const row = document.getElementById('rtm-hidden-row');
  if (!row) return;
  const f = rtmFilter;
  const parts = [];
  if (f.hidden_devices.length) parts.push(`장비 ${f.hidden_devices.length}개`);
  if (f.hidden_rules.length) parts.push(`규칙 ${f.hidden_rules.length}개`);
  if (f.hidden_keywords.length) parts.push(`키워드 ${f.hidden_keywords.length}개`);
  if (!parts.length) { row.style.display = 'none'; row.innerHTML = ''; return; }
  const suppressed = (state.hidden_counts || {}).alerts || 0;
  row.style.display = 'flex';
  row.innerHTML = `
    <span class="material-symbols-rounded">visibility_off</span>
    <span>${parts.join(' · ')}를 숨기고 있습니다${suppressed ? ` — 경고 ${suppressed}건이 가려짐` : ''}.
      숨긴 항목: ${[...f.hidden_devices, ...f.hidden_rules, ...f.hidden_keywords]
        .slice(0, 6).map(rtEscape).join(', ')}${
        (f.hidden_devices.length + f.hidden_rules.length + f.hidden_keywords.length) > 6 ? ' …' : ''}</span>
    <span style="flex:1"></span>
    <button class="btn btn-outlined rtm-mini" id="rtm-unhide-all">모두 표시</button>`;
  row.querySelector('#rtm-unhide-all').addEventListener('click', async () => {
    const result = await call('clear_realtime_filter');
    if (result && result.error) { showToast(result.error, 'error'); return; }
    rtmFilter = result;
    showToast('숨김을 모두 해제했습니다.');
    await refreshRealtimeMonitor();
  });
}

// ===== 상단 고정 카드 =====
// 스크롤을 내리지 않고도 반드시 확인해야 하는 항목(예: Core1 전원, Core2 MLAG)을 위로 뽑는다.
// 체크리스트가 장비 x 7항목이라 장비가 늘면 핵심 항목이 화면 밖으로 밀리는 것이 이유다.
function renderRtmPinnedRow(state) {
  const row = document.getElementById('rtm-pinned-row');
  if (!row) return;
  const pinned = state.pinned || [];
  if (!pinned.length) { row.style.display = 'none'; row.innerHTML = ''; return; }
  row.style.display = 'flex';
  row.innerHTML = `<span class="rtm-target-label">고정</span>` + pinned.map(p => `
    <div class="rtm-pin-card rtm-pin-${p.status}" data-rtm-pin-device="${rtEscape(p.device)}"
         data-rtm-pin-check="${rtEscape(p.check_id)}"
         title="${rtEscape(p.detail || '')} (우클릭 → 고정 해제)">
      <span class="material-symbols-rounded">${RTM_STATUS_ICON[p.status] || 'help'}</span>
      <div class="rtm-pin-body">
        <div class="rtm-pin-head"><strong>${rtEscape(p.device)}</strong>
          <span class="rtm-pin-label">${rtEscape(p.label)}</span></div>
        <div class="rtm-pin-detail">${rtEscape(p.detail || '')}</div>
      </div>
      <span class="rtm-chip rtm-chip-${p.status === 'pending' ? 'ok' : p.status}">${
        RTM_STATUS_LABEL[p.status] || ''}</span>
    </div>`).join('');
  row.querySelectorAll('[data-rtm-pin-device]').forEach(el => {
    el.addEventListener('click', () => openRealtimeAlertDetail(el.dataset.rtmPinDevice));
    el.addEventListener('contextmenu', (e) => openRtmContextMenu(e, {
      device: el.dataset.rtmPinDevice, checkId: el.dataset.rtmPinCheck, pinned: true,
    }));
  });
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

// 장비를 알아내지 못한 로그 파일 — 대부분 이번 점검 대상이 아닌 장비의 로그다(그래서 감시도
// 하지 않는다). 예전에는 이걸 주황색 경고 줄로 상단에 띄웠는데, 정상적인 상황에서도 늘 떠 있어
// 진짜 경고를 가렸다. 이제 줄은 없애고 '파일 진단' 버튼 자체에 색과 개수를 얹는다 —
// 확인할 곳이 그 버튼이므로, 알림을 그 버튼 위에 두는 편이 짧다.
function renderRtmWarnRow(state) {
  const row = document.getElementById('rtm-warn-row');
  if (row) { row.style.display = 'none'; row.innerHTML = ''; }
  const btn = document.getElementById('rtm-btn-probe');
  if (!btn) return;
  const unmatched = state.unmatched_files || [];
  const flagged = !!state.running && unmatched.length > 0;
  btn.classList.toggle('rtm-btn-attention', flagged);
  btn.innerHTML = '<span class="material-symbols-rounded">find_in_page</span>파일 진단'
    + (flagged ? `<span class="rtm-chip rtm-chip-warn">${unmatched.length}</span>` : '');
  btn.title = flagged
    ? `장비를 알아내지 못한 로그 파일 ${unmatched.length}개 — 감시 대상이 아닙니다.`
      + ` 이번 점검 장비인데도 여기 있다면 파일명·프롬프트가 장비 목록과 다릅니다.`
      + ` (${unmatched.slice(0, 3).join(', ')}${unmatched.length > 3 ? ' …' : ''})`
    : 'CRTlog의 각 로그 파일이 어느 장비로 인식되는지 확인합니다';
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

// appended({장비: [새 줄]})를 넘기면 DOM을 갈지 않고 그 줄만 뒤에 붙인다. 이게 3-1의 두 번째
// 이득이다 — 로그 박스가 살아남으므로 사용자의 스크롤 위치, 텍스트 선택, 고정 강조가 흔들리지
// 않고, 장비 30대분 innerHTML 조립·파싱이 0.8초마다 사라진다.
// 붙일 수 없는 상황(배치가 바뀜, 박스가 아직 없음)이면 null을 넘겨 예전처럼 전부 다시 그린다.
function renderRtmLogs(state, appended = null) {
  const body = document.getElementById('rtm-left-body');
  if (appended && rtmAppendRtmLogLines(body, state, appended)) return;
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
    wireRtmLogBoxMenus(body);
    return;
  }

  // 심각도별로 묶어 심각(CRITICAL) 그룹이 맨 위, 그다음 주요/경고/정상 순으로 분리선과 함께 보인다 —
  // 스크롤을 내리지 않아도 지금 가장 문제인 장비가 먼저 보인다.
  const buckets = { CRITICAL: [], MAJOR: [], WARNING: [], NONE: [] };
  devices.forEach(d => buckets[rtmDeviceSeverity(d) || 'NONE'].push(d));
  const bucketLabel = { CRITICAL: '심각', MAJOR: '주요', WARNING: '경고', NONE: '정상' };
  body.innerHTML = ['CRITICAL', 'MAJOR', 'WARNING', 'NONE'].map(key => {
    if (!buckets[key].length) return '';
    return `<div class="rtm-log-sep rtm-log-sep-${key.toLowerCase()}">
        <span>${bucketLabel[key]}</span><span class="rtm-log-sep-count">${buckets[key].length}</span>
      </div>` + buckets[key].map(d => rtmLogBox(d, RTM_BOX_LINES, false)).join('');
  }).join('');
  // 분할 보기에서는 사용자가 위쪽 장비를 보고 있을 수 있으므로, 맨 아래에 있었을 때만 따라 내린다.
  body.scrollTop = atBottom ? body.scrollHeight : scrollTop;
  wireRtmLogBoxMenus(body);
}

// 새 줄만 기존 로그 박스 뒤에 붙인다. 붙일 수 없으면 false를 돌려주고 호출부가 전부 다시 그린다.
// 붙일 수 없는 경우: 박스가 아직 없다(첫 렌더), 또는 박스 내부 구조가 예상과 다르다.
function rtmAppendRtmLogLines(body, state, appended) {
  if (!body) return false;
  const boxes = {};
  body.querySelectorAll('.rtm-log-box[data-rtm-xhl-device]')
    .forEach(box => { boxes[box.dataset.rtmXhlDevice] = box; });
  if (!Object.keys(boxes).length) return false;

  for (const [device, lines] of Object.entries(appended)) {
    const box = boxes[device];
    // 탭 보기에서 지금 안 보이는 장비 — 사본(rtmLastState)에만 쌓아 두면 탭을 옮길 때 그려진다.
    if (!box) continue;
    const holder = box.querySelector('.rtm-log-lines');
    if (!holder) return false;
    // '입력 대기 중…' 자리표시자는 첫 줄이 들어오면 치운다.
    const idle = holder.querySelector('.rtm-log-idle');
    if (idle) idle.remove();
    lines.forEach(line => holder.appendChild(rtmLogLineNode(line)));
    // .rtm-log-lines는 overflow:hidden + justify-content:flex-end라 넘친 줄은 위로 잘려 보이지만,
    // 그대로 두면 DOM이 무한히 자란다 — 박스가 보여줄 수 있는 만큼만 남긴다.
    const max = Number(box.dataset.rtmBoxMax) || RTM_BOX_LINES;
    while (holder.childElementCount > max) holder.removeChild(holder.firstElementChild);
  }

  // 줄 수 배지는 서버 버퍼 길이(line_count)다 — 화면에 보이는 줄 수와 다르므로 따로 갱신한다.
  (state.devices || []).forEach(d => {
    const count = boxes[d.device] && boxes[d.device].querySelector('.rtm-log-count');
    if (count) count.textContent = `${d.line_count}줄`;
  });
  return true;
}

// 로그 한 줄을 DOM으로 만든다. textContent를 쓰므로 이스케이프가 필요 없다(rtEscape는 문자열
// 조립 경로용이다) — 로그 내용에 '<'가 들어와도 그대로 글자로 보인다.
function rtmLogLineNode(line) {
  const row = document.createElement('div');
  row.className = line.history ? 'rtm-log-line rtm-log-history' : 'rtm-log-line';
  const ts = document.createElement('span');
  ts.className = 'rtm-log-ts';
  ts.textContent = line.ts || '';
  row.appendChild(ts);
  row.appendChild(document.createTextNode(line.text || ''));
  return row;
}

// 로그 박스 헤더 우클릭 — 관심 없는 장비를 화면에서 빼는 가장 자연스러운 지점이다.
function wireRtmLogBoxMenus(body) {
  body.querySelectorAll('[data-rtm-box-device]').forEach(box => {
    box.addEventListener('contextmenu', (e) => openRtmContextMenu(e, {
      device: box.dataset.rtmBoxDevice,
    }));
  });
}

function rtmLogBox(device, maxLines, fill) {
  const lines = (device.lines || []).slice(-maxLines);
  const sev = rtmDeviceSeverity(device);
  const sevClass = sev ? `rtm-sev-${sev.toLowerCase()}` : 'rtm-ok';
  const badge = sev ? RTM_SEV_LABEL[sev] : '정상';
  const chipSuffix = sev ? RTM_SEV_CHIP[sev] : 'ok';
  return `
    <div class="rtm-log-box ${sevClass} ${fill ? 'rtm-log-box-fill' : ''}"
         data-rtm-xhl-device="${rtEscape(device.device)}"
         data-rtm-box-max="${maxLines}"
         style="${fill ? '' : `height:${RTM_BOX_HEIGHT}px`}">
      <div class="rtm-log-head" data-rtm-box-device="${rtEscape(device.device)}" title="우클릭 → 이 장비 숨기기">
        <strong>${rtEscape(device.device)}</strong>
        <span class="rtm-chip rtm-chip-${chipSuffix}">${badge}</span>
        ${device.has_baseline ? '' : '<span class="rtm-chip rtm-chip-unknown">Baseline 없음</span>'}
        <span style="flex:1"></span>
        <span class="rtm-log-count">${device.line_count}줄</span>
      </div>
      <div class="rtm-log-lines">${lines.length
        ? lines.map(l => `<div class="rtm-log-line${l.history ? ' rtm-log-history' : ''}"><span class="rtm-log-ts">${rtEscape(l.ts)}</span>${rtEscape(l.text)}</div>`).join('')
        : '<div class="rtm-log-line rtm-log-idle">입력 대기 중…</div>'}</div>
    </div>`;
}

// ===== 우측 상단: 실시간 오류 분석 (좌 1/4 목록 · 우 3/4 상세) =====
// 패널 전체가 두 열이다 — 제목/판정 헤드라인/건수 칩도 각 열 안에 들어간다. 예전에는 이것들이
// 패널 폭 전체를 쓰는 머리글이라 위쪽 두 줄을 통째로 먹었고, 정작 목록과 상세는 남은 높이만
// 나눠 썼다. 아래 실시간 체크리스트와 조작도 모양도 같아진다.
// 좌측: 제목 + 판정 헤드라인 + 발견된 오류를 심각(CRITICAL) → 주요(MAJOR) → 경고(WARNING) 순으로.
// 우측: 건수 칩 + 판정 요약 + 좌측에서 고른 오류가 "어느 장비의 어떤 설정이 잘못됐는지".
//
// 좌측 한 줄 = 오류 한 종류(장비 한 대가 아니다). 백엔드는 장비별로 finding을 만들기 때문에
// MLAG peer-link가 4대에서 끊기면 같은 제목의 finding이 4개 온다 — 그대로 나열하면 목록이
// 같은 문장 네 줄이 되고, '무슨 오류인지'가 아니라 '몇 대인지'만 보인다. 그래서 제목으로 묶어
// "MLAG peer-link 이상 — Core1 외 3대"처럼 한 줄로 보이게 하고, 상세에서 장비별로 펼친다.
//
// 선택은 rtmAnalysisSelectedKey(제목 기반 그룹 키)로 기억하므로 0.8초 폴링으로 목록이 새로 와도,
// 다른 탭에 갔다 와도 마지막에 고른 오류가 그대로 선택돼 있다. 선택한 오류가 해소되어
// 목록에서 사라진 경우에만 맨 위 오류로 자동 이동한다.
function renderRtmAnalysis(state) {
  const el = document.getElementById('rtm-analysis');
  const analysis = state.analysis || {};
  const counts = analysis.counts || {};
  const verdict = analysis.verdict || 'ok';

  const prevListTop = document.getElementById('rtm-analysis-list')?.scrollTop || 0;
  const prevDetailTop = document.getElementById('rtm-analysis-detail')?.scrollTop || 0;

  // 같은 오류(제목)를 장비 구분 없이 한 그룹으로 묶는다.
  const groups = rtmGroupFindings(analysis.findings || []);

  if (!groups.some(g => g.key === rtmAnalysisSelectedKey)) {
    rtmAnalysisSelectedKey = groups.length ? groups[0].key : null;
  }
  const selected = groups.find(g => g.key === rtmAnalysisSelectedKey) || null;

  const selectionChanged = rtmAnalysisSelectedKey !== rtmAnalysisRenderedKey;
  rtmAnalysisRenderedKey = rtmAnalysisSelectedKey;

  el.innerHTML = `
    <div class="rtm-analysis-cols">
      <div class="rtm-analysis-col rtm-analysis-col-list" id="rtm-analysis-list">
        <div class="rtm-pane-head rtm-pane-head-sticky">
          <span class="rtm-pane-title">실시간 오류 분석</span>
        </div>
        <div class="rtm-verdict rtm-${verdict}">
          <div class="rtm-verdict-headline">${rtEscape(analysis.headline || '')}</div>
        </div>
        ${groups.length ? groups.map(g => {
          const sev = g.severity;
          // 목록의 대표 문구 — 같은 분류 안에서 가장 심각한 finding의 제목을 미리보기로 보여준다.
          // (본 라벨은 "분류 / 대수"이고, 이건 그 아래 한 줄 요약이다. 전체 내용은 우측 상세에 있다.)
          const preview = g.items.slice().sort((a, b) =>
            (RTM_SEV_RANK[b.severity] || 0) - (RTM_SEV_RANK[a.severity] || 0))[0];
          return `
          <div class="rtm-err-row rtm-sev-${sev.toLowerCase()} ${g.key === rtmAnalysisSelectedKey ? 'active' : ''}"
               data-rtm-err="${rtEscape(g.key)}"
               data-rtm-xhl-key="${rtEscape(g.key)}"
               data-rtm-devices="${rtEscape(g.devices.join('\n'))}"
               data-rtm-sev="${rtEscape(sev)}"
               title="클릭/마우스오버 → 이 오류를 낸 장비의 좌측 로그와 우측 체크리스트를 같이 강조 (클릭은 고정, 다시 클릭하면 해제) / 우클릭 → 숨기기">
            <div class="rtm-err-row-head">
              <span class="rtm-chip rtm-chip-${RTM_SEV_CHIP[sev] || 'warn'}">${RTM_SEV_LABEL[sev] || sev}</span>
              ${g.fromHistory ? '<span class="rtm-chip rtm-chip-unknown" title="이전 세션·이전 실행의 로그에서 찾은 오류입니다">이전 기록</span>' : ''}
              <span class="rtm-cat-count" style="margin-left:auto">${g.count}건</span>
            </div>
            <div class="rtm-err-category">${rtEscape(g.label)} / ${g.devices.length}대</div>
            <div class="rtm-err-title">${rtEscape((preview && preview.title) || '')}</div>
            <div class="rtm-err-devices">${g.devices.map(d => `<em>${rtEscape(d)}</em>`).join('')}</div>
          </div>`;
        }).join('') : '<p class="rtm-empty">이상 없음 — 오류가 발생하면 심각/주요/경고 순으로 여기에 표시됩니다.</p>'}
      </div>
      <div class="rtm-analysis-col rtm-analysis-col-detail" id="rtm-analysis-detail">
        <div class="rtm-pane-head rtm-pane-head-sticky rtm-analysis-detail-head">
          <span style="flex:1"></span>
          <span class="rtm-chip rtm-chip-fail">심각 ${counts.CRITICAL || 0}</span>
          <span class="rtm-chip rtm-chip-major">주요 ${counts.MAJOR || 0}</span>
          <span class="rtm-chip rtm-chip-warn">경고 ${counts.WARNING || 0}</span>
        </div>
        <div class="rtm-verdict rtm-${verdict} rtm-verdict-summary-only">
          <div class="rtm-verdict-summary">${rtEscape(analysis.summary || '')}</div>
        </div>
        ${selected ? rtmAnalysisGroupDetail(selected, state)
          : '<p class="rtm-empty">왼쪽에서 오류를 선택하면 어느 장비의 어떤 설정이 잘못됐는지 표시됩니다.</p>'}
      </div>
    </div>`;

  const listEl = document.getElementById('rtm-analysis-list');
  const detailEl = document.getElementById('rtm-analysis-detail');
  if (listEl) listEl.scrollTop = prevListTop;
  // 선택이 실제로 바뀐 경우에만 상세를 맨 위로 — 같은 선택으로 폴링 갱신될 때는 보던 위치 유지.
  if (detailEl) detailEl.scrollTop = selectionChanged ? 0 : prevDetailTop;

  listEl?.querySelectorAll('[data-rtm-err]').forEach(row => {
    // 3-Way 교차 강조 — 이 오류를 낸 장비의 로그/체크리스트를 같이 깜빡인다.
    wireRtmCrossHighlight(row);
    row.addEventListener('click', () => {
      rtmAnalysisSelectedKey = row.dataset.rtmErr;
      renderRtmAnalysis(state);
    });
    // 그룹은 여러 장비를 묶고 있다 — 우클릭 '이 장비 숨기기'는 장비가 한 대일 때만 뜻이 분명하다.
    const g = groups.find(x => x.key === row.dataset.rtmErr);
    row.addEventListener('contextmenu', (e) => openRtmContextMenu(e, {
      device: g && g.devices.length === 1 ? g.devices[0] : undefined,
      // 대분류(그룹) 전체 일괄 처리용 — 그룹에 속한 모든 finding의 alert_id를 모아 넘긴다.
      groupLabel: g ? g.label : '',
      groupDeviceCount: g ? g.devices.length : 0,
      groupAlertIds: g ? g.items.flatMap(f => f.alert_ids || []) : [],
    }));
  });
  detailEl?.querySelectorAll('[data-rtm-detail-device]').forEach(node => {
    node.addEventListener('contextmenu', (e) =>
      openRtmContextMenu(e, { device: node.dataset.rtmDetailDevice }));
    // 상세는 장비 한 대 단위다 — 여기서 짚으면 그 한 대만 좌우에서 강조된다.
    node.dataset.rtmDevices = node.dataset.rtmDetailDevice || '';
    node.dataset.rtmXhlKey = `detail:${node.dataset.rtmDetailDevice || ''}`;
    node.dataset.rtmSev = selected ? selected.severity : 'WARNING';
    wireRtmCrossHighlight(node);
  });

  // 클릭으로 고정해 둔 교차 강조를 방금 새로 그린 DOM에 다시 입힌다.
  applyRtmCrossHighlight();

  detailEl?.querySelectorAll('[data-rtm-resolve]').forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      rtmResolveOrIgnore(btn, 'resolve_realtime_finding', '해결 처리했습니다.');
    });
  });
  detailEl?.querySelectorAll('[data-rtm-ignore]').forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      rtmResolveOrIgnore(btn, 'ignore_realtime_finding', '무시 처리했습니다 — 이력에는 남습니다.');
    });
  });
}

// '해결'/'무시' 버튼 공통 처리 — alert_ids를 백엔드로 보내고, 다음 폴링을 기다리지 않고
// 바로 다시 그려서 방금 누른 finding이 즉시 목록에서 빠지는 것처럼 보이게 한다.
async function rtmResolveOrIgnore(btn, apiName, toastMessage) {
  const ids = JSON.parse(btn.dataset.rtmResolve || btn.dataset.rtmIgnore || '[]');
  if (!ids.length) return;
  btn.classList.add('loading');
  btn.disabled = true;
  await rtmBulkResolveOrIgnore(ids, apiName, toastMessage);
  btn.classList.remove('loading');
  btn.disabled = false;
}

// 대분류(그룹) 우클릭 '모두 해결'/'모두 무시' — 그룹에 묶인 모든 finding의 alert_id를
// 한 번에 백엔드로 보낸다. 버튼 하나짜리 처리(rtmResolveOrIgnore)와 로직은 같고,
// 우클릭 메뉴는 특정 DOM 버튼이 없으므로 이 함수를 직접 공유한다.
async function rtmBulkResolveOrIgnore(ids, apiName, toastMessage) {
  if (!ids || !ids.length) return;
  const result = await call(apiName, ids);
  if (!result) { showToast('응답이 없습니다.', 'error'); return; }
  if (result.error) { showToast(result.error, 'error'); return; }
  showToast(toastMessage);
  await refreshRealtimeMonitor();
}

// 같은 기술 분류(category — VLAN/인터페이스/링크/BGP·OSPF 인접/STP·MLAG/운영 명령/기타)의
// finding들을 한 그룹으로 묶는다. 제목 문장이 아니라 분류로 묶는 이유: 장비마다 메시지가
// 조금씩 달라서("VLAN 100 삭제" vs "VLAN 200 삭제") 제목 기준으로는 좀처럼 안 뭉쳐졌다 —
// "VLAN에서 문제가 났다"처럼 큰 분류로 몇 대가 걸렸는지 한눈에 보이는 것이 목적이다.
// 그룹의 심각도는 소속 finding 중 최고치 — 4대 중 1대만 CRITICAL이어도 그룹은 CRITICAL이어야
// '맨 위가 가장 급한 것'이라는 목록 규칙이 유지된다.
// 그룹 키는 category다: 폴링마다 finding 객체가 새로 와도 category가 같으면 같은 그룹이므로
// 선택이 유지된다(장비가 한 대 더 늘거나 빠져도 선택이 튀지 않는다).
function rtmGroupFindings(findings) {
  const byCategory = new Map();
  findings.forEach(f => {
    const category = f.category || 'etc';
    const label = f.category_label || '기타';
    let g = byCategory.get(category);
    if (!g) {
      g = { key: category, label, severity: f.severity || 'WARNING', count: 0, devices: [], items: [],
            fromHistory: true };
      byCategory.set(category, g);
    }
    if ((RTM_SEV_RANK[f.severity] || 0) > (RTM_SEV_RANK[g.severity] || 0)) g.severity = f.severity;
    // 한 장비라도 '지금' 근거가 있으면 그룹은 지난 기록이 아니다.
    if (!f.from_history) g.fromHistory = false;
    g.count += f.count || 0;
    if (f.device && !g.devices.includes(f.device)) g.devices.push(f.device);
    g.items.push(f);
  });
  // 심각도 내림차순 → 같은 심각도면 장비 수 → 발생 건수. (백엔드도 비슷한 순서로 주지만,
  // 묶은 뒤의 순서는 화면이 보장해야 한다.)
  return [...byCategory.values()].sort((a, b) =>
    (RTM_SEV_RANK[b.severity] || 0) - (RTM_SEV_RANK[a.severity] || 0)
    || b.devices.length - a.devices.length
    || b.count - a.count);
}

// 우측 상세 — 그룹을 장비별로 펼친다. 오른쪽 열 전체를 쓰고, 장비가 많아 길어지면 열이 스크롤된다.
// 장비 섹션마다 원인 추정 / 권고 조치 / 문제가 된 입력·출력 / 그 장비의 이상 체크리스트 항목을
// 모두 보여준다 — "MLAG에서 4대 오류"에서 각 장비가 어떤 상태인지가 여기서 갈린다.
function rtmAnalysisGroupDetail(group, state) {
  const sev = group.severity;
  return `
    <div class="rtm-detail-head">
      <span class="rtm-chip rtm-chip-${RTM_SEV_CHIP[sev] || 'warn'}">${RTM_SEV_LABEL[sev] || sev}</span>
      <span class="rtm-err-devcount">경고 ${group.count}건</span>
    </div>
    <div class="rtm-detail-title">${rtEscape(group.label)} / ${group.devices.length}대</div>
    <div class="rtm-detail-devchips">${group.devices.map(d => {
      const dsev = rtmSeverityByDevice[d];
      return `<span class="rtm-chip rtm-chip-${dsev ? RTM_SEV_CHIP[dsev] : 'ok'}">${rtEscape(d)}</span>`;
    }).join('')}</div>
    ${group.items.map(f => `
      <div class="rtm-detail-device-block">${rtmAnalysisDetail(f, state)}</div>`).join('')}`;
}

// 그룹 안의 장비 한 대 — 이 오류가 '어느 장비의 어떤 설정' 문제인지까지 내려간다.
// finding 자체(원인 추정/권고 조치/근거)에 더해, 같은 장비의 체크리스트에서 지금 이상인 항목을
// 함께 보여준다. 그래야 "MLAG 상태 변화"처럼 뭉뚱그려진 제목만 보고 끝나지 않는다.
function rtmAnalysisDetail(f, state) {
  const device = (state.devices || []).find(d => d.device === f.device);
  const badItems = (device?.checklist || [])
    .filter(c => c.status === 'fail' || c.status === 'warn')
    .sort((a, b) => (RTM_SEV_RANK[b.severity] || 0) - (RTM_SEV_RANK[a.severity] || 0));
  const sev = f.severity || 'WARNING';
  return `
    <div class="rtm-detail-head" data-rtm-detail-device="${rtEscape(f.device || '')}">
      <span class="rtm-chip rtm-chip-${RTM_SEV_CHIP[sev] || 'warn'}">${RTM_SEV_LABEL[sev] || sev}</span>
      <strong class="rtm-detail-device">${rtEscape(f.device || '')}</strong>
      ${device && !device.has_baseline ? '<span class="rtm-chip rtm-chip-unknown">Baseline 없음</span>' : ''}
      ${f.count ? `<span class="rtm-cat-count">${f.count}</span>` : ''}
    </div>
    <div class="rtm-finding-line"><span class="rtm-label">원인 추정</span>${rtEscape(f.cause || '')}</div>
    ${f.root_cause ? `<div class="rtm-finding-line rtm-finding-intent">
      <span class="rtm-label">작업 연관</span>${rtEscape(f.root_cause.intent || '')}
      <code>${rtEscape(f.root_cause.raw_line || '')}</code></div>` : ''}
    <div class="rtm-finding-line"><span class="rtm-label">권고 조치</span>${rtEscape(f.action || '')}</div>
    ${(f.alert_ids || []).length ? `
    <div class="rtm-finding-actions">
      <button class="btn btn-outlined" data-rtm-resolve="${rtEscape(JSON.stringify(f.alert_ids))}"
              title="조치를 완료했습니다 — 체크리스트도 '복구'로 바뀝니다">
        <span class="material-symbols-rounded">check_circle</span>해결
      </button>
      <button class="btn btn-outlined" data-rtm-ignore="${rtEscape(JSON.stringify(f.alert_ids))}"
              title="지금 조치 목록에서만 뺍니다 — 이력에는 남고 체크리스트 상태는 그대로 유지됩니다">
        <span class="material-symbols-rounded">visibility_off</span>무시
      </button>
    </div>` : ''}
    ${(f.evidence || []).filter(Boolean).length ? `
      <div class="rtm-detail-section">문제가 된 입력·출력</div>
      <div class="rtm-finding-evidence">${f.evidence.filter(Boolean)
        .map(e => `<code>${rtEscape(e)}</code>`).join('')}</div>` : ''}
    ${badItems.length ? `
      <div class="rtm-detail-section">이 장비에서 이상으로 판정된 설정 항목</div>
      ${badItems.map(c => `
        <div class="rtm-check-row rtm-check-${c.status} ${c.severity ? 'rtm-check-sev-' + c.severity.toLowerCase() : ''}">
          <span class="material-symbols-rounded">${RTM_STATUS_ICON[c.status] || 'help'}</span>
          <span class="rtm-check-label">${rtEscape(c.label)}</span>
          <span class="rtm-check-detail">${rtEscape(c.detail || '')}</span>
          ${c.count ? `<span class="rtm-check-count">${c.count}</span>` : ''}
          ${c.last_ts ? `<span class="rtm-check-ts">${rtEscape(c.last_ts)}</span>` : ''}
        </div>`).join('')}`
      : `<div class="rtm-detail-note">이 경고는 체크리스트 항목(VLAN·인터페이스·라우팅 등)에
           매핑되지 않는 규칙 기반 탐지입니다 — 위의 원문을 직접 확인하세요.</div>`}`;
}

// ===== 우측 하단: 장비 체크리스트 =====
// 좌측은 장비를 2열 그리드로 보여주고, 지금 경고/주요/심각 중인 장비는 그 색으로 박스를
// 하이라이트한다(위 실시간 오류 분석과 같은 색 체계). 장비를 클릭하면 우측 목록에서 그
// 장비의 체크리스트 그룹을 맨 위로 스크롤한다 — 선택 상태는 모듈 전역이라 다른 탭에
// 갔다 와도 그대로 유지된다.
function renderRtmChecklist(state) {
  const el = document.getElementById('rtm-checklist');
  const devices = state.devices || [];
  const totalFail = devices.reduce((n, d) => n + d.fail_count, 0);
  const prevGroupsTop = document.getElementById('rtm-checklist-groups')?.scrollTop || 0;

  el.innerHTML = `
    <div class="rtm-pane-head rtm-pane-head-sticky">
      <span class="rtm-pane-title">실시간 체크리스트</span>
      <span style="flex:1"></span>
      <span class="rtm-chip rtm-chip-fail">이상 ${totalFail}</span>
      <span class="rtm-chip rtm-chip-ok">정상 ${devices.reduce((n, d) => n + d.checklist.filter(c => c.status === 'pending').length, 0)}</span>
    </div>
    ${devices.length ? `
    <div class="rtm-checklist-body">
      <div class="rtm-checklist-devgrid" id="rtm-checklist-devgrid">
        ${devices.map(d => {
          const sev = rtmDeviceSeverity(d);
          return `
          <div class="rtm-checklist-devtile ${sev ? 'rtm-sev-' + sev.toLowerCase() : 'rtm-sev-ok'} ${d.device === rtmChecklistSelectedDevice ? 'active' : ''}"
               data-rtm-devtile="${rtEscape(d.device)}" title="클릭 → 이 장비의 체크리스트로 이동">
            <span class="rtm-chip rtm-chip-${sev ? RTM_SEV_CHIP[sev] : 'ok'}">${sev ? RTM_SEV_LABEL[sev] : '정상'}</span>
            <span class="rtm-checklist-devtile-name">${rtEscape(d.device)}</span>
          </div>`;
        }).join('')}
      </div>
      <div class="rtm-checklist-groups" id="rtm-checklist-groups">
        ${devices.map(rtmChecklistGroup).join('')}
      </div>
    </div>` : '<p class="rtm-empty">감시를 시작하면 장비별 점검 항목이 표시됩니다.</p>'}`;

  const groupsEl = document.getElementById('rtm-checklist-groups');
  if (groupsEl) groupsEl.scrollTop = prevGroupsTop;

  el.querySelectorAll('[data-rtm-devtile]').forEach(tile => {
    tile.addEventListener('click', () => {
      rtmChecklistSelectedDevice = tile.dataset.rtmDevtile;
      renderRtmChecklist(state);
      const target = [...(document.getElementById('rtm-checklist-groups')?.querySelectorAll('[data-rtm-detail]') || [])]
        .find(g => g.dataset.rtmDetail === rtmChecklistSelectedDevice);
      target?.scrollIntoView({ block: 'start' });
    });
  });

  el.querySelectorAll('[data-rtm-detail]').forEach(row => {
    row.addEventListener('click', () => openRealtimeAlertDetail(row.dataset.rtmDetail));
    // 그룹 헤더(장비 단위) 우클릭 — 장비 숨기기
    row.addEventListener('contextmenu', (e) => {
      // 안쪽 항목 행에서 올라온 이벤트는 그쪽에서 이미 처리했다(stopPropagation).
      openRtmContextMenu(e, { device: row.dataset.rtmDetail });
    });
  });
  // 항목 행 우클릭 — 고정/해제 + 장비 숨기기
  el.querySelectorAll('[data-rtm-check]').forEach(row => {
    row.addEventListener('contextmenu', (e) => openRtmContextMenu(e, {
      device: row.dataset.rtmCheckDevice,
      checkId: row.dataset.rtmCheck,
      pinned: row.dataset.rtmPinned === '1',
    }));
  });
}

// 이상 항목을 위로 올린다 — 스크롤을 내리지 않고도 문제부터 보이게.
const RTM_STATUS_ORDER = { fail: 0, warn: 1, recovered: 2, pending: 3, unknown: 4 };
const RTM_STATUS_LABEL = { fail: '이상', warn: '주의', recovered: '복구', pending: '정상', unknown: '기준없음' };
const RTM_STATUS_ICON = { fail: 'cancel', warn: 'warning', recovered: 'restart_alt', pending: 'check_circle', unknown: 'help' };

function rtmChecklistGroup(device) {
  const items = [...device.checklist].sort(
    (a, b) => (RTM_STATUS_ORDER[a.status] ?? 9) - (RTM_STATUS_ORDER[b.status] ?? 9));
  const active = device.device === rtmChecklistSelectedDevice;
  return `
    <div class="rtm-check-group ${active ? 'rtm-check-group-active' : ''}" data-rtm-detail="${rtEscape(device.device)}">
      <div class="rtm-check-group-head">
        <span class="rtm-chip rtm-chip-${device.status}">${RTM_STATUS_LABEL[device.status] || ''}</span>
        <strong>${rtEscape(device.device)}</strong>
        <span style="flex:1"></span>
        <span class="rtm-check-hint">클릭 → 세부 이력</span>
      </div>
      ${items.map(c => {
        // 경고/주요/심각 3단계 색을 행 배경에 얹는다 — 위 실시간 오류 분석과 같은 색으로
        // '지금 이게 어느 정도 심각한지'를 fail/warn 2단계보다 더 직관적으로 보여준다.
        const sevClass = (c.status === 'fail' || c.status === 'warn') && c.severity
          ? `rtm-check-sev-${c.severity.toLowerCase()}` : '';
        return `
        <div class="rtm-check-row rtm-check-${c.status} ${sevClass} ${c.pinned ? 'rtm-check-pinned' : ''}"
             data-rtm-check="${rtEscape(c.key)}" data-rtm-check-device="${rtEscape(device.device)}"
             data-rtm-pinned="${c.pinned ? '1' : '0'}" title="우클릭 → 고정 / 숨기기">
          <span class="material-symbols-rounded">${RTM_STATUS_ICON[c.status] || 'help'}</span>
          <span class="rtm-check-label">${rtEscape(c.label)}</span>
          ${c.pinned ? '<span class="material-symbols-rounded rtm-pin-icon" title="상단 고정됨">push_pin</span>' : ''}
          <span class="rtm-check-detail">${rtEscape(c.detail || '')}</span>
          ${c.count ? `<span class="rtm-check-count">${c.count}</span>` : ''}
          ${c.last_ts ? `<span class="rtm-check-ts">${rtEscape(c.last_ts)}</span>` : ''}
        </div>`;
      }).join('')}
    </div>`;
}

// ===== 우클릭 컨텍스트 메뉴 (Module 4) =====
// connection-context-menu.js의 .term-ctx-menu 스타일을 재사용한다 — 같은 앱 안에서 우클릭
// 메뉴가 두 가지 모양이면 학습된 조작이 깨진다.
function closeRtmCtxMenu() {
  document.querySelectorAll('.rtm-ctx-menu').forEach(el => el.remove());
  document.removeEventListener('click', closeRtmCtxMenu);
}

// ctx: {device?, checkId?, ruleId?, keyword?, pinned?}
function openRtmContextMenu(event, ctx) {
  event.preventDefault();
  event.stopPropagation();
  closeRtmCtxMenu();

  const items = [];
  if (ctx.groupAlertIds && ctx.groupAlertIds.length) {
    const label = ctx.groupLabel || '이 오류';
    items.push({
      label: `모두 해결 (${label} · ${ctx.groupAlertIds.length}건${ctx.groupDeviceCount ? `, ${ctx.groupDeviceCount}대` : ''})`,
      icon: 'check_circle',
      action: () => rtmBulkResolveOrIgnore(ctx.groupAlertIds, 'resolve_realtime_finding',
        `'${label}' 오류를 모두 해결 처리했습니다.`),
    });
    items.push({
      label: `모두 무시 (${label} · ${ctx.groupAlertIds.length}건${ctx.groupDeviceCount ? `, ${ctx.groupDeviceCount}대` : ''})`,
      icon: 'visibility_off',
      action: () => rtmBulkResolveOrIgnore(ctx.groupAlertIds, 'ignore_realtime_finding',
        `'${label}' 오류를 모두 무시 처리했습니다 — 이력에는 남습니다.`),
    });
    items.push({ sep: true });
  }
  if (ctx.device && ctx.checkId) {
    items.push({
      label: ctx.pinned ? '상단 고정 해제' : '상단에 고정',
      icon: ctx.pinned ? 'keep_off' : 'keep',
      action: () => applyRtmFilterCall('toggle_realtime_pin', ctx.device, ctx.checkId,
        !ctx.pinned, ctx.pinned ? '고정을 해제했습니다.' : `${ctx.device} · ${ctx.checkId}를 상단에 고정했습니다.`),
    });
  }
  if (ctx.ruleId) {
    items.push({
      label: `이 규칙 숨기기 (${ctx.ruleId})`,
      icon: 'rule_folder',
      action: () => applyRtmFilterCall('toggle_realtime_filter_entry', 'rule', ctx.ruleId, true,
        `규칙 '${ctx.ruleId}'을(를) 숨겼습니다.`),
    });
  }
  if (ctx.device) {
    items.push({
      label: `이 장비 숨기기 (${ctx.device})`,
      icon: 'visibility_off',
      action: () => applyRtmFilterCall('toggle_realtime_filter_entry', 'device', ctx.device, true,
        `장비 '${ctx.device}'을(를) 화면에서 숨겼습니다. 감시는 계속됩니다.`),
    });
  }
  if (ctx.keyword) {
    items.push({
      label: `이 키워드 숨기기 (${ctx.keyword})`,
      icon: 'text_decrease',
      action: () => applyRtmFilterCall('toggle_realtime_filter_entry', 'keyword', ctx.keyword, true,
        `키워드 '${ctx.keyword}'를 숨겼습니다.`),
    });
  }
  const hiddenTotal = rtmFilter.hidden_rules.length + rtmFilter.hidden_devices.length
                    + rtmFilter.hidden_keywords.length;
  items.push({ sep: true });
  items.push({
    label: `숨김 모두 해제${hiddenTotal ? ` (${hiddenTotal})` : ''}`,
    icon: 'visibility',
    disabled: !hiddenTotal,
    action: async () => {
      const result = await call('clear_realtime_filter');
      if (result && result.error) { showToast(result.error, 'error'); return; }
      rtmFilter = result;
      showToast('숨김을 모두 해제했습니다.');
      await refreshRealtimeMonitor();
    },
  });
  items.push({
    label: '표시 설정 열기…', icon: 'tune', action: openRtmSettingsModal,
  });

  const menu = document.createElement('div');
  menu.className = 'term-ctx-menu rtm-ctx-menu';
  menu.style.left = event.clientX + 'px';
  menu.style.top = event.clientY + 'px';
  menu.innerHTML = items.map((it, i) => it.sep
    ? '<div class="term-ctx-menu-sep"></div>'
    : `<div class="term-ctx-menu-item ${it.disabled ? 'disabled' : ''}" data-idx="${i}">
         <span class="material-symbols-rounded" style="font-size:16px;">${it.icon}</span>${rtEscape(it.label)}</div>`
  ).join('');
  menu.querySelectorAll('[data-idx]').forEach(el => {
    const it = items[parseInt(el.dataset.idx, 10)];
    if (it.disabled) return;
    el.addEventListener('click', async (ev) => {
      ev.stopPropagation();
      closeRtmCtxMenu();
      await it.action();
    });
  });
  document.body.appendChild(menu);
  // 화면 밖으로 나가면 안쪽으로 당긴다(패널 우측/하단에서 우클릭하는 경우가 흔하다).
  const rect = menu.getBoundingClientRect();
  if (rect.right > window.innerWidth) menu.style.left = (window.innerWidth - rect.width - 8) + 'px';
  if (rect.bottom > window.innerHeight) menu.style.top = (window.innerHeight - rect.height - 8) + 'px';
  setTimeout(() => document.addEventListener('click', closeRtmCtxMenu), 0);
}

async function applyRtmFilterCall(method, ...args) {
  const message = args.pop();
  const result = await call(method, ...args);
  if (!result || result.error) { showToast((result && result.error) || '설정 저장 실패', 'error'); return; }
  rtmFilter = result;
  showToast(message);
  await refreshRealtimeMonitor();
}

// ===== 표시 설정 모달 (계층 체크박스 트리) =====
async function openRtmSettingsModal() {
  const catalog = await call('get_realtime_checklist_catalog');
  if (!catalog || catalog.error) { showToast((catalog && catalog.error) || '설정을 불러올 수 없습니다.', 'error'); return; }
  document.getElementById('rtm-settings-modal')?.remove();

  // 모달 안에서는 로컬 사본을 고치고 '저장'에서 한 번에 보낸다 — 체크박스 하나마다
  // 파일을 쓰면 트리를 훑는 동안 디스크 쓰기가 수십 번 발생한다.
  const draft = {
    hidden_devices: new Set(catalog.hidden_devices || []),
    hidden_rules: new Set(catalog.hidden_rules || []),
    hidden_keywords: [...(catalog.hidden_keywords || [])],
    pinned: new Set((catalog.pinned_items || []).map(p => rtmPinKey(p.device, p.check_id))),
  };
  const devices = catalog.devices || [];
  const checks = catalog.checks || [];

  const overlay = document.createElement('div');
  overlay.id = 'rtm-settings-modal';
  overlay.className = 'modal-overlay';
  overlay.innerHTML = `
    <div class="card rtm-settings-modal">
      <div class="rt-alert-modal-head">
        <div>
          <h3 class="card-title">실시간 감시 표시 설정</h3>
          <p class="card-desc">숨긴 항목은 판정에서 빠지지 않습니다 — 화면과 토스트에서만 가려지고 이력에는 남습니다.</p>
        </div>
        <div style="display:flex;gap:6px">
          <button class="btn btn-primary" type="button" data-rtm-save>저장</button>
          <button class="btn btn-outlined" type="button" data-rtm-close>닫기</button>
        </div>
      </div>
      <div class="rtm-settings-body">
        <section class="rtm-tree">
          <h4 class="rtm-tree-title">장비 · 점검항목</h4>
          <p class="rtm-tree-hint">체크를 끄면 그 장비가 화면에서 사라집니다(감시는 계속). 항목의 핀을 켜면 상단 고정 카드로 올라갑니다.</p>
          ${devices.length ? devices.map(d => `
            <div class="rtm-tree-node">
              <label class="rtm-tree-row">
                <input type="checkbox" data-rtm-dev="${rtEscape(d)}" ${draft.hidden_devices.has(d) ? '' : 'checked'}>
                <strong>${rtEscape(d)}</strong>
              </label>
              <div class="rtm-tree-children">
                ${checks.map(c => `
                  <label class="rtm-tree-row rtm-tree-leaf">
                    <input type="checkbox" data-rtm-pin="${rtEscape(rtmPinKey(d, c.key))}"
                      ${draft.pinned.has(rtmPinKey(d, c.key)) ? 'checked' : ''}>
                    <span class="material-symbols-rounded rtm-pin-icon">push_pin</span>
                    ${rtEscape(c.label)} <em>${rtEscape(c.key)}</em>
                  </label>`).join('')}
              </div>
            </div>`).join('')
            : '<p class="rtm-empty">장비 목록에 활성 장비가 없습니다.</p>'}
        </section>

        <section class="rtm-tree">
          <h4 class="rtm-tree-title">규칙</h4>
          <p class="rtm-tree-hint">체크를 끈 규칙의 경고는 화면에 뜨지 않습니다. 오탐이 잦은 규칙을 잠시 접어 두는 용도입니다.</p>
          <div class="rtm-tree-children rtm-rule-list">
            ${(catalog.rule_ids || []).map(r => `
              <label class="rtm-tree-row rtm-tree-leaf">
                <input type="checkbox" data-rtm-rule="${rtEscape(r)}" ${draft.hidden_rules.has(r) ? '' : 'checked'}>
                <code>${rtEscape(r)}</code>
              </label>`).join('')}
          </div>

          <h4 class="rtm-tree-title">키워드 숨김</h4>
          <p class="rtm-tree-hint">한 줄에 하나씩. 경고 문구나 원문 CLI에 이 문자열이 있으면 가립니다(대소문자 무시).</p>
          <textarea class="field rtm-keyword-box" data-rtm-keywords
            placeholder="CHURN&#10;DISCARDS">${rtEscape(draft.hidden_keywords.join('\n'))}</textarea>
        </section>
      </div>
    </div>`;

  const close = () => overlay.remove();
  overlay.addEventListener('click', (e) => { if (e.target === overlay) close(); });
  overlay.querySelector('[data-rtm-close]').addEventListener('click', close);
  overlay.querySelector('[data-rtm-save]').addEventListener('click', async () => {
    const hiddenDevices = [...overlay.querySelectorAll('[data-rtm-dev]')]
      .filter(cb => !cb.checked).map(cb => cb.dataset.rtmDev);
    const hiddenRules = [...overlay.querySelectorAll('[data-rtm-rule]')]
      .filter(cb => !cb.checked).map(cb => cb.dataset.rtmRule);
    const pinnedItems = [...overlay.querySelectorAll('[data-rtm-pin]')]
      .filter(cb => cb.checked).map(cb => {
        const [device, check_id] = cb.dataset.rtmPin.split(RTM_PIN_SEP);
        return { device, check_id };
      });
    const keywords = overlay.querySelector('[data-rtm-keywords]').value
      .split('\n').map(s => s.trim()).filter(Boolean);
    const result = await call('save_realtime_filter', hiddenRules, hiddenDevices, keywords, pinnedItems);
    if (!result || result.error) { showToast((result && result.error) || '저장 실패', 'error'); return; }
    rtmFilter = result;
    showToast('표시 설정을 저장했습니다.');
    close();
    await refreshRealtimeMonitor();
  });
  document.body.appendChild(overlay);
}

// ===== 파일 진단 모달 =====
// 감시는 장비 1대당 '가장 최근에 기록된 로그 파일' 하나만 따라간다(CRTStreamWatcher.latest_only).
// SecureCRT가 접속 세션마다 새 파일을 만들기 때문에, 지난 세션 파일까지 tail하면 이미 끝난
// 작업의 입력이 지금 들어온 것처럼 다시 판정된다. 그래서 이 표는 '추적 중'인 파일을 맨 위로
// 올려 하이라이트하고, 같은 장비의 지난 세션 파일은 '이전 세션'으로 흐리게 내려 둔다.
async function openRtmProbeModal() {
  const result = await call('probe_realtime_log_files');
  if (!result) return;
  document.getElementById('rtm-probe-modal')?.remove();
  const files = result.files || [];

  // tracked = 감시 스레드가 지금 실제로 오프셋을 잡고 읽는 파일(백엔드 status의 active_paths).
  // latest = 그 장비의 최신 파일. 감시를 아직 시작하지 않았으면 tracked는 전부 false이므로,
  // 그때는 latest로 '감시를 켜면 어느 파일이 추적될지'를 미리 보여준다.
  const watching = !!result.watching;
  const isTracked = (f) => (watching ? !!f.tracked : !!f.latest);

  const now = Date.now() / 1000;
  const freshDevices = new Set(((rtmLastState && rtmLastState.devices) || [])
    .filter(d => d.last_activity && (now - d.last_activity) < 5)
    .map(d => d.device));

  // 추적 중 → 식별됐지만 지난 세션 → 식별 실패. 각 묶음 안에서는 최신 기록순(백엔드가 이미 정렬).
  const sorted = [...files].sort((a, b) => {
    const rank = (f) => (isTracked(f) ? 0 : (f.resolved ? 1 : 2));
    return rank(a) - rank(b) || (b.mtime || 0) - (a.mtime || 0);
  });
  const trackedCount = files.filter(isTracked).length;

  const overlay = document.createElement('div');
  overlay.id = 'rtm-probe-modal';
  overlay.className = 'modal-overlay';
  overlay.innerHTML = `
    <div class="card rtm-probe-modal">
      <div class="rtm-alert-modal-head">
        <div>
          <h3 class="card-title">CRT 로그 파일 진단</h3>
          <p class="card-desc">${rtEscape(result.watch_dir || '')} · 파일 ${files.length}개 ·
            장비 목록 ${(result.known_devices || []).length}대 ·
            <b>${watching ? '추적 중' : '감시 시작 시 추적될 파일'} ${trackedCount}개</b>
            (장비별 최신 로그 1개씩)</p>
        </div>
        <button class="btn btn-outlined" type="button" data-rtm-close>닫기</button>
      </div>
      <div class="rtm-probe-body">
        <table class="dtable"><thead><tr>
          <th>상태</th><th>로그 파일</th><th>최종 기록</th><th>파일명 매칭</th><th>내용 매칭</th><th>최종 장비</th><th>크기</th>
        </tr></thead><tbody>
        ${sorted.length ? sorted.map(f => {
          const tracked = isTracked(f);
          const fresh = tracked && freshDevices.has(f.resolved);
          const state = fresh
            ? '<span class="rtm-probe-live-badge rtm-probe-fresh-badge">추적 중 · 방금 갱신</span>'
            : tracked ? '<span class="rtm-probe-live-badge rtm-probe-tracked-badge">추적 중</span>'
            : f.resolved ? '<span class="rtm-probe-old-badge">이전 세션</span>'
            : '<span class="rtm-probe-old-badge">제외</span>';
          return `
          <tr class="${f.resolved ? '' : 'rtm-probe-bad'} ${tracked ? 'rtm-probe-live' : 'rtm-probe-stale'} ${fresh ? 'rtm-probe-fresh' : ''}">
            <td>${state}</td>
            <td>${rtEscape(f.file)}</td>
            <td class="rtm-probe-mtime">${rtEscape(f.mtime_str || '')}</td>
            <td>${f.from_filename ? rtEscape(f.from_filename) : '—'}</td>
            <td>${f.from_content ? rtEscape(f.from_content) : '—'}</td>
            <td><strong>${f.resolved ? rtEscape(f.resolved) : '식별 실패'}</strong></td>
            <td>${(f.size / 1024).toFixed(1)} KB</td>
          </tr>`;
        }).join('')
          : '<tr><td colspan="7">CRTlog 폴더에 .txt / .log 파일이 없습니다.</td></tr>'}
        </tbody></table>
        <p class="rtm-probe-note">파일명이 접속 IP여도 장비 목록의 IP와 대조해 매칭합니다. 그래도 실패하면
          로그 안의 <code>! device: X</code> 헤더나 프롬프트(<code>X#</code>)로 판정합니다.
          <b>추적 중</b>은 지금 감시가 따라가는 파일 — 장비마다 최종 기록이 가장 최신인 1개만 추적합니다.
          <b>이전 세션</b>은 같은 장비의 더 오래된 로그로, 지난 작업이 지금 입력으로 오판되지 않도록 감시에서 제외됩니다.
          <b>제외</b>는 장비를 식별하지 못한 파일(장비 목록에 없거나 프롬프트가 아직 안 찍힌 경우)입니다.</p>
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
