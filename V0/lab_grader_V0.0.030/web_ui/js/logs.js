// ===== 전체 로그 (아키텍처 탭 아래) — 화면에는 최근 로그만, 내보내기는 전체 세션 로그 =====
async function renderLogs() {
  const content = document.getElementById('content');
  content.innerHTML = `
    <h1 class="page-title">전체 로그</h1>
    <p class="page-sub">프로그램의 모든 동작/오류 로그입니다. 화면에는 랙 방지를 위해 최근 로그만 표시되며, '전체 로그 내보내기'를 누르면 이번 실행 세션 시작부터의 모든 로그를 .txt로 저장할 수 있습니다.</p>
    <div class="card">
      <div class="card-header" style="justify-content:space-between;flex-wrap:wrap;gap:8px;">
        <div style="display:flex;gap:8px;align-items:center;">
          <div class="card-icon"><span class="material-symbols-rounded">terminal</span></div>
          <div><p class="card-title">최근 로그</p><p class="card-desc" id="logs-meta">-</p></div>
        </div>
        <div style="display:flex;gap:8px;">
          <button class="btn btn-outlined" id="btn-refresh-logs"><span class="material-symbols-rounded">refresh</span>새로고침</button>
          <button class="btn btn-primary" id="btn-export-logs"><span class="material-symbols-rounded">download</span>전체 로그 내보내기(.txt)</button>
        </div>
      </div>
      <pre id="logs-view" style="max-height:520px;overflow:auto;font-size:12px;line-height:1.5;white-space:pre-wrap;background:var(--hover);padding:12px;border-radius:8px;margin-top:12px;"></pre>
    </div>
  `;

  await refreshLogsView();

  document.getElementById('btn-refresh-logs').addEventListener('click', refreshLogsView);
  document.getElementById('btn-export-logs').addEventListener('click', async () => {
    const btn = document.getElementById('btn-export-logs');
    btn.classList.add('loading');
    const result = await call('export_full_log');
    btn.classList.remove('loading');
    if (result === null || result === undefined) return;
    if (result.error) { alert(result.error); return; }
    if (result.path) alert(`전체 로그를 저장했습니다:\n${result.path}`);
  });
}

async function refreshLogsView() {
  const result = await call('get_recent_logs', 300) || { lines: [], total_count: 0, shown: 0, truncated: false };
  const view = document.getElementById('logs-view');
  view.textContent = (result.lines && result.lines.length) ? result.lines.join('\n') : '(로그 없음)';
  view.scrollTop = view.scrollHeight;
  const meta = document.getElementById('logs-meta');
  meta.textContent = result.truncated
    ? `화면에는 최근 ${result.shown}줄만 표시 중 (전체 ${result.total_count}줄 기록됨 — 내보내기로 전체 확인 가능)`
    : `총 ${result.total_count}줄`;
}
