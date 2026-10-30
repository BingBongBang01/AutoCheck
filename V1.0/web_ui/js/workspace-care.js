// ===== 작업공간 점검·백업 (IMPROVEMENT_PLAN 4-10) =====
// 무결성 점검: 데이터 폴더가 앱이 기대하는 모양인지 훑는다(engine/workspace_check.py) — 목록에 없는 고객사 폴더, 연결되지
// 않은 점검 데이터, 깨진 설정·상태 파일, 이 PC 에서 풀 수 없는 봉인 비밀번호, 남은 임시 파일. 고치지는 않고 무엇을 하면
// 되는지를 적는다. 백업: 설정과 프로파일 정의를 하루 한 번 자동으로 zip 으로 남긴다(engine/workspace_backup.py — 최근 10개).
const WS_CARE_LEVEL = { error: ['오류', 'fail'], warn: ['확인', 'warn'], info: ['정리', 'unknown'] };

function wsCareMarkup() {
  return `
    <div class="card hoverable section-gap" id="ws-care-card">
      <div class="card-header">
        <div class="card-icon"><span class="material-symbols-rounded">health_and_safety</span></div>
        <div><p class="card-title">작업공간 점검·백업</p><p class="card-desc">데이터 폴더가 앱이 기대하는 모양인지 점검합니다(고치지는 않습니다).
          설정·장비 목록·명령 카탈로그·보고서 기록은 하루 한 번 자동으로 백업됩니다(최근 10개 — 점검 로그·보고서 파일은 담지 않습니다).</p></div>
      </div>
      <div class="ws-care-actions">
        <button class="btn btn-outlined" type="button" id="ws-care-check"><span class="material-symbols-rounded">fact_check</span>무결성 점검</button>
        <button class="btn btn-outlined" type="button" id="ws-care-backup"><span class="material-symbols-rounded">backup</span>지금 백업</button>
        <button class="btn btn-outlined" type="button" id="ws-care-folder"><span class="material-symbols-rounded">folder_open</span>백업 폴더 열기</button>
        <span class="ws-care-last" id="ws-care-last"></span>
      </div>
      <div id="ws-care-result"></div>
    </div>`;
}

async function wsInitCare() {
  document.getElementById('ws-care-check')?.addEventListener('click', wsCareCheck);
  document.getElementById('ws-care-backup')?.addEventListener('click', wsCareBackup);
  document.getElementById('ws-care-folder')?.addEventListener('click', async () => {
    const result = await call('open_workspace_backup_folder');
    if (result && result.error) showToast(result.error, 'error');
  });
  await wsCareRefreshBackups();
}

async function wsCareRefreshBackups() {
  const el = document.getElementById('ws-care-last');
  if (!el) return;
  const result = await call('list_workspace_backups');
  const latest = result && result.backups && result.backups[0];
  el.textContent = latest
    ? `마지막 백업 ${String(latest.created_at || '').replace('T', ' ').slice(0, 16)}(${latest.reason === 'auto' ? '자동' : latest.reason === 'manual' ? '수동' : latest.reason || '?'}) · 파일 ${latest.files ?? '?'}개 · ${(latest.size / 1024).toFixed(0)} KB · 보관 ${result.backups.length}/${result.keep}`
    : '아직 백업이 없습니다 — 앱을 켤 때 하루 한 번 자동으로 만듭니다.';
}

async function wsCareBackup(e) {
  const btn = e.currentTarget;
  btn.classList.add('loading');
  const result = await call('create_workspace_backup');
  btn.classList.remove('loading');
  if (!result) return;
  if (result.error) { showToast(result.error, 'error'); return; }
  showToast(`백업했습니다 — ${result.name} (파일 ${result.files}개)`);
  await wsCareRefreshBackups();
}

async function wsCareCheck(e) {
  const btn = e.currentTarget;
  btn.classList.add('loading');
  const result = await call('check_workspace_integrity');
  btn.classList.remove('loading');
  const box = document.getElementById('ws-care-result');
  if (!result || !box) return;
  if (result.error) { showToast(result.error, 'error'); return; }
  const c = result.counts || {};
  const findings = result.findings || [];
  box.innerHTML = `<p class="ws-care-summary">${esc(result.checked_at)} 점검 — ${findings.length
      ? `오류 ${c.error || 0} · 확인 ${c.warn || 0} · 정리 ${c.info || 0}` : '어긋난 곳이 없습니다.'}</p>
    ${findings.length ? `<div class="ws-care-list">${findings.map(f => {
      const [label, tone] = WS_CARE_LEVEL[f.level] || ['?', 'unknown'];
      return `<div class="ws-care-row">
        <span class="rtm-chip rtm-chip-${tone}">${label}</span>
        <div class="ws-care-body"><div><b>${esc(f.area)}</b> ${esc(f.message)}</div>
          <div class="mono ws-care-path">${esc(f.path)}</div>
          ${f.hint ? `<div class="ws-care-hint">${esc(f.hint)}</div>` : ''}</div>
      </div>`;
    }).join('')}</div>` : ''}`;
}
