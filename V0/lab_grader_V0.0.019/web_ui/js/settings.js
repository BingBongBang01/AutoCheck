// ===== Settings — AI 제공자 우선순위(드래그로 재정렬, FLIP 애니메이션) =====
let aiProviders = [];

let cloudApis = [];
let cloudProviderTypes = [];

async function renderSettings() {
  const content = document.getElementById('content');
  const aiCfg = await call('get_ai_settings') || { providers: [] };
  aiProviders = aiCfg.providers || [];
  const localCfg = await call('get_local_ai_config') || {};
  const batchCfg = await call('get_batching_settings') || { batch_chars: 1500, batch_segs: 10, max_tokens: 1000 };
  const cloudCfg = await call('get_cloud_apis') || { provider_types: [], entries: [] };
  cloudApis = cloudCfg.entries || [];
  cloudProviderTypes = cloudCfg.provider_types || [];
  const termUiCfg = await call('get_terminal_ui_settings') || { context_menu_mode: 'menu' };

  content.innerHTML = `
    <h1 class="page-title">설정</h1>
    <p class="page-sub">프로젝트: ${await call('get_active_project') || '-'}</p>
    <div class="card">
      <div class="card-header">
        <div class="card-icon"><span class="material-symbols-rounded">terminal</span></div>
        <div><p class="card-title">터미널 우클릭 동작</p><p class="card-desc">세션 터미널에서 마우스 오른쪽 버튼을 눌렀을 때의 동작을 선택합니다.</p></div>
      </div>
      <div style="display:flex;gap:16px;">
        <label style="display:flex;align-items:center;gap:6px;font-size:13px;">
          <input type="radio" name="term-ctx-mode" value="menu" ${termUiCfg.context_menu_mode === 'menu' ? 'checked' : ''}>
          컨텍스트 메뉴 (복사/잘라내기/붙여넣기/전체선택)
        </label>
        <label style="display:flex;align-items:center;gap:6px;font-size:13px;">
          <input type="radio" name="term-ctx-mode" value="paste" ${termUiCfg.context_menu_mode === 'paste' ? 'checked' : ''}>
          바로 붙여넣기 (메뉴 없이 클립보드 즉시 전송)
        </label>
      </div>
    </div>

    <div class="card section-gap">
      <div class="card-header">
        <div class="card-icon"><span class="material-symbols-rounded">smart_toy</span></div>
        <div><p class="card-title">AI 제공자 우선순위</p><p class="card-desc">박스를 드래그해서 순서를 바꾸세요. 위에서부터 순서대로 시도하고, 실패하면 다음 제공자로 자동 폴백합니다. 규칙기반은 항상 마지막 안전망입니다.</p></div>
      </div>
      <div class="ai-order-list" id="ai-order-list"></div>
    </div>

    <div class="card section-gap">
      <div class="card-header">
        <div class="card-icon"><span class="material-symbols-rounded">memory</span></div>
        <div><p class="card-title">로컬 AI</p><p class="card-desc">로컬 NPU(lemonade-server 등) 서버로 보낼 모델과 생성 파라미터를 설정합니다. '모델 새로고침'을 누르면 해당 서버에 실제로 설치된 모델 목록을 가져옵니다.</p></div>
      </div>
      <div class="grid-cols-2">
        <div><label class="field-label">엔드포인트</label><input class="field" id="local-ai-endpoint" value="${localCfg.endpoint || ''}" placeholder="http://localhost:13305"></div>
        <div><label class="field-label">모델</label>
          <div style="display:flex;gap:8px;">
            <select class="field" id="local-ai-model" style="flex:1" data-saved-model="${localCfg.model || ''}">
              ${localModelOptionsHtml(localCfg.model_choices || [], localCfg.model)}
            </select>
            <button class="btn btn-outlined" id="btn-refresh-local-models" title="lemonade-server에서 설치된 모델 목록 새로고침"><span class="material-symbols-rounded">refresh</span></button>
          </div>
        </div>
        <div><label class="field-label">Temperature (${localCfg.temperature ?? 0.3})</label><input class="field" type="range" id="local-ai-temperature" min="0" max="2" step="0.1" value="${localCfg.temperature ?? 0.3}"></div>
      </div>
      <div style="margin-top:12px;display:flex;gap:8px;align-items:center;">
        <button class="btn btn-primary" id="btn-save-local-ai"><span class="material-symbols-rounded">save</span>저장</button>
        <button class="btn btn-outlined" id="btn-test-local-ai"><span class="material-symbols-rounded">wifi_tethering</span>연결 테스트</button>
        <span id="local-ai-test-result" style="font-size:12px;color:var(--sub);"></span>
      </div>
    </div>

    <div class="card section-gap">
      <div class="card-header">
        <div class="card-icon"><span class="material-symbols-rounded">splitscreen</span></div>
        <div><p class="card-title">컨텍스트 오버플로우 방지 (로컬 NPU 전용)</p><p class="card-desc">로컬 NPU(Lemonade)로 보낼 분량을 제한합니다. 점검 결과가 많을 때 배치 문자 수/세그먼트 수 한도로 여러 번에 나눠 보내고, max_tokens로 응답 길이를 제한합니다. 클라우드 API는 아래 '클라우드 API 키' 항목별로 별도 설정합니다.</p></div>
      </div>
      <div class="grid-cols-2">
        <div><label class="field-label">배치 문자 수 (batch_chars)</label><input class="field" type="number" id="batch-chars" min="1" value="${batchCfg.batch_chars}"></div>
        <div><label class="field-label">세그먼트 수 (batch_segs)</label><input class="field" type="number" id="batch-segs" min="1" value="${batchCfg.batch_segs}"></div>
        <div><label class="field-label">max_tokens</label><input class="field" type="number" id="batch-max-tokens" min="1" value="${batchCfg.max_tokens}"></div>
      </div>
      <div style="margin-top:12px;display:flex;gap:8px;align-items:center;">
        <button class="btn btn-primary" id="btn-save-batching"><span class="material-symbols-rounded">save</span>저장</button>
        <span id="batching-save-result" style="font-size:12px;color:var(--sub);"></span>
      </div>
    </div>

    <div class="card section-gap">
      <div class="card-header">
        <div class="card-icon"><span class="material-symbols-rounded">key</span></div>
        <div><p class="card-title">클라우드 API 키</p><p class="card-desc">Cloud AI API 키를 여러 개 등록하고, 체크박스로 사용할 키만 켤 수 있습니다. 각 키에 이름을 붙여 구분하세요. 체크된 키는 위에서부터 순서대로 시도되고 실패 시 다음 키로 자동 전환됩니다. 키는 이 컴퓨터에만 저장되며 외부로 전송되지 않습니다.</p></div>
      </div>
      <div id="cloud-api-list" style="display:flex;flex-direction:column;gap:10px;"></div>
      <div style="margin-top:12px;display:flex;gap:8px;align-items:center;">
        <select class="field" id="new-cloud-api-provider" style="max-width:200px;">
          ${(cloudCfg.provider_types || []).map(p => `<option value="${p.id}">${p.label}</option>`).join('')}
        </select>
        <input class="field" id="new-cloud-api-name" placeholder="키 이름 (예: 회사용 Claude)" style="max-width:220px;">
        <input class="field" type="password" id="new-cloud-api-key" placeholder="API 키 (선택, 바로 등록)" style="flex:1;min-width:160px;">
        <button class="btn btn-primary" id="btn-add-cloud-api"><span class="material-symbols-rounded">add</span>API 추가</button>
      </div>
    </div>
  `;

  renderAiOrderList();
  wireLocalAiSection();
  wireBatchingSection();
  renderCloudApiList();
  wireAddCloudApiButton();
  autoDetectLocalModels(localCfg.model);

  document.querySelectorAll('input[name="term-ctx-mode"]').forEach(radio => {
    radio.addEventListener('change', async () => {
      const result = await call('save_terminal_ui_settings', radio.value);
      flashSaved(!(result && result.error));
    });
  });
}

// 선택된 모델이 목록에 없더라도(아직 새로고침 전이거나 서버에 없는 경우) 값 자체는 잃지 않도록
// 항상 옵션 목록에 포함시켜서 렌더링 — 이게 없으면 저장된 모델이 하드코딩된 기본 목록에 없을 때
// select가 조용히 첫 번째 옵션으로 되돌아가 "탭 이동하면 모델이 바뀐 것처럼" 보이는 원인이 된다.
function localModelOptionsHtml(models, savedModel) {
  const list = (models || []).slice();
  if (savedModel && !list.some(m => m.id === savedModel)) {
    list.unshift({ id: savedModel, label: savedModel });
  }
  return list.map(m => `<option value="${m.id}" ${m.id === savedModel ? 'selected' : ''}>${m.label}</option>`).join('');
}

// 화면 진입 시 새로고침 버튼을 누르지 않아도 lemonade-server에 실제 설치된 모델 목록을 자동으로 조회해 반영.
async function autoDetectLocalModels(savedModel) {
  const endpoint = document.getElementById('local-ai-endpoint').value.trim();
  const select = document.getElementById('local-ai-model');
  if (!endpoint || !select) return;
  const target = savedModel || select.dataset.savedModel || select.value;
  const result = await call('list_lemonade_models', endpoint);
  if (!result || !result.ok || !result.models.length) return;
  select.innerHTML = localModelOptionsHtml(result.models, target);
  select.dataset.savedModel = target;
}

async function saveLocalAiConfig() {
  const select = document.getElementById('local-ai-model');
  const ok = await call('save_local_ai_config', {
    endpoint: document.getElementById('local-ai-endpoint').value.trim(),
    model: select.value,
    temperature: parseFloat(document.getElementById('local-ai-temperature').value),
  });
  select.dataset.savedModel = select.value;
  flashSaved(ok);
  return ok;
}

function wireLocalAiSection() {
  const tempInput = document.getElementById('local-ai-temperature');
  tempInput.addEventListener('input', () => {
    const label = tempInput.closest('div').querySelector('.field-label');
    label.textContent = `Temperature (${tempInput.value})`;
  });
  // 모든 변경사항 자동저장: 값이 바뀌는 즉시(또는 포커스를 벗어날 때) 서버에 저장해서
  // 저장 버튼을 누르지 않고 다른 탭으로 이동해도 값이 유지되도록 함.
  document.getElementById('local-ai-endpoint').addEventListener('blur', saveLocalAiConfig);
  document.getElementById('local-ai-model').addEventListener('change', saveLocalAiConfig);
  tempInput.addEventListener('change', saveLocalAiConfig);

  document.getElementById('btn-save-local-ai').addEventListener('click', saveLocalAiConfig);

  document.getElementById('btn-test-local-ai').addEventListener('click', async () => {
    const resultEl = document.getElementById('local-ai-test-result');
    resultEl.textContent = '확인 중...';
    const result = await call('test_local_ai_connection', document.getElementById('local-ai-endpoint').value.trim());
    resultEl.textContent = result.detail;
    resultEl.style.color = result.ok ? 'var(--success)' : 'var(--critical)';
  });

  document.getElementById('btn-refresh-local-models').addEventListener('click', async () => {
    const endpoint = document.getElementById('local-ai-endpoint').value.trim();
    const select = document.getElementById('local-ai-model');
    const prevValue = select.value;
    const result = await call('list_lemonade_models', endpoint);
    if (!result || !result.ok || !result.models.length) {
      flashSaved(false);
      return;
    }
    select.innerHTML = localModelOptionsHtml(result.models, prevValue);
    select.dataset.savedModel = prevValue;
    flashSaved(true);
  });
}

async function saveBatchingConfig() {
  const ok = await call('save_batching_settings', {
    batch_chars: parseInt(document.getElementById('batch-chars').value) || 1500,
    batch_segs: parseInt(document.getElementById('batch-segs').value) || 10,
    max_tokens: parseInt(document.getElementById('batch-max-tokens').value) || 1000,
  });
  const resultEl = document.getElementById('batching-save-result');
  resultEl.textContent = ok ? '저장됨' : '저장 실패';
  resultEl.style.color = ok ? 'var(--success)' : 'var(--critical)';
  flashSaved(ok);
  return ok;
}

function wireBatchingSection() {
  ['batch-chars', 'batch-segs', 'batch-max-tokens'].forEach(id => {
    document.getElementById(id).addEventListener('blur', saveBatchingConfig);
  });
  document.getElementById('btn-save-batching').addEventListener('click', saveBatchingConfig);
}

function renderCloudApiList() {
  const list = document.getElementById('cloud-api-list');
  if (!cloudApis.length) {
    list.innerHTML = `<p style="font-size:13px;color:var(--sub);">등록된 API 키가 없습니다. 아래에서 이름과 제공자를 선택하고 'API 추가'를 눌러 새 키를 등록하세요.</p>`;
    return;
  }
  list.innerHTML = cloudApis.map(e => `
    <div class="cloud-api-row" data-id="${e.id}" style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;">
      <input type="checkbox" data-role="enabled" ${e.enabled ? 'checked' : ''} title="사용 여부">
      <input class="field" data-role="name" value="${e.name}" placeholder="키 이름" style="max-width:200px;">
      <span style="font-size:12px;color:var(--sub);min-width:110px;">${(providerLabel(e.provider))}</span>
      <select class="field" data-role="model" style="max-width:220px;">${modelOptionsHtml(e.provider, e.model)}</select>
      <input class="field" type="password" data-role="key" placeholder="${e.has_key ? 'API 키 (변경 시에만 입력)' : 'API 키 입력'}" style="flex:1;min-width:160px;">
      <button class="btn btn-outlined" data-action="test" title="연결 테스트"><span class="material-symbols-rounded">wifi_tethering</span></button>
      <button class="btn btn-primary" data-action="save" title="저장"><span class="material-symbols-rounded">save</span></button>
      <button class="btn btn-outlined" data-action="remove" title="삭제"><span class="material-symbols-rounded">delete</span></button>
      <span data-role="result" style="font-size:12px;color:var(--sub);width:100%;"></span>
      <div style="display:flex;gap:8px;align-items:center;width:100%;padding-left:24px;">
        <label style="font-size:12px;color:var(--sub);">배치 문자 수</label>
        <input class="field" type="number" min="1000" data-role="batch_chars" value="${e.batch_chars}" style="max-width:110px;" title="한 번에 보낼 최대 문자 수(클수록 호출 횟수↓, Rate Limit 방어에 유리)">
        <label style="font-size:12px;color:var(--sub);">max_tokens</label>
        <input class="field" type="number" min="100" data-role="max_tokens" value="${e.max_tokens}" style="max-width:100px;">
      </div>
    </div>
  `).join('');
  wireCloudApiRows();
}

function providerLabel(id) {
  const found = (document.getElementById('new-cloud-api-provider')?.querySelectorAll('option') || []);
  for (const opt of found) if (opt.value === id) return opt.textContent;
  return id;
}

function modelOptionsHtml(providerId, savedModel) {
  const type = cloudProviderTypes.find(p => p.id === providerId);
  const models = (type && type.models) || [];
  if (!models.length) return `<option value="">-</option>`;
  return models.map(m => `<option value="${m}" ${m === savedModel ? 'selected' : ''}>${m}</option>`).join('');
}

function wireCloudApiRows() {
  document.querySelectorAll('#cloud-api-list .cloud-api-row').forEach(row => {
    const id = row.dataset.id;
    const resultEl = row.querySelector('[data-role="result"]');

    row.querySelector('[data-role="enabled"]').addEventListener('change', async (e) => {
      const r = await call('update_cloud_api', id, { enabled: e.target.checked });
      flashSaved(!(r && r.error));
    });

    // 이름도 다른 설정과 동일하게 자동저장(포커스 아웃 시). 키는 명시적 저장 버튼 유지(민감정보라 실수 방지).
    row.querySelector('[data-role="name"]').addEventListener('blur', async (e) => {
      const r = await call('update_cloud_api', id, { name: e.target.value });
      flashSaved(!(r && r.error));
    });

    row.querySelector('[data-role="model"]').addEventListener('change', async (e) => {
      const r = await call('update_cloud_api', id, { model: e.target.value });
      flashSaved(!(r && r.error));
    });

    row.querySelector('[data-role="batch_chars"]').addEventListener('blur', async (e) => {
      const r = await call('update_cloud_api', id, { batch_chars: e.target.value });
      flashSaved(!(r && r.error));
    });

    row.querySelector('[data-role="max_tokens"]').addEventListener('blur', async (e) => {
      const r = await call('update_cloud_api', id, { max_tokens: e.target.value });
      flashSaved(!(r && r.error));
    });

    row.querySelector('[data-action="save"]').addEventListener('click', async () => {
      const fields = {
        name: row.querySelector('[data-role="name"]').value,
        api_key: row.querySelector('[data-role="key"]').value,
        model: row.querySelector('[data-role="model"]').value,
        batch_chars: row.querySelector('[data-role="batch_chars"]').value,
        max_tokens: row.querySelector('[data-role="max_tokens"]').value,
      };
      const r = await call('update_cloud_api', id, fields);
      resultEl.textContent = (r && r.error) ? r.error : '저장됨';
      resultEl.style.color = (r && r.error) ? 'var(--critical)' : 'var(--success)';
      flashSaved(!(r && r.error));
      if (!(r && r.error)) row.querySelector('[data-role="key"]').value = '';
    });

    row.querySelector('[data-action="test"]').addEventListener('click', async () => {
      const key = row.querySelector('[data-role="key"]').value;
      resultEl.textContent = '확인 중...';
      resultEl.style.color = 'var(--sub)';
      const r = await call('test_cloud_api', id, key);
      resultEl.textContent = r.detail;
      resultEl.style.color = r.ok ? 'var(--success)' : 'var(--critical)';
    });

    row.querySelector('[data-action="remove"]').addEventListener('click', async () => {
      await call('remove_cloud_api', id);
      const cfg = await call('get_cloud_apis') || { entries: [] };
      cloudApis = cfg.entries || [];
      renderCloudApiList();
      flashSaved(true);
    });
  });
}

function wireAddCloudApiButton() {
  document.getElementById('btn-add-cloud-api').addEventListener('click', async () => {
    const provider = document.getElementById('new-cloud-api-provider').value;
    const name = document.getElementById('new-cloud-api-name').value;
    const apiKey = document.getElementById('new-cloud-api-key').value;
    const r = await call('add_cloud_api', name, provider, apiKey);
    if (r && r.error) { flashSaved(false); return; }
    const cfg = await call('get_cloud_apis') || { entries: [] };
    cloudApis = cfg.entries || [];
    document.getElementById('new-cloud-api-name').value = '';
    document.getElementById('new-cloud-api-key').value = '';
    renderCloudApiList();
    flashSaved(true);
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
