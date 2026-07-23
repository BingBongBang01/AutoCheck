// ===== Connection — SecureCRT 스타일 멀티 SSH 터미널 =====
// 아래 상태는 모두 모듈 전역(let)이라 navigate()로 다른 탭에 갔다가 돌아와도
// (renderConnection이 매번 #content를 새로 그리더라도) 그대로 유지된다.
let termSessions = [];          // [{session_id, device, ok, error, log}]
let termActiveId = null;        // 현재 포커스 세션 id — 분할/탭 하이라이트, 즉시입력의 단일 대상
let termViewMode = 'tabs';      // 'tabs' | 'split'
let termPollTimer = null;
let termInspectPollTimer = null;
let selectedDeviceNames = null; // Set — 좌측 패널 체크 상태(기본: 전체 체크), 탭 이동해도 유지
let knownDeviceNames = new Set();
let lastClickedDeviceIndex = null;
let termOptions = { instant: true, allSessions: true }; // 즉시입력/모든세션입력 — 기본 모두 활성, 탭 이동해도 유지

async function renderConnection() {
  const content = document.getElementById('content');
  const targets = await call('get_terminal_targets') || [];

  if (!selectedDeviceNames) selectedDeviceNames = new Set();
  targets.forEach(t => {
    if (!knownDeviceNames.has(t.name)) {
      knownDeviceNames.add(t.name);
      selectedDeviceNames.add(t.name); // 새로 보이는 장비는 기본 체크
    }
  });

  content.innerHTML = `
    <h1 class="page-title">연결(터미널)</h1>
    <p class="page-sub">Device Inventory에 등록된 장비로 SSH 터미널 여러 개를 동시에 띄워서 조작합니다.</p>

    <div class="card">
      <div class="term-toolbar">
        <button class="btn btn-primary" id="btn-term-connect"><span class="material-symbols-rounded">power</span>접속 (${targets.length}대 대상)</button>
        <button class="btn btn-outlined" id="btn-term-view-tabs"><span class="material-symbols-rounded">tab</span>탭 보기</button>
        <button class="btn btn-outlined" id="btn-term-view-split"><span class="material-symbols-rounded">grid_view</span>분할 보기</button>
        <button class="btn btn-outlined" id="btn-term-inspect"><span class="material-symbols-rounded">fact_check</span>점검시작</button>
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
  `;

  renderTermArea();
  wireDeviceList(targets);

  document.getElementById('btn-term-connect').addEventListener('click', () => connectAllTerminals(Array.from(selectedDeviceNames)));
  document.getElementById('btn-term-view-tabs').addEventListener('click', () => { termViewMode = 'tabs'; renderTermArea(); });
  document.getElementById('btn-term-view-split').addEventListener('click', () => { termViewMode = 'split'; renderTermArea(); });
  document.getElementById('btn-term-inspect').addEventListener('click', startTerminalInspection);
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
      if (e.target === checkbox) return;
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
  termSessions.push(...results.map(r => ({ ...r, log: '' })));
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
  }
  termSessions = [];
  termActiveId = null;
  renderTermArea();
  updateDeviceRowClasses();
  startTerminalPolling();
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
        <div class="term-pane-body" id="term-body-${s.session_id}" tabindex="0"></div>
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
          <div class="term-pane-body" id="term-body-${s.session_id}" tabindex="0"></div>
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

  // 세션 스크롤백(log)을 복원 + 터미널 안을 직접 클릭해서 포커스/타이핑 가능하게 연결
  area.querySelectorAll('.term-pane-body').forEach(el => {
    const sessionId = el.id.replace('term-body-', '');
    const session = termSessions.find(s => s.session_id === sessionId);
    el.textContent = session ? session.log || '' : '';
    el.scrollTop = el.scrollHeight;
    el.addEventListener('click', () => {
      termActiveId = sessionId;
      el.focus();
      updateActiveTabHighlight();
      updateDeviceRowClasses();
    });
    el.addEventListener('keydown', async (e) => {
      let text = null;
      if (e.key === 'Enter') text = '\r';
      else if (e.key === 'Backspace') text = '\x7f';
      else if (e.key === 'Tab') text = '\t';
      else if (e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey) text = e.key;
      if (text === null) return;
      e.preventDefault();
      await call('send_terminal_input', sessionId, text);
    });
  });
}

function updateActiveTabHighlight() {
  document.querySelectorAll('#term-tabbar .term-tab').forEach(el => el.classList.toggle('active', el.dataset.tab === termActiveId));
  document.querySelectorAll('#term-area [data-focus]').forEach(el => el.classList.toggle('active', el.dataset.focus === termActiveId));
}

async function closeSingleTerminal(sessionId) {
  await call('close_terminal_session', sessionId);
  termSessions = termSessions.filter(s => s.session_id !== sessionId);
  if (termActiveId === sessionId) {
    const remaining = termSessions.find(s => s.ok);
    termActiveId = remaining ? remaining.session_id : null;
  }
  renderTermArea();
  updateDeviceRowClasses();
}

function startTerminalPolling() {
  clearInterval(termPollTimer);
  termPollTimer = setInterval(async () => {
    const okSessions = termSessions.filter(s => s.ok);
    for (const s of okSessions) {
      const out = await call('get_terminal_output', s.session_id);
      if (!out) continue;
      if (out.data) {
        s.log = (s.log || '') + out.data;
        const el = document.getElementById(`term-body-${s.session_id}`);
        if (el) {
          el.textContent = s.log;
          el.scrollTop = el.scrollHeight;
        }
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

async function startTerminalInspection() {
  const ids = termSessions.filter(s => s.ok).map(s => s.session_id);
  if (!ids.length) { alert('연결된 세션이 없습니다. 먼저 접속하세요.'); return; }
  const result = await call('run_terminal_inspection', ids);
  if (result && result.error) { alert(result.error); return; }
  document.getElementById('term-status-msg').textContent = '점검 진행 중...';
  clearInterval(termInspectPollTimer);
  termInspectPollTimer = setInterval(async () => {
    const status = await call('get_terminal_inspection_status');
    if (!status) return;
    document.getElementById('term-status-msg').textContent = status.log[status.log.length - 1] || '점검 진행 중...';
    if (status.done) {
      clearInterval(termInspectPollTimer);
      document.getElementById('term-status-msg').textContent = '점검 완료 — 장비별 결과가 저장되었습니다.';
      flashSaved(true);
    }
  }, 1000);
}
