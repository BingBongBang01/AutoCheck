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
        <div><label class="field-label">관리 네트워크 대역</label><input class="field" id="def-network" value="${defaults.management_network || ''}" placeholder="172.30.1.0/24"></div>
        <div><label class="field-label">기본 SSH 포트</label><input class="field" id="def-port" value="${defaults.default_ssh_port || 22}"></div>
        <div><label class="field-label">기본 사용자명</label><input class="field" id="def-username" value="${defaults.default_username || 'admin'}"></div>
        <div><label class="field-label">기본 비밀번호</label><input class="field" id="def-password" type="password" value="${defaults.default_password || 'admin'}"></div>
      </div>
      <div style="margin-top:8px;"><button class="btn btn-outlined" id="btn-save-defaults"><span class="material-symbols-rounded">save</span>기본값 저장</button></div>

      <div style="height:1px;background:var(--border);margin:10px 0;"></div>

      <div style="display:flex;gap:10px;align-items:flex-end;flex-wrap:wrap;">
        <div><label class="field-label">시작 대역</label><input class="field" id="pool-prefix" style="width:140px" value="${(defaults.ip_pool||{}).prefix || '172.30.1.'}"></div>
        <div><label class="field-label">시작 번호</label><input class="field" id="pool-start" style="width:90px" value="${(defaults.ip_pool||{}).start || 101}"></div>
        <div><label class="field-label">끝 번호</label><input class="field" id="pool-end" style="width:90px" value="${(defaults.ip_pool||{}).end || 120}"></div>
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
            <th data-sort="name">이름</th><th data-sort="role">역할</th><th data-sort="management_ip">IP</th>
            <th>포트</th><th>사용자명</th><th>인증</th><th>활성</th><th>연결</th><th></th>
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
    // IP를 입력하는 순간 change 이벤트가 자동 연결 확인을 띄우므로 여기서 따로 부르지 않는다
    // (지금은 IP가 비어 있어 확인할 대상이 없다).
    inventoryDevices.push(ensureRowIds([{ name: '', role: '', management_ip: '', ssh_port: 22, username: '', password: '',
                            auth_method: 'password', key_path: '', key_content: '', key_passphrase: '', tag: [], memo: '', enabled: true }])[0]);
    renderInventoryTable();
  });

  document.getElementById('btn-probe-all').addEventListener('click', probeAllRows);
  document.getElementById('btn-copy-devices').addEventListener('click', openDeviceCopyModal);

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

  const overlay = document.createElement('div');
  overlay.className = 'modal-overlay';
  overlay.innerHTML = `
    <div class="customer-modal card" style="max-width:560px;">
      <div class="customer-modal-header">
        <div>
          <p class="card-title">다른 회차에서 장비목록 복사</p>
          <p class="card-desc">이름이 같은 장비는 건너뜁니다 — 지금 목록이 지워지지 않습니다.</p>
        </div>
        <button class="btn btn-outlined" id="copy-close">닫기</button>
      </div>
      <div style="padding:16px 20px;display:flex;flex-direction:column;gap:8px;">
        ${sources.map(s => `
          <button class="btn btn-outlined" data-copy-src="${s.id}"
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
    </div>`;
  document.body.appendChild(overlay);
  overlay.querySelector('#copy-close').onclick = () => overlay.remove();
  overlay.onclick = e => { if (e.target === overlay) overlay.remove(); };

  overlay.querySelectorAll('[data-copy-src]').forEach(btn => btn.onclick = async () => {
    const source = sources.find(s => s.id === btn.dataset.copySrc);
    if (!source.device_count) { alert('그 회차에는 등록된 장비가 없습니다.'); return; }
    overlay.remove();
    const result = await call('copy_devices_from_profile', source.id, false);
    if (!result || result.error) { alert(result?.error || '복사하지 못했습니다.'); return; }
    inventoryDevices = ensureRowIds(await call('get_devices') || []);
    renderInventoryTable();
    showToast(`'${source.name}'에서 장비 ${result.added}대 복사됨${result.skipped ? ` · 이름 중복 ${result.skipped}대 건너뜀` : ''}`,
              result.added ? 'success' : 'warn');
    if (result.added) probeAllRows();   // 가져온 IP가 지금도 살아있는지 바로 확인
  });
}
