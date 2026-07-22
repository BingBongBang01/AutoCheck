// ===== Device Inventory (프로젝트별 SSH 접속 IP/계정 — 유일한 관리 화면) =====
let inventoryDevices = [];
let inventorySort = { key: null, asc: true };
let inventoryFilter = '';
let inventoryAutosave = true;
let inventoryAutosaveTimer = null;

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
            <th data-sort="name">이름</th><th data-sort="role">역할</th><th data-sort="management_ip">IP</th>
            <th>포트</th><th>사용자명</th><th>인증</th><th>활성</th><th>연결</th><th></th>
          </tr></thead>
          <tbody id="inv-tbody"></tbody>
        </table>
      </div>
      <div style="margin-top:16px;display:flex;gap:8px;align-items:center;">
        <button class="btn btn-outlined" id="btn-add-inv-device"><span class="material-symbols-rounded">add</span>장비 추가</button>
        <button class="btn btn-outlined" id="btn-import"><span class="material-symbols-rounded">upload_file</span>불러오기 (CSV/YAML/JSON/Excel)</button>
        <button class="btn btn-primary" id="btn-save-inv"><span class="material-symbols-rounded">save</span>저장</button>
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
    const usesKey = d.auth_method === 'public_key' && (d.key_path || d.key_content);
    const authCell = usesKey
      ? `<div style="display:flex;align-items:center;gap:4px;">
           <span class="material-symbols-rounded" style="font-size:15px;color:var(--primary);" title="${d.key_path}">vpn_key</span>
           <button class="btn btn-outlined" style="height:26px;padding:2px 6px;font-size:11px;" data-open-key="${idx}">키 변경</button>
           <button class="btn btn-outlined" style="height:26px;padding:2px 6px;font-size:11px;" data-clear-key="${idx}">해제</button>
         </div>`
      : `<div style="display:flex;align-items:center;gap:4px;">
           <input class="field" style="height:30px;width:90px" type="password" value="${d.password||''}" placeholder="기본값" data-idx="${idx}" data-field="password">
           <button class="btn btn-outlined" style="height:26px;padding:2px 6px;font-size:11px;" data-open-key="${idx}" title="퍼블릭키(개인키 파일)로 접속 전환"><span class="material-symbols-rounded" style="font-size:14px">key</span></button>
         </div>`;
    return `
    <tr>
      <td><input class="field" style="height:30px;width:100px" value="${d.name||''}" data-idx="${idx}" data-field="name"></td>
      <td><select class="field" style="height:30px;width:90px" data-idx="${idx}" data-field="role"><option value="switch" ${d.role === 'switch' ? 'selected' : ''}>스위치</option><option value="linux" ${d.role === 'linux' ? 'selected' : ''}>리눅스</option></select></td>
      <td><input class="field" style="height:30px;width:120px" value="${d.management_ip||''}" data-idx="${idx}" data-field="management_ip"></td>
      <td><input class="field" style="height:30px;width:60px" value="${d.ssh_port||22}" data-idx="${idx}" data-field="ssh_port"></td>
      <td><input class="field" style="height:30px;width:90px" value="${d.username||''}" placeholder="기본값" data-idx="${idx}" data-field="username"></td>
      <td>${authCell}</td>
      <td style="text-align:center"><input type="checkbox" ${d.enabled ? 'checked' : ''} data-idx="${idx}" data-field="enabled"></td>
      <td>
        <button class="btn btn-outlined" style="height:30px;padding:4px 8px;font-size:11px;" data-test="${idx}"><span class="material-symbols-rounded" style="font-size:14px">wifi_tethering</span></button>
        <span id="test-result-${idx}" style="font-size:11px;color:var(--sub);margin-left:4px;"></span>
      </td>
      <td><button class="btn btn-danger" style="height:30px;padding:4px 8px;" data-remove="${idx}"><span class="material-symbols-rounded" style="font-size:15px">delete</span></button></td>
    </tr>`;
  }).join('');

  document.querySelectorAll('#inv-tbody input[data-field]').forEach(inp => {
    inp.addEventListener('change', () => {
      const idx = parseInt(inp.dataset.idx);
      const field = inp.dataset.field;
      inventoryDevices[idx][field] = inp.type === 'checkbox' ? inp.checked : inp.value;
      scheduleAutosave();
    });
  });
  document.querySelectorAll('#inv-tbody [data-open-key]').forEach(btn => {
    btn.addEventListener('click', () => openDeviceKeyModal(parseInt(btn.dataset.openKey)));
  });
  document.querySelectorAll('#inv-tbody [data-clear-key]').forEach(btn => {
    btn.addEventListener('click', () => {
      const idx = parseInt(btn.dataset.clearKey);
      inventoryDevices[idx].auth_method = 'password';
      inventoryDevices[idx].key_path = '';
      renderInventoryTable();
      scheduleAutosave();
    });
  });
  document.querySelectorAll('#inv-tbody [data-remove]').forEach(btn => {
    btn.addEventListener('click', () => {
      inventoryDevices.splice(parseInt(btn.dataset.remove), 1);
      renderInventoryTable();
      scheduleAutosave();
    });
  });
  // 연결 테스트 버튼(신규) — 저장 후 그 IP로 즉시 소켓 체크
  document.querySelectorAll('#inv-tbody [data-test]').forEach(btn => {
    btn.addEventListener('click', async () => {
      const idx = parseInt(btn.dataset.test);
      const resultEl = document.getElementById(`test-result-${idx}`);
      resultEl.textContent = '확인 중...';
      btn.classList.add('loading');
      const device = inventoryDevices[idx];
      await call('save_devices', collectInventoryRows()); // 수정 중인 최신 IP를 먼저 저장 후 테스트
      const result = await call('check_device_reachability', device.name);
      resultEl.textContent = result.detail;
      resultEl.style.color = result.reachable ? 'var(--success)' : 'var(--critical)';
      btn.classList.remove('loading');
    });
  });
}

function collectInventoryRows() {
  return inventoryDevices.filter(d => d.name);
}

// ===== 장비별 SSH 키(퍼블릭키 인증) 등록 팝업 =====
function openDeviceKeyModal(idx) {
  const device = inventoryDevices[idx];
  const overlay = document.createElement('div');
  overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.5);display:flex;align-items:center;justify-content:center;z-index:1000;';
  overlay.innerHTML = `
    <div class="card" style="width:440px;max-width:90vw;">
      <div class="card-header">
        <div class="card-icon"><span class="material-symbols-rounded">key</span></div>
        <div><p class="card-title">SSH 키 등록 — ${device.name || '(이름 없음)'}</p><p class="card-desc">확장자가 없는 id_ed25519 개인키와 텍스트 키를 지원합니다. id_ed25519.pub는 공개키라서 선택하지 않습니다.</p></div>
      </div>
      <label class="field-label">Authentication</label>
      <select class="field" id="modal-auth-method" style="margin-bottom:12px;"><option value="password" ${device.auth_method !== 'public_key' ? 'selected' : ''}>Password</option><option value="public_key" ${device.auth_method === 'public_key' ? 'selected' : ''}>Private Key</option></select>
      <div id="modal-key-fields">
      <label class="field-label">키 파일 경로</label>
      <div style="display:flex;gap:8px;margin-bottom:14px;">
        <input class="field" id="modal-key-path" value="${device.key_path || ''}" readonly placeholder="키 파일을 선택하세요">
        <button class="btn btn-outlined" id="btn-browse-key"><span class="material-symbols-rounded">folder_open</span>찾아보기</button>
      </div>
      <label class="field-label">키 텍스트</label>
      <textarea class="field" id="modal-key-content" rows="5" placeholder="-----BEGIN OPENSSH PRIVATE KEY-----">${device.key_content || ''}</textarea>
      <label class="field-label" style="margin-top:10px;">Private Key Passphrase (선택)</label>
      <input class="field" id="modal-key-passphrase" type="password" value="${device.key_passphrase || ''}" placeholder="Passphrase가 있는 경우 입력">
      </div>
      <div style="display:flex;gap:8px;justify-content:flex-end;">
        <button class="btn btn-outlined" id="btn-key-cancel">취소</button>
        <button class="btn btn-primary" id="btn-key-save">저장</button>
      </div>
    </div>
  `;
  document.body.appendChild(overlay);

  const authSelect = overlay.querySelector('#modal-auth-method');
  const keyFields = overlay.querySelector('#modal-key-fields');
  authSelect.addEventListener('change', () => { keyFields.style.display = authSelect.value === 'public_key' ? 'block' : 'none'; });
  authSelect.dispatchEvent(new Event('change'));

  overlay.querySelector('#btn-browse-key').addEventListener('click', async () => {
    const result = await call('read_key_file');
    if (result && result.error) { alert(result.error); return; }
    if (result) {
      overlay.querySelector('#modal-key-path').value = result.path || '';
      overlay.querySelector('#modal-key-content').value = result.content || '';
    }
  });
  overlay.querySelector('#btn-key-cancel').addEventListener('click', () => overlay.remove());
  overlay.addEventListener('click', (e) => { if (e.target === overlay) overlay.remove(); });
  overlay.querySelector('#btn-key-save').addEventListener('click', () => {
    const path = overlay.querySelector('#modal-key-path').value.trim();
    const content = overlay.querySelector('#modal-key-content').value.trim();
    const passphrase = overlay.querySelector('#modal-key-passphrase').value;
    device.key_path = path;
    device.key_content = content;
    device.key_passphrase = passphrase;
    device.auth_method = authSelect.value === 'public_key' && (path || content) ? 'public_key' : 'password';
    if (device.auth_method === 'password') {
      device.key_path = '';
      device.key_content = '';
      device.key_passphrase = '';
    }
    overlay.remove();
    renderInventoryTable();
    scheduleAutosave();
  });
}
