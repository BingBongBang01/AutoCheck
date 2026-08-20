// ===== Settings — 세션 로그 파일명 규칙 설정 =====
let logNamingPresets = [];
let currentLogNamingTemplate = '';

async function loadLogNamingConfig() {
  const cfg = await call('get_log_naming_settings') || {};
  currentLogNamingTemplate = cfg.template || '{timestamp}_{kind}_{device}.txt';
  logNamingPresets = cfg.presets || [];
  return cfg;
}

function logNamingSectionHtml(cfg) {
  const presets = cfg.presets || [];
  const currentTpl = cfg.template || '{timestamp}_{kind}_{device}.txt';
  const matchedPreset = presets.find(p => p.pattern === currentTpl);
  const presetId = matchedPreset ? matchedPreset.id : 'custom';

  return `
    <div class="card section-gap">
      <div class="card-header">
        <div class="card-icon"><span class="material-symbols-rounded">label</span></div>
        <div>
          <p class="card-title">세션 로그 파일명 규칙 (Session Log Naming)</p>
          <p class="card-desc">
            정기점검 및 세션 점검 수행 시 저장되는 원본 로그 파일의 이름 형식을 설정합니다.
            다운스트림 분석 및 대시보드 연동을 위해 <code>{device}</code> 변수는 필수입니다.
          </p>
        </div>
      </div>

      <div style="display:flex; flex-direction:column; gap:12px; margin-top:4px;">
        <div class="grid-cols-2">
          <div>
            <label class="field-label">프리셋 템플릿 선택</label>
            <select class="field" id="log-naming-preset-select">
              ${presets.map(p => `<option value="${p.id}" ${p.id === presetId ? 'selected' : ''}>${p.name} (${p.pattern})</option>`).join('')}
              <option value="custom" ${presetId === 'custom' ? 'selected' : ''}>직접 입력 (사용자 정의 템플릿)</option>
            </select>
          </div>
          <div>
            <label class="field-label">파일명 템플릿 패턴</label>
            <input class="field" id="log-naming-template-input" value="${escapeHtml(currentTpl)}" placeholder="{timestamp}_{kind}_{device}.txt" spellcheck="false">
          </div>
        </div>

        <div>
          <label class="field-label" style="margin-bottom:6px;">치환 변수 태그 (클릭하여 삽입)</label>
          <div style="display:flex; flex-wrap:wrap; gap:6px;">
            <button type="button" class="btn btn-outlined btn-sm log-token-btn" data-token="{device}" title="장비명 (필수)">+ {device}</button>
            <button type="button" class="btn btn-outlined btn-sm log-token-btn" data-token="{ip}" title="장비 접속 IP">+ {ip}</button>
            <button type="button" class="btn btn-outlined btn-sm log-token-btn" data-token="{timestamp}" title="고정폭 일시 (YYYYMMDD_HHMMSS)">+ {timestamp}</button>
            <button type="button" class="btn btn-outlined btn-sm log-token-btn" data-token="{date}" title="날짜 (YYYYMMDD)">+ {date}</button>
            <button type="button" class="btn btn-outlined btn-sm log-token-btn" data-token="{time}" title="시간 (HHMMSS)">+ {time}</button>
            <button type="button" class="btn btn-outlined btn-sm log-token-btn" data-token="{kind}" title="로그 종류 (raw, problem 등)">+ {kind}</button>
            <button type="button" class="btn btn-outlined btn-sm log-token-btn" data-token="{customer}" title="현재 고객사명">+ {customer}</button>
            <button type="button" class="btn btn-outlined btn-sm log-token-btn" data-token="{profile}" title="현재 정기점검 회차명">+ {profile}</button>
          </div>
        </div>

        <div style="background:var(--bg); border:1px solid var(--border); border-radius:8px; padding:12px 14px;">
          <div style="display:flex; justify-content:space-between; align-items:center;">
            <span style="font-size:12px; font-weight:600; color:var(--sub);">실시간 파일명 생성 미리보기 (Live Preview)</span>
            <span id="log-naming-valid-badge" class="badge badge-success" style="font-size:11px;">유효한 템플릿</span>
          </div>
          <div id="log-naming-preview-text" style="font-family:monospace; font-size:14px; font-weight:600; color:var(--text); margin-top:6px; word-break:break-all;">
            20260818_140000_raw_Core1.txt
          </div>
          <div id="log-naming-parse-info" style="font-size:12px; color:var(--sub); margin-top:4px;">
            파싱 인식 ➔ 장비명: <b style="color:var(--text)">Core1</b> | 일시: <b style="color:var(--text)">20260818_140000</b>
          </div>
        </div>

        <div style="display:flex; gap:8px; align-items:center; margin-top:4px;">
          <button class="btn btn-primary" id="btn-save-log-naming"><span class="material-symbols-rounded">save</span>저장</button>
          <button class="btn btn-outlined" id="btn-reset-log-naming"><span class="material-symbols-rounded">restart_alt</span>기본값 복원</button>
          <span id="log-naming-save-result" style="font-size:12px; color:var(--sub);"></span>
        </div>
      </div>
    </div>
  `;
}

function wireLogNamingSection() {
  const presetSelect = document.getElementById('log-naming-preset-select');
  const templateInput = document.getElementById('log-naming-template-input');
  const previewText = document.getElementById('log-naming-preview-text');
  const parseInfo = document.getElementById('log-naming-parse-info');
  const validBadge = document.getElementById('log-naming-valid-badge');
  const saveBtn = document.getElementById('btn-save-log-naming');
  const resetBtn = document.getElementById('btn-reset-log-naming');
  const saveResult = document.getElementById('log-naming-save-result');

  if (!templateInput) return;

  // 실시간 미리보기 업데이트
  async function updatePreview() {
    const tpl = templateInput.value.trim();
    if (!tpl) {
      if (validBadge) {
        validBadge.className = 'badge badge-danger';
        validBadge.textContent = '템플릿 필요';
      }
      if (previewText) previewText.textContent = '(템플릿을 입력하세요)';
      if (parseInfo) parseInfo.innerHTML = '<span style="color:var(--err);">템플릿이 비어있습니다.</span>';
      return;
    }

    try {
      const res = await call('preview_log_naming', tpl);
      if (res && res.valid) {
        if (validBadge) {
          validBadge.className = 'badge badge-success';
          validBadge.textContent = '유효한 템플릿';
        }
        if (previewText) previewText.textContent = res.preview || '';
        if (parseInfo) {
          parseInfo.innerHTML = `파싱 인식 ➔ 장비명: <b style="color:var(--text);">${escapeHtml(res.parsed_device || '-')}</b> | 일시: <b style="color:var(--text);">${escapeHtml(res.parsed_stamp || '-')}</b>`;
        }
      } else {
        if (validBadge) {
          validBadge.className = 'badge badge-danger';
          validBadge.textContent = '유효하지 않음';
        }
        if (previewText) previewText.textContent = res.preview || '';
        if (parseInfo) {
          parseInfo.innerHTML = `<span style="color:var(--err);">${escapeHtml(res.error || '유효하지 않은 템플릿입니다.')}</span>`;
        }
      }
    } catch (err) {
      console.warn('미리보기 오류:', err);
    }
  }

  // 프리셋 선택 변경 시
  presetSelect?.addEventListener('change', () => {
    const selectedId = presetSelect.value;
    if (selectedId === 'custom') return;
    const preset = logNamingPresets.find(p => p.id === selectedId);
    if (preset) {
      templateInput.value = preset.pattern;
      updatePreview();
    }
  });

  // 인풋 타이핑 시
  templateInput?.addEventListener('input', () => {
    // 일치하는 프리셋이 있는지 확인
    const val = templateInput.value.trim();
    const matched = logNamingPresets.find(p => p.pattern === val);
    if (matched && presetSelect) {
      presetSelect.value = matched.id;
    } else if (presetSelect) {
      presetSelect.value = 'custom';
    }
    updatePreview();
  });

  // 토큰 칩 버튼 클릭 시 커서 위치에 삽입
  document.querySelectorAll('.log-token-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const token = btn.dataset.token;
      if (!token || !templateInput) return;
      const start = templateInput.selectionStart || 0;
      const end = templateInput.selectionEnd || 0;
      const text = templateInput.value;
      templateInput.value = text.slice(0, start) + token + text.slice(end);
      templateInput.focus();
      templateInput.selectionStart = templateInput.selectionEnd = start + token.length;
      presetSelect.value = 'custom';
      updatePreview();
    });
  });

  // 저장 버튼
  saveBtn?.addEventListener('click', async () => {
    const tpl = templateInput.value.trim();
    if (!tpl) {
      if (saveResult) {
        saveResult.textContent = '템플릿을 입력하세요.';
        saveResult.style.color = 'var(--err)';
      }
      return;
    }
    saveBtn.disabled = true;
    try {
      const res = await call('save_log_naming_settings', tpl);
      if (res && res.error) {
        if (saveResult) {
          saveResult.textContent = res.error;
          saveResult.style.color = 'var(--err)';
        }
      } else {
        currentLogNamingTemplate = tpl;
        if (saveResult) {
          saveResult.textContent = '저장되었습니다.';
          saveResult.style.color = 'var(--primary)';
        }
        setTimeout(() => { if (saveResult) saveResult.textContent = ''; }, 3000);
      }
    } catch (err) {
      if (saveResult) {
        saveResult.textContent = `저장 실패: ${err.message || err}`;
        saveResult.style.color = 'var(--err)';
      }
    } finally {
      saveBtn.disabled = false;
    }
  });

  // 기본값 복원 버튼
  resetBtn?.addEventListener('click', async () => {
    const standard = logNamingPresets.find(p => p.id === 'standard');
    const defaultPattern = standard ? standard.pattern : '{timestamp}_{kind}_{device}.txt';
    templateInput.value = defaultPattern;
    if (presetSelect) presetSelect.value = 'standard';
    updatePreview();
    saveBtn?.click();
  });

  // 초기 미리보기 실행
  updatePreview();
}
