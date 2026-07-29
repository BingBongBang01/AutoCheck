// ===== 장비 목록 자동 연결 확인 =====
// 장비를 추가하거나 IP/포트/계정/키를 고치면 잠깐 기다렸다가 자동으로 SSH 접속을 시도한다.
//  - 성공: 행을 초록으로 하이라이트하고, 장비가 알려준 hostname으로 이름을 자동 정정
//  - 실패: 행을 빨강으로 하이라이트하고 실패 사유를 그대로 표시
// 서버에는 저장된 인벤토리가 아니라 "지금 화면에서 편집 중인 값"을 보낸다 —
// 그래야 자동저장이 꺼져 있어도, 저장 버튼을 누르기 전에도 방금 고친 IP로 확인된다.

const PROBE_DEBOUNCE_MS = 900;
// 이 필드들이 바뀌면 접속 결과가 달라질 수 있으므로 다시 확인한다.
// (name/enabled/role은 접속 자체엔 영향이 없어 재확인 대상에서 뺐다 — 이름은 오히려
//  연결 성공 후 이쪽에서 덮어쓰는 값이라 넣으면 무한 재확인이 된다.)
const PROBE_TRIGGER_FIELDS = ['management_ip', 'ssh_port', 'username', 'password'];

let inventoryProbe = {};        // rid -> { status:'checking'|'ok'|'fail', detail, hostname }
let inventoryProbeTimers = {};  // rid -> setTimeout 핸들
let inventoryProbeSeq = {};     // rid -> 요청 일련번호(늦게 도착한 옛 응답을 버리기 위함)
let inventoryRidSeed = 1;

// 행 식별자. 이름은 자동 정정으로 바뀌고 인덱스는 삭제/정렬로 밀리기 때문에
// 결과를 행에 붙여두려면 둘 다 못 쓴다. 저장 시 서버가 모르는 키는 버리므로 안전.
function ensureRowIds(devices) {
  devices.forEach(d => { if (!d._rid) d._rid = `r${inventoryRidSeed++}`; });
  return devices;
}

function probeStateOf(device) {
  return (device && inventoryProbe[device._rid]) || null;
}

function probeRowClass(device) {
  const state = probeStateOf(device);
  if (!state) return '';
  return state.status === 'ok' ? 'probe-ok' : state.status === 'fail' ? 'probe-fail' : 'probe-checking';
}

// 셀에는 짧은 결과만 넣고 자세한 내용(주소·실패 사유·이름 변경 내역)은 툴팁으로 돌린다.
// 긴 문장을 그대로 넣으면 표가 화면 밖으로 밀려나 정작 결과가 안 보인다.
function probeShortLabel(state) {
  if (state.status === 'checking') return '확인 중...';
  if (state.status === 'ok') return state.hostname ? `연결됨 · ${state.hostname}` : '연결됨';
  return state.reachable ? '인증 실패' : '연결 실패';
}

function probeStatusCell(device) {
  const state = probeStateOf(device);
  if (!state) return '';
  const tone = state.status === 'ok' ? 'ok' : state.status === 'fail' ? 'fail' : 'checking';
  return `<span class="probe-status ${tone}" title="${escapeAttr(state.detail || '')}">${escapeHtml(probeShortLabel(state))}</span>`;
}

function escapeHtml(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, c =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
function escapeAttr(s) { return escapeHtml(s); }

// 장비가 알려준 hostname으로 이름을 맞춘다. 이미 같거나, 다른 행이 그 이름을
// 쓰고 있으면 건드리지 않는다(중복 이름은 인벤토리 저장 시 서로를 덮어쓴다).
function applyHostname(device, hostname) {
  if (!hostname || device.name === hostname) return null;
  const taken = inventoryDevices.some(d => d !== device && d.name === hostname);
  if (taken) return { renamed: false, reason: `이름 중복(${hostname})으로 자동 변경 안 함` };
  const before = device.name;
  device.name = hostname;
  return { renamed: true, before, after: hostname };
}

async function probeRow(device, { immediate = false } = {}) {
  const rid = device._rid;
  clearTimeout(inventoryProbeTimers[rid]);

  const run = async () => {
    if (!device.management_ip) {          // IP가 없으면 확인할 게 없다 — 표시도 지운다
      delete inventoryProbe[rid];
      renderInventoryTable();
      return;
    }
    const seq = (inventoryProbeSeq[rid] || 0) + 1;
    inventoryProbeSeq[rid] = seq;

    inventoryProbe[rid] = { status: 'checking', detail: '확인 중...', hostname: null };
    renderInventoryTable();

    const result = await call('probe_device_config', device);
    if (inventoryProbeSeq[rid] !== seq) return;   // 그 사이 값이 또 바뀜 — 이 응답은 폐기
    if (!result) { delete inventoryProbe[rid]; renderInventoryTable(); return; }

    applyProbeResult(device, result);
    renderInventoryTable();
    scheduleAutosave();
  };

  if (immediate) return run();
  inventoryProbeTimers[rid] = setTimeout(run, PROBE_DEBOUNCE_MS);
}

function applyProbeResult(device, result) {
  const ok = !!result.authenticated;
  let detail = result.detail || '';
  if (ok) {
    const renameResult = applyHostname(device, result.hostname);
    if (renameResult && renameResult.renamed) detail += ` · 이름 자동 변경: ${renameResult.before} → ${renameResult.after}`;
    else if (renameResult) detail += ` · ${renameResult.reason}`;
  }
  inventoryProbe[device._rid] = {
    status: ok ? 'ok' : 'fail',
    reachable: !!result.reachable,   // 포트는 열렸는데 인증만 실패한 경우를 구분해서 문구를 다르게 씀
    detail,
    hostname: result.hostname || null,
  };
}

// 여러 장비를 한 번에(불러오기/IP 자동생성 직후, '전체 연결 확인' 버튼)
async function probeAllRows() {
  const targets = inventoryDevices.filter(d => d.management_ip);
  if (targets.length === 0) {
    showToast('확인할 장비가 없습니다 (IP가 입력된 장비 없음)', 'warn');
    return;
  }
  targets.forEach(d => {
    clearTimeout(inventoryProbeTimers[d._rid]);
    inventoryProbeSeq[d._rid] = (inventoryProbeSeq[d._rid] || 0) + 1;
    inventoryProbe[d._rid] = { status: 'checking', detail: '확인 중...', hostname: null };
  });
  renderInventoryTable();

  const results = await call('probe_devices_config', targets) || [];
  results.forEach((result, i) => { if (result) applyProbeResult(targets[i], result); });
  renderInventoryTable();
  scheduleAutosave();

  const okCount = results.filter(r => r && r.authenticated).length;
  showToast(`연결 확인 완료 — 성공 ${okCount} / 실패 ${targets.length - okCount}`,
            okCount === targets.length ? 'success' : 'warn');
}
