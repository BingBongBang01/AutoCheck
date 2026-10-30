// ===== Device Inventory (프로젝트별 SSH 접속 IP/계정 — 유일한 관리 화면) =====
// 테이블 렌더링/다중선택은 inventory-table.js, SSH 키 등록 모달은 inventory-key-modal.js 참고.
let inventoryDevices = [];
let inventorySort = { key: null, asc: true };
let inventoryFilter = '';
let inventoryAutosave = true;
let inventoryAutosaveTimer = null;
let inventorySelected = new Set(); // inventoryDevices 기준 idx 집합 (드래그/시프트/ctrl+a 다중선택)
let inventoryDragging = false;
let inventoryDragStartIdx = null;
let inventoryLastClickedIdx = null;

// ===== 비밀 칸(비밀번호·붙여넣은 개인키·passphrase) — IMPROVEMENT_PLAN 4-1 =====
// 서버는 비밀값을 보내지 않는다 — 저장 여부(has_password 등)와, 이 PC 에서 풀지 못한 칸(unreadable_secrets)만 온다.
// 화면은 새로 입력한 값만 d[칸] 에 들고 있다가 저장 때 보내고, 지우기는 clear_secrets 로 알린다. 서버는 장비 id 로
// 저장된 값을 잇는다. 입력 칸 value 에는 저장된 값을 넣지 않는다(예전엔 비밀번호 평문이 DOM 에 있었다).
const INVENTORY_SECRET_FIELDS = ['password', 'key_content', 'key_passphrase', 'enable_secret'];

// 벤더 — 접속 방식(netmiko 드라이버)과 enable 처리를 고른다(IMPROVEMENT_PLAN 4-5). value 가 vendor 칸에 그대로 저장되고,
// 서버(core/vendors.platform_of)가 get_devices 에 해석 결과(platform)를 붙여 준다.
const INVENTORY_VENDORS = [
  { value: 'Arista', label: 'Arista EOS', platform: 'arista_eos' },
  { value: 'Cisco IOS', label: 'Cisco IOS', platform: 'cisco_ios' },
  { value: 'Cisco IOS-XE', label: 'Cisco IOS-XE', platform: 'cisco_xe' },
  { value: 'Cisco NX-OS', label: 'Cisco NX-OS', platform: 'cisco_nxos' },
];
const INVENTORY_ALIAS_HINT = '이 장비의 다른 이름 — FQDN(core1.lab.local)·바꾸기 전 이름·LLDP 시스템 이름. 쉼표로 여러 개. 구성도·실시간 감시·보고서가 이 이름도 같은 장비로 묶습니다';
const ENABLE_SECRET_HINT = '비우면 기본 enable 비밀번호 — 로그인 뒤 > 에서 시작하는 장비(Cisco 등)가 특권 모드로 들어갈 때 씁니다';
const DEFAULT_SECRET_INPUTS = [
  { field: 'default_password', input: 'def-password', clear: 'btn-clear-def-password',
    empty: '비우면 장비별 비밀번호가 없는 장비는 접속하지 않음' },
  { field: 'default_enable_secret', input: 'def-enable-secret', clear: 'btn-clear-def-enable-secret',
    empty: '장비별 값이 없을 때 사용 — 없으면 Cisco 는 enable 하지 않음' },
];

function newDeviceId() {
  // 화면에서 추가한 행에도 처음부터 id 를 붙인다 — 첫 저장 뒤 화면이 비밀번호를 잊어도 다음 저장이 같은 장비로 이어진다.
  if (window.crypto && typeof window.crypto.randomUUID === 'function') return window.crypto.randomUUID();
  return `c${Date.now().toString(36)}${Math.random().toString(36).slice(2, 10)}`;
}

function secretPlaceholder(d, field, emptyText) {
  if ((d.unreadable_secrets || []).includes(field)) return '이 PC에서 풀 수 없음 — 다시 입력';
  if (d[field]) return '새 값 입력됨 — 저장 대기';
  if (d[`has_${field}`]) return '저장됨 — 바꾸려면 입력';
  return emptyText;
}

function setSecret(d, field, value) {
  if (!value) return;                       // 빈 칸 = 그대로 둠(저장된 값을 지우려면 markSecretCleared)
  d[field] = value;
  d.clear_secrets = (d.clear_secrets || []).filter(f => f !== field);
}

function markSecretCleared(d, field) {
  delete d[field];
  d[`has_${field}`] = false;
  d.unreadable_secrets = (d.unreadable_secrets || []).filter(f => f !== field);
  d.clear_secrets = [...new Set([...(d.clear_secrets || []), field])];
}

// 저장이 끝나면 보낸 평문을 버리고 '저장됨' 표시로 바꾼다. 저장 응답을 기다리는 사이에 또 입력한 값은 남긴다.
async function saveInventoryRows() {
  const rows = collectInventoryRows();
  const sent = rows.map(d => ({ d, values: INVENTORY_SECRET_FIELDS.map(f => d[f]), cleared: [...(d.clear_secrets || [])] }));
  const saved = await call('save_devices', rows);
  if (saved !== true) return saved;
  for (const { d, values, cleared } of sent) {
    INVENTORY_SECRET_FIELDS.forEach((field, i) => {
      if (values[i] && d[field] === values[i]) {
        delete d[field];
        d[`has_${field}`] = true;
        d.unreadable_secrets = (d.unreadable_secrets || []).filter(f => f !== field);
      }
    });
    if (d.clear_secrets) {
      d.clear_secrets = d.clear_secrets.filter(f => !cleared.includes(f));
      if (!d.clear_secrets.length) delete d.clear_secrets;
    }
  }
  return saved;
}

function scheduleAutosave() {
  if (!inventoryAutosave) return;
  const statusEl = document.getElementById('inv-autosave-status');
  if (statusEl) statusEl.textContent = '저장 대기 중...';
  clearTimeout(inventoryAutosaveTimer);
  inventoryAutosaveTimer = setTimeout(async () => {
    const saved = await saveInventoryRows();
    if (statusEl) {
      // 예전엔 결과와 상관없이 '자동저장됨' 이었다 — 활성 프로파일이 없거나 예외가 나도 저장된 줄 알았다.
      statusEl.textContent = saved === true ? '자동저장됨' : '저장 실패 — 다시 시도하세요';
      if (saved === true) setTimeout(() => { if (statusEl) statusEl.textContent = ''; }, 1500);
    }
  }, 800);
}

async function renderInventory() {
  inventoryDevices = ensureRowIds(await call('get_devices') || []);
  inventoryProbe = {};   // 탭을 다시 열면 지난 연결 확인 결과는 버린다(지금 상태가 아님)
  const defaults = await call('get_inventory_defaults') || {};
  const content = document.getElementById('content');

  content.innerHTML = `
    <h1 class="page-title">장비 인벤토리</h1>
    <p class="page-sub">Collection은 여기 등록되고 Enable=true인 장비만 SSH 접속합니다. IP는 오직 여기서만 관리합니다.</p>

    <!-- 기본값/IP Pool은 한 번 정하면 잘 안 바꾸는데 200px 가까이 차지해서 정작 장비 목록을
         화면 밖으로 밀어냈다 — 접어 두고 필요할 때만 펼친다(닫힘이 기본). -->
    <details class="fold">
      <summary>
        <span class="material-symbols-rounded" style="font-size:18px;color:var(--primary);">tune</span>
        프로젝트 기본값 / IP Pool 자동 할당
        <span class="fold-desc">장비별 값이 비어있으면 이 기본값을 사용합니다</span>
        <span class="material-symbols-rounded fold-caret">expand_more</span>
      </summary>
      <div class="fold-body">
      <div class="grid-cols-2">
        <div><label class="field-label">관리 네트워크 대역</label><input class="field" id="def-network" value="${esc(defaults.management_network)}" placeholder="172.30.1.0/24"></div>
        <div><label class="field-label">기본 SSH 포트</label><input class="field" id="def-port" value="${esc(defaults.default_ssh_port || 22)}"></div>
        <div><label class="field-label">기본 사용자명</label><input class="field" id="def-username" placeholder="장비별 값이 없을 때 사용" value="${esc(defaults.default_username || '')}"></div>
        <div><label class="field-label">기본 비밀번호</label>
          <div style="display:flex;gap:6px;align-items:center;">
            <input class="field" id="def-password" type="password" autocomplete="new-password" value=""
                   placeholder="${esc(secretPlaceholder(defaults, 'default_password', DEFAULT_SECRET_INPUTS[0].empty))}">
            <button class="btn btn-outlined" id="btn-clear-def-password" title="저장된 기본 비밀번호 지우기" style="${defaults.has_default_password ? '' : 'display:none;'}">지우기</button>
          </div>
        </div>
        <div><label class="field-label">기본 enable 비밀번호</label>
          <div style="display:flex;gap:6px;align-items:center;">
            <input class="field" id="def-enable-secret" type="password" autocomplete="new-password" value=""
                   placeholder="${esc(secretPlaceholder(defaults, 'default_enable_secret', DEFAULT_SECRET_INPUTS[1].empty))}">
            <button class="btn btn-outlined" id="btn-clear-def-enable-secret" title="저장된 기본 enable 비밀번호 지우기" style="${defaults.has_default_enable_secret ? '' : 'display:none;'}">지우기</button>
          </div>
        </div>
      </div>
      <div style="margin-top:8px;"><button class="btn btn-outlined" id="btn-save-defaults"><span class="material-symbols-rounded">save</span>기본값 저장</button></div>

      <div style="height:1px;background:var(--border);margin:10px 0;"></div>

      <div style="display:flex;gap:10px;align-items:flex-end;flex-wrap:wrap;">
        <div><label class="field-label">시작 대역</label><input class="field" id="pool-prefix" style="width:140px" value="${esc((defaults.ip_pool||{}).prefix || '172.30.1.')}"></div>
        <div><label class="field-label">시작 번호</label><input class="field" id="pool-start" style="width:90px" value="${esc((defaults.ip_pool||{}).start || 101)}"></div>
        <div><label class="field-label">끝 번호</label><input class="field" id="pool-end" style="width:90px" value="${esc((defaults.ip_pool||{}).end || 120)}"></div>
        <button class="btn btn-primary" id="btn-generate-pool"><span class="material-symbols-rounded">auto_fix_high</span>자동 생성</button>
        <span id="pool-preview" style="font-size:12px;color:var(--sub);"></span>
      </div>
      </div>
    </details>

    <div class="card section-gap">
      <div class="card-header sticky" style="justify-content:space-between;align-items:center;">
        <div style="display:flex;gap:10px;align-items:center;min-width:0;">
          <div class="card-icon"><span class="material-symbols-rounded">dns</span></div>
          <div style="min-width:0;"><p class="card-title">장비 목록 (${inventoryDevices.length}대)</p><p class="card-desc" title="IP·포트·계정을 고치면 자동으로 연결을 확인합니다 — 성공한 행은 초록, 실패한 행은 빨강. 접속되면 장비의 hostname으로 이름을 맞춥니다.">IP·포트·계정을 고치면 자동으로 연결을 확인합니다 — 성공한 행은 초록, 실패한 행은 빨강. 접속되면 장비의 hostname으로 이름을 맞춥니다.</p></div>
        </div>
        <input class="field" id="inv-search" placeholder="검색 (이름/IP/역할)" style="width:220px;">
      </div>

      <div class="table-scroll">
        <table class="dtable" id="inv-table">
          <thead><tr>
            <th style="text-align:center;"><input type="checkbox" id="inv-select-all-checkbox" title="전체 선택"></th>
            <th data-sort="name">이름</th><th title="${esc(INVENTORY_ALIAS_HINT)}">별칭</th><th data-sort="role">역할</th>
            <th data-sort="vendor" title="접속 방식(드라이버)과 enable 처리를 고릅니다">벤더</th><th data-sort="management_ip">IP</th>
            <th>포트</th><th>사용자명</th><th>인증</th><th title="enable(특권 모드) 비밀번호 — 비우면 기본 enable 비밀번호">enable</th>
            <th data-sort="location" title="정기점검 보고서 장비목록의 '위치' 열">위치</th>
            <th data-sort="warranty" title="정기점검 보고서 장비목록의 'Warranty' 열">Warranty</th>
            <th>활성</th><th>연결</th><th></th>
          </tr></thead>
          <tbody id="inv-tbody"></tbody>
        </table>
      </div>
      <div style="margin-top:16px;display:flex;gap:8px;align-items:center;flex-wrap:wrap;">
        <button class="btn btn-outlined" id="btn-add-inv-device"><span class="material-symbols-rounded">add</span>장비 추가</button>
        <button class="btn btn-outlined" id="btn-probe-all"><span class="material-symbols-rounded">wifi_tethering</span>전체 연결 확인</button>
        <button class="btn btn-outlined" id="btn-copy-devices"><span class="material-symbols-rounded">content_copy</span>다른 회차에서 복사</button>
        <button class="btn btn-outlined" id="btn-import"><span class="material-symbols-rounded">upload_file</span>불러오기 (CSV/YAML/JSON/Excel)</button>
        <button class="btn btn-outlined" id="btn-export-inv"><span class="material-symbols-rounded">download</span>내보내기 (Excel)</button>
        <label style="display:inline-flex;align-items:center;gap:4px;font-size:12px;color:var(--sub);" title="기본은 비밀번호·enable 비밀번호 열을 비우고 내보냅니다">
          <input type="checkbox" id="inv-export-credentials">비밀번호 포함
        </label>
        <button class="btn btn-primary" id="btn-save-inv"><span class="material-symbols-rounded">save</span>저장</button>
        <span style="width:1px;height:22px;background:var(--border);margin:0 4px;"></span>
        <span id="inv-selected-count" style="font-size:12px;color:var(--sub);"></span>
        <button class="btn btn-danger" id="btn-delete-selected"><span class="material-symbols-rounded">delete</span>선택 삭제</button>
        <button class="btn btn-danger" id="btn-delete-all"><span class="material-symbols-rounded">delete_forever</span>전체 삭제</button>
        <label style="display:flex;align-items:center;gap:6px;font-size:12px;color:var(--sub);margin-left:8px;cursor:pointer;">
          <input type="checkbox" id="inv-autosave" ${defaults.auto_save === false ? '' : 'checked'}>자동저장
        </label>
        <span id="inv-autosave-status" style="font-size:11px;color:var(--sub);"></span>
      </div>
    </div>

    <!-- SSH 호스트키(IMPROVEMENT_PLAN 4-6) — 바뀐 키가 있으면 renderHostKeys() 가 펼친다. -->
    <details class="fold section-gap" id="hostkey-fold">
      <summary>
        <span class="material-symbols-rounded" style="font-size:18px;color:var(--primary);">verified_user</span>
        SSH 호스트키
        <span class="fold-desc" id="hostkey-summary"></span>
        <span class="fold-desc">처음 접속한 장비의 키를 기억하고, 키가 바뀐 장비에는 로그인하지 않습니다(이 고객사의 장비만)</span>
        <span class="material-symbols-rounded fold-caret">expand_more</span>
      </summary>
      <div class="fold-body" id="hostkey-body"></div>
    </details>
  `;

  inventoryAutosave = defaults.auto_save !== false;
  renderInventoryTable();
  renderHostKeys();

  document.getElementById('inv-autosave').addEventListener('change', async (e) => {
    inventoryAutosave = e.target.checked;
    document.getElementById('btn-save-inv').style.display = inventoryAutosave ? 'none' : 'inline-flex';
    await call('save_inventory_defaults', { auto_save: inventoryAutosave });
    if (inventoryAutosave) scheduleAutosave();
  });
  document.getElementById('btn-save-inv').style.display = inventoryAutosave ? 'none' : 'inline-flex';

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
    // IP를 입력하는 순간 change 이벤트가 자동 연결 확인을 띄우므로 여기서 따로 부르지 않는다
    // (지금은 IP가 비어 있어 확인할 대상이 없다).
    inventoryDevices.push(ensureRowIds([{ id: newDeviceId(), name: '', role: '', vendor: 'Arista', management_ip: '', ssh_port: 22, username: '',
                            auth_method: 'password', key_path: '',
                            location: '', warranty: '', tag: [], memo: '', enabled: true }])[0]);
    renderInventoryTable();
  });

  document.getElementById('btn-probe-all').addEventListener('click', probeAllRows);
  document.getElementById('btn-copy-devices').addEventListener('click', openDeviceCopyModal);

  document.getElementById('btn-save-inv').addEventListener('click', async () => {
    const ok = await saveInventoryRows();
    flashSaved(ok);
  });

  // 기본 비밀번호·기본 enable 비밀번호: 칸에 입력했을 때만 바꾸고(빈 칸 = 그대로), '지우기'를 누른 뒤 저장하면 지운다.
  const clearedDefaults = new Set();
  DEFAULT_SECRET_INPUTS.forEach(s => {
    document.getElementById(s.clear).addEventListener('click', () => {
      clearedDefaults.add(s.field);
      const input = document.getElementById(s.input);
      input.value = '';
      input.placeholder = '지움 — 기본값 저장을 누르면 적용';
      document.getElementById(s.clear).style.display = 'none';
    });
  });
  document.getElementById('btn-save-defaults').addEventListener('click', async () => {
    const payload = {
      management_network: document.getElementById('def-network').value,
      default_ssh_port: parseInt(document.getElementById('def-port').value) || 22,
      default_username: document.getElementById('def-username').value,
    };
    const typed = new Set();
    const cleared = [];
    DEFAULT_SECRET_INPUTS.forEach(s => {
      const value = document.getElementById(s.input).value;
      if (value) { payload[s.field] = value; typed.add(s.field); }
      else if (clearedDefaults.has(s.field)) cleared.push(s.field);
    });
    if (cleared.length) payload.clear_secrets = cleared;
    const ok = await call('save_inventory_defaults', payload);
    flashSaved(ok === true);
    if (ok === true) {
      DEFAULT_SECRET_INPUTS.forEach(s => {
        const input = document.getElementById(s.input);
        const stored = typed.has(s.field) || (!cleared.includes(s.field) && !!defaults[`has_${s.field}`]);
        defaults[`has_${s.field}`] = stored;
        input.value = '';
        input.placeholder = secretPlaceholder(defaults, s.field, s.empty);
        document.getElementById(s.clear).style.display = stored ? '' : 'none';
      });
      cleared.forEach(field => clearedDefaults.delete(field));
    }
  });

  document.getElementById('btn-generate-pool').addEventListener('click', async () => {
    const prefix = document.getElementById('pool-prefix').value;
    const start = document.getElementById('pool-start').value;
    const end = document.getElementById('pool-end').value;
    document.getElementById('pool-preview').textContent = `${prefix}${start} ~ ${prefix}${end} 생성 중...`;
    const allocated = await call('auto_allocate_ips', prefix, start, end);
    if (allocated && allocated.error) {
      document.getElementById('pool-preview').textContent = allocated.error;
      document.getElementById('pool-preview').style.color = 'var(--critical)';
      return;
    }
    document.getElementById('pool-preview').style.color = 'var(--sub)';
    document.getElementById('pool-preview').textContent = `${allocated}개 장비에 IP 할당됨`;
    inventoryDevices = ensureRowIds(await call('get_devices') || []);
    renderInventoryTable();
    // 방금 IP가 붙은 장비들이 실제로 살아있는지, 이름이 맞는지 바로 확인한다
    probeAllRows();
  });

  document.getElementById('btn-import').addEventListener('click', async () => {
    const result = await call('import_devices', false);
    if (result && result.error) { alert(result.error); return; }
    if (result) {
      inventoryDevices = ensureRowIds(await call('get_devices') || []);
      renderInventoryTable();
      flashSaved(true);
      // 불러온 장비는 이름/IP가 파일에 적힌 값일 뿐이라 실제와 다를 수 있다 — 바로 확인
      probeAllRows();
    }
  });

  document.getElementById('btn-export-inv').addEventListener('click', async () => {
    const include = document.getElementById('inv-export-credentials').checked;
    if (include && !confirm('장비 비밀번호·enable 비밀번호가 엑셀에 평문으로 들어갑니다. 계속할까요?')) return;
    const result = await call('export_devices', include);
    if (result && result.error) alert(result.error);
    else if (result && result.path) {
      const extra = result.warning || result.notice;
      alert(`Excel로 내보냈습니다: ${result.path}${extra ? `\n${extra}` : ''}`);
    }
  });

  document.getElementById('btn-delete-selected').addEventListener('click', () => {
    if (inventorySelected.size === 0) { alert('선택된 장비가 없습니다.'); return; }
    if (!confirm(`선택한 ${inventorySelected.size}개 장비를 삭제할까요?`)) return;
    inventoryDevices = inventoryDevices.filter((_, idx) => !inventorySelected.has(idx));
    inventorySelected = new Set();
    renderInventoryTable();
    scheduleAutosave();
  });

  document.getElementById('btn-delete-all').addEventListener('click', () => {
    if (inventoryDevices.length === 0) return;
    if (!confirm(`전체 장비 ${inventoryDevices.length}개를 모두 삭제할까요?`)) return;
    inventoryDevices = [];
    inventorySelected = new Set();
    renderInventoryTable();
    scheduleAutosave();
  });

  document.getElementById('inv-table').addEventListener('keydown', (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'a') {
      e.preventDefault();
      const rows = getInventoryRows();
      inventorySelected = new Set(rows.map(d => inventoryDevices.indexOf(d)));
      renderInventoryTable();
    }
  });
  document.getElementById('inv-table').setAttribute('tabindex', '0');
  // 문서 전역 리스너는 화면을 떠날 때 뗀다 — 예전엔 장비 목록을 열 때마다 하나씩 쌓였다.
  const stopRowDrag = () => { inventoryDragging = false; };
  document.addEventListener('mouseup', stopRowDrag);
  onPageLeave(() => document.removeEventListener('mouseup', stopRowDrag));
}

function collectInventoryRows() {
  return inventoryDevices.filter(d => d.name);
}

// ===== 다른 회차(프로파일)에서 장비목록 가져오기 =====
// 같은 고객사를 반복 점검하면 장비·IP·계정이 회차가 바뀌어도 대부분 그대로다.
// 새 프로파일은 생성 시 직전 회차를 자동으로 물려받지만, 그보다 예전 회차에서
// 가져오고 싶거나 자동 복사 후 빠진 장비를 채우고 싶을 때 쓰는 수동 경로.
async function openDeviceCopyModal() {
  const sources = await call('list_device_copy_sources') || [];
  if (!sources.length) {
    alert('이 고객사에 다른 정기점검 회차가 없습니다.\n먼저 다른 회차를 만들거나, 불러오기(CSV/Excel)를 이용하세요.');
    return;
  }

  const { overlay, close } = openModal({ html: `
    <div class="customer-modal card" style="max-width:560px;">
      <div class="customer-modal-header">
        <div>
          <p class="card-title">다른 회차에서 장비목록 복사</p>
          <p class="card-desc">이름이 같은 장비는 건너뜁니다 — 지금 목록이 지워지지 않습니다.</p>
        </div>
        <button class="btn btn-outlined" id="copy-close" data-modal-close>닫기</button>
      </div>
      <div style="padding:16px 20px;display:flex;flex-direction:column;gap:8px;">
        ${sources.map(s => `
          <button class="btn btn-outlined" data-copy-src="${esc(s.id)}"
                  style="justify-content:space-between;height:auto;padding:12px 14px;text-align:left;">
            <span style="display:flex;flex-direction:column;gap:2px;">
              <span style="font-weight:600;">${escapeHtml(s.name)}</span>
              <span style="font-size:11px;color:var(--sub);font-weight:400;">
                생성 ${escapeHtml((s.created_at || '').replace('T', ' '))}${s.inspection_date ? ` · 점검일 ${escapeHtml(s.inspection_date)}` : ''}
              </span>
            </span>
            <span style="font-size:12px;color:var(--primary);font-weight:600;white-space:nowrap;">${s.device_count}대</span>
          </button>`).join('')}
      </div>
    </div>` });

  overlay.querySelectorAll('[data-copy-src]').forEach(btn => btn.onclick = async () => {
    const source = sources.find(s => s.id === btn.dataset.copySrc);
    if (!source.device_count) { alert('그 회차에는 등록된 장비가 없습니다.'); return; }
    close();
    const result = await call('copy_devices_from_profile', source.id, false);
    if (!result || result.error) { alert(result?.error || '복사하지 못했습니다.'); return; }
    inventoryDevices = ensureRowIds(await call('get_devices') || []);
    renderInventoryTable();
    showToast(`'${source.name}'에서 장비 ${result.added}대 복사됨${result.skipped ? ` · 이름 중복 ${result.skipped}대 건너뜀` : ''}`,
              result.added ? 'success' : 'warn');
    if (result.added) probeAllRows();   // 가져온 IP가 지금도 살아있는지 바로 확인
  });
}
