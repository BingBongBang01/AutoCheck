// ===== 터미널 기반 점검 실행/중지 =====
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
