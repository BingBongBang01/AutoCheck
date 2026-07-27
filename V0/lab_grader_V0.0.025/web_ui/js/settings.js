// ===== Settings — 메인 렌더 + 터미널 우클릭 동작 =====
// 로컬 AI/오버플로우 설정은 settings-local-ai.js, 클라우드 API 키는 settings-cloud-api.js,
// AI 제공자 우선순위(드래그 정렬)는 settings-ai-order.js 참고.
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
        <div><p class="card-title">로컬 AI</p><p class="card-desc">로컬 NPU(lemonade-server 등) 서버로 보낼 모델과 생성 파라미터를 설정합니다. '모델 새로고침'을 누르면 해당 서버에 실제로 설치된 모델 목록을 가져옵니다. 모델을 바꾸면 아래 컨텍스트 오버플로우 방지 값도 해당 모델에 맞는 기본값으로 자동 갱신됩니다.</p></div>
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

      <div style="margin-top:20px;padding-top:16px;border-top:1px solid var(--border);">
        <p class="card-title" style="font-size:14px;">컨텍스트 오버플로우 방지 (로컬 NPU 전용)</p>
        <p class="card-desc">점검 결과가 많을 때 배치 문자 수/세그먼트 수 한도로 여러 번에 나눠 보내고, max_tokens로 응답 길이를 제한합니다. 선택한 모델에 맞는 값으로 자동 채워지며, 필요 시 직접 조정할 수 있습니다. 클라우드 API는 아래 '클라우드 API 키' 항목별로 별도 설정합니다.</p>
        <div class="grid-cols-2" style="margin-top:8px;">
          <div><label class="field-label">배치 문자 수 (batch_chars)</label><input class="field" type="number" id="batch-chars" min="1" value="${batchCfg.batch_chars}"></div>
          <div><label class="field-label">세그먼트 수 (batch_segs)</label><input class="field" type="number" id="batch-segs" min="1" value="${batchCfg.batch_segs}"></div>
          <div><label class="field-label">max_tokens</label><input class="field" type="number" id="batch-max-tokens" min="1" value="${batchCfg.max_tokens}"></div>
        </div>
        <div style="margin-top:12px;display:flex;gap:8px;align-items:center;">
          <button class="btn btn-primary" id="btn-save-batching"><span class="material-symbols-rounded">save</span>저장</button>
          <span id="batching-save-result" style="font-size:12px;color:var(--sub);"></span>
        </div>
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
