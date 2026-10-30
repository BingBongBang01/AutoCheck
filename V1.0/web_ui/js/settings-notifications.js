// ===== 설정 — 알림 채널(Teams·Slack·웹훅·이메일) — IMPROVEMENT_PLAN 4-4 =====
// 웹훅 주소와 SMTP 비밀번호는 서버가 화면으로 보내지 않는다(저장 여부·웹훅 호스트만). 입력칸은 비워 두고
// '저장됨 — 바꾸려면 입력'으로 보여 주며, 새로 입력한 값만 보낸다(장비 목록의 비밀 칸과 같은 규칙).

let notifySettings = null;
let notifyEditing = null;          // 고치는 채널 id, 새 채널이면 'new'
const NOTIFY_SECURITY = [['starttls', 'STARTTLS'], ['ssl', 'SSL'], ['none', '암호화 없음(사내 릴레이)']];

function notifyStatusText(status) {
  if (!status || !status.at) return '아직 보낸 적 없음';
  const at = status.at.replace('T', ' ');
  return status.ok ? `마지막 전송 성공 · ${at}` : `마지막 전송 실패 · ${at} — ${status.error || ''}`;
}

function notifyTarget(ch) {
  if (ch.type === 'email') return `${ch.smtp_host || '-'} → ${(ch.recipients || []).join(', ')}`;
  return ch.has_webhook_url ? ch.webhook_host : '주소 없음';
}

function notifySecretPlaceholder(has, unreadable, fallback) {
  if (unreadable) return '이 PC에서 풀 수 없음 — 다시 입력';
  return has ? '저장됨 — 바꾸려면 입력' : fallback;
}

function notifyChannelFormHtml(s, ch) {
  const type = ch.type || 'teams';
  const unreadable = new Set(ch.unreadable_secrets || []);
  return `
    <div style="border:var(--line);border-radius:var(--radius-sm);padding:10px;margin-top:10px;display:flex;flex-direction:column;gap:8px;">
      <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;">
        <select class="field" id="notify-ch-type" style="width:auto;">
          ${Object.entries(s.channel_types || {}).map(([id, label]) => `<option value="${esc(id)}" ${type === id ? 'selected' : ''}>${esc(label)}</option>`).join('')}
        </select>
        <input class="field" id="notify-ch-name" value="${esc(ch.name || '')}" placeholder="이름 (예: 운영팀 Teams)" style="max-width:220px;">
        <label style="display:inline-flex;align-items:center;gap:4px;font-size:12px;"><input type="checkbox" id="notify-ch-enabled" ${ch.enabled !== false ? 'checked' : ''}>사용</label>
      </div>
      <div id="notify-ch-webhook" style="display:${type === 'email' ? 'none' : 'flex'};gap:8px;align-items:center;">
        <input class="field" type="password" id="notify-ch-url" autocomplete="off" style="flex:1;"
               placeholder="${esc(notifySecretPlaceholder(ch.has_webhook_url, unreadable.has('webhook_url'), '웹훅 주소 (https://...)'))}">
      </div>
      <div id="notify-ch-email" style="display:${type === 'email' ? 'flex' : 'none'};gap:8px;flex-wrap:wrap;align-items:center;">
        <input class="field" id="notify-ch-host" value="${esc(ch.smtp_host || '')}" placeholder="SMTP 서버" style="max-width:200px;">
        <input class="field" type="number" id="notify-ch-port" value="${esc(ch.smtp_port || '')}" placeholder="포트" style="max-width:90px;">
        <select class="field" id="notify-ch-security" style="width:auto;">
          ${NOTIFY_SECURITY.map(([value, label]) => `<option value="${esc(value)}" ${(ch.security || 'starttls') === value ? 'selected' : ''}>${label}</option>`).join('')}
        </select>
        <input class="field" id="notify-ch-username" value="${esc(ch.username || '')}" placeholder="사용자 (선택)" style="max-width:180px;">
        <input class="field" type="password" id="notify-ch-password" autocomplete="new-password" style="max-width:200px;"
               placeholder="${esc(notifySecretPlaceholder(ch.has_password, unreadable.has('password'), '비밀번호 (선택)'))}">
        ${ch.has_password ? `<label style="display:inline-flex;align-items:center;gap:4px;font-size:12px;"><input type="checkbox" id="notify-ch-clear-password">비밀번호 지우기</label>` : ''}
        <input class="field" id="notify-ch-sender" value="${esc(ch.sender || '')}" placeholder="보내는 사람 (autocheck@...)" style="max-width:220px;">
        <input class="field" id="notify-ch-recipients" value="${esc((ch.recipients || []).join(', '))}" placeholder="받는 사람 (쉼표로 구분)" style="flex:1;min-width:220px;">
      </div>
      <div style="display:flex;gap:8px;">
        <button class="btn btn-primary" id="notify-ch-save"><span class="material-symbols-rounded">save</span>${ch.id ? '저장' : '채널 추가'}</button>
        <button class="btn btn-outlined" id="notify-ch-cancel">취소</button>
      </div>
    </div>`;
}

function notifyCardHtml(s, editing) {
  const ev = s.events || {};
  const channels = s.channels || [];
  const editingChannel = editing === 'new' ? { enabled: true } : channels.find(c => c.id === editing);
  return `
    <div style="display:flex;gap:14px;flex-wrap:wrap;align-items:center;font-size:12.5px;">
      <label style="display:inline-flex;align-items:center;gap:4px;"><input type="checkbox" id="notify-enabled" ${s.enabled ? 'checked' : ''}>알림 사용</label>
      <label style="display:inline-flex;align-items:center;gap:4px;">가림
        <select class="field" id="notify-mask" style="width:auto;">
          ${Object.entries(s.mask_levels || {}).map(([id, label]) => `<option value="${esc(id)}" ${s.mask === id ? 'selected' : ''}>${esc(label)}</option>`).join('')}
        </select></label>
      <label style="display:inline-flex;align-items:center;gap:4px;">같은 사건은
        <input class="field" type="number" id="notify-dedupe" min="0" max="1440" value="${esc(s.dedupe_minutes)}" style="width:70px;">분에 한 번</label>
      <label style="display:inline-flex;align-items:center;gap:4px;">채널마다 시간당 최대
        <input class="field" type="number" id="notify-max" min="1" max="1000" value="${esc(s.max_per_hour)}" style="width:70px;">건</label>
    </div>
    <div style="display:flex;gap:14px;flex-wrap:wrap;align-items:center;font-size:12.5px;margin-top:8px;">
      <label style="display:inline-flex;align-items:center;gap:4px;"><input type="checkbox" id="notify-ev-realtime" ${ev.realtime ? 'checked' : ''}>실시간 감시 경고</label>
      <select class="field" id="notify-ev-severity" style="width:auto;">
        <option value="CRITICAL" ${ev.realtime_min_severity !== 'MAJOR' ? 'selected' : ''}>CRITICAL 만</option>
        <option value="MAJOR" ${ev.realtime_min_severity === 'MAJOR' ? 'selected' : ''}>MAJOR 이상</option>
      </select>
      <label style="display:inline-flex;align-items:center;gap:4px;"><input type="checkbox" id="notify-ev-scheduled" ${ev.scheduled_failed ? 'checked' : ''}>무인 정기점검 실패</label>
      <label style="display:inline-flex;align-items:center;gap:4px;"><input type="checkbox" id="notify-ev-collection" ${ev.collection_failed ? 'checked' : ''}>무인 점검 수집 실패</label>
      <button class="btn btn-primary" id="notify-save-options"><span class="material-symbols-rounded">save</span>저장</button>
    </div>
    <div style="display:flex;flex-direction:column;gap:6px;margin-top:12px;">
      ${channels.length ? channels.map(ch => `
        <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;border:var(--line);border-radius:var(--radius-sm);padding:6px 8px;">
          <span class="badge ${ch.enabled ? 'badge-pass' : 'badge-neutral'}">${ch.enabled ? '사용' : '꺼짐'}</span>
          <b style="font-size:12.5px;">${esc(ch.name)}</b>
          <span style="font-size:12px;color:var(--sub);">${esc((s.channel_types || {})[ch.type] || ch.type)} · ${esc(notifyTarget(ch))}</span>
          ${(ch.unreadable_secrets || []).length ? `<span class="api-key-warn">이 PC에서 풀 수 없음 — 다시 입력</span>` : ''}
          <span style="flex:1"></span>
          <span style="font-size:11px;color:${ch.status && ch.status.at && !ch.status.ok ? 'var(--critical)' : 'var(--sub)'};">${esc(notifyStatusText(ch.status))}</span>
          <button class="btn btn-outlined" data-notify-test="${esc(ch.id)}" style="height:26px;padding:0 8px;font-size:11px;">시험 보내기</button>
          <button class="btn btn-outlined" data-notify-edit="${esc(ch.id)}" style="height:26px;padding:0 8px;font-size:11px;">편집</button>
          <button class="btn btn-outlined" data-notify-remove="${esc(ch.id)}" style="height:26px;padding:0 8px;font-size:11px;">삭제</button>
        </div>`).join('') : `<p style="font-size:12px;color:var(--sub);">등록된 알림 채널이 없습니다.</p>`}
    </div>
    ${editingChannel ? notifyChannelFormHtml(s, editingChannel)
      : `<button class="btn btn-outlined" id="notify-add" style="margin-top:10px;"><span class="material-symbols-rounded">add</span>채널 추가</button>`}`;
}

function notifyOptionsForm(root) {
  const checked = (id) => root.querySelector(id).checked;
  return {
    enabled: checked('#notify-enabled'),
    mask: root.querySelector('#notify-mask').value,
    dedupe_minutes: Number(root.querySelector('#notify-dedupe').value),
    max_per_hour: Number(root.querySelector('#notify-max').value),
    events: {
      realtime: checked('#notify-ev-realtime'),
      realtime_min_severity: root.querySelector('#notify-ev-severity').value,
      scheduled_failed: checked('#notify-ev-scheduled'),
      collection_failed: checked('#notify-ev-collection'),
    },
  };
}

// 비밀 칸(웹훅 주소·비밀번호)은 새로 입력했을 때만 보낸다 — 빈 칸은 '그대로'다.
function notifyChannelForm(root, editing) {
  const value = (id) => root.querySelector(id).value;
  const fields = {
    id: editing === 'new' ? null : editing,
    type: value('#notify-ch-type'),
    name: value('#notify-ch-name').trim(),
    enabled: root.querySelector('#notify-ch-enabled').checked,
  };
  if (fields.type === 'email') {
    Object.assign(fields, {
      smtp_host: value('#notify-ch-host').trim(), smtp_port: value('#notify-ch-port'),
      security: value('#notify-ch-security'), username: value('#notify-ch-username').trim(),
      sender: value('#notify-ch-sender').trim(), recipients: value('#notify-ch-recipients'),
    });
    if (value('#notify-ch-password')) fields.password = value('#notify-ch-password');
    const clear = root.querySelector('#notify-ch-clear-password');
    if (clear && clear.checked) fields.clear_secrets = ['password'];
  } else if (value('#notify-ch-url').trim()) {
    fields.webhook_url = value('#notify-ch-url').trim();
  }
  return fields;
}

function drawNotificationCard() {
  const el = document.getElementById('notify-card-body');
  if (!el || !notifySettings) return;
  el.innerHTML = notifyCardHtml(notifySettings, notifyEditing);
  wireNotificationCard(el);
}

async function renderNotificationCard() {
  const el = document.getElementById('notify-card-body');
  if (!el) return;
  notifyEditing = null;
  notifySettings = await call('get_notification_settings');
  if (!notifySettings || notifySettings.error) {
    el.innerHTML = `<p style="font-size:12px;color:var(--sub);">${esc((notifySettings && notifySettings.error) || '알림 설정을 불러오지 못했습니다.')}</p>`;
    notifySettings = null;
    return;
  }
  drawNotificationCard();
}

function wireNotificationCard(root) {
  root.querySelector('#notify-save-options').addEventListener('click', async () => {
    const result = await call('save_notification_settings', notifyOptionsForm(root));
    if (!result || result.error) { showToast((result && result.error) || '저장하지 못했습니다.', 'error'); return; }
    notifySettings = result;
    flashSaved(true);
  });
  root.querySelectorAll('[data-notify-test]').forEach(btn => btn.addEventListener('click', async () => {
    btn.disabled = true;
    const result = await call('test_notification_channel', btn.dataset.notifyTest);
    btn.disabled = false;
    showToast((result && (result.detail || result.error)) || '보내지 못했습니다.', result && result.ok ? 'success' : 'error');
    const fresh = await call('get_notification_settings');
    if (fresh && !fresh.error) { notifySettings = fresh; drawNotificationCard(); }
  }));
  root.querySelectorAll('[data-notify-edit]').forEach(btn => btn.addEventListener('click', () => {
    notifyEditing = btn.dataset.notifyEdit;
    drawNotificationCard();
  }));
  root.querySelectorAll('[data-notify-remove]').forEach(btn => btn.addEventListener('click', async () => {
    if (!confirm('이 알림 채널을 지울까요? (저장된 웹훅 주소·비밀번호도 함께 지워집니다)')) return;
    const result = await call('remove_notification_channel', btn.dataset.notifyRemove);
    if (!result || result.error) { showToast((result && result.error) || '지우지 못했습니다.', 'error'); return; }
    notifySettings = result;
    notifyEditing = null;
    drawNotificationCard();
  }));
  const add = root.querySelector('#notify-add');
  if (add) add.addEventListener('click', () => { notifyEditing = 'new'; drawNotificationCard(); });
  const type = root.querySelector('#notify-ch-type');
  if (!type) return;
  type.addEventListener('change', () => {
    root.querySelector('#notify-ch-webhook').style.display = type.value === 'email' ? 'none' : 'flex';
    root.querySelector('#notify-ch-email').style.display = type.value === 'email' ? 'flex' : 'none';
  });
  root.querySelector('#notify-ch-save').addEventListener('click', async () => {
    const result = await call('save_notification_channel', notifyChannelForm(root, notifyEditing));
    if (!result || result.error) { showToast((result && result.error) || '저장하지 못했습니다.', 'error'); return; }
    notifySettings = result;
    notifyEditing = null;
    drawNotificationCard();
    flashSaved(true);
  });
  root.querySelector('#notify-ch-cancel').addEventListener('click', () => { notifyEditing = null; drawNotificationCard(); });
}
