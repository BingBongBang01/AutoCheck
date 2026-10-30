// ===== 터미널 기반 점검 실행/중지 =====
// 점검 진행 폴러는 다른 탭으로 옮겨도 계속 돈다(완료 처리·자동 세션 닫기). 예전엔 그 탭의 문구 요소가
// 없으면 매초 예외로 멈춰서, 탭을 옮기면 완료 처리가 다시 돌아올 때까지 늦어졌다.
function setTermStatusMsg(text) {
  const el = document.getElementById('term-status-msg');
  if (el) el.textContent = text;
}

// 점검 진행을 1초마다 폴링하고 끝나면 정리한다 — 점검을 시작할 때와, 점검 중에 연결 탭을 다시 열었을 때
// 같은 함수를 쓴다(예전엔 두 곳에 복사돼 있었고 다시 연 쪽은 자동 세션 닫기를 빠뜨렸다). 끝난 이유는
// status 로 구분한다: done(완료) / cancelled(중지 — 그때까지 결과는 저장됨) / error(오류).
// 상태바의 진행 표시는 작업 센터(js/job-center.js)가 맡는다 — 여기는 연결 탭의 문구·버튼과 끝난 뒤 정리만.
function pollTerminalInspection() {
  clearInterval(termInspectPollTimer);
  termInspectPollTimer = setInterval(async () => {
    const status = await call('get_terminal_inspection_status');
    if (!status) return;
    setTermStatusMsg(status.log[status.log.length - 1] || '점검 진행 중...');
    if (!status.done) return;

    clearInterval(termInspectPollTimer);
    termInspectPollTimer = null;
    refreshInspectionButtons(false);
    if (status.status === 'error') {
      setTermStatusMsg(`점검 오류 — ${status.error || '알 수 없는 오류'}`);
      showToast(`점검 오류: ${status.error || '알 수 없는 오류'}`, 'error');
      return;
    }
    setTermStatusMsg(status.status === 'cancelled'
      ? '점검 중지됨 — 그때까지 수집된 결과가 저장되었습니다.'
      : '점검 완료 — 장비별 결과가 저장되었습니다.');
    flashSaved(true);
    if (autoInspectAndClose) {
      closeAllTerminals();
    }
  }, 1000);
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
  pollTerminalInspection();
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
