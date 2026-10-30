// ===== 장비별 SSH 키(퍼블릭키 인증) 등록 팝업 =====
function openDeviceKeyModal(idx) {
  const device = inventoryDevices[idx];
  const overlay = document.createElement('div');
  overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.5);display:flex;align-items:center;justify-content:center;z-index:1000;';
  overlay.innerHTML = `
    <div class="card" style="width:440px;max-width:90vw;">
      <div class="card-header">
        <div class="card-icon"><span class="material-symbols-rounded">key</span></div>
        <div><p class="card-title">SSH 키 등록 — ${esc(device.name || '(이름 없음)')}</p><p class="card-desc">확장자가 없는 id_ed25519 개인키와 텍스트 키를 지원합니다. id_ed25519.pub는 공개키라서 선택하지 않습니다.</p></div>
      </div>
      <label class="field-label">Authentication</label>
      <select class="field" id="modal-auth-method" style="margin-bottom:12px;"><option value="password" ${device.auth_method !== 'public_key' ? 'selected' : ''}>Password</option><option value="public_key" ${device.auth_method === 'public_key' ? 'selected' : ''}>Private Key</option></select>
      <div id="modal-key-fields">
      <label class="field-label">키 파일 경로</label>
      <div style="display:flex;gap:8px;margin-bottom:14px;">
        <input class="field" id="modal-key-path" value="${esc(device.key_path)}" readonly placeholder="키 파일을 선택하세요">
        <button class="btn btn-outlined" id="btn-browse-key"><span class="material-symbols-rounded">folder_open</span>찾아보기</button>
      </div>
      <label class="field-label">키 텍스트</label>
      <textarea class="field" id="modal-key-content" rows="5" placeholder="${esc(device.key_content ? '새 키 입력됨 — 저장 대기' : (device.has_key_content ? '저장된 키가 있습니다 — 바꾸려면 새 키를 붙여넣거나 파일을 고르세요' : '-----BEGIN OPENSSH PRIVATE KEY-----'))}"></textarea>
      <label class="field-label" style="margin-top:10px;">Private Key Passphrase (선택)</label>
      <div style="display:flex;gap:6px;align-items:center;">
        <input class="field" id="modal-key-passphrase" type="password" autocomplete="new-password" value="" placeholder="${esc(secretPlaceholder(device, 'key_passphrase', 'Passphrase가 있는 경우 입력'))}">
        ${(device.has_key_passphrase || device.key_passphrase) ? '<button class="btn btn-outlined" id="btn-clear-passphrase" type="button">지우기</button>' : ''}
      </div>
      </div>
      <div style="display:flex;gap:8px;justify-content:flex-end;">
        <button class="btn btn-outlined" id="btn-key-cancel">취소</button>
        <button class="btn btn-primary" id="btn-key-save">저장</button>
      </div>
    </div>
  `;
  document.body.appendChild(overlay);

  const authSelect = overlay.querySelector('#modal-auth-method');
  const keyFields = overlay.querySelector('#modal-key-fields');
  authSelect.addEventListener('change', () => { keyFields.style.display = authSelect.value === 'public_key' ? 'block' : 'none'; });
  authSelect.dispatchEvent(new Event('change'));

  overlay.querySelector('#btn-browse-key').addEventListener('click', async () => {
    const result = await call('read_key_file');
    if (result && result.error) { alert(result.error); return; }
    if (result) {
      overlay.querySelector('#modal-key-path').value = result.path || '';
      overlay.querySelector('#modal-key-content').value = result.content || '';
    }
  });
  let clearPassphrase = false;
  const clearPassphraseBtn = overlay.querySelector('#btn-clear-passphrase');
  if (clearPassphraseBtn) clearPassphraseBtn.addEventListener('click', () => {
    clearPassphrase = true;
    overlay.querySelector('#modal-key-passphrase').placeholder = '지움 — 저장을 누르면 적용';
    clearPassphraseBtn.style.display = 'none';
  });
  overlay.querySelector('#btn-key-cancel').addEventListener('click', () => overlay.remove());
  overlay.addEventListener('click', (e) => { if (e.target === overlay) overlay.remove(); });
  overlay.querySelector('#btn-key-save').addEventListener('click', () => {
    const path = overlay.querySelector('#modal-key-path').value.trim();
    const content = overlay.querySelector('#modal-key-content').value.trim();
    const passphrase = overlay.querySelector('#modal-key-passphrase').value;
    // 비밀 칸은 새로 입력했을 때만 바꾼다(저장된 키·passphrase 는 화면에 오지 않는다 — IMPROVEMENT_PLAN 4-1).
    device.key_path = path;
    setSecret(device, 'key_content', content);
    if (passphrase) setSecret(device, 'key_passphrase', passphrase);
    else if (clearPassphrase) markSecretCleared(device, 'key_passphrase');
    const hasKey = !!(path || device.key_content || device.has_key_content);
    device.auth_method = authSelect.value === 'public_key' && hasKey ? 'public_key' : 'password';
    if (device.auth_method === 'password') {
      device.key_path = '';
      markSecretCleared(device, 'key_content');
      markSecretCleared(device, 'key_passphrase');
    }
    overlay.remove();
    renderInventoryTable();
    scheduleAutosave();
  });
}
