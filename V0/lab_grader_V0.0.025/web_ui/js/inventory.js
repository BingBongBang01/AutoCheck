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

function scheduleAutosave() {
  if (!inventoryAutosave) return;
  const statusEl = document.getElementById('inv-autosave-status');
  if (statusEl) statusEl.textContent = '저장 대기 중...';
  clearTimeout(inventoryAutosaveTimer);
  inventoryAutosaveTimer = setTimeout(async () => {
    await call('save_devices', collectInventoryRows());
    if (statusEl) {
      statusEl.textContent = '자동저장됨';
      setTimeout(() => { if (statusEl) statusEl.textContent = ''; }, 1500);
    }
  }, 800);
}

async function renderInventory() {
  inventoryDevices = await call('get_devices') || [];
  const defaults = await call('get_inventory_defaults') || {};
  const content = document.getElementById('content');

  content.innerHTML = `
    <h1 class="page-title">장비 인벤토리</h1>
    <p class="page-sub">Collection은 여기 등록되고 Enable=true인 장비만 SSH 접속합니다. IP는 오직 여기서만 관리합니다.</p>

    <div class="card">
      <div class="card-header">
        <div class="card-icon"><span class="material-symbols-rounded">tune</span></div>
        <div><p class="card-title">프로젝트 기본값 / IP Pool 자동 할당</p><p class="card-desc">장비별 값이 비어있으면 이 기본값을 사용합니다</p></div>
      </div>
      <div class="grid-cols-2">
        <div><label class="field-label">관리 네트워크 대역</label><input class="field" id="def-network" value="${defaults.management_network || ''}" placeholder="172.30.1.0/24"></div>
        <div><label class="field-label">기본 SSH 포트</label><input class="field" id="def-port" value="${defaults.default_ssh_port || 22}"></div>
        <div><label class="field-label">기본 사용자명</label><input class="field" id="def-username" value="${defaults.default_username || 'admin'}"></div>
        <div><label class="field-label">기본 비밀번호</label><input class="field" id="def-password" type="password" value="${defaults.default_password || 'admin'}"></div>
      </div>
      <div style="margin-top:12px;"><button class="btn btn-outlined" id="btn-save-defaults"><span class="material-symbols-rounded">save</span>기본값 저장</button></div>

      <div style="height:1px;background:var(--border);margin:20px 0;"></div>

      <div style="display:flex;gap:16px;align-items:flex-end;flex-wrap:wrap;">
        <div><label class="field-label">시작 대역</label><input class="field" id="pool-prefix" style="width:140px" value="${(defaults.ip_pool||{}).prefix || '172.30.1.'}"></div>
        <div><label class="field-label">시작 번호</label><input class="field" id="pool-start" style="width:90px" value="${(defaults.ip_pool||{}).start || 101}"></div>
        <div><label class="field-label">끝 번호</label><input class="field" id="pool-end" style="width:90px" value="${(defaults.ip_pool||{}).end || 120}"></div>
        <button class="btn btn-primary" id="btn-generate-pool"><span class="material-symbols-rounded">auto_fix_high</span>자동 생성</button>
        <span id="pool-preview" style="font-size:12px;color:var(--sub);"></span>
      </div>
    </div>

    <div class="card section-gap">
      <div class="card-header" style="justify-content:space-between;align-items:center;">
        <div style="display:flex;gap:12px;align-items:center;">
          <div class="card-icon"><span class="material-symbols-rounded">dns</span></div>
          <div><p class="card-title">장비 목록 (${inventoryDevices.length}대)</p><p class="card-desc">이름/역할/IP/포트/사용자명/비밀번호/활성 + SSH 키 등록 + 연결 테스트</p></div>
        </div>
        <input class="field" id="inv-search" placeholder="검색 (이름/IP/역할)" style="width:220px;">
      </div>

      <div style="overflow-x:auto;">
        <table class="dtable" id="inv-table">
          <thead><tr>
            <th style="text-align:center;"><input type="checkbox" id="inv-select-all-checkbox" title="전체 선택"></th>
            <th data-sort="name">이름</th><th data-sort="role">역할</th><th data-sort="management_ip">IP</th>
            <th>포트</th><th>사용자명</th><th>인증</th><th>활성</th><th>연결</th><th></th>
          </tr></thead>
          <tbody id="inv-tbody"></tbody>
        </table>
      </div>
      <div style="margin-top:16px;display:flex;gap:8px;align-items:center;flex-wrap:wrap;">
        <button class="btn btn-outlined" id="btn-add-inv-device"><span class="material-symbols-rounded">add</span>장비 추가</button>
        <button class="btn btn-outlined" id="btn-import"><span class="material-symbols-rounded">upload_file</span>불러오기 (CSV/YAML/JSON/Excel)</button>
        <button class="btn btn-outlined" id="btn-export-inv"><span class="material-symbols-rounded">download</span>내보내기 (Excel)</button>
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
  `;

  inventoryAutosave = defaults.auto_save !== false;
  renderInventoryTable();

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
    inventoryDevices.push({ name: '', role: '', management_ip: '', ssh_port: 22, username: '', password: '',
                            auth_method: 'password', key_path: '', key_content: '', key_passphrase: '', tag: [], memo: '', enabled: true });
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
    if (allocated && allocated.error) {
      document.getElementById('pool-preview').textContent = allocated.error;
      document.getElementById('pool-preview').style.color = 'var(--critical)';
      return;
    }
    document.getElementById('pool-preview').style.color = 'var(--sub)';
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

  document.getElementById('btn-export-inv').addEventListener('click', async () => {
    const result = await call('export_devices');
    if (result && result.error) alert(result.error);
    else if (result && result.path) alert(`Excel로 내보냈습니다: ${result.path}`);
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
  document.addEventListener('mouseup', () => { inventoryDragging = false; });
}

function collectInventoryRows() {
  return inventoryDevices.filter(d => d.name);
}
