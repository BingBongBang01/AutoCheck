// ===== SSH 호스트키 (IMPROVEMENT_PLAN 4-6) =====
// 처음 접속한 장비의 키를 기억하고(TOFU), 키가 바뀌면 비밀번호를 보내기 전에 끊는다. 여기서 바뀐 키를 확인해
// '다시 신뢰'하거나 기억한 키를 지운다. 키는 고객사마다 따로 기억한다(서버가 활성 프로파일의 고객사로 고른다).
// 화면에는 키 본문 대신 지문(SHA256:…)만 온다 — 장비에서 확인한 지문과 대조한다.

function hostKeyAddress(entry) {
  return Number(entry.port) === 22 ? entry.host : `${entry.host}:${entry.port}`;
}

function hostKeyTime(value) {
  return (value || '').replace('T', ' ');
}

// 다시 신뢰는 사용자가 지문을 대조했다는 확인이다 — 무엇을 확인해야 하는지 창에 적는다.
function confirmHostKeyTrust(label, entry) {
  return confirm(`${label}의 SSH 호스트키가 바뀌었습니다.\n\n`
    + `기억한 키: ${entry.trusted || '-'}\n지금 키: ${entry.got || '-'}\n\n`
    + '장비를 바꾸거나 다시 설치한 것이 맞고, 지금 키의 지문이 장비에서 직접 확인한 지문과 같을 때만 다시 신뢰하세요.\n'
    + '다시 신뢰할까요?');
}

// 장비 목록 행의 '다시 신뢰' — 연결 확인 결과(probe)에 담긴 바뀐 키로.
async function trustChangedHostKey(device) {
  const state = probeStateOf(device);
  const changed = state && state.hostKey;
  if (!changed || changed.status !== 'changed') return;
  if (!confirmHostKeyTrust(`${device.name || changed.host} (${hostKeyAddress(changed)})`, changed)) return;
  const result = await call('trust_host_key', changed.host, changed.port);
  if (!result || result.error) { showToast((result && result.error) || '다시 신뢰하지 못했습니다', 'error'); return; }
  showToast('새 호스트키를 기억했습니다 — 연결을 다시 확인합니다', 'success');
  probeRow(device, { immediate: true });
  renderHostKeys();
}

function hostKeyRowsHtml(data) {
  const changed = data.changed.map((k, i) => `
    <tr class="probe-fail">
      <td>${esc(k.device || '-')}</td>
      <td class="mono">${esc(hostKeyAddress(k))}</td>
      <td>${esc(k.key_type)}</td>
      <td class="mono" style="font-size:11px;"><div>기억한 키 ${esc(k.trusted_fingerprint || '-')}</div><div style="color:var(--critical);">지금 키 ${esc(k.fingerprint)}</div></td>
      <td>${esc(hostKeyTime(k.seen_at))} 바뀜</td>
      <td style="white-space:nowrap;"><button class="btn btn-outlined" data-hk-trust="${i}" title="지문을 장비에서 확인한 뒤에만">다시 신뢰</button></td>
    </tr>`).join('');
  const trusted = data.trusted.map((k, i) => `
    <tr>
      <td>${esc(k.device || '-')}</td>
      <td class="mono">${esc(hostKeyAddress(k))}</td>
      <td>${esc(k.key_type)}</td>
      <td class="mono" style="font-size:11px;">${esc(k.fingerprint)}</td>
      <td>${esc(hostKeyTime(k.first_seen))}</td>
      <td style="white-space:nowrap;"><button class="btn btn-outlined" data-hk-forget="${i}" title="다음 접속이 그때 받은 키를 새로 기억합니다">잊기</button></td>
    </tr>`).join('');
  return changed + trusted;
}

async function renderHostKeys() {
  const body = document.getElementById('hostkey-body');
  if (!body) return;
  const data = await call('get_host_keys') || {};
  data.trusted = data.trusted || [];
  data.changed = data.changed || [];
  const summary = document.getElementById('hostkey-summary');
  if (summary) {
    summary.textContent = data.changed.length
      ? `바뀐 키 ${data.changed.length}개 — 확인 필요 · 기억한 키 ${data.trusted.length}개`
      : `기억한 키 ${data.trusted.length}개`;
    summary.style.color = data.changed.length ? 'var(--critical)' : '';
  }
  const fold = document.getElementById('hostkey-fold');
  if (fold && data.changed.length) fold.open = true;   // 바뀐 키는 펼쳐서 보인다
  if (!data.trusted.length && !data.changed.length) {
    body.innerHTML = '<p style="font-size:12px;color:var(--sub);margin:0;">아직 기억한 키가 없습니다 — 연결 확인·터미널·점검으로 처음 접속할 때 기억합니다.</p>';
    return;
  }
  body.innerHTML = `
    <div class="table-scroll"><table class="dtable">
      <thead><tr><th>장비</th><th>주소</th><th>종류</th><th>지문</th><th>기억한 때</th><th></th></tr></thead>
      <tbody>${hostKeyRowsHtml(data)}</tbody>
    </table></div>`;
  body.querySelectorAll('[data-hk-trust]').forEach(btn => {
    btn.addEventListener('click', async () => {
      const entry = data.changed[parseInt(btn.dataset.hkTrust, 10)];
      if (!confirmHostKeyTrust(`${entry.device || entry.host} (${hostKeyAddress(entry)})`,
                               { trusted: entry.trusted_fingerprint, got: entry.fingerprint })) return;
      const result = await call('trust_host_key', entry.host, entry.port);
      if (!result || result.error) { showToast((result && result.error) || '다시 신뢰하지 못했습니다', 'error'); return; }
      showToast('새 호스트키를 기억했습니다', 'success');
      renderHostKeys();
    });
  });
  body.querySelectorAll('[data-hk-forget]').forEach(btn => {
    btn.addEventListener('click', async () => {
      const entry = data.trusted[parseInt(btn.dataset.hkForget, 10)];
      if (!confirm(`${entry.device || entry.host} (${hostKeyAddress(entry)})의 기억한 SSH 호스트키를 지울까요?\n`
                   + '다음 접속이 그때 받은 키를 새로 기억합니다 — 장비를 교체했을 때만 쓰세요.')) return;
      const result = await call('forget_host_key', entry.host, entry.port);
      if (!result || result.error) { showToast((result && result.error) || '지우지 못했습니다', 'error'); return; }
      renderHostKeys();
    });
  });
}
