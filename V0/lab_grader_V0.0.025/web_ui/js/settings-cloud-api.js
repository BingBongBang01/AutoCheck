// ===== 설정 — 클라우드 API 키(Anthropic/Gemini/OpenAI) 목록 관리 =====
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
      if (!(r && r.error)) {
        // 모델 변경 시 서버가 자동 적용한 batch_chars/max_tokens 기본값을 화면에도 반영
        const cfg = await call('get_cloud_apis') || { entries: [] };
        cloudApis = cfg.entries || [];
        const updated = cloudApis.find(x => x.id === id);
        if (updated) {
          row.querySelector('[data-role="batch_chars"]').value = updated.batch_chars;
          row.querySelector('[data-role="max_tokens"]').value = updated.max_tokens;
        }
      }
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
