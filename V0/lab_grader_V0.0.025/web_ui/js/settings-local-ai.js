// ===== 설정 — 로컬 AI(Lemonade NPU) 모델/파라미터 + 컨텍스트 오버플로우(배치) 설정 =====

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
  const modelChanged = select.value !== select.dataset.savedModel;
  const ok = await call('save_local_ai_config', {
    endpoint: document.getElementById('local-ai-endpoint').value.trim(),
    model: select.value,
    temperature: parseFloat(document.getElementById('local-ai-temperature').value),
  });
  select.dataset.savedModel = select.value;
  // 모델이 바뀌면 서버가 그 모델에 맞는 배치/세그먼트/max_tokens 기본값을 자동 저장하므로,
  // 화면에 표시된 값도 최신값으로 다시 불러와 보여준다.
  if (modelChanged) {
    const batchCfg = await call('get_batching_settings') || {};
    if (batchCfg.batch_chars != null) document.getElementById('batch-chars').value = batchCfg.batch_chars;
    if (batchCfg.batch_segs != null) document.getElementById('batch-segs').value = batchCfg.batch_segs;
    if (batchCfg.max_tokens != null) document.getElementById('batch-max-tokens').value = batchCfg.max_tokens;
  }
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
