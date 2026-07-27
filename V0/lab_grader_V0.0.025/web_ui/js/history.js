// ===== History =====
async function renderHistory() {
  const sessions = await call('list_history') || [];
  const content = document.getElementById('content');
  const rows = sessions.map(s => `
    <tr><td>${s.session}</td><td>${s.elapsed_sec}초</td>
    <td><span class="badge badge-neutral">${s.stage_count || '-'} stages</span></td>
    <td><button class="btn btn-outlined" data-view-history="${s.session}">보기</button><button class="btn btn-danger" data-delete-history="${s.session}">삭제</button></td></tr>`).join('');
  content.innerHTML = `
    <h1 class="page-title">이력</h1>
    <p class="page-sub">세션 이력 및 Trend</p>
    <div class="card">
      <table class="dtable">
        <thead><tr><th>세션</th><th>소요시간</th><th>단계</th><th></th></tr></thead>
        <tbody>${rows || '<tr><td colspan="3" style="color:var(--sub)">이력 없음</td></tr>'}</tbody>
      </table>
    </div><div class="history-viewer" id="history-viewer"><div class="history-resizer"></div><pre class="terminal" id="history-output">이력을 선택하세요.</pre></div>
  `;
  document.querySelectorAll('[data-view-history]').forEach(btn => btn.addEventListener('click', async () => {
    const data = await call('get_history', btn.dataset.viewHistory);
    document.getElementById('history-output').textContent = JSON.stringify(data, null, 2);
  }));
  document.querySelectorAll('[data-delete-history]').forEach(btn => btn.addEventListener('click', async () => { if (confirm('이력을 삭제할까요?')) { await call('delete_history', btn.dataset.deleteHistory); renderHistory(); } }));
  const viewer = document.getElementById('history-viewer');
  const resizer = viewer.querySelector('.history-resizer');
  let resizing = false;
  resizer.addEventListener('mousedown', () => { resizing = true; });
  document.addEventListener('mousemove', e => { if (resizing) viewer.style.width = `${Math.max(360, e.clientX - viewer.getBoundingClientRect().left)}px`; });
  document.addEventListener('mouseup', () => { resizing = false; });
}
