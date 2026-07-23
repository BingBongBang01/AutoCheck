// ===== Reports =====
async function renderReports() {
  const content = document.getElementById('content');
  const formats = await call('list_report_formats') || ['markdown'];
  content.innerHTML = `
    <h1 class="page-title">보고서</h1>
    <p class="page-sub">최신 채점 결과 기반 보고서 생성</p>
    <div style="display:flex;gap:8px;align-items:center;margin-bottom:10px;">
      <select class="field" id="report-format" style="width:140px;">
        ${formats.map(f => `<option value="${f}">${f}</option>`).join('')}
      </select>
      <button class="btn btn-primary" id="btn-gen-report"><span class="material-symbols-rounded">description</span>보고서 생성</button>
    </div>
    <div class="card section-gap">
      <pre class="mono" id="report-output" style="white-space:pre-wrap;color:var(--sub);">아직 생성된 보고서가 없습니다.</pre>
    </div>
  `;
  document.getElementById('btn-gen-report').addEventListener('click', async () => {
    const format = document.getElementById('report-format').value;
    if (format === 'markdown') {
      const md = await call('generate_report');
      document.getElementById('report-output').textContent = md || '(이력 없음 — 채점을 먼저 실행하세요)';
    } else {
      const result = await call('generate_report_as', format);
      document.getElementById('report-output').textContent = result.error || `생성됨: ${result.path}`;
    }
  });
}
