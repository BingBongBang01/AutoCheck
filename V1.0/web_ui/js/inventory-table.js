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

// tbody를 innerHTML로 통째로 갈아끼우므로, 자동 연결 확인 결과가 도착해 다시 그릴 때
// 사용자가 입력 중이던 칸의 포커스와 커서 위치가 날아간다. 다시 그리기 직전에 어디에
// 있었는지 기억했다가 복원한다(입력 중 확인 결과가 와도 타이핑이 안 끊기게).
function captureInventoryFocus() {
  const el = document.activeElement;
  if (!el || !el.dataset || el.dataset.idx === undefined) return null;
  const snapshot = { idx: el.dataset.idx, field: el.dataset.field };
  if (typeof el.selectionStart === 'number') {
    snapshot.start = el.selectionStart;
    snapshot.end = el.selectionEnd;
  }
  return snapshot;
}

function restoreInventoryFocus(snapshot) {
  if (!snapshot) return;
  const el = document.querySelector(`#inv-tbody [data-idx="${snapshot.idx}"][data-field="${snapshot.field}"]`);
  if (!el) return;
  el.focus();
  if (snapshot.start !== undefined && typeof el.setSelectionRange === 'function') {
    try { el.setSelectionRange(snapshot.start, snapshot.end); } catch (e) { /* number 등 미지원 타입 */ }
  }
}

// 벤더 선택 — 목록에 없는 값(예전에 적어 둔 글자)은 지우지 않고 서버가 읽은 플랫폼과 함께 맨 위에 보여 준다.
function vendorSelect(d, idx) {
  const current = d.vendor || 'Arista';
  let extra = '';
  if (!INVENTORY_VENDORS.some(v => v.value === current)) {
    const match = INVENTORY_VENDORS.find(v => v.platform === d.platform);
    extra = `<option value="${esc(current)}" selected>${esc(current + (match ? ` (${match.label})` : ' — 지원 안 함'))}</option>`;
  }
  const options = INVENTORY_VENDORS.map(v =>
    `<option value="${esc(v.value)}" ${v.value === current ? 'selected' : ''}>${esc(v.label)}</option>`).join('');
  return `<select class="field" style="height:30px;width:120px" data-idx="${idx}" data-field="vendor" title="접속 방식(드라이버)과 enable 처리를 고릅니다">${extra}${options}</select>`;
}

function renderInventoryTable() {
  const focusSnapshot = captureInventoryFocus();
  const rows = getInventoryRows();
  // 필터/삭제로 더 이상 존재하지 않는 idx는 선택에서 제거
  const validIdx = new Set(inventoryDevices.map((_, i) => i));
  inventorySelected = new Set([...inventorySelected].filter(i => validIdx.has(i)));

  const countEl = document.getElementById('inv-selected-count');
  if (countEl) countEl.textContent = inventorySelected.size > 0 ? `${inventorySelected.size}개 선택됨` : '';

  document.getElementById('inv-tbody').innerHTML = rows.map((d) => {
    const idx = inventoryDevices.indexOf(d);
    const isSelected = inventorySelected.has(idx);
    const usesKey = d.auth_method === 'public_key' && (d.key_path || d.key_content || d.has_key_content);
    const authCell = usesKey
      ? `<div style="display:flex;align-items:center;gap:4px;">
           <span class="material-symbols-rounded" style="font-size:15px;color:var(--primary);" title="${esc(d.key_path)}">vpn_key</span>
           <button class="btn btn-outlined" style="height:26px;padding:2px 6px;font-size:11px;" data-open-key="${idx}">키 변경</button>
           <button class="btn btn-outlined" style="height:26px;padding:2px 6px;font-size:11px;" data-clear-key="${idx}">해제</button>
         </div>`
      : `<div style="display:flex;align-items:center;gap:4px;">
           <input class="field" style="height:30px;width:90px" type="password" value="" autocomplete="new-password" placeholder="${esc(secretPlaceholder(d, 'password', '기본값'))}" title="${esc(secretPlaceholder(d, 'password', '비우면 기본 비밀번호'))}" data-idx="${idx}" data-field="password">
           ${(d.has_password || d.password) ? `<button class="btn btn-outlined" style="height:26px;padding:2px 6px;font-size:11px;" data-clear-password="${idx}" title="저장된 비밀번호 지우기(기본 비밀번호를 쓰게)"><span class="material-symbols-rounded" style="font-size:14px">backspace</span></button>` : ''}
           <button class="btn btn-outlined" style="height:26px;padding:2px 6px;font-size:11px;" data-open-key="${idx}" title="퍼블릭키(개인키 파일)로 접속 전환"><span class="material-symbols-rounded" style="font-size:14px">key</span></button>
         </div>`;
    const enableCell = `<div style="display:flex;align-items:center;gap:4px;">
           <input class="field" style="height:30px;width:90px" type="password" value="" autocomplete="new-password" placeholder="${esc(secretPlaceholder(d, 'enable_secret', '기본값'))}" title="${esc(secretPlaceholder(d, 'enable_secret', ENABLE_SECRET_HINT))}" data-idx="${idx}" data-field="enable_secret">
           ${(d.has_enable_secret || d.enable_secret) ? `<button class="btn btn-outlined" style="height:26px;padding:2px 6px;font-size:11px;" data-clear-enable="${idx}" title="저장된 enable 비밀번호 지우기(기본 enable 비밀번호를 쓰게)"><span class="material-symbols-rounded" style="font-size:14px">backspace</span></button>` : ''}
         </div>`;
    // 선택 표시는 outline + 첫 셀 좌측 핀 바(파랑 5px, ► 마커), 연결 확인 결과는 배경색
    // (초록/빨강) — 둘은 서로 다른 정보라 한 행에 겹쳐도 각각 보여야 한다. 선택된 행이
    // 연결 결과 색을 갖고 있으면 그 위에 파란 오버레이를 한 겹 얹어 둘을 동시에 읽게 한다.
    // 자세한 규칙은 style.css의 .probe-* / tr.inv-selected 참고.
    const rowClass = [isSelected ? 'inv-selected' : '', probeRowClass(d)].filter(Boolean).join(' ');
    return `
    <tr class="${rowClass}" data-row-idx="${idx}">
      <td style="text-align:center;cursor:pointer;" data-select-cell="${idx}"><input type="checkbox" ${isSelected ? 'checked' : ''} data-select="${idx}"></td>
      <td><input class="field" style="height:30px;width:100px" value="${esc(d.name)}" data-idx="${idx}" data-field="name"></td>
      <td><input class="field" style="height:30px;width:120px" value="${esc(Array.isArray(d.aliases) ? d.aliases.join(', ') : (d.aliases || ''))}"
                 placeholder="FQDN·옛 이름" title="${esc(INVENTORY_ALIAS_HINT)}" data-idx="${idx}" data-field="aliases"></td>
      <td><select class="field" style="height:30px;width:90px" data-idx="${idx}" data-field="role"><option value="switch" ${d.role === 'switch' ? 'selected' : ''}>스위치</option><option value="linux" ${d.role === 'linux' ? 'selected' : ''}>리눅스</option></select></td>
      <td>${vendorSelect(d, idx)}</td>
      <td><input class="field" style="height:30px;width:120px" value="${esc(d.management_ip)}" data-idx="${idx}" data-field="management_ip"></td>
      <td><input class="field" style="height:30px;width:60px" value="${esc(d.ssh_port || 22)}" data-idx="${idx}" data-field="ssh_port"></td>
      <td><input class="field" style="height:30px;width:90px" value="${esc(d.username)}" placeholder="기본값" data-idx="${idx}" data-field="username"></td>
      <td>${authCell}</td>
      <td>${enableCell}</td>
      <td><input class="field" style="height:30px;width:100px" value="${esc(d.location)}" placeholder="예: 1층 IDF" data-idx="${idx}" data-field="location"></td>
      <td><input class="field" style="height:30px;width:100px" value="${esc(d.warranty)}" placeholder="예: ~2027-05" data-idx="${idx}" data-field="warranty"></td>
      <td style="text-align:center"><input type="checkbox" ${d.enabled ? 'checked' : ''} data-idx="${idx}" data-field="enabled"></td>
      <td style="white-space:nowrap;">
        <button class="btn btn-outlined" style="height:30px;padding:4px 8px;font-size:11px;" data-test="${idx}" title="지금 바로 연결 확인"><span class="material-symbols-rounded" style="font-size:14px">wifi_tethering</span></button>
        ${probeStatusCell(d)}
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
  // 전체 선택 체크박스는 머리줄에 있어 다시 그려지지 않는다 — 그릴 때마다 addEventListener 로 붙이면 리스너가 쌓이고,
  // 장비를 읽기 전(행 0개)에 붙은 리스너가 먼저 돌며 '선택 없음'으로 다시 그려 체크를 풀어 버렸다(눌러도 아무 일도
  // 없었다). 처리기는 onchange 하나로 두고, 누르는 그때의 행(검색 결과)을 읽는다.
  const selectAllCb = document.getElementById('inv-select-all-checkbox');
  if (selectAllCb) {
    const rowIdxs = rows.map(d => inventoryDevices.indexOf(d));
    selectAllCb.checked = rowIdxs.length > 0 && rowIdxs.every(i => inventorySelected.has(i));
    selectAllCb.onchange = () => {
      const current = getInventoryRows().map(d => inventoryDevices.indexOf(d));
      if (selectAllCb.checked) current.forEach(i => inventorySelected.add(i));
      else current.forEach(i => inventorySelected.delete(i));
      renderInventoryTable();
    };
  }

  // select[data-field](역할)도 함께 잡는다 — input만 걸려 있어서 역할을 바꿔도
  // 저장이 안 되고 있었다. 역할은 hostname 조회 커맨드(리눅스: hostname / 스위치:
  // show hostname)를 고르는 데도 쓰이므로 반드시 반영돼야 한다.
  document.querySelectorAll('#inv-tbody input[data-field], #inv-tbody select[data-field]').forEach(inp => {
    inp.addEventListener('change', () => {
      const idx = parseInt(inp.dataset.idx);
      const field = inp.dataset.field;
      if (INVENTORY_SECRET_FIELDS.includes(field)) {
        // 비밀번호·enable 비밀번호 칸은 비어 있는 게 기본이다(저장된 값을 보여주지 않는다) — 입력했을 때만 바꾼다.
        if (!inp.value) return;
        setSecret(inventoryDevices[idx], field, inp.value);
      } else {
        inventoryDevices[idx][field] = inp.type === 'checkbox' ? inp.checked : inp.value;
      }
      scheduleAutosave();
      // 접속 결과가 달라질 수 있는 값이면 자동으로 다시 연결 확인
      if (PROBE_TRIGGER_FIELDS.includes(field)) probeRow(inventoryDevices[idx]);
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
      // 붙여넣어 둔 개인키·passphrase 도 지운다 — 예전엔 해제해도 키 본문이 파일에 남았다.
      markSecretCleared(inventoryDevices[idx], 'key_content');
      markSecretCleared(inventoryDevices[idx], 'key_passphrase');
      renderInventoryTable();
      scheduleAutosave();
    });
  });
  document.querySelectorAll('#inv-tbody [data-clear-password]').forEach(btn => {
    btn.addEventListener('click', () => {
      markSecretCleared(inventoryDevices[parseInt(btn.dataset.clearPassword)], 'password');
      renderInventoryTable();
      scheduleAutosave();
    });
  });
  document.querySelectorAll('#inv-tbody [data-clear-enable]').forEach(btn => {
    btn.addEventListener('click', () => {
      markSecretCleared(inventoryDevices[parseInt(btn.dataset.clearEnable)], 'enable_secret');
      renderInventoryTable();
      scheduleAutosave();
    });
  });
  document.querySelectorAll('#inv-tbody [data-trust-key]').forEach(btn => {
    btn.addEventListener('click', () => {
      const device = inventoryDevices.find(d => d._rid === btn.dataset.trustKey);
      if (device) trustChangedHostKey(device);
    });
  });
  document.querySelectorAll('#inv-tbody [data-remove]').forEach(btn => {
    btn.addEventListener('click', () => {
      inventoryDevices.splice(parseInt(btn.dataset.remove), 1);
      renderInventoryTable();
      scheduleAutosave();
    });
  });
  // 연결 테스트 버튼 — 디바운스를 기다리지 않고 지금 즉시 확인(자동 확인과 같은 경로).
  // 예전에는 저장을 먼저 강제한 뒤 이름으로 조회했지만, 이제 화면의 값을 그대로
  // 서버에 넘기므로 자동저장이 꺼져 있어도 방금 고친 IP로 확인된다.
  document.querySelectorAll('#inv-tbody [data-test]').forEach(btn => {
    btn.addEventListener('click', () => {
      probeRow(inventoryDevices[parseInt(btn.dataset.test)], { immediate: true });
    });
  });

  restoreInventoryFocus(focusSnapshot);
}
