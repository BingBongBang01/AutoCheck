// ===== pywebview 브리지 준비 대기 =====
let API_READY = false;
window.addEventListener('pywebviewready', () => { API_READY = true; });

async function call(fn, ...args) {
  if (window.pywebview && window.pywebview.api) {
    return await window.pywebview.api[fn](...args);
  }
  return MOCK[fn] ? MOCK[fn](...args) : null;
}

const MOCK = {
  list_projects: () => [{ id: 'lab1_campus', display_name: 'LAB1 Campus' }],
  get_active_project: () => 'lab1_campus',
  get_dashboard: () => ({
    kpi: { health: 66, critical: 3, warning: 8, devices: 7, sessions: 2 },
    stages: [
      { label: 'VLAN', pass: 18, total: 18, status: 'COMPLETE' },
      { label: 'STP', pass: 3, total: 14, status: 'IN_PROGRESS' },
      { label: 'LACP', pass: 0, total: 0, status: 'SKIPPED' },
    ],
    ai_summary: '전체 21/32건 PASS(66%). 완료 단계: VLAN. 미해결 단계: STP.',
  }),
};

document.addEventListener('click', (e) => {
  const btn = e.target.closest('.btn');
  if (!btn) return;
  const rect = btn.getBoundingClientRect();
  const ripple = document.createElement('span');
  ripple.className = 'ripple';
  const size = Math.max(rect.width, rect.height);
  ripple.style.width = ripple.style.height = size + 'px';
  ripple.style.left = (e.clientX - rect.left - size / 2) + 'px';
  ripple.style.top = (e.clientY - rect.top - size / 2) + 'px';
  btn.appendChild(ripple);
  setTimeout(() => ripple.remove(), 500);
});

let currentPage = 'dashboard';

function setActiveNav(page) {
  document.querySelectorAll('.nav-item[data-page]').forEach(el => {
    el.classList.toggle('active', el.dataset.page === page);
  });
}

document.querySelectorAll('.nav-item[data-page]').forEach(el => {
  el.addEventListener('click', () => navigate(el.dataset.page));
});

document.getElementById('nav-collapse').addEventListener('click', () => {
  document.getElementById('sidebar').classList.toggle('collapsed');
});

async function navigate(page) {
  currentPage = page;
  setActiveNav(page);
  const content = document.getElementById('content');
  content.style.opacity = 0;
  await new Promise(r => setTimeout(r, 120));

  const renderers = {
    dashboard: renderDashboard,
    inventory: renderInventory,
    connection: renderConnection,
    catalog: renderCatalog,
    inspection: () => renderComingSoon('Inspection Profile', 'Stage/Target State 관리 (target_state.yaml, stages.yaml 편집)'),
    discovery: renderDiscovery,
    collection: () => renderComingSoon('Collection', '병렬 수집 실시간 진행 화면'),
    analysis: () => renderComingSoon('Analysis', 'Parser/Rule/Evidence/AI/Health/TargetState/Baseline'),
    findings: () => renderComingSoon('Findings', 'Severity/Status/Owner 기반 이슈 트래커'),
    reports: renderReports,
    history: renderHistory,
    knowledge: () => renderComingSoon('Knowledge', 'Folder/Document/Search/Tag 탐색기'),
    settings: renderSettings,
  };
  await (renderers[page] || renderers.dashboard)();
  content.style.opacity = 1;
}

function renderComingSoon(title, desc) {
  document.getElementById('content').innerHTML = `
    <h1 class="page-title">${title}</h1>
    <p class="page-sub">${desc}</p>
    <div class="card">
      <div class="card-header">
        <div class="card-icon"><span class="material-symbols-rounded">construction</span></div>
        <div>
          <p class="card-title">준비 중</p>
          <p class="card-desc">이 탭은 UI 스캐폴드만 완성되어 있고, 실제 기능 연결은 다음 버전에서 진행 예정입니다.</p>
        </div>
      </div>
    </div>`;
}

async function renderDashboard() {
  const data = await call('get_dashboard');
  const kpi = data.kpi;
  const content = document.getElementById('content');
  content.innerHTML = `
    <h1 class="page-title">Dashboard</h1>
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

    <div class="section-gap grid-cols-2">
      <div class="card hoverable">
        <div class="card-header">
          <div class="card-icon"><span class="material-symbols-rounded">hub</span></div>
          <div><p class="card-title">Topology Preview</p><p class="card-desc">Discovery 결과 요약</p></div>
        </div>
        <p style="font-size:12px;color:var(--sub);">Discovery 탭에서 .unl 분석 실행 시 여기 표시됩니다.</p>
      </div>
      <div class="card hoverable">
        <div class="card-header">
          <div class="card-icon"><span class="material-symbols-rounded">history</span></div>
          <div><p class="card-title">Recent Inspection</p><p class="card-desc">최근 점검 세션</p></div>
        </div>
        <p style="font-size:12px;color:var(--sub);">History 탭에서 전체 이력을 확인할 수 있습니다.</p>
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

async function renderDiscovery() {
  const content = document.getElementById('content');
  content.innerHTML = `
    <h1 class="page-title">Discovery</h1>
    <p class="page-sub">.unl 파일 분석 — 토폴로지 / 계층 / 이미지버전 / 인벤토리</p>
    <button class="btn btn-primary" id="btn-run-discovery"><span class="material-symbols-rounded">upload_file</span>.unl 파일 선택</button>
    <button class="btn btn-outlined" id="btn-register-inv" style="display:none;margin-left:8px;"><span class="material-symbols-rounded">playlist_add</span>Inventory 등록</button>
    <div class="card section-gap">
      <div class="terminal" id="discovery-output" style="height:400px;">결과가 여기 표시됩니다.</div>
    </div>
  `;
  let discoveredNodes = [];
  document.getElementById('btn-run-discovery').addEventListener('click', async () => {
    const result = await call('run_discovery');
    if (!result) { document.getElementById('discovery-output').textContent = '(파일 선택 취소됨)'; return; }
    document.getElementById('discovery-output').textContent = result.text;
    discoveredNodes = result.node_names || [];
    document.getElementById('btn-register-inv').style.display = discoveredNodes.length ? 'inline-flex' : 'none';
  });
  document.getElementById('btn-register-inv').addEventListener('click', async () => {
    const added = await call('register_discovered_devices', discoveredNodes);
    flashSaved(true);
    alert(`Device Inventory에 ${added}건 신규 등록됨(IP 미입력·비활성 상태 — Device Inventory 탭에서 채워주세요)`);
  });
}

async function renderReports() {
  const content = document.getElementById('content');
  content.innerHTML = `
    <h1 class="page-title">Reports</h1>
    <p class="page-sub">최신 채점 결과 기반 보고서 생성</p>
    <button class="btn btn-primary" id="btn-gen-report"><span class="material-symbols-rounded">description</span>보고서 생성</button>
    <div class="card section-gap">
      <pre class="mono" id="report-output" style="white-space:pre-wrap;color:var(--sub);">아직 생성된 보고서가 없습니다.</pre>
    </div>
  `;
  document.getElementById('btn-gen-report').addEventListener('click', async () => {
    const md = await call('generate_report');
    document.getElementById('report-output').textContent = md || '(이력 없음 — 채점을 먼저 실행하세요)';
  });
}

async function renderHistory() {
  const sessions = await call('list_history') || [];
  const content = document.getElementById('content');
  const rows = sessions.map(s => `
    <tr><td>${s.session}</td><td>${s.elapsed_sec}초</td>
    <td><span class="badge badge-neutral">${s.stage_count || '-'} stages</span></td></tr>`).join('');
  content.innerHTML = `
    <h1 class="page-title">History</h1>
    <p class="page-sub">세션 이력 및 Trend</p>
    <div class="card">
      <table class="dtable">
        <thead><tr><th>세션</th><th>소요시간</th><th>단계</th></tr></thead>
        <tbody>${rows || '<tr><td colspan="3" style="color:var(--sub)">이력 없음</td></tr>'}</tbody>
      </table>
    </div>
  `;
}

// ===== Device Inventory (신규 — 유일한 IP/계정 관리 화면) =====
let inventoryDevices = [];
let inventorySort = { key: null, asc: true };
let inventoryFilter = '';

async function renderInventory() {
  inventoryDevices = await call('get_devices') || [];
  const defaults = await call('get_inventory_defaults') || {};
  const content = document.getElementById('content');

  content.innerHTML = `
    <h1 class="page-title">Device Inventory</h1>
    <p class="page-sub">Collection은 여기 등록되고 Enable=true인 장비만 SSH 접속합니다. IP는 오직 여기서만 관리합니다.</p>

    <div class="card">
      <div class="card-header">
        <div class="card-icon"><span class="material-symbols-rounded">tune</span></div>
        <div><p class="card-title">프로젝트 기본값 / IP Pool 자동 할당</p><p class="card-desc">장비별 값이 비어있으면 이 기본값을 사용합니다</p></div>
      </div>
      <div class="grid-cols-2">
        <div><label class="field-label">Management Network</label><input class="field" id="def-network" value="${defaults.management_network || ''}" placeholder="172.30.1.0/24"></div>
        <div><label class="field-label">Default SSH Port</label><input class="field" id="def-port" value="${defaults.default_ssh_port || 22}"></div>
        <div><label class="field-label">Default Username</label><input class="field" id="def-username" value="${defaults.default_username || 'admin'}"></div>
        <div><label class="field-label">Default Password</label><input class="field" id="def-password" type="password" value="${defaults.default_password || 'admin'}"></div>
      </div>
      <div style="margin-top:12px;"><button class="btn btn-outlined" id="btn-save-defaults"><span class="material-symbols-rounded">save</span>기본값 저장</button></div>

      <div style="height:1px;background:var(--border);margin:20px 0;"></div>

      <div style="display:flex;gap:16px;align-items:flex-end;flex-wrap:wrap;">
        <div><label class="field-label">Prefix</label><input class="field" id="pool-prefix" style="width:140px" value="${(defaults.ip_pool||{}).prefix || '172.30.1.'}"></div>
        <div><label class="field-label">Start</label><input class="field" id="pool-start" style="width:90px" value="${(defaults.ip_pool||{}).start || 101}"></div>
        <div><label class="field-label">End</label><input class="field" id="pool-end" style="width:90px" value="${(defaults.ip_pool||{}).end || 120}"></div>
        <button class="btn btn-primary" id="btn-generate-pool"><span class="material-symbols-rounded">auto_fix_high</span>Generate</button>
        <span id="pool-preview" style="font-size:12px;color:var(--sub);"></span>
      </div>
    </div>

    <div class="card section-gap">
      <div class="card-header" style="justify-content:space-between;align-items:center;">
        <div style="display:flex;gap:12px;align-items:center;">
          <div class="card-icon"><span class="material-symbols-rounded">dns</span></div>
          <div><p class="card-title">장비 목록 (${inventoryDevices.length}대)</p><p class="card-desc">Name/Role/IP/Port/Username/Password/Vendor/Model/Zone/Site/Tag/Memo/Enable</p></div>
        </div>
        <input class="field" id="inv-search" placeholder="검색 (이름/IP/역할)" style="width:220px;">
      </div>

      <div style="overflow-x:auto;">
        <table class="dtable" id="inv-table">
          <thead><tr>
            <th data-sort="name">Name</th><th data-sort="role">Role</th><th data-sort="management_ip">IP</th>
            <th>Port</th><th>Username</th><th>Password</th><th data-sort="vendor">Vendor</th>
            <th>Model</th><th>Zone</th><th>Site</th><th>Enable</th><th></th>
          </tr></thead>
          <tbody id="inv-tbody"></tbody>
        </table>
      </div>
      <div style="margin-top:16px;display:flex;gap:8px;">
        <button class="btn btn-outlined" id="btn-add-inv-device"><span class="material-symbols-rounded">add</span>장비 추가</button>
        <button class="btn btn-outlined" id="btn-import"><span class="material-symbols-rounded">upload_file</span>Import (CSV/YAML/JSON/Excel)</button>
        <button class="btn btn-primary" id="btn-save-inv"><span class="material-symbols-rounded">save</span>저장</button>
      </div>
    </div>
  `;

  renderInventoryTable();

  document.getElementById('inv-search').addEventListener('input', (e) => {
    inventoryFilter = e.target.value.toLowerCase();
    renderInventoryTable();
  });
  document.querySelectorAll('#inv-table th[data-sort]').forEach(th => {
    th.addEventListener('click', () => {
      const key = th.dataset.sort;
      inventorySort = { key, asc: inventorySort.key === key ? !inventorySort.asc : true };
      renderInventoryTable();
    });
  });

  document.getElementById('btn-add-inv-device').addEventListener('click', () => {
    inventoryDevices.push({ name: '', role: '', management_ip: '', ssh_port: 22, username: '', password: '',
                            vendor: 'Arista', model: '', zone: '', site: '', tag: [], memo: '', enabled: true });
    renderInventoryTable();
  });

  document.getElementById('btn-save-inv').addEventListener('click', async () => {
    const ok = await call('save_devices', collectInventoryRows());
    flashSaved(ok);
  });

  document.getElementById('btn-save-defaults').addEventListener('click', async () => {
    const ok = await call('save_inventory_defaults', {
      management_network: document.getElementById('def-network').value,
      default_ssh_port: parseInt(document.getElementById('def-port').value) || 22,
      default_username: document.getElementById('def-username').value,
      default_password: document.getElementById('def-password').value,
    });
    flashSaved(ok);
  });

  document.getElementById('btn-generate-pool').addEventListener('click', async () => {
    const prefix = document.getElementById('pool-prefix').value;
    const start = document.getElementById('pool-start').value;
    const end = document.getElementById('pool-end').value;
    document.getElementById('pool-preview').textContent = `${prefix}${start} ~ ${prefix}${end} 생성 중...`;
    const allocated = await call('auto_allocate_ips', prefix, start, end);
    document.getElementById('pool-preview').textContent = `${allocated}개 장비에 IP 할당됨`;
    inventoryDevices = await call('get_devices') || [];
    renderInventoryTable();
  });

  document.getElementById('btn-import').addEventListener('click', async () => {
    const result = await call('import_devices', false);
    if (result && result.error) { alert(result.error); return; }
    if (result) {
      inventoryDevices = await call('get_devices') || [];
      renderInventoryTable();
      flashSaved(true);
    }
  });
}

function renderInventoryTable() {
  let rows = [...inventoryDevices];
  if (inventoryFilter) {
    rows = rows.filter(d => (d.name + d.role + d.management_ip).toLowerCase().includes(inventoryFilter));
  }
  if (inventorySort.key) {
    rows.sort((a, b) => {
      const av = (a[inventorySort.key] || '').toString();
      const bv = (b[inventorySort.key] || '').toString();
      return inventorySort.asc ? av.localeCompare(bv) : bv.localeCompare(av);
    });
  }

  document.getElementById('inv-tbody').innerHTML = rows.map((d) => {
    const idx = inventoryDevices.indexOf(d);
    return `
    <tr>
      <td><input class="field" style="height:30px;width:100px" value="${d.name||''}" data-idx="${idx}" data-field="name"></td>
      <td><input class="field" style="height:30px;width:80px" value="${d.role||''}" data-idx="${idx}" data-field="role"></td>
      <td><input class="field" style="height:30px;width:120px" value="${d.management_ip||''}" data-idx="${idx}" data-field="management_ip"></td>
      <td><input class="field" style="height:30px;width:60px" value="${d.ssh_port||22}" data-idx="${idx}" data-field="ssh_port"></td>
      <td><input class="field" style="height:30px;width:90px" value="${d.username||''}" placeholder="기본값" data-idx="${idx}" data-field="username"></td>
      <td><input class="field" style="height:30px;width:90px" type="password" value="${d.password||''}" placeholder="기본값" data-idx="${idx}" data-field="password"></td>
      <td><input class="field" style="height:30px;width:80px" value="${d.vendor||''}" data-idx="${idx}" data-field="vendor"></td>
      <td><input class="field" style="height:30px;width:100px" value="${d.model||''}" data-idx="${idx}" data-field="model"></td>
      <td><input class="field" style="height:30px;width:70px" value="${d.zone||''}" data-idx="${idx}" data-field="zone"></td>
      <td><input class="field" style="height:30px;width:70px" value="${d.site||''}" data-idx="${idx}" data-field="site"></td>
      <td style="text-align:center"><input type="checkbox" ${d.enabled ? 'checked' : ''} data-idx="${idx}" data-field="enabled"></td>
      <td><button class="btn btn-danger" style="height:30px;padding:4px 8px;" data-remove="${idx}"><span class="material-symbols-rounded" style="font-size:15px">delete</span></button></td>
    </tr>`;
  }).join('');

  document.querySelectorAll('#inv-tbody input[data-field]').forEach(inp => {
    inp.addEventListener('change', () => {
      const idx = parseInt(inp.dataset.idx);
      const field = inp.dataset.field;
      inventoryDevices[idx][field] = inp.type === 'checkbox' ? inp.checked : inp.value;
    });
  });
  document.querySelectorAll('#inv-tbody [data-remove]').forEach(btn => {
    btn.addEventListener('click', () => {
      inventoryDevices.splice(parseInt(btn.dataset.remove), 1);
      renderInventoryTable();
    });
  });
}

function collectInventoryRows() {
  return inventoryDevices.filter(d => d.name);
}

// ===== Connection (SSH Port/Timeout/Retry/Thread 전용) =====
async function renderConnection() {
  const content = document.getElementById('content');
  content.innerHTML = `
    <h1 class="page-title">Connection</h1>
    <p class="page-sub">SSH 접속 옵션만 관리합니다 (IP는 Device Inventory에서 관리).</p>
    <div class="card">
      <div class="grid-cols-2" id="conn-fields"></div>
      <div style="margin-top:16px;"><button class="btn btn-primary" id="btn-save-conn"><span class="material-symbols-rounded">save</span>저장</button></div>
    </div>
  `;
  const connCfg = await call('get_connection_settings') || {};
  const connEl = document.getElementById('conn-fields');
  const connFields = [
    ['check_target_node', '사전점검(Pre-flight) 대상 장비명', connCfg.check_target_node || 'Core1'],
    ['ssh_timeout', 'SSH Timeout(초)', connCfg.ssh_timeout || 20],
    ['retry_count', 'Retry 횟수', connCfg.retry_count || 1],
    ['retry_delay_sec', 'Retry 대기(초)', connCfg.retry_delay_sec || 5],
    ['max_parallel_workers', 'Thread(병렬 연결 수, 비우면 자동)', connCfg.max_parallel_workers || ''],
  ];
  connEl.innerHTML = connFields.map(([key, label, val]) => `
    <div><label class="field-label">${label}</label><input class="field" id="conn-${key}" value="${val}"></div>
  `).join('');
  document.getElementById('btn-save-conn').addEventListener('click', async () => {
    const payload = {};
    connFields.forEach(([key]) => payload[key] = document.getElementById(`conn-${key}`).value);
    const ok = await call('save_connection_settings', payload);
    flashSaved(ok);
  });
}

// ===== Command Catalog =====
async function renderCatalog() {
  const content = document.getElementById('content');
  const catalog = await call('get_catalog') || [];
  const essential = catalog.filter(c => c.category === 'essential');
  const optional = catalog.filter(c => c.category === 'optional');
  const custom = catalog.filter(c => c.category === 'custom');

  const rowHtml = (c) => `
    <div style="display:flex;align-items:center;gap:10px;padding:6px 0;">
      <input type="checkbox" ${c.enabled ? 'checked' : ''} data-cmd-id="${c.id}">
      <span class="mono" style="width:280px;">${c.command}</span>
      <span style="font-size:12px;color:var(--sub);flex:1;">${c.description}</span>
      ${c.category === 'custom' ? `<button class="btn btn-danger" style="height:28px;padding:2px 8px;" data-remove-cmd="${c.id}"><span class="material-symbols-rounded" style="font-size:14px">delete</span></button>` : ''}
    </div>`;

  content.innerHTML = `
    <h1 class="page-title">Command Catalog</h1>
    <p class="page-sub">체크된 커맨드가 Collection 시 함께 실행됩니다.</p>
    <div class="card"><p class="card-title" style="margin-bottom:8px;">필수</p>${essential.map(rowHtml).join('')}</div>
    <div class="card section-gap"><p class="card-title" style="margin-bottom:8px;">선택사항</p>${optional.map(rowHtml).join('')}</div>
    <div class="card section-gap"><p class="card-title" style="margin-bottom:8px;">커스텀</p><div id="custom-rows">${custom.map(rowHtml).join('')}</div>
      <div style="display:flex;gap:8px;margin-top:10px;">
        <input class="field" id="new-cmd" placeholder="show ip route" style="width:240px;">
        <input class="field" id="new-desc" placeholder="설명" style="width:180px;">
        <button class="btn btn-outlined" id="btn-add-cmd"><span class="material-symbols-rounded">add</span>추가</button>
      </div>
    </div>
    <button class="btn btn-primary section-gap" id="btn-save-catalog"><span class="material-symbols-rounded">save</span>저장</button>
  `;

  document.getElementById('btn-add-cmd').addEventListener('click', async () => {
    const cmd = document.getElementById('new-cmd').value.trim();
    const desc = document.getElementById('new-desc').value.trim();
    if (!cmd) return;
    await call('add_catalog_command', cmd, desc);
    renderCatalog();
  });
  document.querySelectorAll('[data-remove-cmd]').forEach(btn => {
    btn.addEventListener('click', async () => {
      await call('remove_catalog_command', btn.dataset.removeCmd);
      renderCatalog();
    });
  });
  document.getElementById('btn-save-catalog').addEventListener('click', async () => {
    const toggles = {};
    document.querySelectorAll('[data-cmd-id]').forEach(inp => toggles[inp.dataset.cmdId] = inp.checked);
    const ok = await call('save_catalog_toggles', toggles);
    flashSaved(ok);
  });
}

// ===== Settings (간소화 — 장비/연결은 전용 페이지로 이동) =====
async function renderSettings() {
  const content = document.getElementById('content');
  content.innerHTML = `
    <h1 class="page-title">Settings</h1>
    <p class="page-sub">프로젝트: ${await call('get_active_project') || '-'}</p>
    <div class="card">
      <div class="card-header">
        <div class="card-icon"><span class="material-symbols-rounded">smart_toy</span></div>
        <div><p class="card-title">AI 제공자 체인</p><p class="card-desc">설정이 없거나 전부 실패해도 규칙기반 분석은 항상 동작함</p></div>
      </div>
      <div style="font-size:12px;color:var(--sub);line-height:1.8;">
        1순위: API (Anthropic) — 환경변수 필요<br>
        2순위: 로컬 NPU (Gemma/Lemonade) — http://localhost:13305<br>
        최종: 규칙기반(rule_based) — 항상 사용 가능
      </div>
    </div>
    <p style="color:var(--sub);font-size:12px;margin-top:16px;">장비/IP 설정은 <b>Device Inventory</b>, SSH 옵션은 <b>Connection</b>, 점검 커맨드는 <b>Command Catalog</b> 탭으로 이동했습니다.</p>
  `;
}

function flashSaved(ok) {
  const el = document.createElement('div');
  el.textContent = ok ? '저장됨' : '저장 실패';
  el.style.cssText = `position:fixed;bottom:40px;right:24px;background:${ok ? 'var(--success)' : 'var(--critical)'};color:#0B1220;padding:8px 16px;border-radius:10px;font-size:13px;font-weight:600;z-index:999;transition:opacity 300ms;`;
  document.body.appendChild(el);
  setTimeout(() => { el.style.opacity = 0; setTimeout(() => el.remove(), 300); }, 1500);
}

function tickClock() {
  document.getElementById('sb-clock').textContent = new Date().toLocaleString('ko-KR');
}
setInterval(tickClock, 1000);
tickClock();

async function refreshStatusBar() {
  const project = await call('get_active_project');
  document.getElementById('sb-project').textContent = '프로젝트: ' + (project || '-');
  document.getElementById('tb-project-name').textContent = project || '프로젝트 없음';
}

(async function init() {
  await refreshStatusBar();
  navigate('dashboard');
})();
