// ===== Analysis (Parser/Rule/Evidence/AI/Health/Vendor 통합) =====
async function renderAnalysis() {
  const content = document.getElementById('content');
  const data = await call('get_analysis');

  if (!data) {
    content.innerHTML = `
      <h1 class="page-title">분석</h1>
      <p class="page-sub">Parser/Rule/Evidence/AI/Health/TargetState/Baseline</p>
      <div class="card"><p style="color:var(--sub);font-size:13px;">이력 없음 — Collection 탭에서 먼저 실행하세요.</p></div>`;
    return;
  }

  const ruleRows = data.rule_breakdown.map(s => {
    const ratio = s.total ? Math.round(100 * s.pass / s.total) : 0;
    const color = s.status === 'COMPLETE' ? 'var(--success)' : s.status === 'IN_PROGRESS' ? 'var(--critical)' : 'var(--sub)';
    return `<div style="display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid var(--border);font-size:13px;">
      <span>${s.stage}</span><span style="color:${color};font-weight:600;">${s.pass}/${s.total} (${ratio}%)</span>
    </div>`;
  }).join('');

  const evidenceRows = data.evidence_samples.map(e => `
    <div style="padding:8px 0;border-bottom:1px solid var(--border);">
      <div style="font-size:13px;"><b>${e.device}</b> / <span class="mono">${e.check_id}</span></div>
      <div style="font-size:12px;color:var(--sub);">기대: ${e.expected} · 실제: ${e.actual}</div>
    </div>`).join('') || '<p style="color:var(--sub);font-size:13px;">FAIL/UNKNOWN 없음</p>';

  const health = data.health;
  const deviceHealthRows = Object.entries(health.device_scores || {})
    .filter(([name]) => name !== '(network-wide)')
    .sort((a, b) => a[1] - b[1])
    .map(([name, score]) => {
      const color = score >= 80 ? 'var(--success)' : score >= 50 ? 'var(--warning)' : 'var(--critical)';
      return `<span class="badge" style="background:${color}22;color:${color};margin:2px;">${name}: ${score}</span>`;
    }).join('');

  content.innerHTML = `
    <h1 class="page-title">분석</h1>
    <p class="page-sub">세션 ${data.session} 기준 — Parser/Rule/Evidence/AI/Health 통합 뷰</p>

    <div class="grid-cols-2">
      <div class="card hoverable">
        <div class="card-header">
          <div class="card-icon"><span class="material-symbols-rounded">rule</span></div>
          <div><p class="card-title">Rule Engine — Stage별 판정</p></div>
        </div>
        ${ruleRows}
      </div>
      <div class="card hoverable">
        <div class="card-header">
          <div class="card-icon"><span class="material-symbols-rounded">smart_toy</span></div>
          <div><p class="card-title">AI 분석 (출처: ${data.ai_source})</p></div>
        </div>
        <p style="font-size:13px;color:var(--sub);">${data.ai_summary}</p>
      </div>
    </div>

    <div class="grid-cols-2 section-gap">
      <div class="card hoverable">
        <div class="card-header">
          <div class="card-icon"><span class="material-symbols-rounded">fact_check</span></div>
          <div><p class="card-title">Evidence 샘플</p></div>
        </div>
        ${evidenceRows}
      </div>
      <div class="card hoverable">
        <div class="card-header">
          <div class="card-icon"><span class="material-symbols-rounded">monitor_heart</span></div>
          <div><p class="card-title">Health Score — 프로젝트 ${health.project_score ?? '-'}점</p></div>
        </div>
        <div>${deviceHealthRows || '<span style="color:var(--sub);font-size:12px;">데이터 없음</span>'}</div>
      </div>
    </div>

    <div class="card section-gap">
      <div class="card-header">
        <div class="card-icon"><span class="material-symbols-rounded">extension</span></div>
        <div><p class="card-title">Vendor / Parser Registry</p></div>
      </div>
      <p style="font-size:12px;color:var(--sub);">등록된 Vendor: ${data.vendor_info.vendors.join(', ') || '없음'} · 등록된 Parser: ${data.vendor_info.parser_count}개</p>
    </div>
  `;
}
