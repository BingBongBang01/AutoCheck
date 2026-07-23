// ===== Findings (Jira 스타일) =====
async function renderFindings() {
  const content = document.getElementById('content');
  const findings = await call('get_findings') || [];
  const fails = findings.filter(f => f.result !== 'PASS');

  const sevBadge = (sev) => (sev === 'Critical' || sev === 'High') ? 'badge-fail' : sev === 'Medium' ? 'badge-warn' : 'badge-neutral';

  const rows = fails.map(f => `
    <tr>
      <td><span class="badge ${sevBadge(f.severity)}">${f.severity}</span></td>
      <td>${f.status}</td>
      <td>${f.owner || '-'}</td>
      <td>${f.device}</td>
      <td>${f.category}</td>
      <td class="mono" style="max-width:160px;overflow:hidden;text-overflow:ellipsis;">${f.check_id}</td>
      <td style="font-size:12px;color:var(--sub);max-width:220px;">${f.recommendation || '-'}</td>
    </tr>`).join('');

  content.innerHTML = `
    <h1 class="page-title">발견사항</h1>
    <p class="page-sub">최신 세션 기준 — PASS 제외, ${fails.length}건 (전체 ${findings.length}건 중)</p>
    <div class="card">
      ${findings.length === 0 ? '<p style="color:var(--sub);font-size:13px;">이력 없음 — Collection 탭에서 먼저 실행하세요 (v0.0.10 이전 세션은 findings 데이터가 없을 수 있음)</p>' : `
      <table class="dtable">
        <thead><tr><th>Severity</th><th>Status</th><th>Owner</th><th>Device</th><th>Category</th><th>Check ID</th><th>Recommendation</th></tr></thead>
        <tbody>${rows || '<tr><td colspan="7" style="color:var(--sub)">FAIL/UNKNOWN 없음 — 전부 PASS</td></tr>'}</tbody>
      </table>`}
    </div>
  `;
}
