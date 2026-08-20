// ===== 점검 로그 (원본) — 본문 스크롤 및 상단 로그 파일명 규칙 설정 UI =====
let inspLogNamingPresets = [];
let inspLogNamingCollapsed = false;

async function renderInspectionLog() {
  const content = document.getElementById('content');
  const cfg = await call('get_log_naming_settings') || {};
  const currentTpl = cfg.template || '{timestamp}_{kind}_{device}.txt';
  inspLogNamingPresets = cfg.presets || [];
  const matchedPreset = inspLogNamingPresets.find(p => p.pattern === currentTpl);
  const presetId = matchedPreset ? matchedPreset.id : 'custom';

  content.innerHTML = `
    <div class="inspection-log-page">
      <div style="display:flex; align-items:flex-end; justify-content:space-between;">
        <div>
          <h1 class="page-title" style="margin-bottom:2px;">점검 로그</h1>
          <p class="page-sub" style="margin-bottom:0;">장비에서 수집한 원본 CLI 출력을 확인하고 파일명 저장 규칙을 관리합니다.</p>
        </div>
      </div>

      <!-- 상단: 로그 파일명 형식 설정 카드 -->
      <div class="log-naming-quick-card ${inspLogNamingCollapsed ? 'collapsed' : ''}" id="insp-log-naming-card">
        <div class="log-naming-header" id="insp-log-naming-header">
          <div class="card-icon" style="width:32px; height:32px; font-size:18px; display:flex; align-items:center; justify-content:center; background:var(--input-bg); border:1px solid var(--border); border-radius:var(--radius-sm);"><span class="material-symbols-rounded" style="font-size:18px; color:var(--primary);">label</span></div>
          <div class="log-naming-header-info">
            <div style="display:flex; align-items:center; gap:8px;">
              <span style="font-weight:700; font-size:13px; color:var(--text);">세션 로그 파일명 규칙 (Log Naming Format)</span>
              <span id="insp-log-naming-active-pill" style="font-family:var(--font-mono); font-size:11px; padding:1px 6px; background:var(--input-bg); border:1px solid var(--border); border-radius:var(--radius-sm); color:var(--primary); font-weight:600;">${escapeHtml(currentTpl)}</span>
            </div>
            <p style="margin:2px 0 0; font-size:11px; color:var(--sub);">점검 실행 시 생성될 파일명 형식을 변경하면 모든 다운스트림 탭과 즉시 연동됩니다.</p>
          </div>
          <button type="button" class="btn btn-outlined btn-sm" id="btn-toggle-log-naming" style="height:28px; font-size:11px; gap:4px;" title="설정 영역 접기/펼치기">
            <span class="material-symbols-rounded" style="font-size:16px;">${inspLogNamingCollapsed ? 'expand_more' : 'expand_less'}</span>
            <span>${inspLogNamingCollapsed ? '규칙 펼치기' : '규칙 접기'}</span>
          </button>
        </div>

        <div class="log-naming-body" style="margin-top:10px; padding-top:10px; border-top:1px solid var(--border); display:flex; flex-direction:column; gap:10px;">
          <div class="grid-cols-2" style="gap:10px;">
            <div>
              <label class="field-label" style="font-size:11px;">프리셋 선택</label>
              <select class="field" id="insp-preset-select" style="height:32px; font-size:12px;">
                ${inspLogNamingPresets.map(p => `<option value="${p.id}" ${p.id === presetId ? 'selected' : ''}>${p.name} (${p.pattern})</option>`).join('')}
                <option value="custom" ${presetId === 'custom' ? 'selected' : ''}>직접 입력 (사용자 정의 템플릿)</option>
              </select>
            </div>
            <div>
              <label class="field-label" style="font-size:11px;">파일명 템플릿 패턴</label>
              <input class="field" id="insp-template-input" value="${escapeHtml(currentTpl)}" placeholder="{timestamp}_{kind}_{device}.txt" spellcheck="false" style="height:32px; font-size:12px; font-family:var(--font-mono);">
            </div>
          </div>

          <div>
            <label class="field-label" style="font-size:11px; margin-bottom:4px;">치환 변수 태그 (클릭하여 삽입)</label>
            <div style="display:flex; flex-wrap:wrap; gap:5px;">
              <button type="button" class="log-token-chip" data-token="{device}" title="장비명 (필수)">+ {device}</button>
              <button type="button" class="log-token-chip" data-token="{ip}" title="장비 IP">+ {ip}</button>
              <button type="button" class="log-token-chip" data-token="{timestamp}" title="고정폭 일시 (YYYYMMDD_HHMMSS)">+ {timestamp}</button>
              <button type="button" class="log-token-chip" data-token="{date}" title="날짜 (YYYYMMDD)">+ {date}</button>
              <button type="button" class="log-token-chip" data-token="{time}" title="시간 (HHMMSS)">+ {time}</button>
              <button type="button" class="log-token-chip" data-token="{kind}" title="로그 종류 (raw, problem 등)">+ {kind}</button>
              <button type="button" class="log-token-chip" data-token="{customer}" title="고객사명">+ {customer}</button>
              <button type="button" class="log-token-chip" data-token="{profile}" title="회차명">+ {profile}</button>
            </div>
          </div>

          <div class="log-preview-box">
            <div style="display:flex; justify-content:space-between; align-items:center;">
              <span style="font-size:11px; font-weight:600; color:var(--sub);">실시간 파일명 생성 미리보기</span>
              <span id="insp-valid-badge" class="badge badge-success" style="font-size:10px;">유효한 템플릿</span>
            </div>
            <div id="insp-preview-text" style="font-family:var(--font-mono); font-size:13px; font-weight:700; color:var(--text); margin-top:4px; word-break:break-all;">
              20260818_140000_raw_Core1.txt
            </div>
            <div id="insp-parse-info" style="font-size:11px; color:var(--sub); margin-top:2px;">
              파싱 인식 ➔ 장비명: <b style="color:var(--text)">Core1</b> | 일시: <b style="color:var(--text)">20260818_140000</b>
            </div>
          </div>

          <div style="display:flex; gap:8px; align-items:center;">
            <button class="btn btn-primary btn-sm" id="btn-save-insp-log-naming" style="height:30px; font-size:12px;"><span class="material-symbols-rounded" style="font-size:16px;">save</span>저장</button>
            <button class="btn btn-outlined btn-sm" id="btn-reset-insp-log-naming" style="height:30px; font-size:12px;"><span class="material-symbols-rounded" style="font-size:16px;">restart_alt</span>기본값 복원</button>
            <span id="insp-save-result" style="font-size:12px; color:var(--sub);"></span>
          </div>
        </div>
      </div>

      <!-- 하단: 수집된 로그 뷰어 카드 -->
      <div class="card inspection-log-viewer-card">
        <div class="card-header" style="flex-shrink:0;">
          <div class="card-icon"><span class="material-symbols-rounded">folder_open</span></div>
          <div>
            <p class="card-title">수집된 로그</p>
            <p class="card-desc">세션 터미널 점검 또는 수집/채점 실행으로 쌓인 로그 파일 목록입니다.</p>
          </div>
          <span style="flex:1"></span>
          <button class="btn btn-outlined" id="btn-open-log-folder"><span class="material-symbols-rounded">folder_open</span>폴더 열기</button>
        </div>
        <div class="log-viewer" id="log-viewer" style="flex:1; flex-direction:column; min-height:0; display:flex;"></div>
      </div>
    </div>
  `;

  document.getElementById('btn-open-log-folder').addEventListener('click', async () => {
    const result = await call('open_inspection_log_folder', 'original');
    if (result && result.error) alert(result.error);
  });

  wireInspectionLogNaming();
  await refreshLogViewer(false);
}

function wireInspectionLogNaming() {
  const card = document.getElementById('insp-log-naming-card');
  const toggleBtn = document.getElementById('btn-toggle-log-naming');
  const presetSelect = document.getElementById('insp-preset-select');
  const templateInput = document.getElementById('insp-template-input');
  const previewText = document.getElementById('insp-preview-text');
  const parseInfo = document.getElementById('insp-parse-info');
  const validBadge = document.getElementById('insp-valid-badge');
  const activePill = document.getElementById('insp-log-naming-active-pill');
  const saveBtn = document.getElementById('btn-save-insp-log-naming');
  const resetBtn = document.getElementById('btn-reset-insp-log-naming');
  const saveResult = document.getElementById('insp-save-result');

  if (!templateInput) return;

  // 접기 / 펼치기 토글
  toggleBtn?.addEventListener('click', (e) => {
    e.stopPropagation();
    inspLogNamingCollapsed = !inspLogNamingCollapsed;
    card.classList.toggle('collapsed', inspLogNamingCollapsed);
    const icon = toggleBtn.querySelector('.material-symbols-rounded');
    const label = toggleBtn.querySelector('span:not(.material-symbols-rounded)');
    if (icon) icon.textContent = inspLogNamingCollapsed ? 'expand_more' : 'expand_less';
    if (label) label.textContent = inspLogNamingCollapsed ? '규칙 펼치기' : '규칙 접기';
  });

  // 실시간 미리보기 갱신
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
      console.warn('미리보기 갱신 오류:', err);
    }
  }

  // 프리셋 선택 변경
  presetSelect?.addEventListener('change', () => {
    const selectedId = presetSelect.value;
    if (selectedId === 'custom') return;
    const preset = inspLogNamingPresets.find(p => p.id === selectedId);
    if (preset) {
      templateInput.value = preset.pattern;
      updatePreview();
    }
  });

  // 템플릿 인풋 타이핑
  templateInput?.addEventListener('input', () => {
    const val = templateInput.value.trim();
    const matched = inspLogNamingPresets.find(p => p.pattern === val);
    if (matched && presetSelect) {
      presetSelect.value = matched.id;
    } else if (presetSelect) {
      presetSelect.value = 'custom';
    }
    updatePreview();
  });

  // 토큰 칩 버튼 클릭
  document.querySelectorAll('.log-token-chip').forEach(btn => {
    btn.addEventListener('click', () => {
      const token = btn.dataset.token;
      if (!token || !templateInput) return;
      const start = templateInput.selectionStart || 0;
      const end = templateInput.selectionEnd || 0;
      const text = templateInput.value;
      templateInput.value = text.slice(0, start) + token + text.slice(end);
      templateInput.focus();
      templateInput.selectionStart = templateInput.selectionEnd = start + token.length;
      if (presetSelect) presetSelect.value = 'custom';
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
        if (activePill) activePill.textContent = tpl;
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

  // 기본값 복원
  resetBtn?.addEventListener('click', async () => {
    const standard = inspLogNamingPresets.find(p => p.id === 'standard');
    const defaultPattern = standard ? standard.pattern : '{timestamp}_{kind}_{device}.txt';
    templateInput.value = defaultPattern;
    if (presetSelect) presetSelect.value = 'standard';
    updatePreview();
    saveBtn?.click();
  });

  // 초기 1회 미리보기 실행
  updatePreview();
}
