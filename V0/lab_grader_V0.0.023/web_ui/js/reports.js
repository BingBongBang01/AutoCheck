// ===== Reports =====
async function renderReports() {
  const content = document.getElementById('content');
  const formats = await call('list_report_formats') || ['markdown'];
  content.innerHTML = `
    <h1 class="page-title">보고서</h1>
    <p class="page-sub">점검 로그 기반 이상 징후 + 최신 채점 결과 기반 보고서 생성</p>
  `;

  await renderAnomalySection(content);

  const gradeSection = document.createElement('div');
  gradeSection.innerHTML = `
    <div style="display:flex;gap:8px;align-items:center;margin:16px 0 10px;">
      <select class="field" id="report-format" style="width:140px;">
        ${formats.map(f => `<option value="${f}">${f}</option>`).join('')}
      </select>
      <button class="btn btn-primary" id="btn-gen-report"><span class="material-symbols-rounded">description</span>보고서 생성(채점 이력 기반)</button>
    </div>
    <div class="card section-gap">
      <pre class="mono" id="report-output" style="white-space:pre-wrap;color:var(--sub);">아직 생성된 보고서가 없습니다.</pre>
    </div>
  `;
  content.appendChild(gradeSection);
  gradeSection.querySelector('#btn-gen-report').addEventListener('click', async () => {
    const format = document.getElementById('report-format').value;
    if (format === 'markdown') {
      const md = await call('generate_report');
      document.getElementById('report-output').textContent = md || "(채점 이력 없음 — '수집/채점' 탭에서 '채점 실행'을 먼저 눌러주세요)";
    } else {
      const result = await call('generate_report_as', format);
      document.getElementById('report-output').textContent = (result && result.error) ? `${result.error} — '수집/채점' 탭에서 '채점 실행'을 먼저 눌러주세요.` : `생성됨: ${result.path}`;
    }
  });

  await renderRawLogReportSection(content);
}

// ===== 점검 로그 기반 이상 징후 하이라이트 (채점 이력과 무관 — 요구사항 4) =====
const ANOMALY_TONE = {
  FAIL: 'critical', ERROR: 'critical', CRITICAL: 'critical', DOWN: 'critical',
  'TIMEOUT': 'critical', 'UNREACHABLE': 'critical',
  CRC: 'warning', DROPS: 'warning', 'ERR-DISABLED': 'warning',
};

async function renderAnomalySection(content) {
  const devices = await call('get_raw_log_findings') || [];
  const section = document.createElement('div');
  section.className = 'card';
  const totalFindings = devices.reduce((sum, d) => sum + d.findings.length, 0);
  section.innerHTML = `
    <div class="card-header">
      <div class="card-icon" style="background:var(--critical)22;color:var(--critical);"><span class="material-symbols-rounded">report</span></div>
      <div><p class="card-title">이상 징후(Anomaly) 하이라이트</p><p class="card-desc">점검 로그(세션 터미널 수집 원본)에서 FAIL/ERROR/CRITICAL/DOWN/CRC/drops 등 키워드가 포함된 줄을 자동으로 찾아 표시합니다. 채점 이력 없이도 바로 확인 가능합니다.</p></div>
    </div>
    ${!devices.length
      ? `<p style="font-size:12px;color:var(--sub);">${totalFindings === 0 ? '이상 징후가 발견되지 않았거나(정상), 아직 점검 로그가 없습니다 — 세션 터미널에서 점검을 먼저 실행하세요.' : ''}</p>`
      : devices.map(d => `
        <div style="margin-bottom:14px;">
          <div style="font-weight:700;font-size:13px;margin-bottom:6px;">${d.device} <span class="badge badge-fail">${d.findings.length}건</span></div>
          <div style="display:flex;flex-direction:column;gap:4px;">
            ${d.findings.map(f => `
              <div style="display:flex;gap:8px;align-items:baseline;padding:6px 10px;border-radius:6px;background:var(--${ANOMALY_TONE[f.keyword] || 'warning'})14;border-left:3px solid var(--${ANOMALY_TONE[f.keyword] || 'warning'});">
                <span class="badge badge-fail" style="flex-shrink:0;">${f.keyword}</span>
                <span style="font-size:11px;color:var(--sub);flex-shrink:0;">${f.command}:${f.line_no}</span>
                <span class="mono" style="font-size:12px;word-break:break-all;">${f.line}</span>
              </div>`).join('')}
          </div>
        </div>`).join('')}
  `;
  content.appendChild(section);
}

// ===== 점검 로그(세션 터미널 수집 원본) 기반 Excel/PPTX 보고서 =====
async function renderRawLogReportSection(content) {
  const devices = await call('get_report_devices') || [];
  const section = document.createElement('div');
  section.className = 'card section-gap';
  section.innerHTML = `
    <div class="card-header">
      <div class="card-icon"><span class="material-symbols-rounded">table_view</span></div>
      <div><p class="card-title">점검 로그 기반 Excel / PPTX 보고서</p>
        <p class="card-desc">세션 터미널에서 수집된 장비별 원본 출력(show version / show processes top)을 파싱해 생성합니다.</p></div>
    </div>
    <p style="font-size:12px;color:var(--sub);margin-bottom:10px;">대상 장비(점검 로그 보유): ${devices.length ? devices.join(', ') : '없음 — 먼저 세션 터미널에서 점검을 실행하세요.'}</p>
    <div style="display:flex;gap:8px;align-items:center;margin-bottom:10px;flex-wrap:wrap;">
      <button class="btn btn-primary" id="btn-gen-excel" ${devices.length ? '' : 'disabled'}><span class="material-symbols-rounded">grid_on</span>Excel 생성</button>
      <select class="field" id="pptx-device" style="width:160px;" ${devices.length ? '' : 'disabled'}>
        ${devices.map(d => `<option value="${d}">${d}</option>`).join('')}
      </select>
      <button class="btn btn-outlined" id="btn-gen-pptx" ${devices.length ? '' : 'disabled'}><span class="material-symbols-rounded">slideshow</span>PPTX 생성(선택 장비)</button>
      <button class="btn btn-outlined" id="btn-export-report-excel"><span class="material-symbols-rounded">ios_share</span>Export to Excel</button>
    </div>
    <pre class="mono" id="rawlog-report-output" style="white-space:pre-wrap;color:var(--sub);"></pre>
  `;
  content.appendChild(section);

  section.querySelector('#btn-gen-excel')?.addEventListener('click', async () => {
    const result = await call('generate_excel_report');
    section.querySelector('#rawlog-report-output').textContent = result.error || `생성됨: ${result.path}`;
    if (!result.error) flashSaved(true);
  });
  section.querySelector('#btn-gen-pptx')?.addEventListener('click', async () => {
    const device = section.querySelector('#pptx-device').value;
    const result = await call('generate_pptx_report', device);
    section.querySelector('#rawlog-report-output').textContent = result.error || `생성됨: ${result.path}`;
    if (!result.error) flashSaved(true);
  });
  section.querySelector('#btn-export-report-excel')?.addEventListener('click', async () => {
    const btn = section.querySelector('#btn-export-report-excel');
    btn.disabled = true;
    btn.classList.add('loading');
    try {
      const currentProjectId = await call('get_active_project');
      const result = await call('save_report_excel', currentProjectId);
      if (!result) {
        showReportToast('Export cancelled.', 'warn');
      } else if (result.success) {
        showReportToast('Excel report exported successfully.', 'success');
      } else if (result.reason === 'cancelled') {
        showReportToast('Export cancelled.', 'warn');
      } else {
        showReportToast(result.reason || 'Export failed.', 'error');
      }
    } catch (e) {
      showReportToast('Export failed.', 'error');
    } finally {
      btn.disabled = false;
      btn.classList.remove('loading');
    }
  });
}

// showReportToast는 core.js의 공용 showToast()로 옮겨졌다 — 이름만 남겨 하위 호환 유지
// (다른 곳에서 showReportToast(...)로 부르던 코드를 안 건드리기 위한 얇은 별칭).
const showReportToast = showToast;
