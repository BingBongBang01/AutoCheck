// ===== 설정 — AI 제공자 우선순위(드래그로 재정렬, FLIP 애니메이션) =====
function renderAiOrderList() {
  const list = document.getElementById('ai-order-list');
  list.innerHTML = aiProviders.map((p, i) => `
    <div class="ai-order-item" draggable="true" data-id="${p.id}">
      <span class="material-symbols-rounded drag-handle">drag_indicator</span>
      <span class="order-rank">${i + 1}</span>
      <div class="order-icon"><span class="material-symbols-rounded">${p.icon || 'smart_toy'}</span></div>
      <div style="flex:1">
        <div class="order-label">${p.label}</div>
        <div class="order-desc">${p.desc || ''}</div>
      </div>
    </div>
  `).join('');

  let draggedId = null;

  list.querySelectorAll('.ai-order-item').forEach(item => {
    item.addEventListener('dragstart', () => {
      draggedId = item.dataset.id;
      requestAnimationFrame(() => item.classList.add('dragging'));
    });
    item.addEventListener('dragend', () => {
      item.classList.remove('dragging');
      list.querySelectorAll('.drop-target').forEach(el => el.classList.remove('drop-target'));
    });
    item.addEventListener('dragover', (e) => {
      e.preventDefault();
      if (item.dataset.id === draggedId) return;
      list.querySelectorAll('.drop-target').forEach(el => el.classList.remove('drop-target'));
      item.classList.add('drop-target');
    });
    item.addEventListener('drop', (e) => {
      e.preventDefault();
      item.classList.remove('drop-target');
      const targetId = item.dataset.id;
      if (!draggedId || draggedId === targetId) return;
      reorderAiProviders(draggedId, targetId);
    });
  });
}

// FLIP: 이동 전 위치를 기록 → DOM 재배치 → 이전 위치에서 새 위치로 transform 보간하며 애니메이션
function reorderAiProviders(draggedId, targetId) {
  const list = document.getElementById('ai-order-list');
  const items = [...list.children];
  const firstRects = new Map(items.map(el => [el.dataset.id, el.getBoundingClientRect()]));

  const fromIdx = aiProviders.findIndex(p => p.id === draggedId);
  const toIdx = aiProviders.findIndex(p => p.id === targetId);
  const [moved] = aiProviders.splice(fromIdx, 1);
  aiProviders.splice(toIdx, 0, moved);

  renderAiOrderList();

  const newList = document.getElementById('ai-order-list');
  [...newList.children].forEach(el => {
    const first = firstRects.get(el.dataset.id);
    if (!first) return;
    const last = el.getBoundingClientRect();
    const dy = first.top - last.top;
    if (dy === 0) return;
    el.style.transition = 'none';
    el.style.transform = `translateY(${dy}px)`;
    requestAnimationFrame(() => {
      el.style.transition = 'transform 240ms cubic-bezier(0.2,0,0,1)';
      el.style.transform = '';
    });
  });

  call('save_ai_settings', aiProviders.map(p => p.id)).then(flashSaved);
}
