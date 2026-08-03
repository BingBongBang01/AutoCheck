// ===== Dashboard =====
// 화면 구성(위 -> 아래)은 관제 화면의 하향식 스캔 순서를 따른다.
//   1) 맥락      : 고객사 / 프로파일 / 최신 수집 시각(= 수치의 신뢰도)
//   2) 핵심 KPI  : Health, 수집률, 비정상 비율, 수집 지연
//   3) 구성 비율 : 장비 수집 커버리지 + 정상/비정상 로그 도넛
//   4) 원인 분해 : 에러 유형 Top N(수평 막대) + 비정상 상위 장비(테이블)
//   5) 상세      : Stage 진행률 / AI 요약 / 장비별 Health Score
// 색상만으로 상태를 전달하지 않는다 — 모든 계열에 도형 마커(●▲)와 수치 레이블을 함께 붙이고,
// 차트 계열색은 색맹 안전 팔레트(--cb-*)를 쓴다.

function dashEsc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, c =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function dashSkeleton() {
  const card = '<div class="card"><div class="skeleton skeleton-card"></div></div>';
  const block = '<div class="card"><div class="skeleton skeleton-line" style="width:40%"></div>' +
                '<div class="skeleton skeleton-block"></div></div>';
  return `
    <h1 class="page-title">대시보드</h1>
    <p class="page-sub">데이터를 집계하는 중…</p>
    <div class="grid-cols-4">${card.repeat(4)}</div>
    <div class="grid-cols-2 section-gap">${block.repeat(2)}</div>`;
}

// value(0~100)를 도넛 게이지로. 색은 임계값에 따라 바뀌지만 중앙에 수치가 항상 함께 표시된다.
function dashGauge(value, color, sub) {
  const r = 34, c = 2 * Math.PI * r;
  const filled = c * Math.max(0, Math.min(100, value)) / 100;
  return `
    <svg class="dash-donut" width="92" height="92" viewBox="0 0 92 92" role="img"
         aria-label="${dashEsc(sub || '')} ${value}%">
      <circle cx="46" cy="46" r="${r}" fill="none" stroke="var(--hover)" stroke-width="10"></circle>
      <circle cx="46" cy="46" r="${r}" fill="none" stroke="${color}" stroke-width="10"
              stroke-linecap="round" stroke-dasharray="${filled} ${c - filled}"
              transform="rotate(-90 46 46)"></circle>
      <text class="dash-donut-center" x="46" y="45" text-anchor="middle">${value}%</text>
      ${sub ? `<text class="dash-donut-sub" x="46" y="58" text-anchor="middle">${dashEsc(sub)}</text>` : ''}
    </svg>`;
}

// 부분 대 전체 — 3개 내외 범주에만 쓴다(파이보다 중앙에 총합을 놓을 수 있는 도넛이 유리).
function dashDonut(segments, centerText, centerSub) {
  const total = segments.reduce((s, g) => s + g.value, 0);
  const r = 38, c = 2 * Math.PI * r;
  let offset = 0;
  const arcs = segments.map(g => {
    const len = total ? c * g.value / total : 0;
    const arc = `<circle cx="46" cy="46" r="${r}" fill="none" stroke="${g.color}" stroke-width="12"
      stroke-dasharray="${len} ${c - len}" stroke-dashoffset="${-offset}" transform="rotate(-90 46 46)"></circle>`;
    offset += len;
    return arc;
  }).join('');
  return `
    <svg class="dash-donut" width="100" height="100" viewBox="0 0 92 92">
      <circle cx="46" cy="46" r="${r}" fill="none" stroke="var(--hover)" stroke-width="12"></circle>
      ${arcs}
      <text class="dash-donut-center" x="46" y="45" text-anchor="middle">${dashEsc(centerText)}</text>
      ${centerSub ? `<text class="dash-donut-sub" x="46" y="58" text-anchor="middle">${dashEsc(centerSub)}</text>` : ''}
    </svg>`;
}

function dashLegend(rows) {
  return `<div class="dash-legend">${rows.map(r => `
    <div class="dash-legend-row">
      <span class="dash-legend-mark" style="color:${r.color}">${r.mark}</span>
      <span>${dashEsc(r.label)}</span>
      <span class="dash-legend-value" style="color:${r.color}">${dashEsc(r.value)}</span>
    </div>`).join('')}</div>`;
}

function dashHBars(rows, emptyText) {
  if (!rows.length) return `<p class="dash-note">${dashEsc(emptyText)}</p>`;
  const max = Math.max(...rows.map(r => r.count));
  return rows.map(r => `
    <div class="dash-hbar-row">
      <span title="${dashEsc(r.label)}" style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">
        ${r.mark ? `<span style="color:${r.color}">${r.mark}</span> ` : ''}${dashEsc(r.label)}
      </span>
      <span class="dash-bar"><span style="width:${max ? Math.round(100 * r.count / max) : 0}%;background:${r.color};"></span></span>
      <span class="dash-hbar-count">${r.count.toLocaleString()}${r.pct != null ? ` (${r.pct}%)` : ''}</span>
    </div>`).join('');
}

async function renderDashboard() {
  const content = document.getElementById('content');
  content.innerHTML = dashSkeleton();

  const data = await call('get_dashboard');
  if (!data) {
    content.innerHTML = `<h1 class="page-title">대시보드</h1>
      <div class="card"><p class="empty-state">대시보드 데이터를 불러올 수 없습니다.
      데스크톱 앱(python main.py)으로 실행했는지 확인하세요.</p></div>`;
    return;
  }

  const kpi = data.kpi || {};
  const ctx = data.context || {};
  const cov = data.coverage || {};
  const logs = data.logs || {};
  const fresh = data.freshness || {};
  const inc = data.incidents || {};
  const healthColor = kpi.health >= 80 ? 'var(--success)' : kpi.health >= 50 ? 'var(--warning)' : 'var(--critical)';

  content.innerHTML = `
    <h1 class="page-title">대시보드</h1>

    <div class="dash-context">
      <span class="dash-chip"><span class="material-symbols-rounded">apartment</span>
        <span class="dash-chip-label">고객사</span><b>${dashEsc(ctx.customer || '-')}</b></span>
      <span class="dash-chip"><span class="material-symbols-rounded">assignment</span>
        <span class="dash-chip-label">프로파일</span><b>${dashEsc(ctx.profile || '-')}</b></span>
      <span class="dash-chip ${fresh.stale ? 'stale' : ''}">
        <span class="material-symbols-rounded">${fresh.stale ? 'warning' : 'schedule'}</span>
        <span class="dash-chip-label">최신 수집</span>
        <b>${dashEsc(fresh.latest_at || '수집 로그 없음')}</b>
        ${fresh.lag_text && fresh.lag_text !== '-' ? `<span class="dash-chip-label">(${dashEsc(fresh.lag_text)})</span>` : ''}
      </span>
      <button class="btn btn-outlined" id="btn-dash-refresh" style="margin-left:auto;">
        <span class="material-symbols-rounded">refresh</span>새로고침
      </button>
    </div>
    ${fresh.stale ? `<p class="dash-note" style="color:var(--warning);">
      ⚠ 최신 점검 로그가 ${dashEsc(fresh.lag_text)} 데이터입니다 — 아래 수치는 실시간 상태가 아니라
      마지막 점검 시점의 통계입니다. 세션 터미널에서 점검을 다시 실행하세요.</p>` : ''}

    <div class="grid-cols-4" style="margin-top:16px;">
      <div class="card hoverable">
        <div class="kpi-label">전체 Health</div>
        <div class="dash-donut-wrap" style="margin-top:8px;gap:12px;">
          ${dashGauge(kpi.health || 0, healthColor, 'Health')}
          <div style="font-size:11px;color:var(--sub);line-height:1.5;">
            ${dashEsc(data.health_basis || '-')}<br>
            인시던트 Critical <b style="color:var(--critical)">${inc.critical ?? 0}</b> ·
            Warning <b style="color:var(--warning)">${inc.warning ?? 0}</b>
          </div>
        </div>
      </div>
      <div class="card hoverable">
        <div class="kpi-label">로그 수집률</div>
        <div class="dash-metric-row"><div class="kpi-value" style="color:var(--cb-normal)">${cov.collected_pct ?? 0}%</div>
          <span class="dash-metric-sub">${cov.collected ?? 0} / ${cov.enabled ?? 0}대</span></div>
        <div class="dash-bar" style="margin-top:10px;">
          <span style="width:${cov.collected_pct ?? 0}%;background:var(--cb-normal);"></span>
          <span style="width:${cov.missing_pct ?? 0}%;background:var(--cb-abnormal);"></span>
        </div>
        <p class="dash-note">미수집 ${cov.missing ?? 0}대 (${cov.missing_pct ?? 0}%)</p>
      </div>
      <div class="card hoverable">
        <div class="kpi-label">비정상 로그 비율</div>
        <div class="dash-metric-row"><div class="kpi-value" style="color:var(--cb-abnormal)">${logs.abnormal_pct ?? 0}%</div>
          <span class="dash-metric-sub">${(logs.abnormal_lines ?? 0).toLocaleString()}줄</span></div>
        <p class="dash-note">전체 ${(logs.total_lines ?? 0).toLocaleString()}줄 중 ·
          정상 ${logs.normal_pct ?? 0}%</p>
      </div>
      <div class="card hoverable">
        <div class="kpi-label">인시던트 (중복 제거)</div>
        <div class="dash-metric-row"><div class="kpi-value" style="color:var(--cb-magenta)">${inc.total ?? 0}</div>
          <span class="dash-metric-sub">건</span></div>
        <p class="dash-note">Critical ${inc.critical ?? 0} · Warning ${inc.warning ?? 0}<br>
          이상 징후 줄 ${(inc.raw_lines ?? 0).toLocaleString()}건을 (장비 × 원인) 단위로 묶은 값</p>
      </div>
    </div>

    <div class="grid-cols-4" style="margin-top:16px;" id="kpi-reach-row">
      ${kpiCard('Reachable', kpi.reachable == null ? '-' : kpi.reachable, 'wifi', 'success')}
      ${kpiCard('Offline', kpi.offline == null ? '-' : kpi.offline, 'wifi_off', 'critical')}
      ${kpiCard(`설정 장비 (Enabled ${kpi.running ?? 0})`, kpi.total_devices ?? 0, 'dns', 'primary')}
      ${kpiCard('저장된 로그/세션', kpi.sessions ?? 0, 'history', 'primary')}
    </div>
    <button class="btn btn-outlined" id="btn-check-reach" style="margin-top:12px;">
      <span class="material-symbols-rounded">refresh</span>Reachable/Offline 지금 확인 (소켓 체크, 몇 초 소요)
    </button>

    <div class="section-gap grid-cols-2">
      <div class="card hoverable">
        <div class="card-header">
          <div class="card-icon"><span class="material-symbols-rounded">dns</span></div>
          <div><p class="card-title">장비 로그 수집 커버리지</p>
               <p class="card-desc">Enabled 장비 중 최신 점검 로그가 생성된 비율</p></div>
        </div>
        <div class="dash-donut-wrap">
          ${dashDonut([
            { value: cov.collected || 0, color: 'var(--cb-normal)' },
            { value: cov.missing || 0, color: 'var(--cb-abnormal)' },
          ], String(cov.enabled || 0), '장비')}
          ${dashLegend([
            { mark: '●', color: 'var(--cb-normal)', label: '로그 수집됨', value: `${cov.collected || 0}대 (${cov.collected_pct || 0}%)` },
            { mark: '▲', color: 'var(--cb-abnormal)', label: '미수집(접속 실패/미실행)', value: `${cov.missing || 0}대 (${cov.missing_pct || 0}%)` },
            { mark: '■', color: 'var(--sub)', label: '전체 설정 장비', value: `${cov.total || 0}대` },
          ])}
        </div>
        ${(cov.missing_devices || []).length ? `<p class="dash-note">
          <b>미수집 장비:</b> ${(cov.missing_devices || []).map(dashEsc).join(', ')}</p>` : ''}
        ${(cov.untagged_devices || []).length ? `<p class="dash-note" style="color:var(--warning);">
          ⚠ 인벤토리에 없는 로그(태깅 누락): ${(cov.untagged_devices || []).map(dashEsc).join(', ')}
          — Device Inventory에 등록하면 수집률 집계에 포함됩니다.</p>` : ''}
      </div>

      <div class="card hoverable">
        <div class="card-header">
          <div class="card-icon"><span class="material-symbols-rounded">pie_chart</span></div>
          <div><p class="card-title">정상 / 비정상 로그</p>
               <p class="card-desc">최신 점검 로그 전체 줄 수 기준</p></div>
        </div>
        <div class="dash-donut-wrap">
          ${dashDonut([
            { value: logs.normal_lines || 0, color: 'var(--cb-normal)' },
            { value: logs.abnormal_lines || 0, color: 'var(--cb-abnormal)' },
          ], (logs.total_lines || 0).toLocaleString(), '줄')}
          ${dashLegend([
            { mark: '●', color: 'var(--cb-normal)', label: '정상', value: `${(logs.normal_lines || 0).toLocaleString()}줄 (${logs.normal_pct || 0}%)` },
            { mark: '▲', color: 'var(--cb-abnormal)', label: '비정상', value: `${(logs.abnormal_lines || 0).toLocaleString()}줄 (${logs.abnormal_pct || 0}%)` },
          ])}
        </div>
        <p class="dash-note">비정상 판정 기준은 config/log_rules.json의 이상 징후 키워드
          (benign 예외 반영) — Reports 탭 하이라이트와 동일합니다.</p>
      </div>
    </div>

    <div class="section-gap grid-cols-2">
      <div class="card hoverable">
        <div class="card-header">
          <div class="card-icon"><span class="material-symbols-rounded">bar_chart</span></div>
          <div><p class="card-title">에러 유형 Top 10</p>
               <p class="card-desc">비정상 줄을 키워드별로 분해 — 많은 순</p></div>
        </div>
        ${dashHBars((data.error_types || []).map(e => ({
          label: e.keyword, count: e.count, pct: e.pct,
          mark: e.severity === 'critical' ? '▲' : '●',
          color: e.severity === 'critical' ? 'var(--cb-magenta)' : 'var(--cb-yellow)',
        })), '비정상 로그가 없습니다.')}
        <p class="dash-note">▲ Critical 계열(FAIL/ERROR/CRITICAL/DOWN/TIMEOUT/UNREACHABLE) ·
          ● Warning 계열(CRC/DROPS 등)</p>
      </div>

      <div class="card hoverable">
        <div class="card-header">
          <div class="card-icon"><span class="material-symbols-rounded">device_hub</span></div>
          <div><p class="card-title">비정상 발생 상위 장비</p>
               <p class="card-desc">Critical 많은 순 — 타겟 트러블슈팅 대상</p></div>
        </div>
        <div id="dash-top-hosts"></div>
      </div>
    </div>

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
        <p style="font-size:13px;color:var(--sub);line-height:1.6;">${dashEsc(data.ai_summary || '이력 없음')}</p>
      </div>
    </div>

    <div class="card section-gap" id="device-score-card" style="display:none;">
      <div class="card-header">
        <div class="card-icon"><span class="material-symbols-rounded">monitor_heart</span></div>
        <div><p class="card-title">장비별 Health Score</p>
             <p class="card-desc">100점 시작, 감점 방식: ${dashEsc(data.health_basis || '-')}</p></div>
      </div>
      <div id="device-score-rows"></div>
    </div>
  `;

  document.getElementById('btn-dash-refresh').addEventListener('click', () => renderDashboard());

  document.getElementById('btn-check-reach').addEventListener('click', async (e) => {
    const btn = e.currentTarget;
    btn.classList.add('loading');
    const reach = await call('check_reachability');
    const values = Object.values(reach || {});
    const reachableCount = values.filter(v => v).length;
    const offlineCount = values.length - reachableCount;
    // Reachable/Offline은 #kpi-reach-row의 첫 두 칸이다.
    const cells = document.querySelectorAll('#kpi-reach-row .kpi-value');
    if (cells.length >= 2) {
      cells[0].textContent = reachableCount;
      cells[1].textContent = offlineCount;
    }
    btn.classList.remove('loading');
  });

  // --- 비정상 상위 장비 테이블 ---
  const hostsEl = document.getElementById('dash-top-hosts');
  const hosts = data.top_hosts || [];
  if (!hosts.length) {
    hostsEl.innerHTML = '<p class="dash-note">이상 징후가 발견된 장비가 없습니다.</p>';
  } else {
    hostsEl.innerHTML = `
      <table class="dtable">
        <thead><tr><th>장비</th><th>인시던트 (반복 횟수)</th>
          <th style="text-align:right;">비정상/전체 줄</th><th>수집 시각</th></tr></thead>
        <tbody>${hosts.map(h => `
          <tr>
            <td><b>${dashEsc(h.device)}</b></td>
            <td>${(h.incidents || []).map(i => `
              <span class="badge ${i.severity === 'critical' ? 'badge-fail' : 'badge-warn'}"
                    title="${dashEsc(i.keyword)} ${i.count}줄">${dashEsc(i.keyword)} ×${i.count}</span>`).join(' ')}</td>
            <td style="text-align:right;" class="mono">${h.abnormal.toLocaleString()} / ${h.lines.toLocaleString()}</td>
            <td class="mono">${dashEsc(h.collected_at)}</td>
          </tr>`).join('')}</tbody>
      </table>
      <p class="dash-note">같은 원인이 반복된 줄은 하나의 인시던트로 묶고 반복 횟수(×N)만 표시합니다.</p>`;
  }

  const barsEl = document.getElementById('stage-bars');
  if (!(data.stages || []).length) {
    barsEl.innerHTML = '<p class="dash-note">채점 이력이 없습니다.</p>';
  }
  (data.stages || []).forEach(s => {
    const ratio = s.total ? Math.round(100 * s.pass / s.total) : 0;
    const badge = s.status === 'COMPLETE' ? 'badge-pass' : s.status === 'IN_PROGRESS' ? 'badge-fail' : 'badge-neutral';
    const barColor = s.status === 'COMPLETE' ? 'var(--success)' : s.status === 'IN_PROGRESS' ? 'var(--critical)' : 'var(--hover)';
    const row = document.createElement('div');
    row.style.marginBottom = '14px';
    row.innerHTML = `
      <div style="display:flex;justify-content:space-between;font-size:13px;margin-bottom:6px;">
        <span>${dashEsc(s.label)}</span>
        <span class="badge ${badge}">${s.pass}/${s.total}</span>
      </div>
      <div class="dash-bar">
        <span style="width:${ratio}%;background:${barColor};"></span>
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
        <span style="width:80px;font-size:13px;">${dashEsc(device)}</span>
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
