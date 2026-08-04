// ===== 설정 — 클라우드 API 키 목록 관리 =====
// 필드 순서는 저장된 행과 추가 행이 동일하다: [제공자] [이름] [모델] [API 키].
// 예전에는 저장된 행이 이름->제공자->모델, 추가 행이 제공자->이름(모델 없음) 순이어서
// 같은 값을 다른 자리에서 입력해야 했다. 순서를 바꿀 때는 두 곳을 같이 고쳐야 한다.
function renderCloudApiList() {
  const list = document.getElementById('cloud-api-list');
  if (!cloudApis.length) {
    list.innerHTML = `<p style="font-size:13px;color:var(--sub);">등록된 API 키가 없습니다. 아래에서 제공자·이름·모델을 고르고 'API 추가'를 눌러 새 키를 등록하세요.</p>`;
    return;
  }
  list.innerHTML = cloudApis.map(e => `
    <div class="cloud-api-row" data-id="${e.id}" style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;">
      <input type="checkbox" data-role="enabled" ${e.enabled ? 'checked' : ''} title="사용 여부">
      <span style="font-size:12px;color:var(--sub);min-width:150px;">${providerLabel(e.provider)}</span>
      <input class="field" data-role="name" value="${e.name}" placeholder="키 이름" style="max-width:200px;">
      <select class="field" data-role="model" style="max-width:230px;">${modelOptionsHtml(e.provider, e.model, e.model_is_custom)}</select>
      <input class="field" data-role="model-custom" value="${e.model_is_custom ? e.model : ''}" placeholder="모델 ID 직접 입력" style="max-width:230px;display:none;">
      ${providerNeedsEndpoint(e.provider) ? `<input class="field" data-role="endpoint" value="${e.endpoint || ''}" placeholder="API 주소 (예: https://.../v1/chat/completions)" style="min-width:260px;flex:1;">` : ''}
      <input class="field" type="password" data-role="key" placeholder="${e.has_key ? 'API 키 (변경 시에만 입력)' : 'API 키 입력'}" style="flex:1;min-width:160px;">
      ${e.has_key ? '' : `<span class="api-key-warn" title="이 항목은 키가 저장되지 않아 호출 시 건너뜁니다">
        <span class="material-symbols-rounded" style="font-size:13px">warning</span>API 키 미설정</span>`}
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

// 제공자 표(cloudProviderTypes)는 서버의 CLOUD_PROVIDER_TYPES를 그대로 받은 것 —
// 라벨·모델 목록의 정본이 서버 한 곳이라 제공자를 추가할 때 이 파일은 손대지 않는다.
// (예전에는 추가 행의 <option> DOM을 훑어 라벨을 찾아서, 그 select가 없는 화면에서는 id가 그대로 보였다.)
function providerLabel(id) {
  const type = cloudProviderTypes.find(p => p.id === id);
  return type ? type.label : id;
}

// 모델 목록 맨 끝에 '직접입력'을 붙인다 — 제공자들이 모델을 수시로 추가하는데 목록에 없으면
// 쓸 수 없으니 탈출구가 필요하다. 이걸 고르면 옆 텍스트 칸이 나타난다(wireModelField 참고).
const CUSTOM_MODEL = '__custom__';

function modelOptionsHtml(providerId, savedModel, savedIsCustom) {
  const type = cloudProviderTypes.find(p => p.id === providerId);
  const models = (type && type.models) || [];
  // 저장된 모델이 목록에 없으면(직접입력으로 넣은 값) '직접입력'이 선택된 상태로 보여준다.
  const isCustom = savedIsCustom || (!!savedModel && !models.includes(savedModel));
  const opts = models.map(m =>
    `<option value="${m}" ${!isCustom && m === savedModel ? 'selected' : ''}>${m}</option>`);
  opts.push(`<option value="${CUSTOM_MODEL}" ${isCustom ? 'selected' : ''}>직접입력…</option>`);
  return opts.join('');
}

// 모델 select + 직접입력 텍스트 칸을 한 쌍으로 묶는다. 저장된 행과 추가 행이 같은 규칙을 쓴다.
// getModelValue()가 '실제로 저장할 모델 문자열'을 돌려준다.
function wireModelField(root) {
  const sel = root.querySelector('[data-role="model"]');
  const txt = root.querySelector('[data-role="model-custom"]');
  if (!sel || !txt) return;
  const sync = () => {
    const custom = sel.value === CUSTOM_MODEL;
    txt.style.display = custom ? '' : 'none';
  };
  // 추가 행은 제공자를 바꿀 때마다 이 함수를 다시 부른다(모델 목록이 갈리므로).
  // 그때 change 리스너가 중복 등록되지 않도록 한 번만 건다.
  if (!sel.dataset.modelFieldWired) {
    sel.dataset.modelFieldWired = '1';
    sel.addEventListener('change', () => {
      sync();
      if (sel.value === CUSTOM_MODEL) txt.focus();   // 사용자가 직접 고른 경우에만 커서 이동
    });
  }
  sync();
}

function getModelValue(root) {
  const sel = root.querySelector('[data-role="model"]');
  const txt = root.querySelector('[data-role="model-custom"]');
  if (!sel) return '';
  return sel.value === CUSTOM_MODEL ? (txt ? txt.value.trim() : '') : sel.value;
}

// 직접입력 제공자만 주소 칸이 필요하다 — 목록에 있는 제공자는 서버 표의 주소를 쓴다.
function providerNeedsEndpoint(providerId) {
  const type = cloudProviderTypes.find(p => p.id === providerId);
  return !!(type && type.needs_endpoint);
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

    wireModelField(row);

    // 모델 저장은 '목록에서 고른 값'과 '직접입력 칸의 값' 중 실제로 쓸 쪽을 getModelValue()가 정한다.
    const saveModel = async () => {
      const model = getModelValue(row);
      if (!model) return;                 // '직접입력…'만 고르고 아직 안 적은 상태 — 저장하지 않는다
      const r = await call('update_cloud_api', id, { model });
      flashSaved(!(r && r.error));
      if (r && r.error) { resultEl.textContent = r.error; resultEl.style.color = 'var(--critical)'; return; }
      // 모델 변경 시 서버가 자동 적용한 batch_chars/max_tokens 기본값을 화면에도 반영
      const cfg = await call('get_cloud_apis') || { entries: [] };
      cloudApis = cfg.entries || [];
      const updated = cloudApis.find(x => x.id === id);
      if (updated) {
        row.querySelector('[data-role="batch_chars"]').value = updated.batch_chars;
        row.querySelector('[data-role="max_tokens"]').value = updated.max_tokens;
      }
    };
    row.querySelector('[data-role="model"]').addEventListener('change', saveModel);
    row.querySelector('[data-role="model-custom"]').addEventListener('blur', saveModel);

    const endpointEl = row.querySelector('[data-role="endpoint"]');
    if (endpointEl) {
      endpointEl.addEventListener('blur', async (e) => {
        const r = await call('update_cloud_api', id, { endpoint: e.target.value });
        if (r && r.error) { resultEl.textContent = r.error; resultEl.style.color = 'var(--critical)'; }
        flashSaved(!(r && r.error));
      });
    }

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
        model: getModelValue(row),
        batch_chars: row.querySelector('[data-role="batch_chars"]').value,
        max_tokens: row.querySelector('[data-role="max_tokens"]').value,
      };
      if (endpointEl) fields.endpoint = endpointEl.value;
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
  const addRow = document.getElementById('btn-add-cloud-api').parentElement;
  const providerEl = document.getElementById('new-cloud-api-provider');
  const modelEl = document.getElementById('new-cloud-api-model');
  const endpointEl = document.getElementById('new-cloud-api-endpoint');

  // 제공자를 고르면 그 제공자가 지원하는 모델(+ 맨 끝 '직접입력…')로 목록을 다시 채우고,
  // 직접입력 제공자일 때만 주소 칸을 보여준다.
  const syncProvider = () => {
    const needsEndpoint = providerNeedsEndpoint(providerEl.value);
    if (modelEl) {
      modelEl.innerHTML = modelOptionsHtml(providerEl.value, null);
      // 직접입력 제공자는 고를 모델 목록이 없으므로 처음부터 직접입력 상태로 둔다.
      if (needsEndpoint) modelEl.value = CUSTOM_MODEL;
    }
    if (endpointEl) {
      endpointEl.style.display = needsEndpoint ? '' : 'none';
      if (!needsEndpoint) endpointEl.value = '';
    }
    wireModelField(addRow);
  };
  providerEl.addEventListener('change', syncProvider);
  syncProvider();

  document.getElementById('btn-add-cloud-api').addEventListener('click', async () => {
    const provider = providerEl.value;
    const name = document.getElementById('new-cloud-api-name').value;
    const apiKey = document.getElementById('new-cloud-api-key').value;
    const model = getModelValue(addRow);
    const endpoint = endpointEl ? endpointEl.value : '';
    const r = await call('add_cloud_api', name, provider, apiKey, model, endpoint);
    if (r && r.error) { showToast(r.error, 'error'); return; }
    const cfg = await call('get_cloud_apis') || { entries: [] };
    cloudApis = cfg.entries || [];
    document.getElementById('new-cloud-api-name').value = '';
    document.getElementById('new-cloud-api-key').value = '';
    const customEl = document.getElementById('new-cloud-api-model-custom');
    if (customEl) customEl.value = '';
    if (endpointEl) endpointEl.value = '';
    renderCloudApiList();
    flashSaved(true);
  });
}
