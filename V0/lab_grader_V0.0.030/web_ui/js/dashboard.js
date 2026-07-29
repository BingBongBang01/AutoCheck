// ===== Dashboard =====
async function renderDashboard() {
  const data = await call('get_dashboard');
  const kpi = data.kpi;
  const content = document.getElementById('content');
  content.innerHTML = `
    <h1 class="page-title">대시보드</h1>
    <p class="page-sub">${await call('get_active_project') || '프로젝트 없음'}</p>

    <div class="grid-cols-4" id="kpi-row">
      ${kpiCard('전체 Health', kpi.health + '%', 'monitor_heart', kpi.health >= 80 ? 'success' : kpi.health >= 50 ? 'warning' : 'critical')}
      ${kpiCard('Total Devices', kpi.total_devices, 'dns', 'primary')}
      ${kpiCard('Reachable', kpi.reachable === null ? '-' : kpi.reachable, 'wifi', 'success')}
      ${kpiCard('Offline', kpi.offline === null ? '-' : kpi.offline, 'wifi_off', 'critical')}
    </div>
    <div class="grid-cols-4" style="margin-top:16px;">
      ${kpiCard('Running(Enabled)', kpi.running, 'play_circle', 'primary')}
      ${kpiCard('Critical Findings', kpi.critical, 'error', 'critical')}
      ${kpiCard('Warning Findings', kpi.warning, 'warning', 'warning')}
      ${kpiCard('Sessions', kpi.sessions, 'history', 'primary')}
    </div>
    <button class="btn btn-outlined" id="btn-check-reach" style="margin-top:12px;">
      <span class="material-symbols-rounded">refresh</span>Reachable/Offline 지금 확인 (소켓 체크, 몇 초 소요)
    </button>

    <div class="section-gap grid-cols-2">
      <div class="card hoverable">
        <div class="card-header">
          <div class="card-icon"><span class="material-symbols-rounded">trending_up</span></div>
          <div><p class="card-title">Stage 진행률</p><p class="card-desc">단계별 PASS/TOTAL</p></div>
        </div>
        <div id="stage-bars"></div>
      </div>
      <div class="card hoverable">
        <div class="card-header">
          <div class="card-icon"><span class="material-symbols-rounded">smart_toy</span></div>
          <div><p class="card-title">AI Summary</p><p class="card-desc">규칙기반 자동 요약</p></div>
        </div>
        <p style="font-size:13px;color:var(--sub);line-height:1.6;">${data.ai_summary || '이력 없음'}</p>
      </div>
    </div>

    <div class="card section-gap" id="device-score-card" style="display:none;">
      <div class="card-header">
        <div class="card-icon"><span class="material-symbols-rounded">monitor_heart</span></div>
        <div><p class="card-title">장비별 Health Score</p><p class="card-desc">100점 시작, Rule 위반마다 감점(Critical -30/High -15/Medium -5 등)</p></div>
      </div>
      <div id="device-score-rows"></div>
    </div>
  `;

  document.getElementById('btn-check-reach').addEventListener('click', async (e) => {
    const btn = e.currentTarget;
    btn.classList.add('loading');
    const reach = await call('check_reachability');
    const values = Object.values(reach || {});
    const reachableCount = values.filter(v => v).length;
    const offlineCount = values.length - reachableCount;
    document.querySelectorAll('#kpi-row .kpi-value')[2].textContent = reachableCount;
    document.querySelectorAll('#kpi-row .kpi-value')[3].textContent = offlineCount;
    btn.classList.remove('loading');
  });

  const barsEl = document.getElementById('stage-bars');
  data.stages.forEach(s => {
    const ratio = s.total ? Math.round(100 * s.pass / s.total) : 0;
    const badge = s.status === 'COMPLETE' ? 'badge-pass' : s.status === 'IN_PROGRESS' ? 'badge-fail' : 'badge-neutral';
    const barColor = s.status === 'COMPLETE' ? 'var(--success)' : s.status === 'IN_PROGRESS' ? 'var(--critical)' : 'var(--hover)';
    const row = document.createElement('div');
    row.style.marginBottom = '14px';
    row.innerHTML = `
      <div style="display:flex;justify-content:space-between;font-size:13px;margin-bottom:6px;">
        <span>${s.label}</span>
        <span class="badge ${badge}">${s.pass}/${s.total}</span>
      </div>
      <div style="height:6px;border-radius:4px;background:var(--hover);overflow:hidden;">
        <div style="width:${ratio}%;height:100%;background:${barColor};transition:width 400ms var(--ease);"></div>
      </div>`;
    barsEl.appendChild(row);
  });

  const deviceScores = data.device_scores || {};
  if (Object.keys(deviceScores).length > 0) {
    document.getElementById('device-score-card').style.display = 'block';
    const rowsEl = document.getElementById('device-score-rows');
    Object.entries(deviceScores).sort((a, b) => a[1] - b[1]).forEach(([device, score]) => {
      const color = score >= 80 ? 'var(--success)' : score >= 50 ? 'var(--warning)' : 'var(--critical)';
      const row = document.createElement('div');
      row.style.cssText = 'display:flex;align-items:center;gap:10px;padding:5px 0;';
      row.innerHTML = `
        <span style="width:80px;font-size:13px;">${device}</span>
        <div style="flex:1;height:6px;border-radius:4px;background:var(--hover);overflow:hidden;">
          <div style="width:${score}%;height:100%;background:${color};"></div>
        </div>
        <span style="width:36px;text-align:right;font-size:13px;font-weight:600;color:${color};">${score}</span>`;
      rowsEl.appendChild(row);
    });
  }
}

function kpiCard(label, value, icon, tone) {
  const toneColor = { success: 'var(--success)', warning: 'var(--warning)', critical: 'var(--critical)', primary: 'var(--primary)' }[tone];
  return `
    <div class="card hoverable">
      <div class="card-icon" style="background:${toneColor}22;color:${toneColor};margin-bottom:10px;">
        <span class="material-symbols-rounded">${icon}</span>
      </div>
      <div class="kpi-label">${label}</div>
      <div class="kpi-value" style="color:${toneColor}">${value}</div>
    </div>`;
}
