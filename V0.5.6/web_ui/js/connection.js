// ===== Connection — SecureCRT 스타일 멀티 SSH 터미널 (xterm.js 기반) =====
// 아래 상태는 모두 모듈 전역(let)이라 navigate()로 다른 탭에 갔다가 돌아와도
// (renderConnection이 매번 #content를 새로 그리더라도) 그대로 유지된다.
// 터미널 세션/xterm 관리는 connection-terminal.js, 우클릭 메뉴는 connection-context-menu.js,
// 점검 실행/중지는 connection-inspection.js 참고.
let termViewMode = 'tabs';      // 'tabs' | 'split'
let selectedDeviceNames = null; // Set — 좌측 패널 체크 상태(기본: 전체 체크), 탭 이동해도 유지
let knownDeviceNames = new Set();
let lastClickedDeviceIndex = null;
let termOptions = { instant: true, allSessions: true }; // 즉시입력/모든세션입력 — 기본 모두 활성, 탭 이동해도 유지
let termCtxMenuMode = 'menu';   // 'menu' | 'paste' — 설정 탭에서 변경
let autoInspectAndClose = true; // 자동 접속 및 완료 후 닫기 상태 저장

async function renderConnection() {
  const content = document.getElementById('content');
  await ensureXterm();   // 이 페이지에서만 쓰는 xterm을 여기서 처음 로드한다(시작 시간 단축)
  const targets = await call('get_terminal_targets') || [];
  const uiCfg = await call('get_terminal_ui_settings') || { context_menu_mode: 'menu' };
  termCtxMenuMode = uiCfg.context_menu_mode || 'menu';

  if (!selectedDeviceNames) selectedDeviceNames = new Set();
  targets.forEach(t => {
    if (!knownDeviceNames.has(t.name)) {
      knownDeviceNames.add(t.name);
      selectedDeviceNames.add(t.name); // 새로 보이는 장비는 기본 체크
    }
  });

  content.innerHTML = `
   <div class="term-page">
    <h1 class="page-title">연결(터미널)</h1>
    <p class="page-sub">Device Inventory에 등록된 장비로 SSH 터미널 여러 개를 동시에 띄워서 조작합니다.</p>

    <div class="card">
      <div class="term-toolbar">
        <button class="btn btn-primary" id="btn-term-connect"><span class="material-symbols-rounded">power</span>접속 (${targets.length}대 대상)</button>
        <button class="btn btn-outlined" id="btn-term-view-tabs"><span class="material-symbols-rounded">tab</span>탭 보기</button>
        <button class="btn btn-outlined" id="btn-term-view-split"><span class="material-symbols-rounded">grid_view</span>분할 보기</button>
        <button class="btn btn-outlined" id="btn-term-auto-rename" title="터미널 프롬프트에서 호스트명을 파싱하여 장비 이름을 자동 갱신합니다."><span class="material-symbols-rounded">sync_alt</span>이름 동기화</button>
        <button class="btn btn-outlined" id="btn-term-inspect"><span class="material-symbols-rounded">fact_check</span>점검시작</button>
        <button class="btn btn-danger" id="btn-term-stop-inspect" style="display:none;"><span class="material-symbols-rounded">stop_circle</span>중지</button>
        <label title="선택된 장비 중 미연결 장비를 자동 접속하고, 점검 100% 완료 시 전체 세션을 자동으로 닫습니다." style="display:inline-flex; align-items:center; gap:4px; font-size:12px; color:var(--text); cursor:pointer;">
          <input type="checkbox" id="cb-auto-inspect" ${autoInspectAndClose ? 'checked' : ''}>자동 접속 및 완료 후 닫기
        </label>
        <button class="btn btn-danger" id="btn-term-close-all"><span class="material-symbols-rounded">close</span>전체 닫기</button>
        <span id="term-status-msg" style="font-size:12px;color:var(--sub);"></span>
      </div>

      <div class="term-split">
        <div class="term-side-panel">
          <div class="term-side-header">
            <span>대상 장비 (${targets.length})</span>
            <button class="btn btn-outlined" id="btn-select-all-devices" style="height:22px;padding:0 6px;font-size:10px;">전체선택</button>
          </div>
          <div class="connection-device-list" id="connection-device-list">${targets.map((t, i) => `
            <label class="connection-device" data-idx="${i}" data-name="${t.name}">
              <input type="checkbox" data-name="${t.name}" ${selectedDeviceNames.has(t.name) ? 'checked' : ''}>
              <span class="device-name">${t.name}</span><span class="ip">${t.ip}:${t.port}</span>
            </label>`).join('')}</div>
        </div>
        <div class="term-main">
          <div id="term-tabbar" class="term-tabs" style="display:none;"></div>
          <div id="term-area"></div>
        </div>
      </div>

      <div class="term-input-bar">
        <input type="text" id="term-input" placeholder="명령 입력 (즉시입력 시 키 입력마다 바로 전송, Enter로 줄 전송)">
        <label><input type="checkbox" id="term-immediate" ${termOptions.instant ? 'checked' : ''}>즉시입력</label>
        <label><input type="checkbox" id="term-broadcast" ${termOptions.allSessions ? 'checked' : ''}>모든세션입력</label>
      </div>
    </div>
   </div>
  `;

  renderTermArea();
  wireDeviceList(targets);

  document.getElementById('btn-term-connect').addEventListener('click', () => connectAllTerminals(Array.from(selectedDeviceNames)));
  document.getElementById('btn-term-view-tabs').addEventListener('click', () => { termViewMode = 'tabs'; renderTermArea(); });
  document.getElementById('btn-term-view-split').addEventListener('click', () => { termViewMode = 'split'; renderTermArea(); });
  document.getElementById('btn-term-auto-rename').addEventListener('click', async () => {
    const okSessions = termSessions.filter(s => s.ok);
    if (!okSessions.length) return;
    
    let changed = false;
    for (const s of okSessions) {
      const res = await call('auto_rename_device_from_session', s.session_id);
      if (res && res.success) {
        knownDeviceNames.delete(res.old_name);
        knownDeviceNames.add(res.new_name);
        if (selectedDeviceNames.has(res.old_name)) {
          selectedDeviceNames.delete(res.old_name);
          selectedDeviceNames.add(res.new_name);
        }
        s.device = res.new_name;
        changed = true;
      }
    }
    
    if (changed) {
      navigate('connection'); // 화면 갱신
    }
  });
  document.getElementById('btn-term-inspect').addEventListener('click', startTerminalInspection);
  document.getElementById('btn-term-stop-inspect').addEventListener('click', stopTerminalInspection);
  document.getElementById('btn-term-close-all').addEventListener('click', closeAllTerminals);

  document.getElementById('btn-select-all-devices').addEventListener('click', () => {
    const allChecked = targets.every(t => selectedDeviceNames.has(t.name));
    targets.forEach(t => allChecked ? selectedDeviceNames.delete(t.name) : selectedDeviceNames.add(t.name));
    document.querySelectorAll('#connection-device-list input[type=checkbox]').forEach(cb => cb.checked = !allChecked);
    updateDeviceRowClasses();
  });

  const immediateBox = document.getElementById('term-immediate');
  const broadcastBox = document.getElementById('term-broadcast');
  const autoInspectBox = document.getElementById('cb-auto-inspect');
  immediateBox.addEventListener('change', () => { termOptions.instant = immediateBox.checked; });
  broadcastBox.addEventListener('change', () => { termOptions.allSessions = broadcastBox.checked; });
  if (autoInspectBox) {
    autoInspectBox.addEventListener('change', () => { autoInspectAndClose = autoInspectBox.checked; });
  }

  const input = document.getElementById('term-input');
  input.addEventListener('keydown', async (e) => {
    if (termOptions.instant) {
      // 즉시입력: 키를 누르는 즉시 세션으로 전달(입력창에는 누적하지 않음)
      if (e.key === 'Enter') { e.preventDefault(); await sendTerminalText('\r'); input.value = ''; return; }
      if (e.key === 'Backspace') { e.preventDefault(); await sendTerminalText('\x7f'); return; }
      if (e.key === 'Tab') { e.preventDefault(); await sendTerminalText('\t'); return; }
      if (e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey) { e.preventDefault(); await sendTerminalText(e.key); return; }
      return;
    }
    if (e.key !== 'Enter') return;
    const text = input.value + '\n';
    await sendTerminalText(text);
    input.value = '';
  });

  startTerminalPolling();
  const inspectStatus = await call('get_terminal_inspection_status');
  refreshInspectionButtons(inspectStatus && inspectStatus.running);
  if (inspectStatus && inspectStatus.running && !termInspectPollTimer) {
    termInspectPollTimer = setInterval(async () => {
      const status = await call('get_terminal_inspection_status');
      if (!status) return;
      document.getElementById('term-status-msg').textContent = status.log[status.log.length - 1] || '점검 진행 중...';
      
      renderInspectionProgress(status);

      if (status.done) {
        clearInterval(termInspectPollTimer);
        termInspectPollTimer = null;
        refreshInspectionButtons(false);
        document.getElementById('term-status-msg').textContent = '점검 완료 — 장비별 결과가 저장되었습니다.';
        renderInspectionProgress({running: false, done: true});
      }
    }, 1000);
  }
}

function wireDeviceList(targets) {
  const listEl = document.getElementById('connection-device-list');
  const rows = [...document.querySelectorAll('#connection-device-list .connection-device')];
  let dragSelecting = true; // 드래그 시작 행의 상태 반대값으로 나머지를 맞춘다
  let dragStartIdx = null;

  const applyDrag = (idx) => {
    const start = Math.min(dragStartIdx, idx);
    const end = Math.max(dragStartIdx, idx);
    for (let i = start; i <= end; i++) {
      const cb = rows[i].querySelector('input');
      cb.checked = dragSelecting;
      if (dragSelecting) selectedDeviceNames.add(cb.dataset.name);
      else selectedDeviceNames.delete(cb.dataset.name);
    }
    updateDeviceRowClasses();
  };

  // 드래그 범위선택 + 목록 가장자리 자동 스크롤(core.js 공용) — 장비가 많아 목록이 잘릴 때
  // 드래그한 채 아래로 내리면 계속 선택된다.
  const dragger = createDragRangeSelect({
    container: listEl,
    rowSelector: '.connection-device',
    rows,
    applyTo: applyDrag,
    onEnd: (dragged) => {
      if (dragged) lastClickedDeviceIndex = dragStartIdx;
      dragStartIdx = null;
    },
  });

  rows.forEach((label, idx) => {
    const checkbox = label.querySelector('input');
    checkbox.addEventListener('click', (e) => {
      e.stopPropagation();
      if (checkbox.checked) selectedDeviceNames.add(checkbox.dataset.name);
      else selectedDeviceNames.delete(checkbox.dataset.name);
      lastClickedDeviceIndex = idx;
      updateDeviceRowClasses();
    });
    label.addEventListener('click', (e) => {
      if (e.target === checkbox || dragger.isDragging()) return;
      if (e.shiftKey && lastClickedDeviceIndex !== null) {
        const start = Math.min(lastClickedDeviceIndex, idx);
        const end = Math.max(lastClickedDeviceIndex, idx);
        for (let i = start; i <= end; i++) {
          const cb = rows[i].querySelector('input');
          cb.checked = true;
          selectedDeviceNames.add(cb.dataset.name);
        }
      } else {
        checkbox.checked = !checkbox.checked;
        if (checkbox.checked) selectedDeviceNames.add(checkbox.dataset.name);
        else selectedDeviceNames.delete(checkbox.dataset.name);
        lastClickedDeviceIndex = idx;
      }
      updateDeviceRowClasses();
    });
    label.addEventListener('dblclick', async () => {
      checkbox.checked = true;
      selectedDeviceNames.add(checkbox.dataset.name);
      await connectAllTerminals([checkbox.dataset.name]);
    });
    // 드래그 선택: mousedown한 행에서 시작해 마우스를 지나간 행 전체를 선택/해제
    label.addEventListener('mousedown', (e) => {
      if (e.target === checkbox || e.button !== 0) return;
      e.preventDefault();
      dragStartIdx = idx;                    // 실제 다른 행으로 이동해야 드래그로 인정(단순 클릭과 구분)
      dragSelecting = !checkbox.checked;
      dragger.begin(idx);
    });
  });

  updateDeviceRowClasses();
}

function updateDeviceRowClasses() {
  const activeDevices = new Set(termSessions.filter(s => s.ok).map(s => s.device));
  document.querySelectorAll('#connection-device-list .connection-device').forEach(label => {
    const name = label.dataset.name;
    label.classList.toggle('selected', selectedDeviceNames.has(name));
    label.classList.toggle('session-active', activeDevices.has(name));
  });
}
