// ===== 터미널 기반 점검 실행/중지 =====
function renderInspectionProgress(status) {
  let wrap = document.getElementById('sb-inspection-progress');
  if (!wrap) return;

  if (!status || (!status.running && !status.done)) {
    wrap.style.display = 'none';
    return;
  }
  
  if (!status.running && status.done) {
    wrap.style.display = 'none'; // 숨김 처리
    return;
  }
  
  wrap.style.display = 'flex';
  
  let el = document.getElementById('sb-job-inspection');
  if (!el) {
    el = document.createElement('div');
    el.id = 'sb-job-inspection';
    el.style.cssText = 'display:flex;align-items:center;gap:6px;min-width:0;';
    el.innerHTML = `
      <span class="job-label" style="font-size:11px;color:var(--sub);white-space:nowrap;"></span>
      <div style="width:80px;height:6px;background:var(--border);border-radius:3px;overflow:hidden;">
        <div class="job-bar" style="height:100%;width:0%;background:var(--primary);transition:width .25s;"></div>
      </div>
    `;
    wrap.appendChild(el);
  }
  
  const pct = status.total > 0 ? Math.min(100, Math.round((status.current / status.total) * 100)) : 0;
  el.querySelector('.job-label').textContent = `정기점검 ${pct}%`;
  el.querySelector('.job-bar').style.width = pct + '%';
}

function refreshInspectionButtons(running) {
  const startBtn = document.getElementById('btn-term-inspect');
  const stopBtn = document.getElementById('btn-term-stop-inspect');
  if (!startBtn || !stopBtn) return;
  startBtn.style.display = running ? 'none' : 'inline-flex';
  stopBtn.style.display = running ? 'inline-flex' : 'none';
}

async function startTerminalInspection() {
  if (autoInspectAndClose) {
    const activeDevices = new Set(termSessions.filter(s => s.ok).map(s => s.device));
    const toConnect = Array.from(selectedDeviceNames).filter(d => !activeDevices.has(d));
    if (toConnect.length > 0) {
      await connectAllTerminals(toConnect);
      await new Promise(r => setTimeout(r, 1000));
    }
  }

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
    
    renderInspectionProgress(status);

    if (status.done) {
      clearInterval(termInspectPollTimer);
      termInspectPollTimer = null;
      refreshInspectionButtons(false);
      document.getElementById('term-status-msg').textContent = '점검 완료 — 장비별 결과가 저장되었습니다.';
      renderInspectionProgress({running: false, done: true});
      flashSaved(true);
      
      if (autoInspectAndClose) {
        closeAllTerminals();
      }
    }
  }, 1000);
}

// 중지 = 지금까지 수집된 것을 그대로 저장. 예전에는 prompt()로 "저장"/"폐기"를 입력받았는데,
// 중지를 누르는 상황에서 원하는 건 사실상 항상 저장이라 확인 단계를 없앴다(폐기가 필요하면
// 저장된 로그를 '점검 로그' 탭에서 지우면 된다 — 그쪽이 되돌릴 수 있는 방향).
// stop_terminal_inspection()은 인자를 받지 않는다 — 예전에 저장/폐기를 고르던 discard 인자를
// 없앤 함수다. JS에서 인자를 하나라도 넘기면 pywebview 브리지에서 TypeError가 나고
// 중지 요청이 서버에 도달하지 않아 '중지가 안 되는' 것처럼 보인다. 인자 추가 금지.
async function stopTerminalInspection() {
  const btn = document.getElementById('btn-term-stop-inspect');
  if (btn) btn.classList.add('loading');
  const result = await call('stop_terminal_inspection');
  if (btn) btn.classList.remove('loading');
  if (result && result.error) {
    // 이미 끝난 점검에 중지를 누른 경우 — 버튼 상태만 정상으로 되돌린다.
    document.getElementById('term-status-msg').textContent = result.error;
    refreshInspectionButtons(false);
    return;
  }
  document.getElementById('term-status-msg').textContent = '중지 요청됨 — 진행 중인 커맨드 완료 후 저장...';
}
