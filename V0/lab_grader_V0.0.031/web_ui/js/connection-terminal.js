// ===== 터미널 세션 관리 (xterm.js 인스턴스, 접속/해제, 폴링) — connection.js의 렌더/장비목록 상태를 사용 =====
let termSessions = [];          // [{session_id, device, ok, error, term, fitAddon}]
let termActiveId = null;        // 현재 포커스 세션 id — 분할/탭 하이라이트, 즉시입력의 단일 대상
let termPollTimer = null;
let termInspectPollTimer = null;

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
  // fit() is called in renderTermArea via requestAnimationFrame
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

  // 세션마다 xterm.js 인스턴스를 붙인다
  area.querySelectorAll('.term-pane-body').forEach(el => {
    const sessionId = el.id.replace('term-body-', '');
    const session = termSessions.find(s => s.session_id === sessionId);
    if (!session) return;
    if (!session.term) {
      makeXterm(session, el);
    } else if (session.term.element && session.term.element.parentElement !== el) {
      el.appendChild(session.term.element);
    }
  });

  window.removeEventListener('resize', refitActiveTerminal);
  window.addEventListener('resize', refitActiveTerminal);

  // 모든 DOM 업데이트 완료 후 표시된 터미널만 리핏
  requestAnimationFrame(() => {
    refitActiveTerminal();
    const activeSession = termSessions.find(s => s.session_id === termActiveId);
    if (activeSession && activeSession.term) {
      activeSession.term.focus();
    }
  });
}

function refitActiveTerminal() {
  termSessions.filter(s => s.ok && s.fitAddon && s.term && s.term.element).forEach(s => { 
    try { 
      // 화면에 보일 때만 fit() 호출 (탭 모드에서 숨겨진 터미널 리핏 에러 방지)
      if (s.term.element.offsetParent !== null) {
        s.fitAddon.fit(); 
      }
    } catch (e) {} 
  });
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

function startTerminalPolling() {
  clearInterval(termPollTimer);
  let polling = false;
  termPollTimer = setInterval(async () => {
    // 이전 폴링 왕복이 아직 안 끝났으면 겹쳐 쌓이지 않게 건너뛴다(탭이 늘어도 주기가 밀리지 않게).
    if (polling) return;
    polling = true;
    try {
      const okSessions = termSessions.filter(s => s.ok);
      if (!okSessions.length) return;
      // 탭마다 순차 await하면 왕복 지연(js_api 브리지)이 열린 탭 수만큼 누적되어
      // 탭이 늘수록 폴링 주기가 점점 벌어지는 문제가 있었다 — 배치 API 한 번으로 대체.
      const ids = okSessions.map(s => s.session_id);
      const outMap = await call('get_terminal_output_multi', ids);
      if (!outMap) return;
      for (const s of okSessions) {
        const out = outMap[s.session_id];
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
    } finally {
      polling = false;
    }
  }, 400);
}
