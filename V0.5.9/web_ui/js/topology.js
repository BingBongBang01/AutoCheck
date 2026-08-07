// 네트워크 구성도 탭 — 수집된 점검 로그에서 만든 구성도를 그리고, 실시간 감시의 링크 상태를
// 그 위에 얹는다. SVG 는 백엔드(engine/topology_svg.py)가 만든다 — 화면과 내보낸 파일이 같은
// 렌더러를 써야 '화면과 파일이 다르다'가 생기지 않기 때문이다.
//
// 여기서 하는 일은 세 가지뿐이다: 삽입/갱신, 줌·팬·드래그, 클릭 상세.

let tpState = null;         // 마지막으로 받은 전체 응답
let tpVersion = null;       // 지금 DOM 에 그려진 구성도의 지문
let tpPollTimer = null;
let tpSelected = null;      // {kind: 'node'|'link', id}
let tpView = { scale: 1, x: 0, y: 0 };
let tpDragNode = null;      // 노드를 끌고 있는 중
let tpPanning = null;
let tpMoved = {};           // 이번 세션에서 옮긴 좌표(저장 전)

const TP_POLL_MS = 2000;    // 구조는 지문이 같으면 다시 그리지 않으므로 이 주기로 충분하다
const TP_SCALE_MIN = 0.3;
const TP_SCALE_MAX = 3;

async function renderTopology() {
  const content = document.getElementById('content');
  content.innerHTML = `
    <h1 class="page-title">네트워크 구성도</h1>
    <p class="page-sub">장비 목록과 수집된 점검 로그(<code>show lldp neighbors</code>,
      <code>show interfaces status</code>, <code>show mlag</code>)에서 물리 구성을 복원합니다.
      실시간 감시가 켜져 있으면 링크 상태를 즉시 반영합니다. 노드를 끌어 배치를 고칠 수 있습니다.</p>

    <div class="card tp-card" id="tp-card">
      <div class="tp-toolbar-wrap"><div class="tp-toolbar">
        <span class="tp-status" id="tp-status">불러오는 중…</span>
        <span style="flex:1"></span>
        <button class="btn btn-outlined" id="tp-zoom-out" title="축소"><span class="material-symbols-rounded">zoom_out</span></button>
        <button class="btn btn-outlined" id="tp-zoom-fit" title="화면에 맞추기"><span class="material-symbols-rounded">fit_screen</span>맞추기</button>
        <button class="btn btn-outlined" id="tp-zoom-in" title="확대"><span class="material-symbols-rounded">zoom_in</span></button>
        <button class="btn btn-outlined" id="tp-reset-layout" title="자동 배치로 되돌립니다"><span class="material-symbols-rounded">restart_alt</span>배치 초기화</button>
        <button class="btn btn-outlined" id="tp-diagnose" title="구성도가 비어 보이는 이유를 확인합니다"><span class="material-symbols-rounded">find_in_page</span>진단</button>
        <button class="btn btn-primary" id="tp-export"><span class="material-symbols-rounded">download</span>SVG 저장</button>
      </div></div>
      <div class="tp-warn-row" id="tp-warn-row" style="display:none;"></div>
      <div class="tp-body">
        <div class="tp-canvas" id="tp-canvas">
          <div class="tp-stage" id="tp-stage"></div>
          <div class="tp-empty" id="tp-empty" style="display:none;"></div>
        </div>
        <div class="tp-detail" id="tp-detail"></div>
      </div>
    </div>`;

  document.getElementById('tp-zoom-in').addEventListener('click', () => tpZoom(1.2));
  document.getElementById('tp-zoom-out').addEventListener('click', () => tpZoom(1 / 1.2));
  document.getElementById('tp-zoom-fit').addEventListener('click', tpFit);
  document.getElementById('tp-export').addEventListener('click', tpExport);
  document.getElementById('tp-diagnose').addEventListener('click', tpOpenDiagnostics);
  document.getElementById('tp-reset-layout').addEventListener('click', tpResetLayout);
  tpBindCanvas();

  tpVersion = null;
  tpSelected = null;
  tpMoved = {};
  await refreshTopology(true);
  tpStartPolling();
}

function tpStartPolling() {
  if (tpPollTimer) clearInterval(tpPollTimer);
  tpPollTimer = setInterval(async () => {
    // 다른 탭으로 이동하면 카드가 사라진다 — 그때 폴링을 멈춘다.
    if (!document.getElementById('tp-card')) {
      clearInterval(tpPollTimer);
      tpPollTimer = null;
      return;
    }
    await refreshTopology(false);
  }, TP_POLL_MS);
}

async function refreshTopology(fit) {
  const result = await call('get_network_topology', true);
  if (!result) return;
  if (!result.ok) { tpShowEmpty(result.error || '구성도를 만들 수 없습니다.'); return; }
  tpState = result;
  tpRenderStatus(result);
  tpRenderWarnings(result.warnings || []);
  // 지문이 같으면 DOM 을 건드리지 않는다 — 매 폴링마다 갈아치우면 줌·선택 강조가 튄다.
  if (result.version !== tpVersion) {
    tpVersion = result.version;
    const stage = document.getElementById('tp-stage');
    if (!stage) return;
    stage.innerHTML = result.svg;
    document.getElementById('tp-empty').style.display = 'none';
    tpBindSvg();
    tpApplySelection();
    if (fit) tpFit(); else tpApplyView();
  }
  tpRenderDetail();
}

function tpShowEmpty(message) {
  const stage = document.getElementById('tp-stage');
  const empty = document.getElementById('tp-empty');
  if (!stage || !empty) return;
  stage.innerHTML = '';
  empty.style.display = 'flex';
  empty.innerHTML = `<div>
      <span class="material-symbols-rounded">hub</span>
      <p>${tpEsc(message)}</p>
      <p class="tp-empty-hint">점검을 1회 수행하면 그 로그로 구성도가 만들어집니다.
         이미 점검했는데 비어 있으면 '진단'을 눌러 원인을 확인하세요.</p>
    </div>`;
  tpRenderStatus(null);
}

function tpRenderStatus(result) {
  const el = document.getElementById('tp-status');
  if (!el) return;
  if (!result) { el.textContent = '구성 정보 없음'; el.className = 'tp-status'; return; }
  const live = result.live || {};
  const edges = result.edges || [];
  const down = edges.filter(e => e.state === 'down').length;
  const degraded = edges.filter(e => e.state === 'degraded').length;
  const unknown = edges.filter(e => e.state === 'unknown').length;
  const bits = [`장비 ${(result.nodes || []).length}대`, `링크 ${edges.length}개`];
  if (result.run_id) bits.push(`회차 ${result.run_id}`);
  bits.push(live.running ? '실시간 반영 중' : '점검 시점 기준(실시간 감시 꺼짐)');
  if (down) bits.push(`DOWN ${down}`);
  if (degraded) bits.push(`이중화 저하 ${degraded}`);
  if (unknown) bits.push(`판정 불가 ${unknown}`);
  el.textContent = bits.join(' · ');
  el.className = 'tp-status' + (down ? ' tp-status-bad'
    : (degraded || unknown) ? ' tp-status-warn' : ' tp-status-ok');
}

function tpRenderWarnings(warnings) {
  const row = document.getElementById('tp-warn-row');
  if (!row) return;
  if (!warnings.length) { row.style.display = 'none'; row.innerHTML = ''; return; }
  row.style.display = 'flex';
  row.innerHTML = warnings.map(w => `
    <span class="tp-warn tp-warn-${tpEsc(w.kind || '')}">
      <span class="material-symbols-rounded">${w.kind === 'unregistered' ? 'device_unknown' : 'warning'}</span>
      ${tpEsc(w.message || '')}
    </span>`).join('');
}

// ===== 줌 / 팬 / 노드 드래그 =====
function tpBindCanvas() {
  const canvas = document.getElementById('tp-canvas');
  if (!canvas) return;

  canvas.addEventListener('wheel', (e) => {
    e.preventDefault();
    tpZoom(e.deltaY < 0 ? 1.12 : 1 / 1.12);
  }, { passive: false });

  canvas.addEventListener('mousedown', (e) => {
    if (e.button !== 0) return;
    const node = e.target.closest('[data-tp-node]');
    if (node) {
      // 노드 드래그 — 배치를 직접 고친다. 계층 자동 판정이 틀렸을 때의 탈출구이자,
      // 도면을 손에 맞게 정리하는 수단이다.
      tpDragNode = { id: node.dataset.tpNode, startX: e.clientX, startY: e.clientY, moved: false };
      canvas.classList.add('tp-dragging');
      return;
    }
    tpPanning = { x: e.clientX, y: e.clientY, ox: tpView.x, oy: tpView.y };
    canvas.classList.add('tp-panning');
  });

  window.addEventListener('mousemove', (e) => {
    if (tpDragNode) {
      const dx = (e.clientX - tpDragNode.startX) / tpView.scale;
      const dy = (e.clientY - tpDragNode.startY) / tpView.scale;
      if (!tpDragNode.moved && Math.abs(dx) + Math.abs(dy) < 3) return;
      tpDragNode.moved = true;
      tpDragNode.dx = dx;
      tpDragNode.dy = dy;
      tpPreviewNodeMove(tpDragNode.id, dx, dy);
      return;
    }
    if (tpPanning) {
      tpView.x = tpPanning.ox + (e.clientX - tpPanning.x);
      tpView.y = tpPanning.oy + (e.clientY - tpPanning.y);
      tpApplyView();
    }
  });

  window.addEventListener('mouseup', async () => {
    const canvasEl = document.getElementById('tp-canvas');
    if (canvasEl) canvasEl.classList.remove('tp-dragging', 'tp-panning');
    if (tpDragNode) {
      const drag = tpDragNode;
      tpDragNode = null;
      if (drag.moved) await tpCommitNodeMove(drag);
      else tpSelect('node', drag.id);
    }
    tpPanning = null;
  });
}

function tpZoom(factor) {
  tpView.scale = Math.min(TP_SCALE_MAX, Math.max(TP_SCALE_MIN, tpView.scale * factor));
  tpApplyView();
}

function tpFit() {
  const canvas = document.getElementById('tp-canvas');
  if (!canvas || !tpState) return;
  const pad = 24;
  const sx = (canvas.clientWidth - pad * 2) / tpState.width;
  const sy = (canvas.clientHeight - pad * 2) / tpState.height;
  tpView.scale = Math.min(TP_SCALE_MAX, Math.max(TP_SCALE_MIN, Math.min(sx, sy)));
  tpView.x = (canvas.clientWidth - tpState.width * tpView.scale) / 2;
  tpView.y = pad;
  tpApplyView();
}

function tpApplyView() {
  const stage = document.getElementById('tp-stage');
  if (stage) {
    stage.style.transform = `translate(${tpView.x}px, ${tpView.y}px) scale(${tpView.scale})`;
  }
}

// 드래그 중에는 서버를 부르지 않고 SVG 그룹만 옮겨 보여준다 — 매 픽셀마다 왕복하면 밀린다.
function tpPreviewNodeMove(id, dx, dy) {
  const group = document.querySelector(`[data-tp-node="${CSS.escape(id)}"]`);
  if (group) group.setAttribute('transform', `translate(${dx},${dy})`);
}

async function tpCommitNodeMove(drag) {
  const node = (tpState.nodes || []).find(n => n.id === drag.id);
  if (!node) return;
  tpMoved[drag.id] = [Math.round(node.x + (drag.dx || 0)), Math.round(node.y + (drag.dy || 0))];
  // 이미 옮겨 둔 다른 노드의 좌표도 함께 보낸다 — 서버는 받은 dict 로 통째로 갈아끼운다.
  const positions = Object.assign({}, tpLayoutPositions(), tpMoved);
  const result = await call('save_topology_layout', positions);
  if (result && result.error) { showToast(result.error, 'error'); return; }
  tpVersion = null;               // 좌표가 바뀌었으니 다음 폴링에서 다시 그린다
  await refreshTopology(false);
}

function tpLayoutPositions() {
  const out = {};
  (tpState && tpState.nodes || []).forEach(n => { if (n.manual) out[n.id] = [n.x, n.y]; });
  return out;
}

async function tpResetLayout() {
  if (!confirm('직접 옮긴 배치를 모두 버리고 자동 배치로 되돌립니다.')) return;
  const result = await call('reset_topology_layout');
  if (result && result.error) { showToast(result.error, 'error'); return; }
  tpMoved = {};
  tpVersion = null;
  await refreshTopology(true);
  showToast('자동 배치로 되돌렸습니다.');
}

// ===== 선택 / 상세 =====
function tpBindSvg() {
  document.querySelectorAll('[data-tp-link]').forEach(el => {
    el.addEventListener('click', (e) => { e.stopPropagation(); tpSelect('link', el.dataset.tpLink); });
  });
  const canvas = document.getElementById('tp-canvas');
  if (canvas) {
    canvas.addEventListener('click', (e) => {
      if (!e.target.closest('[data-tp-node]') && !e.target.closest('[data-tp-link]')) tpSelect(null);
    });
  }
}

function tpSelect(kind, id) {
  tpSelected = kind ? { kind, id } : null;
  tpApplySelection();
  tpRenderDetail();
}

function tpApplySelection() {
  document.querySelectorAll('.tp-selected').forEach(el => el.classList.remove('tp-selected'));
  if (!tpSelected) return;
  const attr = tpSelected.kind === 'node' ? 'data-tp-node' : 'data-tp-link';
  const el = document.querySelector(`[${attr}="${CSS.escape(tpSelected.id)}"]`);
  if (el) el.classList.add('tp-selected');
}

const TP_KIND_LABEL = { l2switch: 'L2 스위치', l3switch: 'L3 스위치', router: '라우터',
                        firewall: '방화벽', unknown: '종류 불명' };
const TP_STATE_LABEL = { up: '정상', down: 'DOWN', degraded: '이중화 저하', unknown: '판정 불가' };

function tpRenderDetail() {
  const panel = document.getElementById('tp-detail');
  if (!panel || !tpState) return;
  if (!tpSelected) {
    panel.innerHTML = `<div class="tp-detail-empty">
      <p>장비나 링크를 클릭하면 상세가 보입니다.</p>
      <p class="tp-detail-hint">노드를 끌어 배치를 고칠 수 있고, 휠로 확대·축소, 빈 곳을 끌면 이동합니다.</p>
      ${tpSummaryHtml()}</div>`;
    return;
  }
  panel.innerHTML = tpSelected.kind === 'node' ? tpNodeDetailHtml() : tpLinkDetailHtml();
}

function tpSummaryHtml() {
  const pairs = tpState.pairs || [];
  if (!pairs.length) return '';
  return `<div class="tp-detail-block"><h4>이중화 쌍</h4>${pairs.map(p => `
    <div class="tp-kv"><span>${tpEsc(p.a)} ↔ ${tpEsc(p.b)}</span>
      <em class="${p.healthy ? 'tp-ok' : 'tp-bad'}">${p.healthy ? '정상' : '이상'}</em></div>
    <div class="tp-kv-sub">${tpEsc(p.peer_link || '')}${p.domain ? ' · ' + tpEsc(p.domain) : ''}</div>
  `).join('')}</div>`;
}

function tpNodeDetailHtml() {
  const node = (tpState.nodes || []).find(n => n.id === tpSelected.id);
  if (!node) return '';
  const links = (tpState.edges || []).filter(e => e.a === node.id || e.b === node.id);
  const alerts = ((tpState.live || {}).unresolved_alerts || {})[node.id] || 0;
  return `
    <div class="tp-detail-head">
      <h3>${tpEsc(node.name)}</h3>
      <span class="tp-chip">${tpEsc(TP_KIND_LABEL[node.kind] || node.kind || '')}</span>
      ${node.registered ? '' : '<span class="tp-chip tp-chip-warn">장비 목록에 없음</span>'}
      ${node.has_log ? '' : '<span class="tp-chip tp-chip-warn">점검 로그 없음</span>'}
      ${alerts ? `<span class="tp-chip tp-chip-bad">미해결 경고 ${alerts}건</span>` : ''}
    </div>
    <div class="tp-detail-block">
      ${node.ip ? `<div class="tp-kv"><span>관리 IP</span><em>${tpEsc(node.ip)}</em></div>` : ''}
      ${node.vendor ? `<div class="tp-kv"><span>제조사</span><em>${tpEsc(node.vendor)}</em></div>` : ''}
      ${node.model ? `<div class="tp-kv"><span>모델</span><em>${tpEsc(node.model)}</em></div>` : ''}
      ${node.role ? `<div class="tp-kv"><span>역할</span><em>${tpEsc(node.role)}</em></div>` : ''}
      <div class="tp-kv"><span>배치</span><em>${node.manual ? '직접 지정' : '자동'}</em></div>
    </div>
    <div class="tp-detail-block">
      <h4>연결 ${links.length}개</h4>
      ${links.map(e => {
        const mine = e.a === node.id;
        const port = mine ? e.a_port : e.b_port;
        const peer = mine ? e.b : e.a;
        const peerPort = mine ? e.b_port : e.a_port;
        return `<div class="tp-link-row tp-state-${tpEsc(e.state)}" data-tp-jump="${tpEsc(e.id)}">
          <span>${tpEsc(tpShortPort(port))} → ${tpEsc(peer)} / ${tpEsc(tpShortPort(peerPort))}</span>
          <em>${e.bundle ? tpEsc(tpShortPort(e.bundle)) + ' ×' + e.count + ' · ' : ''}${TP_STATE_LABEL[e.state] || e.state}</em>
        </div>`;
      }).join('')}
    </div>`;
}

function tpLinkDetailHtml() {
  const edge = (tpState.edges || []).find(e => e.id === tpSelected.id);
  if (!edge) return '';
  return `
    <div class="tp-detail-head">
      <h3>${tpEsc(edge.a)} ↔ ${tpEsc(edge.b)}</h3>
      <span class="tp-chip tp-state-${tpEsc(edge.state)}">${TP_STATE_LABEL[edge.state] || edge.state}</span>
      ${edge.one_sided ? '<span class="tp-chip tp-chip-warn">한쪽만 관측</span>' : ''}
    </div>
    <div class="tp-detail-block">
      ${edge.bundle ? `<div class="tp-kv"><span>묶음</span><em>${tpEsc(edge.bundle)} (${edge.count}개)</em></div>` : ''}
      ${edge.label ? `<div class="tp-kv"><span>설명</span><em>${tpEsc(edge.label)}</em></div>` : ''}
    </div>
    <div class="tp-detail-block">
      <h4>물리 링크 ${(edge.members || []).length}개</h4>
      ${(edge.members || []).map(m => `
        <div class="tp-link-row tp-state-${tpEsc(m.state)}">
          <span>${tpEsc(edge.a)}/${tpEsc(tpShortPort(m.a_port))} ↔ ${tpEsc(edge.b)}/${tpEsc(tpShortPort(m.b_port))}</span>
          <em>${TP_STATE_LABEL[m.state] || m.state}</em>
        </div>`).join('')}
    </div>
    ${edge.one_sided ? `<p class="tp-detail-hint">이 링크는 한쪽 장비의 LLDP 에만 보입니다 —
      반대편에서 LLDP 가 꺼져 있거나 실제로 끊겼을 수 있습니다.</p>` : ''}`;
}

function tpShortPort(port) {
  if (!port) return '';
  const map = [['Port-Channel', 'Po'], ['TenGigabitEthernet', 'Te'], ['GigabitEthernet', 'Gi'],
               ['FortyGigE', 'Fo'], ['Ethernet', 'Et'], ['Management', 'Ma'], ['Vlan', 'Vl'],
               ['Loopback', 'Lo']];
  for (const [long, short] of map) if (port.startsWith(long)) return short + port.slice(long.length);
  return port;
}

// ===== 내보내기 / 진단 =====
async function tpExport() {
  const result = await call('save_topology_svg');
  if (!result) return;
  if (!result.ok) { showToast(result.error || 'SVG 저장에 실패했습니다.', 'error'); return; }
  showToast(`${result.name} 으로 저장했습니다.`);
}

async function tpOpenDiagnostics() {
  const result = await call('get_topology_diagnostics');
  if (!result) return;
  if (!result.ok) { showToast(result.error || '진단할 수 없습니다.', 'error'); return; }
  const rows = (result.rows || []).map(r => `
    <tr class="${r.neighbors ? '' : 'tp-diag-bad'}">
      <td>${tpEsc(r.device)}</td>
      <td>${r.registered ? '등록' : '<b>미등록</b>'}</td>
      <td>${r.has_log ? r.commands + '개 커맨드' : '<b>없음</b>'}</td>
      <td>${r.has_lldp_output ? '있음' : '<b>없음</b>'}</td>
      <td>${r.neighbors}</td>
    </tr>`).join('');
  tpShowModal('구성도 진단', `
    <div class="tp-diag">
      <p class="tp-detail-hint">회차 ${tpEsc(result.run_id || '-')} · 링크 ${result.edge_count}개<br>
        <code>${tpEsc(result.raw_dir || '')}</code></p>
      <table class="tp-diag-table">
        <thead><tr><th>장비</th><th>장비 목록</th><th>점검 로그</th><th>LLDP 출력</th><th>이웃 수</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
      ${(result.warnings || []).map(w => `<p class="tp-diag-warn">${tpEsc(w.message)}</p>`).join('')}
      <p class="tp-detail-hint">이웃 수가 0이면 그 장비에서 <code>show lldp neighbors</code> 출력이
        비어 있다는 뜻입니다 — 장비에서 LLDP 가 켜져 있는지 확인하세요.</p>
    </div>`);
}

// 모달은 앱 공용 패턴(.modal-overlay + .card)을 그대로 쓴다 — 실시간 감시의 '파일 진단'과 같은 모양.
function tpShowModal(title, bodyHtml) {
  document.getElementById('tp-modal')?.remove();
  const overlay = document.createElement('div');
  overlay.id = 'tp-modal';
  overlay.className = 'modal-overlay';
  overlay.innerHTML = `
    <div class="card tp-modal-card">
      <div class="rt-alert-modal-head">
        <h3>${tpEsc(title)}</h3>
        <button class="btn btn-outlined" id="tp-modal-close">
          <span class="material-symbols-rounded">close</span>닫기</button>
      </div>
      <div class="tp-modal-body">${bodyHtml}</div>
    </div>`;
  document.body.appendChild(overlay);
  const close = () => overlay.remove();
  overlay.querySelector('#tp-modal-close').addEventListener('click', close);
  overlay.addEventListener('click', (e) => { if (e.target === overlay) close(); });
}

// 이 탭 안에서만 쓰는 이스케이프 — 다른 기능의 헬퍼(rtEscape)를 빌려 쓰면 스크립트 로드
// 순서에 묶이고, 그 파일을 지우거나 옮길 때 조용히 깨진다.
function tpEsc(text) {
  const div = document.createElement('div');
  div.textContent = text == null ? '' : String(text);
  return div.innerHTML;
}
