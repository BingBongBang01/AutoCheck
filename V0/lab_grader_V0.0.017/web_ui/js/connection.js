// ===== Connection — SecureCRT 스타일 멀티 SSH 터미널 (xterm.js 기반) =====
// 아래 상태는 모두 모듈 전역(let)이라 navigate()로 다른 탭에 갔다가 돌아와도
// (renderConnection이 매번 #content를 새로 그리더라도) 그대로 유지된다.
let termSessions = [];          // [{session_id, device, ok, error, term, fitAddon}]
let termActiveId = null;        // 현재 포커스 세션 id — 분할/탭 하이라이트, 즉시입력의 단일 대상
let termViewMode = 'tabs';      // 'tabs' | 'split'
let termPollTimer = null;
let termInspectPollTimer = null;
let selectedDeviceNames = null; // Set — 좌측 패널 체크 상태(기본: 전체 체크), 탭 이동해도 유지
let knownDeviceNames = new Set();
let lastClickedDeviceIndex = null;
let termOptions = { instant: true, allSessions: true }; // 즉시입력/모든세션입력 — 기본 모두 활성, 탭 이동해도 유지
let termCtxMenuMode = 'menu';   // 'menu' | 'paste' — 설정 탭에서 변경

async function renderConnection() {
  const content = document.getElementById('content');
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
        <button class="btn btn-outlined" id="btn-term-inspect"><span class="material-symbols-rounded">fact_check</span>점검시작</button>
        <button class="btn btn-danger" id="btn-term-stop-inspect" style="display:none;"><span class="material-symbols-rounded">stop_circle</span>중지</button>
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
  immediateBox.addEventListener('change', () => { termOptions.instant = immediateBox.checked; });
  broadcastBox.addEventListener('change', () => { termOptions.allSessions = broadcastBox.checked; });

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
      if (status.done) {
        clearInterval(termInspectPollTimer);
        termInspectPollTimer = null;
        refreshInspectionButtons(false);
        document.getElementById('term-status-msg').textContent = '점검 완료 — 장비별 결과가 저장되었습니다.';
      }
    }, 1000);
  }
}

function currentTerminalTargetIds() {
  const okIds = termSessions.filter(s => s.ok).map(s => s.session_id);
  if (termOptions.allSessions) return okIds;
  return termActiveId && okIds.includes(termActiveId) ? [termActiveId] : [];
}

async function sendTerminalText(text) {
  const ids = currentTerminalTargetIds();
  if (!ids.length) return;
  if (ids.length === 1) await call('send_terminal_input', ids[0], text);
  else await call('broadcast_terminal_input', ids, text);
}

function wireDeviceList(targets) {
  const rows = [...document.querySelectorAll('#connection-device-list .connection-device')];
  let dragActive = false;
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
      if (e.target === checkbox || dragActive) return;
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
      dragActive = false; // 실제 다른 행으로 이동해야 드래그로 인정(단순 클릭과 구분)
      dragStartIdx = idx;
      dragSelecting = !checkbox.checked;
    });
  });

  document.addEventListener('mousemove', (e) => {
    if (dragStartIdx === null || !e.buttons) return;
    const overLabel = e.target.closest('#connection-device-list .connection-device');
    if (!overLabel) return;
    const idx = rows.indexOf(overLabel);
    if (idx === -1) return;
    dragActive = true;
    applyDrag(idx);
  });
  document.addEventListener('mouseup', () => {
    if (dragActive) lastClickedDeviceIndex = dragStartIdx;
    dragStartIdx = null;
    setTimeout(() => { dragActive = false; }, 0);
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

async function connectAllTerminals(deviceNames = []) {
  const btn = document.getElementById('btn-term-connect');
  btn.classList.add('loading');
  const results = await call('connect_terminal_sessions', deviceNames) || [];
  btn.classList.remove('loading');

  const failed = results.filter(r => !r.ok);
  termSessions.push(...results.map(r => ({ ...r, term: null, fitAddon: null })));
  if (!termActiveId) {
    const firstOk = results.find(r => r.ok);
    if (firstOk) termActiveId = firstOk.session_id;
  }
  document.getElementById('term-status-msg').textContent =
    failed.length ? `${results.length}대 중 ${failed.length}대 접속 실패` : `${results.length}대 전체 접속됨`;
  renderTermArea();
  updateDeviceRowClasses();
}

async function closeAllTerminals() {
  clearInterval(termPollTimer);
  for (const s of termSessions.filter(s => s.ok)) {
    await call('close_terminal_session', s.session_id);
    if (s.term) s.term.dispose();
  }
  termSessions = [];
  termActiveId = null;
  renderTermArea();
  updateDeviceRowClasses();
  startTerminalPolling();
}

function makeXterm(session, hostEl) {
  const term = new Terminal({
    convertEol: true,
    fontFamily: 'var(--font-mono), monospace',
    fontSize: 12.5,
    cursorBlink: true,
    scrollback: 5000,
    theme: { background: '#0B1220', foreground: '#D7F5D7', cursor: '#D7F5D7', selectionBackground: 'rgba(215,245,215,0.35)' },
  });
  const fitAddon = new FitAddon.FitAddon();
  term.loadAddon(fitAddon);
  term.open(hostEl);
  fitAddon.fit();
  term.onData((data) => {
    call('send_terminal_input', session.session_id, data);
  });
  hostEl.addEventListener('contextmenu', (e) => onTermContextMenu(e, session, term));
  session.term = term;
  session.fitAddon = fitAddon;
}

function renderTermArea() {
  const okSessions = termSessions.filter(s => s.ok);
  const tabbar = document.getElementById('term-tabbar');
  const area = document.getElementById('term-area');
  if (!tabbar || !area) return;

  if (!okSessions.length) {
    tabbar.style.display = 'none';
    area.innerHTML = `<p style="font-size:12px;color:var(--sub);padding:20px 0;">접속된 터미널이 없습니다. 위 '접속' 버튼을 눌러 좌측에서 선택한 장비에 연결하세요.</p>`;
    return;
  }

  if (termViewMode === 'tabs') {
    tabbar.style.display = 'flex';
    tabbar.innerHTML = okSessions.map(s => `
      <div class="term-tab ${s.session_id === termActiveId ? 'active' : ''}" data-tab="${s.session_id}">
        <span class="material-symbols-rounded" style="font-size:14px;">dns</span>${s.device}
        <span class="material-symbols-rounded term-tab-close" data-close-tab="${s.session_id}">close</span>
      </div>
    `).join('');
    tabbar.querySelectorAll('[data-tab]').forEach(el => {
      el.addEventListener('click', (e) => {
        if (e.target.closest('[data-close-tab]')) return;
        termActiveId = el.dataset.tab;
        renderTermArea();
        updateDeviceRowClasses();
      });
    });
    tabbar.querySelectorAll('[data-close-tab]').forEach(el => {
      el.addEventListener('click', async (e) => {
        e.stopPropagation();
        await closeSingleTerminal(el.dataset.closeTab);
      });
    });
    area.innerHTML = okSessions.map(s => `
      <div class="term-pane-wrap" data-pane="${s.session_id}" style="display:${s.session_id === termActiveId ? 'flex' : 'none'};">
        <div class="term-pane-body" id="term-body-${s.session_id}"></div>
      </div>
    `).join('');
  } else {
    tabbar.style.display = 'none';
    const cols = Math.min(3, Math.ceil(Math.sqrt(okSessions.length)));
    area.innerHTML = `<div class="term-grid" style="grid-template-columns:repeat(${cols},1fr);">` +
      okSessions.map(s => `
        <div class="term-pane-wrap" data-pane="${s.session_id}">
          <div class="term-pane-header ${s.session_id === termActiveId ? 'active' : ''}" data-focus="${s.session_id}">
            <span class="material-symbols-rounded" style="font-size:14px;">dns</span>${s.device}
            <span style="flex:1"></span>
            <span class="material-symbols-rounded term-tab-close" data-close-tab="${s.session_id}">close</span>
          </div>
          <div class="term-pane-body" id="term-body-${s.session_id}"></div>
        </div>
      `).join('') + `</div>`;
    area.querySelectorAll('[data-focus]').forEach(el => {
      el.addEventListener('click', (e) => {
        if (e.target.closest('[data-close-tab]')) return;
        termActiveId = el.dataset.focus;
        renderTermArea();
        updateDeviceRowClasses();
      });
    });
    area.querySelectorAll('[data-close-tab]').forEach(el => {
      el.addEventListener('click', async (e) => {
        e.stopPropagation();
        await closeSingleTerminal(el.dataset.closeTab);
      });
    });
  }

  // 세션마다 xterm.js 인스턴스를 붙인다(이미 있으면 이동만 하고 재생성하지 않음 — 스크롤백 유지).
  area.querySelectorAll('.term-pane-body').forEach(el => {
    const sessionId = el.id.replace('term-body-', '');
    const session = termSessions.find(s => s.session_id === sessionId);
    if (!session) return;
    if (!session.term) {
      makeXterm(session, el);
    } else if (session.term.element && session.term.element.parentElement !== el) {
      el.appendChild(session.term.element);
      session.fitAddon.fit();
    }
    if (sessionId === termActiveId) {
      requestAnimationFrame(() => { session.fitAddon.fit(); session.term.focus(); });
    }
  });

  window.removeEventListener('resize', refitActiveTerminal);
  window.addEventListener('resize', refitActiveTerminal);
}

function refitActiveTerminal() {
  termSessions.filter(s => s.ok && s.fitAddon).forEach(s => { try { s.fitAddon.fit(); } catch (e) {} });
}

function updateActiveTabHighlight() {
  document.querySelectorAll('#term-tabbar .term-tab').forEach(el => el.classList.toggle('active', el.dataset.tab === termActiveId));
  document.querySelectorAll('#term-area [data-focus]').forEach(el => el.classList.toggle('active', el.dataset.focus === termActiveId));
}

async function closeSingleTerminal(sessionId) {
  await call('close_terminal_session', sessionId);
  const session = termSessions.find(s => s.session_id === sessionId);
  if (session && session.term) session.term.dispose();
  termSessions = termSessions.filter(s => s.session_id !== sessionId);
  if (termActiveId === sessionId) {
    const remaining = termSessions.find(s => s.ok);
    termActiveId = remaining ? remaining.session_id : null;
  }
  renderTermArea();
  updateDeviceRowClasses();
}

// ===== 우클릭 메뉴 / 바로 붙여넣기 =====
function closeTermCtxMenu() {
  document.querySelectorAll('.term-ctx-menu').forEach(el => el.remove());
  document.removeEventListener('click', closeTermCtxMenu);
}

async function onTermContextMenu(e, session, term) {
  e.preventDefault();
  if (termCtxMenuMode === 'paste') {
    try {
      const text = await navigator.clipboard.readText();
      if (text) await call('send_terminal_input', session.session_id, text);
    } catch (err) { /* 클립보드 접근 거부 — 조용히 무시 */ }
    return;
  }
  closeTermCtxMenu();
  const hasSelection = term.hasSelection();
  const menu = document.createElement('div');
  menu.className = 'term-ctx-menu';
  menu.style.left = e.clientX + 'px';
  menu.style.top = e.clientY + 'px';
  const items = [
    { label: '복사', icon: 'content_copy', disabled: !hasSelection, action: async () => {
      await navigator.clipboard.writeText(term.getSelection());
    } },
    { label: '잘라내기', icon: 'content_cut', disabled: !hasSelection, action: async () => {
      await navigator.clipboard.writeText(term.getSelection());
    } },
    { label: '붙여넣기', icon: 'content_paste', disabled: false, action: async () => {
      const text = await navigator.clipboard.readText();
      if (text) await call('send_terminal_input', session.session_id, text);
    } },
    { sep: true },
    { label: '전체 선택', icon: 'select_all', disabled: false, action: () => term.selectAll() },
    { label: '실행 취소', icon: 'undo', disabled: true, action: () => {} }, // 원격 셸은 undo 개념이 없음 — 항상 비활성
  ];
  menu.innerHTML = items.map((it, i) => it.sep
    ? '<div class="term-ctx-menu-sep"></div>'
    : `<div class="term-ctx-menu-item ${it.disabled ? 'disabled' : ''}" data-idx="${i}"><span class="material-symbols-rounded" style="font-size:16px;">${it.icon}</span>${it.label}</div>`
  ).join('');
  menu.querySelectorAll('[data-idx]').forEach(el => {
    const it = items[parseInt(el.dataset.idx, 10)];
    if (it.disabled) return;
    el.addEventListener('click', async (ev) => {
      ev.stopPropagation();
      closeTermCtxMenu();
      await it.action();
    });
  });
  document.body.appendChild(menu);
  const rect = menu.getBoundingClientRect();
  if (rect.right > window.innerWidth) menu.style.left = (window.innerWidth - rect.width - 8) + 'px';
  if (rect.bottom > window.innerHeight) menu.style.top = (window.innerHeight - rect.height - 8) + 'px';
  setTimeout(() => document.addEventListener('click', closeTermCtxMenu), 0);
}

function startTerminalPolling() {
  clearInterval(termPollTimer);
  termPollTimer = setInterval(async () => {
    const okSessions = termSessions.filter(s => s.ok);
    for (const s of okSessions) {
      const out = await call('get_terminal_output', s.session_id);
      if (!out) continue;
      if (out.data && s.term) {
        s.term.write(out.data);
      }
      if (!out.connected && s.ok) {
        s.ok = false;
        s.error = out.error || '연결 끊김';
        renderTermArea();
        updateDeviceRowClasses();
      }
    }
  }, 400);
}

function refreshInspectionButtons(running) {
  const startBtn = document.getElementById('btn-term-inspect');
  const stopBtn = document.getElementById('btn-term-stop-inspect');
  if (!startBtn || !stopBtn) return;
  startBtn.style.display = running ? 'none' : 'inline-flex';
  stopBtn.style.display = running ? 'inline-flex' : 'none';
}

async function startTerminalInspection() {
  const ids = termSessions.filter(s => s.ok).map(s => s.session_id);
  if (!ids.length) { alert('연결된 세션이 없습니다. 먼저 접속하세요.'); return; }
  const result = await call('run_terminal_inspection', ids);
  if (result && result.error) { alert(result.error); return; }
  document.getElementById('term-status-msg').textContent = '점검 진행 중...';
  refreshInspectionButtons(true);
  clearInterval(termInspectPollTimer);
  termInspectPollTimer = setInterval(async () => {
    const status = await call('get_terminal_inspection_status');
    if (!status) return;
    document.getElementById('term-status-msg').textContent = status.log[status.log.length - 1] || '점검 진행 중...';
    if (status.done) {
      clearInterval(termInspectPollTimer);
      termInspectPollTimer = null;
      refreshInspectionButtons(false);
      document.getElementById('term-status-msg').textContent = '점검 완료 — 장비별 결과가 저장되었습니다.';
      flashSaved(true);
    }
  }, 1000);
}

async function stopTerminalInspection() {
  const choice = prompt('진행 중인 점검을 중지합니다.\n"저장"을 입력하면 지금까지 수집된 데이터를 저장하고,\n"폐기"를 입력하면 지금까지의 데이터를 버립니다.', '저장');
  if (choice === null) return;
  const discard = choice.trim() === '폐기';
  const result = await call('stop_terminal_inspection', discard);
  if (result && result.error) { alert(result.error); return; }
  document.getElementById('term-status-msg').textContent = discard ? '중지 요청됨 — 데이터 폐기 예정...' : '중지 요청됨 — 진행 중인 커맨드 완료 후 저장...';
}
