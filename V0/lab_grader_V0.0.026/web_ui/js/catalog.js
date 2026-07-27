// ===== Command Catalog (통합 단일 목록) =====
const CATALOG_CATEGORY_LABELS = { essential: '필수', optional: '선택사항', custom: '커스텀' };
let catalogSelectedIds = new Set();

async function renderCatalog() {
  const content = document.getElementById('content');
  const catalog = await call('get_catalog') || [];
  let lastSelectedIndex = null;
  let lastRankClickId = null;
  // 선택 상태는 화면이 다시 그려져도 유지하되, 사라진 id는 정리.
  const allIds = new Set(catalog.map(c => c.id));
  catalogSelectedIds.forEach(id => { if (!allIds.has(id)) catalogSelectedIds.delete(id); });

  const categoryOptions = (current) => Object.entries(CATALOG_CATEGORY_LABELS)
    .map(([key, label]) => `<option value="${key}" ${key === current ? 'selected' : ''}>${label}</option>`).join('');

  const rowHtml = (c, rank) => `
    <div class="catalog-row${catalogSelectedIds.has(c.id) ? ' selected' : ''}${c.enabled ? '' : ' cmd-disabled'}" draggable="true" data-row-id="${c.id}" style="display:flex;align-items:center;gap:10px;padding:6px 0;">
      <span class="material-symbols-rounded catalog-drag-handle" data-select-handle="${c.id}" title="클릭: 선택 / Shift+클릭: 범위선택 / 드래그: 이동" style="cursor:grab;color:var(--sub)">drag_indicator</span>
      <input type="number" min="1" class="field mono catalog-rank-input" value="${rank}" data-rank-id="${c.id}" title="순서 번호 직접 입력" style="width:44px;padding:4px 4px;text-align:center;">
      <select class="field catalog-category-select" data-category-id="${c.id}" title="카테고리 변경" style="width:96px;font-size:12px;padding:4px 4px;">${categoryOptions(c.category)}</select>
      <input type="checkbox" ${c.enabled ? 'checked' : ''} data-cmd-id="${c.id}">
      <input class="field mono" style="width:280px" value="${c.command}" data-edit-command="${c.id}">
      <input class="field" style="font-size:12px;flex:1" value="${c.description}" data-edit-description="${c.id}">
      <button class="btn btn-outlined" style="height:28px;padding:2px 8px;" data-save-cmd="${c.id}" title="수정 저장"><span class="material-symbols-rounded" style="font-size:14px">save</span></button>
      <button class="btn btn-danger" style="height:28px;padding:2px 8px;" data-remove-cmd="${c.id}" title="삭제"><span class="material-symbols-rounded" style="font-size:14px">delete</span></button>
    </div>`;

  content.innerHTML = `
    <h1 class="page-title">커맨드 카탈로그</h1>
    <p class="page-sub">필수·선택사항·커스텀을 하나로 합친 통합 목록입니다. 체크된 커맨드가 Collection 시 함께 실행됩니다.
      손잡이(⠿) 클릭으로 여러 행을 선택(Shift=범위선택)한 뒤 드래그하면 한번에 이동하고, 순서 칸의 숫자로 바로 순위를 바꿀 수 있으며, 카테고리 배지로 필수/선택사항/커스텀 분류만 바꿀 수 있습니다.</p>
    <div class="card">
      <div id="catalog-rows" class="catalog-drop-zone" style="min-height:24px;">${catalog.map((c, i) => rowHtml(c, i + 1)).join('')}</div>
      <div style="display:flex;gap:8px;margin-top:10px;">
        <input class="field" id="new-cmd" placeholder="show ip route" style="width:240px;">
        <input class="field" id="new-desc" placeholder="설명" style="width:180px;">
        <button class="btn btn-outlined" id="btn-add-cmd"><span class="material-symbols-rounded">add</span>추가(커스텀)</button>
      </div>
    </div>
    <div class="section-gap" style="display:flex;gap:8px;">
      <button class="btn btn-primary" id="btn-save-catalog"><span class="material-symbols-rounded">save</span>저장</button>
      <label style="display:flex;align-items:center;gap:6px;font-size:12px;color:var(--sub);"><input type="checkbox" id="catalog-autosave" checked>자동저장</label>
      <button class="btn btn-outlined" id="btn-reset-catalog"><span class="material-symbols-rounded">restart_alt</span>기본값으로 초기화</button>
      <button class="btn btn-outlined" id="btn-export-catalog"><span class="material-symbols-rounded">file_download</span>Excel Export</button>
      <button class="btn btn-outlined" id="btn-import-catalog"><span class="material-symbols-rounded">file_upload</span>Excel Import</button>
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
      catalogSelectedIds.delete(btn.dataset.removeCmd);
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
    inputs.forEach(cb => cb.closest('.catalog-row')?.classList.toggle('cmd-disabled', !cb.checked));
    if (document.getElementById('catalog-autosave').checked) await saveCatalogState();
  }));
  document.querySelectorAll('[data-category-id]').forEach(sel => sel.addEventListener('change', async () => {
    await call('set_catalog_category', sel.dataset.categoryId, sel.value);
    flashSaved(true);
  }));

  document.querySelectorAll('[data-select-handle]').forEach(handle => {
    handle.addEventListener('click', e => {
      const id = handle.dataset.selectHandle;
      const rows = [...document.querySelectorAll('#catalog-rows [data-row-id]')].map(r => r.dataset.rowId);
      if (e.shiftKey && lastRankClickId && rows.includes(lastRankClickId)) {
        const start = rows.indexOf(lastRankClickId), end = rows.indexOf(id);
        const [lo, hi] = [Math.min(start, end), Math.max(start, end)];
        rows.slice(lo, hi + 1).forEach(rid => catalogSelectedIds.add(rid));
      } else if (e.ctrlKey || e.metaKey) {
        catalogSelectedIds.has(id) ? catalogSelectedIds.delete(id) : catalogSelectedIds.add(id);
      } else {
        const onlyThisSelected = catalogSelectedIds.size === 1 && catalogSelectedIds.has(id);
        catalogSelectedIds.clear();
        if (!onlyThisSelected) catalogSelectedIds.add(id);
      }
      lastRankClickId = id;
      document.querySelectorAll('[data-row-id]').forEach(r => r.classList.toggle('selected', catalogSelectedIds.has(r.dataset.rowId)));
    });
  });

  document.querySelectorAll('[data-rank-id]').forEach(inp => {
    const commit = async () => {
      const id = inp.dataset.rankId;
      const newRank = parseInt(inp.value, 10);
      if (!Number.isFinite(newRank) || newRank < 1) { renderCatalog(); return; }
      await performCatalogMove([id], newRank - 1);
    };
    inp.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); inp.blur(); } });
    inp.addEventListener('blur', commit);
  });

  wireDragAndDrop();

  document.getElementById('btn-save-catalog').addEventListener('click', async () => {
    const ok = await saveCatalogState();
    flashSaved(ok);
  });
  document.getElementById('btn-reset-catalog').addEventListener('click', async () => {
    if (!confirm('커스텀으로 추가한 커맨드를 포함해 전체 카탈로그를 기본값으로 되돌립니다. 계속할까요?')) return;
    catalogSelectedIds.clear();
    await call('reset_catalog_defaults');
    renderCatalog();
    flashSaved(true);
  });
  document.getElementById('btn-export-catalog').addEventListener('click', async () => {
    const result = await call('export_catalog_excel');
    if (result && result.error) alert(result.error);
    else if (result && result.path) alert(`Excel로 내보냈습니다: ${result.path}`);
  });
  document.getElementById('btn-import-catalog').addEventListener('click', async () => {
    if (!confirm('Excel 파일 내용으로 현재 카탈로그 전체를 대체합니다. 계속할까요?')) return;
    const result = await call('import_catalog_excel');
    if (result && result.error) { alert(result.error); return; }
    if (result && result.count) {
      catalogSelectedIds.clear();
      renderCatalog();
      alert(`${result.count}개 커맨드를 가져왔습니다.`);
    }
  });
}

function wireDragAndDrop() {
  const container = document.getElementById('catalog-rows');
  if (!container) return;
  let draggingIds = [];

  const clearDropTargets = () => document.querySelectorAll('.drop-target').forEach(el => el.classList.remove('drop-target'));

  document.querySelectorAll('[data-row-id]').forEach(row => {
    row.addEventListener('dragstart', () => {
      // 이 행이 이미 다중 선택에 포함돼 있으면 선택된 행 전체를 함께 옮기고,
      // 아니면(선택 안 된 행을 그냥 드래그하면) 이 행 하나만 옮긴다.
      const id = row.dataset.rowId;
      draggingIds = catalogSelectedIds.has(id) && catalogSelectedIds.size > 1
        ? [...catalogSelectedIds]
        : [id];
      requestAnimationFrame(() => {
        draggingIds.forEach(did => document.querySelector(`[data-row-id="${did}"]`)?.classList.add('dragging'));
      });
    });
    row.addEventListener('dragend', () => {
      document.querySelectorAll('.dragging').forEach(el => el.classList.remove('dragging'));
      clearDropTargets();
      draggingIds = [];
    });
    row.addEventListener('dragover', e => {
      e.preventDefault();
      e.stopPropagation();
      if (draggingIds.includes(row.dataset.rowId)) return;
      clearDropTargets();
      row.classList.add('drop-target');
    });
    row.addEventListener('drop', async e => {
      e.preventDefault();
      e.stopPropagation();
      row.classList.remove('drop-target');
      const targetId = row.dataset.rowId;
      if (!draggingIds.length || draggingIds.includes(targetId)) return;
      const siblings = [...container.querySelectorAll('[data-row-id]')].map(r => r.dataset.rowId).filter(id => !draggingIds.includes(id));
      await performCatalogMove(draggingIds, siblings.indexOf(targetId));
    });
  });

  container.addEventListener('dragover', e => {
    e.preventDefault();
    if (e.target !== container) return;
    clearDropTargets();
    container.classList.add('drop-target');
  });
  container.addEventListener('dragleave', e => {
    if (e.target === container) container.classList.remove('drop-target');
  });
  container.addEventListener('drop', async e => {
    e.preventDefault();
    container.classList.remove('drop-target');
    if (e.target !== container) return; // 행 위에 놓은 경우는 그 행의 drop 핸들러가 처리
    if (!draggingIds.length) return;
    await performCatalogMove(draggingIds, null);
  });
}

async function performCatalogMove(ids, targetIndex) {
  const oldRects = captureCatalogRects();
  await call('move_catalog_items', ids, targetIndex);
  await renderCatalog();
  playCatalogFlip(oldRects);
}

// ===== FLIP 애니메이션: 재정렬 시 각 행이 이전 위치에서 새 위치로 부드럽게 이동하는 것처럼 보이게 함 =====
function captureCatalogRects() {
  const rects = new Map();
  document.querySelectorAll('.catalog-row[data-row-id]').forEach(row => {
    rects.set(row.dataset.rowId, row.getBoundingClientRect());
  });
  return rects;
}

function playCatalogFlip(oldRects) {
  document.querySelectorAll('.catalog-row[data-row-id]').forEach(row => {
    const before = oldRects.get(row.dataset.rowId);
    if (!before) return;
    const after = row.getBoundingClientRect();
    const dx = before.left - after.left;
    const dy = before.top - after.top;
    if (!dx && !dy) return;
    row.style.transition = 'none';
    row.style.transform = `translate(${dx}px, ${dy}px)`;
    requestAnimationFrame(() => {
      row.style.transition = 'transform 260ms cubic-bezier(0.22, 0.8, 0.2, 1)';
      row.style.transform = '';
    });
  });
}

async function saveCatalogState() {
  const toggles = {};
  document.querySelectorAll('[data-cmd-id]').forEach(inp => toggles[inp.dataset.cmdId] = inp.checked);
  return call('save_catalog_toggles', toggles);
}
