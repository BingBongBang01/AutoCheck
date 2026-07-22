// ===== Architecture (반영된 것 / 반영 예정) =====
async function renderArchitecture() {
  const status = await call('get_architecture_status') || { implemented: [], pending: [] };
  const content = document.getElementById('content');

  const doneRow = (item) => `
    <div style="display:flex;gap:10px;padding:8px 0;border-bottom:1px solid var(--border);">
      <span class="material-symbols-rounded" style="color:var(--success);font-size:18px;">check_circle</span>
      <div><div style="font-size:13px;">${item.name}</div><div style="font-size:11px;color:var(--sub);">${item.detail}</div></div>
    </div>`;
  const pendingRow = (item) => `
    <div style="display:flex;gap:10px;padding:8px 0;border-bottom:1px solid var(--border);">
      <span class="material-symbols-rounded" style="color:var(--text-muted, #9A998F);font-size:18px;">radio_button_unchecked</span>
      <div><div style="font-size:13px;color:var(--sub);">${item.name}</div><div style="font-size:11px;color:var(--sub);">${item.detail}</div></div>
    </div>`;

  content.innerHTML = `
    <h1 class="page-title">아키텍처</h1>
    <p class="page-sub">Network Engineering Platform 리팩토링 진행 현황</p>
    <div class="grid-cols-2">
      <div class="card">
        <div class="card-header"><div class="card-icon" style="background:var(--success)22;color:var(--success);"><span class="material-symbols-rounded">check_circle</span></div>
          <div><p class="card-title">반영됨 (${status.implemented.length})</p></div></div>
        ${status.implemented.map(doneRow).join('')}
      </div>
      <div class="card">
        <div class="card-header"><div class="card-icon" style="background:var(--hover);color:var(--sub);"><span class="material-symbols-rounded">pending</span></div>
          <div><p class="card-title">반영 예정 (${status.pending.length})</p></div></div>
        ${status.pending.map(pendingRow).join('')}
      </div>
    </div>
  `;
}
