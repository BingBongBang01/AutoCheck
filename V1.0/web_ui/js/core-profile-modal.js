// ===== 고객사/점검 프로파일 전환·관리 팝업 (Project 시스템을 그대로 사용 — 완전 독립 저장) =====
document.getElementById('tb-context-selector')?.addEventListener('click', openCustomerProfileModal);

async function openCustomerProfileModal() {
  let tree = await call('get_customer_profiles') || [];
  let active = await call('get_active_project');
  // 고객사별 '마지막으로 쓴 프로파일' — 정기점검은 고객사마다 진행 중인 회차가 따로라서,
  // 고객사를 고르면 지난번에 보던 회차가 바로 열려야 목록을 다시 뒤지지 않는다.
  const lastByCustomer = await call('get_last_profile_by_customer') || {};
  const overlay = document.createElement('div');
  overlay.className = 'modal-overlay';
  overlay.innerHTML = `<div class="customer-modal card"><div class="customer-modal-header"><div><p class="card-title">고객사 / 정기점검</p><p class="card-desc">고객사별 정기점검 프로파일을 관리합니다.</p></div><div class="customer-modal-tools"><button class="btn btn-outlined" id="customer-import" title="다른 PC 에서 내보낸 정기점검 zip 을 새 프로파일로 가져옵니다"><span class="material-symbols-rounded">upload_file</span>가져오기</button><button class="btn btn-outlined" id="customer-archive" title="지운(보관한) 정기점검 프로파일을 복원하거나 영구 삭제합니다"><span class="material-symbols-rounded">inventory_2</span>보관함</button><button class="btn btn-outlined" id="customer-close">닫기</button></div></div><div class="customer-modal-body"><aside class="customer-nav"><div class="customer-nav-actions"><button class="btn btn-primary" id="customer-add">고객사 추가</button></div><div id="customer-list"></div></aside><section class="customer-detail" id="customer-detail"></section></div></div>`;
  document.body.appendChild(overlay);
  // 고객사를 클릭하는 것만으로도 활성 프로파일이 바뀔 수 있다 — 닫을 때 바뀌어 있으면
  // 현재 화면을 다시 그려야 한다(프로파일마다 장비목록·이력이 완전히 다르기 때문).
  const openedWith = active;
  const closeModal = () => { overlay.remove(); if (active !== openedWith) navigate(currentPage); };
  overlay.querySelector('#customer-close').onclick = closeModal;
  // 보관함에서 복원하면 고객사 목록에 다시 나타나야 한다 — 트리를 다시 읽어 그린다.
  overlay.querySelector('#customer-archive').onclick = () => openProfileArchiveModal(async () => {
    tree = await call('get_customer_profiles') || [];
    if (!tree.some(item => item.id === selectedId)) selectedId = tree[0]?.id || null;
    render();
  });
  // 다른 PC 에서 내보낸 정기점검 zip — 비밀번호를 담아 내보냈으면 들어오는 즉시 이 PC 의 봉인으로 다시 감싼다.
  // 같은 고객사에 같은 이름이 있으면 서버가 '(가져옴)'을 붙인다(두 프로파일이 data 폴더를 함께 쓰지 않게).
  overlay.querySelector('#customer-import').onclick = async () => {
    const result = await call('import_project');
    if (!result) return;
    if (result.error) { alert(result.error); return; }
    tree = await call('get_customer_profiles') || [];
    selectedId = tree.find(item => item.profiles.some(profile => profile.id === result.project_id))?.id || selectedId;
    render();
    showToast(`'${result.customer} / ${result.profile_name}'(으)로 가져왔습니다`
      + (result.renamed ? ' — 같은 이름이 있어 이름 뒤에 (가져옴)을 붙였습니다' : '')
      + '. 비밀번호가 비어 있는 장비는 장비 목록에서 다시 입력하세요', result.renamed ? 'warn' : 'success');
  };
  overlay.onclick = e => { if (e.target === overlay) closeModal(); };
  let selectedId = tree.find(item => item.profiles.some(profile => profile.id === active))?.id || tree[0]?.id || null;
  const render = () => {
    const list = overlay.querySelector('#customer-list');
    list.innerHTML = tree.map(customer => `<button class="customer-nav-item ${customer.id === selectedId ? 'selected' : ''}" data-customer-id="${customer.id}"><span class="material-symbols-rounded">business</span><span class="customer-nav-name">${esc(customer.name)}</span><span class="customer-nav-count">${customer.profiles.length}</span><span class="material-symbols-rounded customer-more" data-customer-menu="${customer.id}">more_vert</span></button>`).join('') || '<div class="empty-state">등록된 고객사가 없습니다.</div>';
    // 고객사를 고르면 그 고객사에서 마지막으로 쓰던 프로파일로 바로 전환한다 — 목록에서 한 번 더
    // '선택'을 누르지 않아도 되게. (기억된 게 없으면 전환 없이 목록만 보여준다.)
    list.querySelectorAll('[data-customer-id]').forEach(item => item.onclick = async e => {
      if (e.target.closest('[data-customer-menu]')) return;
      selectedId = item.dataset.customerId;
      const customer = tree.find(value => value.id === selectedId);
      const lastId = lastByCustomer[selectedId];
      const last = customer?.profiles.find(p => p.id === lastId);
      if (last && last.id !== active) {
        await call('set_active_project', last.id);
        active = last.id;
        await refreshStatusBar();
        showToast(`'${customer.name} / ${last.profile_name}' — 마지막으로 사용한 프로파일을 열었습니다`);
      }
      render();
    });
    list.querySelectorAll('[data-customer-menu]').forEach(item => item.onclick = async e => { e.stopPropagation(); const customer = tree.find(value => value.id === item.dataset.customerMenu); const name = prompt('새 고객사 이름', customer.name); if (name === null) return; const result = await call('rename_customer', customer.id, name); if (result?.error) alert(result.error); else { tree = await call('get_customer_profiles') || []; render(); } });
    const customer = tree.find(item => item.id === selectedId);
    const detail = overlay.querySelector('#customer-detail');
    if (!customer) { detail.innerHTML = '<div class="empty-state">고객사를 선택하세요.</div>'; return; }
    detail.innerHTML = `<div class="customer-detail-header"><div><h2>${esc(customer.name)}</h2><p>${customer.profiles.length}개 정기점검 프로파일</p></div><div><button class="btn btn-primary" id="profile-add">정기점검 추가</button><button class="btn btn-danger" id="customer-delete">고객사 삭제</button></div></div><div class="inspection-profile-list">${customer.profiles.map(profile => `<details class="inspection-profile-card ${profile.id === active ? 'profile-current' : 'profile-idle'}"${profile.id === active ? ' open' : ''}><summary>${profile.id === active ? '<span class="profile-current-banner"><span class="material-symbols-rounded" style="font-size:14px">check_circle</span>CURRENT ACTIVE PROFILE / 현재 사용 중</span>' : ''}<span class="material-symbols-rounded">folder</span><span>${esc(profile.profile_name)}</span>${profile.id === active ? '<span class="profile-status">사용 중</span>' : ''}<span class="profile-status">${esc(profile.status)}</span>${profile.is_virtual ? '<span class="profile-virtual-badge" title="가상환경 — 전원/FAN/온도/모듈/트랜시버 점검을 해당없음으로 처리합니다"><span class="material-symbols-rounded" style="font-size:12px">cloud</span>가상환경</span>' : ''}</summary><div class="inspection-profile-content"><p>${esc(profile.description || '설명 없음')}</p><p class="profile-date">점검일: ${esc(profile.inspection_date || '미정')}</p><button class="btn btn-primary" data-profile-select="${profile.id}">선택</button><button class="btn btn-outlined" data-profile-edit="${profile.id}">수정</button><button class="btn btn-outlined" data-profile-virtual="${profile.id}" title="가상환경(vEOS-lab·EVE-NG 등) 여부. 켜면 전원/FAN/온도/모듈/트랜시버 점검이 '해당없음'이 되고 AI 분석도 가상 플랫폼 기준으로 바뀝니다"><span class="material-symbols-rounded" style="font-size:15px">${profile.is_virtual ? 'cloud_off' : 'cloud'}</span>${profile.is_virtual ? '실제 장비로 전환' : '가상환경으로 전환'}</button>${profile.id === active ? '' : `<button class="btn btn-outlined" data-profile-copy-devices="${profile.id}" title="이 프로파일의 장비목록을 지금 사용 중인 프로파일로 가져옵니다"><span class="material-symbols-rounded" style="font-size:15px">content_copy</span>장비목록 복사</button>`}<button class="btn btn-outlined" data-profile-export="${profile.id}" title="이 정기점검의 정의(프로젝트 정보·장비 목록 등)를 zip 하나로 — 다른 PC 로 옮길 때. 점검 로그·보고서는 워크스페이스 탭의 내보내기로"><span class="material-symbols-rounded" style="font-size:15px">download</span>내보내기</button><button class="btn btn-danger" data-profile-delete="${profile.id}">삭제</button></div></details>`).join('') || '<div class="empty-state">정기점검 프로파일이 없습니다.</div>'}</div>`;
    // 두 번째 프로파일부터는 서버가 직전(최신) 회차의 장비목록을 자동으로 물려준다 —
    // 복사됐으면 몇 대가 넘어왔는지 알려줘야 사용자가 장비목록 탭에서 놀라지 않는다.
    detail.querySelector('#profile-add')?.addEventListener('click', async () => {
      const name = prompt('정기점검 이름');
      if (!name) return;
      // 가상환경 회차(vEOS-lab·EVE-NG 등)는 PSU/FAN/온도센서/모듈이 아예 없어서 그 점검 항목이
      // 매 회차 오탐으로 올라온다 — 만들 때 물어보고 프로파일에 기록해 둔다(나중에 카드에서 전환 가능).
      const isVirtual = confirm('가상환경(vEOS-lab·EVE-NG 등) 점검 회차입니까?\n\n확인 = 가상환경 (전원/FAN/온도/모듈/트랜시버를 해당없음으로 처리)\n취소 = 실제 장비');
      const result = await call('create_inspection_profile', customer.id, name, '', '', isVirtual);
      if (!result || result.error) { alert(result?.error || '만들지 못했습니다.'); return; }
      tree = await call('get_customer_profiles') || [];
      render();
      await refreshStatusBar();
      if (result.reused_data) showToast(`'${name}' 이름의 이전 기록(data 폴더)이 있어 이어서 씁니다`, 'warn');
      else if (result.copied_from) showToast(`'${result.copied_from}'의 장비 ${result.copied_count}대를 그대로 가져왔습니다`);
      else showToast('정기점검 프로파일을 만들었습니다 — 장비 목록 탭에서 장비를 등록하세요');
    });
    detail.querySelector('#customer-delete')?.addEventListener('click', async () => { if (!confirm(`고객사 '${customer.name}'와 모든 정기점검을 보관합니다.\n보관본은 data 폴더의 _archived_profiles 에 남아 복원하거나 영구 삭제할 수 있습니다. 계속할까요?`)) return; const result = await call('delete_customer', customer.id); if (result?.error) alert(result.error); else { tree = await call('get_customer_profiles') || []; selectedId = tree[0]?.id || null; render(); await refreshStatusBar(); } });
    detail.querySelectorAll('[data-profile-select]').forEach(button => button.onclick = async () => { await call('set_active_project', button.dataset.profileSelect); overlay.remove(); await refreshStatusBar(); navigate(currentPage); });
    detail.querySelectorAll('[data-profile-edit]').forEach(button => button.onclick = async () => { const profile = customer.profiles.find(item => item.id === button.dataset.profileEdit); const name = prompt('정기점검 이름', profile.profile_name); if (!name) return; const result = await call('rename_inspection_profile', profile.id, name, profile.description, profile.inspection_date); if (result?.error) alert(result.error); else { tree = await call('get_customer_profiles') || []; render(); } });
    // 가상환경 전환 — 이미 만든 회차도 나중에 바꿀 수 있어야 한다(실습망에서 실기로, 또는 그 반대).
    detail.querySelectorAll('[data-profile-virtual]').forEach(button => button.onclick = async () => {
      const profile = customer.profiles.find(item => item.id === button.dataset.profileVirtual);
      const next = !profile.is_virtual;
      const result = await call('set_inspection_profile_virtual', profile.id, next);
      if (!result || result.error) { alert(result?.error || '변경하지 못했습니다.'); return; }
      tree = await call('get_customer_profiles') || [];
      render();
      showToast(next
        ? `'${profile.profile_name}' — 가상환경으로 전환했습니다. 전원/FAN/온도/모듈/트랜시버는 '해당없음'으로 보고됩니다`
        : `'${profile.profile_name}' — 실제 장비 기준으로 전환했습니다`);
    });
    detail.querySelectorAll('[data-profile-delete]').forEach(button => button.onclick = async () => { if (!confirm('이 정기점검 프로파일을 보관할까요? (목록에서 빠지고, 보관본에서 복원할 수 있습니다)')) return; const result = await call('delete_inspection_profile', button.dataset.profileDelete); if (result?.error) alert(result.error); tree = await call('get_customer_profiles') || []; render(); });
    // 내보내기 — 기본(취소·Esc)은 비밀번호를 비운다. 담으면 zip 안이 평문이라 묻고, 결과에도 경고를 띄운다.
    detail.querySelectorAll('[data-profile-export]').forEach(button => button.onclick = async () => {
      const include = confirm('장비 비밀번호·개인키도 함께 내보낼까요?\n\n'
        + '[확인] 포함 — zip 안에 평문으로 들어갑니다(다른 PC 로 옮길 때만, 전달·보관에 주의).\n'
        + '[취소] 비우고 내보냅니다(가져간 PC 에서 다시 입력).');
      const result = await call('export_project', button.dataset.profileExport, include);
      if (!result) return;
      if (result.error) { alert(result.error); return; }
      showToast(result.warning || result.notice || '내보냈습니다', result.warning ? 'warn' : 'success');
    });
    // 장비목록 복사 — 이 프로파일(원본)의 장비를 "지금 사용 중인" 프로파일로 가져온다.
    // 이름이 같은 장비는 건너뛰므로 현재 프로파일의 데이터가 사라지지 않는다.
    detail.querySelectorAll('[data-profile-copy-devices]').forEach(button => button.onclick = async () => {
      const profile = customer.profiles.find(item => item.id === button.dataset.profileCopyDevices);
      if (!confirm(`'${profile.profile_name}'의 장비목록을 지금 사용 중인 프로파일로 가져옵니다.\n이름이 같은 장비는 건너뜁니다. 계속할까요?`)) return;
      const result = await call('copy_devices_from_profile', profile.id, false);
      if (!result || result.error) { alert(result?.error || '복사하지 못했습니다.'); return; }
      showToast(`장비 ${result.added}대 복사됨${result.skipped ? ` · 이름 중복 ${result.skipped}대 건너뜀` : ''}`,
                result.added ? 'success' : 'warn');
      if (currentPage === 'inventory') navigate('inventory');
    });
  };
  overlay.querySelector('#customer-add').onclick = async () => { const name = prompt('고객사 이름'); if (!name) return; const result = await call('create_customer', name); if (result?.error) alert(result.error); else { tree = await call('get_customer_profiles') || []; selectedId = tree.at(-1)?.id || null; render(); } };
  render();
}
