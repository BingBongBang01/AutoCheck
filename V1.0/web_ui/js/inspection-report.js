// ===== 보고서 탭 — 원본로그/장비목록으로 정기점검 보고서 엑셀을 만든다 =====
// 파일명과 표지 제목은 활성 고객사 + 정기점검 프로파일 이름으로 조립되고, 결과물은
// data/<고객사>/<프로파일>/reports/ 에 저장된다(폴더가 없으면 백엔드가 만든다).

let reportContext = null;
let reportFiles = [];
let reportSelectedDevices = new Set();
let reportExpandedDevice = null;

const REPORT_STATUS_TONE = {
  '정상': 'badge-pass',
  '확인필요': 'badge-warn',
  '접속 불가': 'badge-fail',
  // 미수집(봐야 하는데 못 봤다)과 해당없음(볼 것이 없다)은 둘 다 판정이 아니라 회색이다.
  // 상태 문구 자체가 다르므로 배지 색이 같아도 화면에서 구분된다.
  '미수집': 'badge-neutral',
  '해당없음': 'badge-neutral',
};

function reportStatusBadge(status) {
  return `<span class="badge ${REPORT_STATUS_TONE[status] || 'badge-neutral'}">${status || '-'}</span>`;
}

function escapeReportText(value) { return esc(value); }

let inspectionReportPollTimer = null;

function startInspectionReportPolling() {
  if (inspectionReportPollTimer) clearInterval(inspectionReportPollTimer);
  onPageLeave(() => { clearInterval(inspectionReportPollTimer); inspectionReportPollTimer = null; });
  inspectionReportPollTimer = setInterval(async () => {
    if (typeof currentPage !== 'undefined' && currentPage !== 'report') {
      clearInterval(inspectionReportPollTimer);
      inspectionReportPollTimer = null;
      return;
    }
    const newFiles = await call('list_inspection_reports') || [];
    if (JSON.stringify(newFiles) !== JSON.stringify(reportFiles)) {
      reportFiles = newFiles;
      renderInspectionReportPanes();
    }
  }, 1500);
}

async function refreshInspectionReport() {
  await loadLogRuns();
  reportContext = await call('get_inspection_report_context', effectiveLogRunId(true));
  reportFiles = await call('list_inspection_reports') || [];
  if (reportContext && reportContext.devices) {
    const alive = new Set(reportContext.devices.map(d => d.name));
    reportSelectedDevices = new Set([...reportSelectedDevices].filter(n => alive.has(n)));
    if (!reportSelectedDevices.size) reportContext.devices.forEach(d => reportSelectedDevices.add(d.name));
  }
  renderInspectionReportPanes();
  startInspectionReportPolling();
}

function renderReportSettingsCard() {
  const ctx = reportContext || {};
  const manager = ctx.manager || {};
  const inspector = ctx.inspector || {};
  return `
    <div class="card">
      <div class="card-header">
        <div class="card-icon"><span class="material-symbols-rounded">description</span></div>
        <div>
          <p class="card-title">보고서 정보</p>
          <p class="card-desc">표지 제목과 파일명은 고객사·정기점검 이름으로 자동 조립됩니다. 담당자/점검자는 표지 서명란에 들어갑니다.</p>
          ${ctx.is_virtual ? '<p class="card-desc" style="color:var(--sub);">이 프로파일은 <b>가상환경</b>으로 설정되어 있습니다 — 전원/FAN/온도/모듈/트랜시버는 \'해당없음\'으로 보고됩니다.</p>' : ''}
        </div>
      </div>
      <div class="report-run-row">${logRunSelectMarkup('rp-run', '장비마다 가장 최근 로그 (모든 회차)')}
        <span class="card-desc">보고서에 쓸 점검의 원본 로그 — 회차를 고르면 그 점검의 로그만, 기본값은 장비마다 가장 최근 것입니다.</span></div>
      <div class="grid-cols-2" style="gap:10px;margin-top:4px;">
        <div class="field-label">표지 제목<input class="field" id="rp-title" value="${escapeReportText(ctx.title || '')}" readonly></div>
        <div class="field-label">점검일자<input class="field" id="rp-date" type="date" value="${escapeReportText(ctx.inspection_date || '')}"></div>
      </div>
      <div class="grid-cols-4" style="gap:10px;margin-top:10px;">
        <div class="field-label">담당자 회사<input class="field" id="rp-mgr-company" value="${escapeReportText(manager.company)}"></div>
        <div class="field-label">담당자명<input class="field" id="rp-mgr-name" value="${escapeReportText(manager.name)}"></div>
        <div class="field-label">담당자 연락처<input class="field" id="rp-mgr-contact" value="${escapeReportText(manager.contact)}"></div>
        <div class="field-label">점검 항목 수<input class="field" value="${ctx.check_item_count || 0}개" readonly></div>
      </div>
      <div class="grid-cols-4" style="gap:10px;margin-top:10px;">
        <div class="field-label">점검자 회사<input class="field" id="rp-insp-company" value="${escapeReportText(inspector.company)}"></div>
        <div class="field-label">점검자명<input class="field" id="rp-insp-name" value="${escapeReportText(inspector.name)}"></div>
        <div class="field-label">점검자 연락처<input class="field" id="rp-insp-contact" value="${escapeReportText(inspector.contact)}"></div>
        <div class="field-label">전월 점검값<input class="field" value="${ctx.previous_available ? '직전 회차에서 가져옴' : '직전 회차 보고서 없음'}" readonly></div>
      </div>
      <div class="field-label" style="margin-top:10px;">파일명
        <input class="field" id="rp-filename" value="${escapeReportText(ctx.filename || '')}">
      </div>
      <p class="card-desc" style="margin-top:8px;">저장 위치: <span class="mono">${escapeReportText(ctx.reports_dir || '-')}</span></p>
      ${reportTemplateLine(ctx.template)}
      <div style="display:flex;gap:8px;margin-top:12px;flex-wrap:wrap;">
        <button class="btn btn-primary" id="rp-btn-export"><span class="material-symbols-rounded">file_download</span>보고서 생성</button>
        <button class="btn btn-primary" id="rp-btn-export-pdf"><span class="material-symbols-rounded">picture_as_pdf</span>PDF 보고서 생성(표준양식)</button>
        <button class="btn btn-outlined" id="rp-btn-open-folder"><span class="material-symbols-rounded">folder_open</span>보고서 폴더 열기</button>
        <button class="btn btn-outlined" id="rp-btn-refresh"><span class="material-symbols-rounded">refresh</span>다시 읽기</button>
      </div>
    </div>`;
}

// 고객사 보고서 템플릿(IMPROVEMENT_PLAN 4-10) — 앱 템플릿 위에 얹는 고객사별 차이(data/<고객사>/report_template.yaml):
// 표지 값, 항목 고치기·더하기·빼기, 벤더를 정한 항목(vendors). 쓰는지와 무엇을 바꿨는지, 읽지 못했으면 그 이유를 보인다.
function reportTemplateLine(t) {
  if (!t) return '';
  let text;
  if (!t.exists) {
    text = '보고서 템플릿: <b>앱 기본</b> — 이 고객사만 항목·표지를 바꾸려면 고객사 템플릿을 만드세요.';
  } else if (!t.readable) {
    text = `<span style="color:var(--critical)">고객사 템플릿을 읽지 못해 앱 기본 템플릿으로 만듭니다 — ${escapeReportText(t.error)}</span>`;
  } else {
    const parts = [];
    if (t.changed.length) parts.push(`고친 항목 ${t.changed.length}`);
    if (t.added.length) parts.push(`더한 항목 ${t.added.length}`);
    if (t.removed.length) parts.push(`뺀 항목 ${t.removed.length}`);
    if (t.vendor_items.length) parts.push(`벤더 지정 ${t.vendor_items.length}`);
    text = `보고서 템플릿: <b>고객사 템플릿</b> — ${parts.length ? parts.join(' · ') : '바꾼 것 없음'}`
      + (t.unknown_removed.length ? ` <span style="color:var(--warning)">· 기본 템플릿에 없는 이름이라 빼지 못함: ${
        escapeReportText(t.unknown_removed.join(', '))}</span>` : '');
  }
  return `<div class="report-template-line"><span>${text}</span>
      <button class="btn btn-outlined rtm-mini" type="button" id="rp-btn-template" title="${escapeReportText(t.path || '')}">${
        t.exists ? '템플릿 폴더 열기' : '고객사 템플릿 만들기'}</button></div>`;
}

function renderReportDeviceRows() {
  const devices = (reportContext && reportContext.devices) || [];
  if (!devices.length) return `<tr><td colspan="9" style="color:var(--sub);">표시할 장비가 없습니다.</td></tr>`;
  return devices.map(device => {
    const checked = reportSelectedDevices.has(device.name) ? 'checked' : '';
    const expanded = reportExpandedDevice === device.name;
    const detail = expanded ? renderReportItemDetail(device.name) : '';
    return `
      <tr data-rp-device="${escapeReportText(device.name)}" style="cursor:pointer;">
        <td><input type="checkbox" data-rp-check="${escapeReportText(device.name)}" ${checked}></td>
        <td class="mono">${escapeReportText(device.name)}</td>
        <td>${escapeReportText(device.model)}</td>
        <td class="mono">${escapeReportText(device.ip)}</td>
        <td>${escapeReportText(device.os_version)}</td>
        <td style="font-size:11px;color:var(--sub);">${escapeReportText(device.collected_at || '-')}</td>
        <td style="text-align:right;">${device.command_count || 0}</td>
        <td>${reportStatusBadge(device.overall_status)}</td>
        <td style="font-size:11px;white-space:pre-wrap;">${escapeReportText(device.remarks || '특이사항 없음')}</td>
      </tr>
      ${detail}`;
  }).join('');
}

function renderReportItemDetail(deviceName) {
  const items = (reportContext && reportContext.device_items && reportContext.device_items[deviceName]) || [];
  if (!items.length) {
    return `<tr><td colspan="9" style="color:var(--sub);font-size:12px;">원본로그가 없어 점검 항목을 판정하지 못했습니다.</td></tr>`;
  }
  const rows = items.map(item => `
    <tr>
      <td style="text-align:right;">${item.no}</td>
      <td>${escapeReportText(item.group)}</td>
      <td>${escapeReportText(item.name)}</td>
      <td class="mono" style="font-size:11px;color:var(--sub);">${escapeReportText(item.method)}</td>
      <td style="font-size:11px;">${escapeReportText(item.criteria)}</td>
      <td style="font-size:11px;">${escapeReportText(item.previous)}</td>
      <td style="font-size:11px;white-space:pre-wrap;">${escapeReportText(item.value)}</td>
      <td>${reportStatusBadge(item.status)}</td>
    </tr>`).join('');
  return `
    <tr><td colspan="9" style="padding:0;background:var(--hover);">
      <table class="dtable" style="margin:0;">
        <thead><tr>
          <th style="width:40px;">No</th><th style="width:120px;">구분</th><th style="width:160px;">점검항목</th>
          <th>점검 방법</th><th>기준값</th><th style="width:140px;">전월 점검값</th>
          <th style="width:180px;">당월 점검값</th><th style="width:90px;">결과</th>
        </tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </td></tr>`;
}

function renderReportDevicesCard() {
  const devices = (reportContext && reportContext.devices) || [];
  const warn = devices.filter(d => d.overall_status === '확인필요').length;
  const unreachable = devices.filter(d => d.unreachable).length;
  // 미수집/해당없음 항목 수도 함께 보여준다 — 확인필요 0 이라고 해서 전부 점검된 것은 아니다.
  const notJudged = devices.reduce((sum, d) => sum + (d.na_count || 0), 0);
  return `
    <div class="card">
      <div class="card-header">
        <div class="card-icon"><span class="material-symbols-rounded">dns</span></div>
        <div>
          <p class="card-title">보고서에 포함할 장비 — ${devices.length}대 (확인필요 ${warn} / 접속 불가 ${unreachable} / 미수집·해당없음 ${notJudged}항목)</p>
          <p class="card-desc">장비 1대가 보고서 시트 1장이 됩니다. 행을 클릭하면 항목별 판정값을 펼쳐 봅니다.</p>
        </div>
      </div>
      <div style="display:flex;gap:8px;align-items:center;margin-bottom:8px;">
        <button class="btn btn-outlined" id="rp-btn-select-all" style="height:26px;padding:0 10px;font-size:11px;">전체 선택</button>
        <button class="btn btn-outlined" id="rp-btn-select-none" style="height:26px;padding:0 10px;font-size:11px;">전체 해제</button>
        <span style="flex:1"></span>
        <span style="font-size:11px;color:var(--sub);">${reportSelectedDevices.size}대 선택됨</span>
      </div>
      <div style="overflow:auto;">
        <table class="dtable">
          <thead><tr>
            <th style="width:34px;"></th><th>Hostname</th><th>모델</th><th>IP</th><th>OS</th>
            <th>수집 시각</th><th style="text-align:right;">커맨드</th><th>점검결과</th><th>특이사항</th>
          </tr></thead>
          <tbody>${renderReportDeviceRows()}</tbody>
        </table>
      </div>
    </div>`;
}

function renderReportFilesCard() {
  const rows = reportFiles.length ? reportFiles.map(file => `
    <tr>
      <td class="mono">${escapeReportText(file.name)}</td>
      <td>${escapeReportText(file.mtime_str)}</td>
      <td style="text-align:right;">${(file.size / 1024).toFixed(1)} KB</td>
      <td style="text-align:right;">
        <button class="btn btn-danger" data-rp-delete="${escapeReportText(file.name)}" style="height:24px;padding:0 8px;font-size:11px;">삭제</button>
      </td>
    </tr>`).join('')
    : `<tr><td colspan="4" style="color:var(--sub);">아직 생성된 보고서가 없습니다.</td></tr>`;
  return `
    <div class="card">
      <div class="card-header">
        <div class="card-icon"><span class="material-symbols-rounded">folder_zip</span></div>
        <div>
          <p class="card-title">생성된 보고서</p>
          <p class="card-desc">이 정기점검 회차의 reports/ 폴더에 저장된 엑셀 파일 목록입니다.</p>
        </div>
      </div>
      <table class="dtable">
        <thead><tr><th>파일명</th><th style="width:180px;">생성 시각</th><th style="width:100px;text-align:right;">크기</th><th style="width:80px;"></th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;
}

function collectReportOptions() {
  const value = (id) => (document.getElementById(id) || {}).value || '';
  return {
    inspection_date: value('rp-date'),
    filename: value('rp-filename'),
    manager: {company: value('rp-mgr-company'), name: value('rp-mgr-name'), contact: value('rp-mgr-contact')},
    inspector: {company: value('rp-insp-company'), name: value('rp-insp-name'), contact: value('rp-insp-contact')},
    devices: [...reportSelectedDevices],
    run_id: effectiveLogRunId(true),
  };
}

function wireInspectionReportEvents() {
  // 회차를 바꾸면 그 점검의 로그로 미리보기를 다시 만든다.
  wireLogRunSelect('rp-run', () => {
    reportContext = null;
    renderInspectionReportPanes();
    refreshInspectionReport();
  });

  const exportBtn = document.getElementById('rp-btn-export');
  if (exportBtn) exportBtn.addEventListener('click', async (event) => {
    if (!reportSelectedDevices.size) { showToast('보고서에 포함할 장비를 1대 이상 선택하세요.', 'warn'); return; }
    const btn = event.currentTarget;
    btn.classList.add('loading');
    const result = await call('export_inspection_report', collectReportOptions());
    btn.classList.remove('loading');
    if (!result) { showToast('보고서 생성에 응답이 없습니다.', 'error'); return; }
    if (result.error) { showToast(result.error, 'error'); return; }
    showToast(`${result.filename} 생성됨 (시트 ${result.sheets.length}장)`);
    reportFiles = await call('list_inspection_reports') || [];
    renderInspectionReportPanes();
  });

  const exportPdfBtn = document.getElementById('rp-btn-export-pdf');
  if (exportPdfBtn) exportPdfBtn.addEventListener('click', async (event) => {
    if (!reportSelectedDevices.size) { showToast('보고서에 포함할 장비를 1대 이상 선택하세요.', 'warn'); return; }
    const btn = event.currentTarget;
    btn.classList.add('loading');
    // 7개 실제 정기점검 PDF를 분석해 만든 표준 양식(스위치/서버 분리, 14개 고정 항목,
    // 정상/비정상 2-state) — 사이트명/벤더/확인자는 아직 이 폼에 없어 config/
    // inspection_report_template.yaml의 meta 기본값을 그대로 쓴다.
    const result = await call('export_inspection_pdf_report', collectReportOptions());
    btn.classList.remove('loading');
    if (!result) { showToast('PDF 보고서 생성에 응답이 없습니다.', 'error'); return; }
    if (result.error) { showToast(result.error, 'error'); return; }
    showToast(`${result.filename} 생성됨 (장비 ${result.device_count}대)`);
    reportFiles = await call('list_inspection_reports') || [];
    renderInspectionReportPanes();
  });

  const openBtn = document.getElementById('rp-btn-open-folder');
  if (openBtn) openBtn.addEventListener('click', async () => {
    const result = await call('open_inspection_report_folder');
    if (result && result.error) showToast(result.error, 'error');
  });

  const refreshBtn = document.getElementById('rp-btn-refresh');
  if (refreshBtn) refreshBtn.addEventListener('click', refreshInspectionReport);

  const templateBtn = document.getElementById('rp-btn-template');
  if (templateBtn) templateBtn.addEventListener('click', async () => {
    const result = await call('create_customer_report_template');
    if (!result) return;
    if (result.error) { showToast(result.error, 'error'); return; }
    showToast('고객사 템플릿 폴더를 열었습니다 — report_template.yaml 을 고친 뒤 \'다시 읽기\'를 누르세요.');
    await refreshInspectionReport();
  });

  const selectAll = document.getElementById('rp-btn-select-all');
  if (selectAll) selectAll.addEventListener('click', () => {
    ((reportContext && reportContext.devices) || []).forEach(d => reportSelectedDevices.add(d.name));
    renderInspectionReportPanes();
  });
  const selectNone = document.getElementById('rp-btn-select-none');
  if (selectNone) selectNone.addEventListener('click', () => {
    reportSelectedDevices.clear();
    renderInspectionReportPanes();
  });

  document.querySelectorAll('[data-rp-check]').forEach(box => {
    box.addEventListener('click', (event) => {
      event.stopPropagation();  // 체크박스 클릭이 행 펼치기로 번지지 않게.
      const name = box.dataset.rpCheck;
      if (box.checked) reportSelectedDevices.add(name); else reportSelectedDevices.delete(name);
      renderInspectionReportPanes();
    });
  });

  document.querySelectorAll('[data-rp-device]').forEach(row => {
    row.addEventListener('click', () => {
      const name = row.dataset.rpDevice;
      reportExpandedDevice = reportExpandedDevice === name ? null : name;
      renderInspectionReportPanes();
    });
  });

  document.querySelectorAll('[data-rp-delete]').forEach(btn => {
    btn.addEventListener('click', async () => {
      const name = btn.dataset.rpDelete;
      if (!confirm(`${name} 을(를) 삭제할까요?`)) return;
      const result = await call('delete_inspection_report', name);
      if (result && result.error) { showToast(result.error, 'error'); return; }
      reportFiles = await call('list_inspection_reports') || [];
      renderInspectionReportPanes();
    });
  });
}

function renderInspectionReportPanes() {
  const el = document.getElementById('report-panes');
  if (!el) return;
  if (!reportContext) {
    el.innerHTML = `<div class="card"><p class="card-desc">불러오는 중...</p></div>`;
    return;
  }
  // error가 있어도 고객사/프로파일 정보와 폴더 경로는 함께 오므로 안내 + 설정 카드를 같이 보여준다.
  const notice = reportContext.error
    ? `<div class="card" style="border-left:3px solid var(--warning);">
         <p class="card-title">보고서를 만들 수 없습니다</p>
         <p class="card-desc">${escapeReportText(reportContext.error)}</p>
       </div>`
    : '';
  el.innerHTML = notice + renderReportSettingsCard() + renderReportDevicesCard() + renderReportFilesCard();
  wireInspectionReportEvents();
}

async function renderInspectionReport() {
  const content = document.getElementById('content');
  content.innerHTML = `
    <h1 class="page-title">보고서</h1>
    <p class="page-sub">원본로그와 장비 목록으로 정기점검 보고서 엑셀을 만들어 이 회차의 reports/ 폴더에 저장합니다.</p>
    <div id="report-panes"></div>`;
  reportContext = null;
  renderInspectionReportPanes();
  await refreshInspectionReport();
}
