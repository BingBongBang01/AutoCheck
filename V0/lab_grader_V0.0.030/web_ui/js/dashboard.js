// ===== Dashboard =====
async function renderDashboard() {
  const data = await call('get_dashboard');
  const kpi = data.kpi;
  const content = document.getElementById('content');
  
  const heroColor = kpi.health >= 80 ? 'var(--success)' : kpi.health >= 50 ? 'var(--warning)' : 'var(--critical)';
  const heroRgb = kpi.health >= 80 ? '34, 197, 94' : kpi.health >= 50 ? '245, 158, 11' : '239, 68, 68';
  
  let anomaliesHtml = '<div class="empty-state" style="padding:20px;">조치 필요한 이상 항목이 없습니다.</div>';
  if (data.top_priority_anomalies && data.top_priority_anomalies.length > 0) {
    anomaliesHtml = data.top_priority_anomalies.map(a => {
      const isFail = a.result === 'FAIL' || a.keyword === 'FAIL' || a.keyword === 'ERROR' || a.keyword === 'CRITICAL';
      const toneClass = isFail ? '' : 'warning';
      const icon = isFail ? 'error' : 'warning';
      const device = a.device || '-';
      const check = a.check || a.rule || a.keyword || 'Unknown Check';
      const desc = a.suggested_action || a.message || (a.actual ? `Expected ${a.expected}, but got ${a.actual}` : '수동 확인 필요');
      return `
        <div class="anomaly-item ${toneClass}">
          <span class="material-symbols-rounded anomaly-icon">${icon}</span>
          <div class="anomaly-content">
            <div class="anomaly-title"><span class="anomaly-device">${device}</span><span>${check}</span></div>
            <div class="anomaly-desc">${desc}</div>
          </div>
        </div>
      `;
    }).join('');
  }
  
  let devicesHtml = '<div class="empty-state">장비별 채점 이력이 없습니다.</div>';
  const deviceScores = data.device_scores || {};
  if (Object.keys(deviceScores).length > 0) {
    devicesHtml = Object.entries(deviceScores).sort((a, b) => a[1] - b[1]).map(([device, score]) => {
      const status = score >= 80 ? 'success' : score >= 50 ? 'warning' : 'critical';
      return `
        <div class="device-tile" data-status="${status}">
          <div class="device-tile-info">
            <span class="device-tile-name">${device}</span>
            <span class="device-tile-status">${status.toUpperCase()}</span>
          </div>
          <div class="device-tile-score">${score}</div>
        </div>
      `;
    }).join('');
  }

  content.innerHTML = `
    <h1 class="page-title">대시보드</h1>
    <p class="page-sub">${await call('get_active_project') || '프로젝트 없음'}</p>

    <div class="dashboard-top-row">
      <!-- Left: Hero -->
      <div class="dashboard-hero" style="--hero-color: ${heroColor}; --hero-rgb: ${heroRgb};">
        <div class="dashboard-hero-title">Overall Health Score</div>
        <div class="hero-score-circle">${kpi.health}</div>
        <div class="hero-score-desc">${kpi.health >= 80 ? '네트워크 상태가 양호합니다' : '조치가 필요한 항목이 있습니다'}</div>
      </div>
      
      <!-- Right: Stats & Top Anomalies -->
      <div style="display: flex; flex-direction: column; gap: var(--gap-lg);">
        <div class="dashboard-stats-grid">
          <div class="dashboard-stat-card">
            <div class="dashboard-stat-label"><span class="material-symbols-rounded">dns</span>Total Devices</div>
            <div class="dashboard-stat-value">${kpi.total_devices}</div>
          </div>
          <div class="dashboard-stat-card">
            <div class="dashboard-stat-label"><span class="material-symbols-rounded">wifi</span>Reachable / Offline</div>
            <div class="dashboard-stat-value" style="font-size: 20px;">
              <span style="color:var(--success)">${kpi.reachable === null ? '-' : kpi.reachable}</span> / 
              <span style="color:var(--critical)">${kpi.offline === null ? '-' : kpi.offline}</span>
            </div>
            <button class="btn btn-outlined" id="btn-check-reach" style="height:24px; padding:0 8px; font-size:11px; margin-top:8px;">지금 확인</button>
          </div>
          <div class="dashboard-stat-card">
            <div class="dashboard-stat-label"><span class="material-symbols-rounded">history</span>Inspection Sessions</div>
            <div class="dashboard-stat-value">${kpi.sessions}</div>
          </div>
        </div>

        <div class="card" style="flex: 1;">
          <div class="card-header" style="margin-bottom: 8px;">
            <div class="card-icon" style="background: rgba(239,68,68,0.14); color: var(--critical);"><span class="material-symbols-rounded">gpp_bad</span></div>
            <div><p class="card-title">Actionable Insights (Top 5)</p><p class="card-desc">가장 시급한 조치 필요 항목</p></div>
          </div>
          <div class="anomaly-list">
            ${anomaliesHtml}
          </div>
        </div>
      </div>
    </div>

    <!-- Middle: Device Health Grid -->
    <div class="card section-gap">
      <div class="card-header">
        <div class="card-icon"><span class="material-symbols-rounded">grid_view</span></div>
        <div><p class="card-title">Device Health Map</p><p class="card-desc">각 장비별 상태 (점수 기준)</p></div>
      </div>
      <div class="device-grid">
        ${devicesHtml}
      </div>
    </div>

    <!-- Bottom: Stages & AI Summary -->
    <div class="section-gap grid-cols-2">
      <div class="card hoverable">
        <div class="card-header">
          <div class="card-icon"><span class="material-symbols-rounded">trending_up</span></div>
          <div><p class="card-title">Stage 진행률</p><p class="card-desc">단계별 통과 비율</p></div>
        </div>
        <div id="stage-bars"></div>
      </div>
      <div class="card hoverable">
        <div class="card-header">
          <div class="card-icon"><span class="material-symbols-rounded">smart_toy</span></div>
          <div><p class="card-title">AI Summary</p><p class="card-desc">전체 점검 내용 요약</p></div>
        </div>
        <p style="font-size:13px;color:var(--text);line-height:1.6; padding: 16px; background: rgba(59,130,246,0.05); border-radius: var(--radius-sm); border: 1px solid rgba(59,130,246,0.1);">
          ${data.ai_summary || '이력 없음'}
        </p>
      </div>
    </div>
  `;

  document.getElementById('btn-check-reach').addEventListener('click', async (e) => {
    const btn = e.currentTarget;
    btn.classList.add('loading');
    const reach = await call('check_reachability');
    const values = Object.values(reach || {});
    const reachableCount = values.filter(v => v).length;
    const offlineCount = values.length - reachableCount;
    const statValEl = btn.previousElementSibling;
    statValEl.innerHTML = \`<span style="color:var(--success)">\${reachableCount}</span> / <span style="color:var(--critical)">\${offlineCount}</span>\`;
    btn.classList.remove('loading');
  });

  const barsEl = document.getElementById('stage-bars');
  if (data.stages && data.stages.length > 0) {
    data.stages.forEach(s => {
      const ratio = s.total ? Math.round(100 * s.pass / s.total) : 0;
      const badge = s.status === 'COMPLETE' ? 'badge-pass' : s.status === 'IN_PROGRESS' ? 'badge-fail' : 'badge-neutral';
      const barColor = s.status === 'COMPLETE' ? 'var(--success)' : s.status === 'IN_PROGRESS' ? 'var(--critical)' : 'var(--hover)';
      const row = document.createElement('div');
      row.style.marginBottom = '14px';
      row.innerHTML = \`
        <div style="display:flex;justify-content:space-between;font-size:13px;margin-bottom:6px;">
          <span>\${s.label}</span>
          <span class="badge \${badge}">\${s.pass}/\${s.total}</span>
        </div>
        <div style="height:6px;border-radius:4px;background:var(--hover);overflow:hidden;">
          <div style="width:\${ratio}%;height:100%;background:\${barColor};transition:width 400ms var(--ease);"></div>
        </div>\`;
      barsEl.appendChild(row);
    });
  } else {
    barsEl.innerHTML = '<div class="empty-state" style="padding:20px;">진행된 Stage 이력이 없습니다.</div>';
  }
}
