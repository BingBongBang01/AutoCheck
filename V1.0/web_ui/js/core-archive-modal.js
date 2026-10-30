// ===== 보관함 (IMPROVEMENT_PLAN 4-10 — 2-5 의 보관을 되돌리는 화면) =====
// 정기점검 프로파일을 지우면(고객사 트리·프로젝트 모달·워크스페이스 '보관') 지워지지 않고 보관된다
// (engine/profile_archive.py — data/<고객사>/_archived_profiles/). 여기서 보관본을 보고 복원하거나 영구 삭제한다.
// 영구 삭제는 되돌릴 수 없으므로 프로파일 이름을 입력해야 지운다. 복원은 같은 이름·같은 id 의 프로파일이 있으면
// 서버가 거절한다(다른 프로파일과 섞이지 않게) — 그 이유를 그대로 보여 준다.
async function openProfileArchiveModal(onChanged) {
  const { overlay, close } = openModal({ id: 'archive-modal', html: `
    <div class="card archive-modal">
      <div class="rt-alert-modal-head">
        <div>
          <h3 class="card-title">보관함</h3>
          <p class="card-desc">지운 정기점검 프로파일은 여기 보관됩니다. 복원하면 고객사 목록에 다시 나타납니다.</p>
        </div>
        <button class="btn btn-outlined" type="button" data-modal-close>닫기</button>
      </div>
      <div class="archive-body" id="archive-body"><div class="empty-state">불러오는 중…</div></div>
    </div>` });

  const refresh = async () => {
    const archives = await call('list_archived_profiles') || [];
    const body = overlay.querySelector('#archive-body');
    if (!body) return;
    if (!archives.length) {
      body.innerHTML = '<div class="empty-state">보관된 프로파일이 없습니다.</div>';
      return;
    }
    body.innerHTML = `<table class="dtable"><thead><tr>
        <th>고객사</th><th>정기점검</th><th>보관 시각</th><th></th>
      </tr></thead><tbody>${archives.map((a, i) => `
        <tr>
          <td>${esc(a.customer_name || '—')}</td>
          <td><strong>${esc(a.profile_name || a.archive)}</strong>${a.restorable ? ''
            : '<div class="archive-note">예전 보관본 — 복원 정보가 없어 폴더를 직접 옮겨야 합니다</div>'}</td>
          <td class="archive-when">${esc(archiveTime(a.archived_at))}</td>
          <td class="archive-actions">
            <button class="btn btn-outlined rtm-mini" type="button" data-archive-restore="${i}"
                    ${a.restorable ? '' : 'disabled'}>복원</button>
            <button class="btn btn-danger rtm-mini" type="button" data-archive-purge="${i}">영구 삭제</button>
          </td>
        </tr>`).join('')}</tbody></table>`;
    body.querySelectorAll('[data-archive-restore]').forEach(btn => btn.addEventListener('click', async () => {
      const a = archives[Number(btn.dataset.archiveRestore)];
      btn.disabled = true;
      const result = await call('restore_archived_profile', a.archive, a.customer_name);
      if (!result || result.error) {
        showToast((result && result.error) || '복원하지 못했습니다.', 'error');
        btn.disabled = false;
        return;
      }
      showToast(`'${a.profile_name}' 프로파일을 복원했습니다.`);
      if (onChanged) await onChanged();
      await refresh();
    }));
    body.querySelectorAll('[data-archive-purge]').forEach(btn => btn.addEventListener('click', async () => {
      const a = archives[Number(btn.dataset.archivePurge)];
      const name = a.profile_name || a.archive;
      const typed = prompt(`'${name}' 보관본을 영구 삭제합니다 — 점검 로그·보고서까지 모두 지워지고 되돌릴 수 없습니다.\n`
        + '지우려면 정기점검 이름을 그대로 입력하세요.');
      if (typed === null) return;
      if (typed.trim() !== name) { showToast('이름이 달라 지우지 않았습니다.', 'warn'); return; }
      btn.disabled = true;
      const result = await call('purge_archived_profile', a.archive, a.customer_name);
      if (!result || result.error) {
        showToast((result && result.error) || '지우지 못했습니다.', 'error');
        btn.disabled = false;
        return;
      }
      showToast(`'${name}' 보관본을 영구 삭제했습니다.`);
      await refresh();
    }));
  };
  await refresh();
  return close;
}

// 보관 시각(ISO) — 'YYYY-MM-DD HH:MM'. 없으면 대시.
function archiveTime(stamp) {
  if (!stamp) return '—';
  return String(stamp).replace('T', ' ').slice(0, 16);
}
