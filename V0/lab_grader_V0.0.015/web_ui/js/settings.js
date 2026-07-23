// ===== Settings — AI 제공자 우선순위(드래그로 재정렬, FLIP 애니메이션) =====
let aiProviders = [];

async function renderSettings() {
  const content = document.getElementById('content');
  const aiCfg = await call('get_ai_settings') || { providers: [] };
  aiProviders = aiCfg.providers || [];
  const localCfg = await call('get_local_ai_config') || {};
  const apiKeyCfg = await call('get_api_key_settings') || {};

  content.innerHTML = `
    <h1 class="page-title">설정</h1>
    <p class="page-sub">프로젝트: ${await call('get_active_project') || '-'}</p>
    <div class="card">
      <div class="card-header">
        <div class="card-icon"><span class="material-symbols-rounded">smart_toy</span></div>
        <div><p class="card-title">AI 제공자 우선순위</p><p class="card-desc">박스를 드래그해서 순서를 바꾸세요. 위에서부터 순서대로 시도하고, 실패하면 다음 제공자로 자동 폴백합니다. 규칙기반은 항상 마지막 안전망입니다.</p></div>
      </div>
      <div class="ai-order-list" id="ai-order-list"></div>
    </div>

    <div class="card section-gap">
      <div class="card-header">
        <div class="card-icon"><span class="material-symbols-rounded">memory</span></div>
        <div><p class="card-title">로컬 AI</p><p class="card-desc">로컬 NPU(Lemonade 등) 서버로 보낼 모델과 생성 파라미터를 설정합니다.</p></div>
      </div>
      <div class="grid-cols-2">
        <div><label class="field-label">엔드포인트</label><input class="field" id="local-ai-endpoint" value="${localCfg.endpoint || ''}" placeholder="http://localhost:13305"></div>
        <div><label class="field-label">모델</label>
          <select class="field" id="local-ai-model">
            ${(localCfg.model_choices || []).map(m => `<option value="${m.id}" ${m.id === localCfg.model ? 'selected' : ''}>${m.label}</option>`).join('')}
          </select>
        </div>
        <div><label class="field-label">Temperature (${localCfg.temperature ?? 0.3})</label><input class="field" type="range" id="local-ai-temperature" min="0" max="2" step="0.1" value="${localCfg.temperature ?? 0.3}"></div>
        <div><label class="field-label">Max Tokens</label><input class="field" type="number" id="local-ai-max-tokens" min="1" value="${localCfg.max_tokens ?? 800}"></div>
      </div>
      <div style="margin-top:12px;display:flex;gap:8px;align-items:center;">
        <button class="btn btn-primary" id="btn-save-local-ai"><span class="material-symbols-rounded">save</span>저장</button>
        <button class="btn btn-outlined" id="btn-test-local-ai"><span class="material-symbols-rounded">wifi_tethering</span>연결 테스트</button>
        <span id="local-ai-test-result" style="font-size:12px;color:var(--sub);"></span>
      </div>
    </div>

    <div class="card section-gap">
      <div class="card-header">
        <div class="card-icon"><span class="material-symbols-rounded">key</span></div>
        <div><p class="card-title">API 키</p><p class="card-desc">Cloud AI 제공자의 API 키를 로컬에 저장하고 유효성을 테스트합니다. 이 컴퓨터에만 저장되며 외부로 전송되지 않습니다.</p></div>
      </div>
      <div id="api-key-list" style="display:flex;flex-direction:column;gap:14px;">
        ${Object.entries(apiKeyCfg).map(([type, info]) => `
          <div>
            <label class="field-label">${info.label} (환경변수: ${info.api_key_env}${info.has_env_value || info.has_saved_key ? ' — 설정됨' : ''})</label>
            <div style="display:flex;gap:8px;">
              <input class="field" type="password" data-provider="${type}" id="api-key-${type}" placeholder="API 키 입력">
              <button class="btn btn-outlined" data-test-key="${type}"><span class="material-symbols-rounded">wifi_tethering</span>테스트</button>
              <button class="btn btn-primary" data-save-key="${type}"><span class="material-symbols-rounded">save</span>저장</button>
            </div>
            <span id="api-key-result-${type}" style="font-size:12px;color:var(--sub);"></span>
          </div>
        `).join('')}
      </div>
    </div>
  `;

  renderAiOrderList();
  wireLocalAiSection();
  wireApiKeySection();
}

function wireLocalAiSection() {
  const tempInput = document.getElementById('local-ai-temperature');
  tempInput.addEventListener('input', () => {
    const label = tempInput.closest('div').querySelector('.field-label');
    label.textContent = `Temperature (${tempInput.value})`;
  });

  document.getElementById('btn-save-local-ai').addEventListener('click', async () => {
    const ok = await call('save_local_ai_config', {
      endpoint: document.getElementById('local-ai-endpoint').value.trim(),
      model: document.getElementById('local-ai-model').value,
      temperature: parseFloat(document.getElementById('local-ai-temperature').value),
      max_tokens: parseInt(document.getElementById('local-ai-max-tokens').value) || 800,
    });
    flashSaved(ok);
  });

  document.getElementById('btn-test-local-ai').addEventListener('click', async () => {
    const resultEl = document.getElementById('local-ai-test-result');
    resultEl.textContent = '확인 중...';
    const result = await call('test_local_ai_connection', document.getElementById('local-ai-endpoint').value.trim());
    resultEl.textContent = result.detail;
    resultEl.style.color = result.ok ? 'var(--success)' : 'var(--critical)';
  });
}

function wireApiKeySection() {
  document.querySelectorAll('#api-key-list [data-save-key]').forEach(btn => {
    btn.addEventListener('click', async () => {
      const type = btn.dataset.saveKey;
      const key = document.getElementById(`api-key-${type}`).value;
      const result = await call('save_api_key', type, key);
      const resultEl = document.getElementById(`api-key-result-${type}`);
      if (result && result.error) {
        resultEl.textContent = result.error;
        resultEl.style.color = 'var(--critical)';
      } else {
        resultEl.textContent = '저장됨';
        resultEl.style.color = 'var(--success)';
        flashSaved(true);
      }
    });
  });
  document.querySelectorAll('#api-key-list [data-test-key]').forEach(btn => {
    btn.addEventListener('click', async () => {
      const type = btn.dataset.testKey;
      const key = document.getElementById(`api-key-${type}`).value;
      const resultEl = document.getElementById(`api-key-result-${type}`);
      resultEl.textContent = '확인 중...';
      resultEl.style.color = 'var(--sub)';
      const result = await call('test_api_key', type, key);
      resultEl.textContent = result.detail;
      resultEl.style.color = result.ok ? 'var(--success)' : 'var(--critical)';
    });
  });
}

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
