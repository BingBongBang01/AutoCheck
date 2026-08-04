// ===== 설정 — AI 제공자 우선순위(드래그로 재정렬, FLIP 애니메이션) =====
// 이 제공자를 지금 실제로 쓸 수 있는가 — 클라우드 API는 '사용 체크 + 키 저장'이 둘 다 돼 있어야
// 호출이 성립한다. 1순위가 키 없는 제공자면 매번 폴백으로 흘러가므로, 순위 목록에서 바로
// 경고를 보여 준다(키 목록 카드까지 내려가서야 알게 되는 걸 막는 것이 목적).
function aiProviderKeyMissing(providerId) {
  if (providerId !== 'cloud_apis') return false;
  return !(cloudApis || []).some(e => e.enabled && e.has_key);
}

function renderAiOrderList() {
  const list = document.getElementById('ai-order-list');
  list.innerHTML = aiProviders.map((p, i) => {
    const main = i === 0;
    const keyMissing = aiProviderKeyMissing(p.id);
    return `
    <div class="ai-order-item ${main ? 'ai-main-provider' : ''}" draggable="true" data-id="${p.id}">
      ${main ? `<span class="ai-main-crown">
        <span class="material-symbols-rounded" style="font-size:13px">workspace_premium</span>
        MAIN PROVIDER / 1순위 주 분석기</span>` : ''}
      <span class="material-symbols-rounded drag-handle">drag_indicator</span>
      <span class="order-rank">${i + 1}</span>
      <div class="order-icon"><span class="material-symbols-rounded">${p.icon || 'smart_toy'}</span></div>
      <div style="flex:1">
        <div class="order-label">${p.label}</div>
        <div class="order-desc">${p.desc || ''}</div>
      </div>
      ${keyMissing ? `<span class="api-key-warn" title="사용 체크된 API 키가 없습니다 — 아래 '클라우드 API 키'에서 키를 등록하세요">
        <span class="material-symbols-rounded" style="font-size:13px">warning</span>API 키 미설정</span>` : ''}
    </div>`;
  }).join('');

  let draggedId = null;
  const autoScroller = createDragAutoScroller();

  list.querySelectorAll('.ai-order-item').forEach(item => {
    item.addEventListener('dragstart', () => {
      draggedId = item.dataset.id;
      requestAnimationFrame(() => item.classList.add('dragging'));
    });
    item.addEventListener('dragend', () => {
      item.classList.remove('dragging');
      list.querySelectorAll('.drop-target').forEach(el => el.classList.remove('drop-target'));
      autoScroller.stop();
    });
    item.addEventListener('dragover', (e) => {
      e.preventDefault();
      autoScroller.update(e);
      if (item.dataset.id === draggedId) return;
      list.querySelectorAll('.drop-target').forEach(el => el.classList.remove('drop-target'));
      item.classList.add('drop-target');
    });
    item.addEventListener('drop', (e) => {
      e.preventDefault();
      item.classList.remove('drop-target');
      autoScroller.stop();
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
