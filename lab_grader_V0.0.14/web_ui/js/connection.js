// ===== Connection — SecureCRT 스타일 멀티 SSH 터미널 =====
let termSessions = [];      // [{session_id, device, ok, error}]
let termActiveId = null;    // 현재 활성(포커스) 세션 id — 탭 모드에서 보이는 대상, 즉시입력의 대상
let termViewMode = 'tabs';  // 'tabs' | 'split'
let termPollTimer = null;
let termInspectPollTimer = null;

async function renderConnection() {
  const content = document.getElementById('content');
  const targets = await call('get_terminal_targets') || [];

  content.innerHTML = `
    <h1 class="page-title">연결(터미널)</h1>
    <p class="page-sub">Device Inventory에 등록된 장비로 SSH 터미널 여러 개를 동시에 띄워서 조작합니다.</p>

    <div class="card">
      <div class="connection-device-list" id="connection-device-list">${targets.map(t => `<label class="connection-device"><input type="checkbox" value="${t.name}">${t.name} <span>${t.ip}:${t.port}</span></label>`).join('')}</div>
      <div class="term-toolbar">
        <button class="btn btn-primary" id="btn-term-connect"><span class="material-symbols-rounded">power</span>접속 (${targets.length}대 대상)</button>
        <button class="btn btn-outlined" id="btn-term-view-tabs"><span class="material-symbols-rounded">tab</span>탭 보기</button>
        <button class="btn btn-outlined" id="btn-term-view-split"><span class="material-symbols-rounded">grid_view</span>분할 보기</button>
        <button class="btn btn-outlined" id="btn-term-inspect"><span class="material-symbols-rounded">fact_check</span>점검시작</button>
        <button class="btn btn-danger" id="btn-term-close-all"><span class="material-symbols-rounded">close</span>전체 닫기</button>
        <span id="term-status-msg" style="font-size:12px;color:var(--sub);"></span>
      </div>

      <div id="term-tabbar" class="term-tabs" style="display:none;"></div>
      <div id="term-area"></div>

      <div class="term-input-bar">
        <input type="text" id="term-input" placeholder="명령 입력 후 Enter (즉시입력/모든세션입력 중 최소 하나를 체크해야 전송됩니다)">
        <label><input type="checkbox" id="term-immediate">즉시입력</label>
        <label><input type="checkbox" id="term-broadcast">모든세션입력</label>
      </div>
    </div>

    
  `;

  renderTermArea();

  document.getElementById('btn-term-connect').addEventListener('click', () => connectAllTerminals([...document.querySelectorAll('#connection-device-list input:checked')].map(e => e.value)));
  document.getElementById('btn-term-view-tabs').addEventListener('click', () => { termViewMode = 'tabs'; renderTermArea(); });
  document.getElementById('btn-term-view-split').addEventListener('click', () => { termViewMode = 'split'; renderTermArea(); });
  document.getElementById('btn-term-inspect').addEventListener('click', startTerminalInspection);
  document.getElementById('btn-term-close-all').addEventListener('click', closeAllTerminals);

  document.getElementById('term-input').addEventListener('keydown', async (e) => {
    if (e.key !== 'Enter') return;
    const immediate = document.getElementById('term-immediate').checked;
    const broadcast = document.getElementById('term-broadcast').checked;
    if (!immediate && !broadcast) return;
    const text = e.target.value + '\n';
    if (broadcast) {
      const ids = termSessions.filter(s => s.ok).map(s => s.session_id);
      if (ids.length) await call('broadcast_terminal_input', ids, text);
    } else if (immediate && termActiveId) {
      await call('send_terminal_input', termActiveId, text);
    }
    e.target.value = '';
  });

  document.querySelectorAll('#connection-device-list input').forEach((input, index, inputs) => input.addEventListener('click', e => {
    if (!e.shiftKey || index < 1) return;
    const previous = [...inputs].slice(0, index).reverse().find(x => x.checked || !x.checked);
    const start = Math.min(index, [...inputs].indexOf(previous));
    for (let i = start; i <= index; i++) inputs[i].checked = input.checked;
  }));
  document.querySelectorAll('#connection-device-list .connection-device').forEach(label => label.addEventListener('dblclick', async () => {
    label.querySelector('input').checked = true;
    await connectAllTerminals([label.querySelector('input').value]);
  }));

  startTerminalPolling();
}

async function connectAllTerminals(deviceNames = []) {
  const btn = document.getElementById('btn-term-connect');
  btn.classList.add('loading');
  const results = await call('connect_terminal_sessions', deviceNames) || [];
  btn.classList.remove('loading');

  const failed = results.filter(r => !r.ok);
  termSessions.push(...results);
  if (!termActiveId) {
    const firstOk = results.find(r => r.ok);
    if (firstOk) termActiveId = firstOk.session_id;
  }
  document.getElementById('term-status-msg').textContent =
    failed.length ? `${results.length}대 중 ${failed.length}대 접속 실패` : `${results.length}대 전체 접속됨`;
  renderTermArea();
}

async function closeAllTerminals() {
  clearInterval(termPollTimer);
  for (const s of termSessions.filter(s => s.ok)) {
    await call('close_terminal_session', s.session_id);
  }
  termSessions = [];
  termActiveId = null;
  renderTermArea();
  startTerminalPolling();
}

function renderTermArea() {
  const okSessions = termSessions.filter(s => s.ok);
  const tabbar = document.getElementById('term-tabbar');
  const area = document.getElementById('term-area');

  if (!okSessions.length) {
    tabbar.style.display = 'none';
    area.innerHTML = `<p style="font-size:12px;color:var(--sub);padding:20px 0;">접속된 터미널이 없습니다. 위 '접속' 버튼을 눌러 Device Inventory의 활성 장비에 연결하세요.</p>`;
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
      });
    });
    area.querySelectorAll('[data-close-tab]').forEach(el => {
      el.addEventListener('click', async (e) => {
        e.stopPropagation();
        await closeSingleTerminal(el.dataset.closeTab);
      });
    });
  }
}

async function closeSingleTerminal(sessionId) {
  await call('close_terminal_session', sessionId);
  termSessions = termSessions.filter(s => s.session_id !== sessionId);
  if (termActiveId === sessionId) {
    const remaining = termSessions.find(s => s.ok);
    termActiveId = remaining ? remaining.session_id : null;
  }
  renderTermArea();
}

function startTerminalPolling() {
  clearInterval(termPollTimer);
  termPollTimer = setInterval(async () => {
    const okSessions = termSessions.filter(s => s.ok);
    for (const s of okSessions) {
      const el = document.getElementById(`term-body-${s.session_id}`);
      if (!el) continue;
      const out = await call('get_terminal_output', s.session_id);
      if (!out) continue;
      if (out.data) {
        el.textContent += out.data;
        el.scrollTop = el.scrollHeight;
      }
      if (!out.connected && s.ok) {
        s.ok = false;
        s.error = out.error || '연결 끊김';
        renderTermArea();
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
