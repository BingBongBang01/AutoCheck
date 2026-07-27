// ===== 장비 목록 테이블 렌더링 + 다중 선택(체크박스/shift/드래그/전체선택) — inventory.js의 상태를 사용 =====
function getInventoryRows() {
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
  return rows;
}

function renderInventoryTable() {
  const rows = getInventoryRows();
  // 필터/삭제로 더 이상 존재하지 않는 idx는 선택에서 제거
  const validIdx = new Set(inventoryDevices.map((_, i) => i));
  inventorySelected = new Set([...inventorySelected].filter(i => validIdx.has(i)));

  const countEl = document.getElementById('inv-selected-count');
  if (countEl) countEl.textContent = inventorySelected.size > 0 ? `${inventorySelected.size}개 선택됨` : '';

  document.getElementById('inv-tbody').innerHTML = rows.map((d) => {
    const idx = inventoryDevices.indexOf(d);
    const isSelected = inventorySelected.has(idx);
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
    <tr class="${isSelected ? 'selected' : ''}" data-row-idx="${idx}" style="${isSelected ? 'background:var(--primary-tint,rgba(59,130,246,0.12));' : ''}">
      <td style="text-align:center;cursor:pointer;" data-select-cell="${idx}"><input type="checkbox" ${isSelected ? 'checked' : ''} data-select="${idx}"></td>
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

  // ===== 다중 선택: 체크박스 클릭, 행 클릭(shift 범위선택), 드래그 다중선택, 전체선택 =====
  function setRowSelected(idx, selected) {
    if (selected) inventorySelected.add(idx); else inventorySelected.delete(idx);
  }
  document.querySelectorAll('#inv-tbody [data-select]').forEach(cb => {
    cb.addEventListener('click', (e) => e.stopPropagation());
    cb.addEventListener('change', () => {
      const idx = parseInt(cb.dataset.select);
      setRowSelected(idx, cb.checked);
      inventoryLastClickedIdx = idx;
      renderInventoryTable();
    });
  });
  document.querySelectorAll('#inv-tbody tr[data-row-idx]').forEach(tr => {
    const idx = parseInt(tr.dataset.rowIdx);
    tr.addEventListener('mousedown', (e) => {
      if (e.target.closest('input, select, button, textarea')) return; // 편집/버튼 클릭은 선택과 분리
      inventoryDragging = true;
      inventoryDragStartIdx = idx;
      if (e.shiftKey && inventoryLastClickedIdx !== null) {
        const rowIdxs = rows.map(d => inventoryDevices.indexOf(d));
        const a = rowIdxs.indexOf(inventoryLastClickedIdx), b = rowIdxs.indexOf(idx);
        if (a !== -1 && b !== -1) {
          const [lo, hi] = a < b ? [a, b] : [b, a];
          rowIdxs.slice(lo, hi + 1).forEach(i => inventorySelected.add(i));
        }
      } else if (e.ctrlKey || e.metaKey) {
        setRowSelected(idx, !inventorySelected.has(idx));
        inventoryLastClickedIdx = idx;
      } else {
        inventorySelected = new Set([idx]);
        inventoryLastClickedIdx = idx;
      }
      renderInventoryTable();
    });
    tr.addEventListener('mouseenter', () => {
      if (!inventoryDragging || inventoryDragStartIdx === null) return;
      const rowIdxs = rows.map(d => inventoryDevices.indexOf(d));
      const a = rowIdxs.indexOf(inventoryDragStartIdx), b = rowIdxs.indexOf(idx);
      if (a === -1 || b === -1) return;
      const [lo, hi] = a < b ? [a, b] : [b, a];
      inventorySelected = new Set(rowIdxs.slice(lo, hi + 1));
      renderInventoryTable();
    });
  });
  const selectAllCb = document.getElementById('inv-select-all-checkbox');
  if (selectAllCb) {
    const rowIdxs = rows.map(d => inventoryDevices.indexOf(d));
    selectAllCb.checked = rowIdxs.length > 0 && rowIdxs.every(i => inventorySelected.has(i));
    selectAllCb.addEventListener('change', () => {
      if (selectAllCb.checked) rowIdxs.forEach(i => inventorySelected.add(i));
      else rowIdxs.forEach(i => inventorySelected.delete(i));
      renderInventoryTable();
    });
  }

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
