// ===== Command Catalog =====
async function renderCatalog() {
  const content = document.getElementById('content');
  const catalog = await call('get_catalog') || [];
  let lastSelectedIndex = null;
  const essential = catalog.filter(c => c.category === 'essential');
  const optional = catalog.filter(c => c.category === 'optional');
  const custom = catalog.filter(c => c.category === 'custom');

  const rowHtml = (c) => `
    <div draggable="true" data-row-id="${c.id}" style="display:flex;align-items:center;gap:10px;padding:6px 0;">
      <span class="material-symbols-rounded" style="cursor:grab;color:var(--sub)">drag_indicator</span>
      <input type="checkbox" ${c.enabled ? 'checked' : ''} data-cmd-id="${c.id}">
      <input class="field mono" style="width:280px" value="${c.command}" data-edit-command="${c.id}">
      <input class="field" style="font-size:12px;flex:1" value="${c.description}" data-edit-description="${c.id}">
      <button class="btn btn-outlined" style="height:28px;padding:2px 8px;" data-save-cmd="${c.id}" title="수정 저장"><span class="material-symbols-rounded" style="font-size:14px">save</span></button>
      <button class="btn btn-danger" style="height:28px;padding:2px 8px;" data-remove-cmd="${c.id}" title="삭제"><span class="material-symbols-rounded" style="font-size:14px">delete</span></button>
    </div>`;

  content.innerHTML = `
    <h1 class="page-title">커맨드 카탈로그</h1>
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
    <div class="section-gap" style="display:flex;gap:8px;">
      <button class="btn btn-primary" id="btn-save-catalog"><span class="material-symbols-rounded">save</span>저장</button>
      <label style="display:flex;align-items:center;gap:6px;font-size:12px;color:var(--sub);"><input type="checkbox" id="catalog-autosave" checked>자동저장</label>
      <button class="btn btn-outlined" id="btn-reset-catalog"><span class="material-symbols-rounded">restart_alt</span>기본값으로 초기화</button>
    </div>
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
  document.querySelectorAll('[data-save-cmd]').forEach(btn => btn.addEventListener('click', async () => {
    const id = btn.dataset.saveCmd;
    await call('update_catalog_command', id, document.querySelector(`[data-edit-command="${id}"]`).value, document.querySelector(`[data-edit-description="${id}"]`).value);
    flashSaved(true);
  }));
  document.querySelectorAll('[data-cmd-id]').forEach(inp => inp.addEventListener('click', async e => {
    const inputs = [...document.querySelectorAll('[data-cmd-id]')];
    if (e.shiftKey && lastSelectedIndex !== null) {
      const current = inputs.indexOf(inp);
      const start = Math.min(current, lastSelectedIndex), end = Math.max(current, lastSelectedIndex);
      for (let i = start; i <= end; i++) inputs[i].checked = inp.checked;
    }
    lastSelectedIndex = inputs.indexOf(inp);
    if (document.getElementById('catalog-autosave').checked) await saveCatalogState();
  }));
  document.querySelectorAll('[data-row-id]').forEach(row => {
    row.addEventListener('dragstart', e => e.dataTransfer.setData('text/plain', row.dataset.rowId));
    row.addEventListener('dragover', e => e.preventDefault());
    row.addEventListener('drop', async e => {
      e.preventDefault();
      const from = e.dataTransfer.getData('text/plain');
      const ids = [...document.querySelectorAll('[data-row-id]')].map(x => x.dataset.rowId);
      ids.splice(ids.indexOf(from), 1); ids.splice(ids.indexOf(row.dataset.rowId), 0, from);
      await call('reorder_catalog', ids); renderCatalog();
    });
  });
  document.getElementById('btn-save-catalog').addEventListener('click', async () => {
    const ok = await saveCatalogState();
    flashSaved(ok);
  });
  document.getElementById('btn-reset-catalog').addEventListener('click', async () => {
    if (!confirm('커스텀으로 추가한 커맨드를 포함해 전체 카탈로그를 기본값으로 되돌립니다. 계속할까요?')) return;
    await call('reset_catalog_defaults');
    renderCatalog();
    flashSaved(true);
  });
}

async function saveCatalogState() {
  const toggles = {};
  document.querySelectorAll('[data-cmd-id]').forEach(inp => toggles[inp.dataset.cmdId] = inp.checked);
  return call('save_catalog_toggles', toggles);
}
