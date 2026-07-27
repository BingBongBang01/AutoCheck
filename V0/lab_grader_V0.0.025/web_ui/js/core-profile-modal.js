// ===== 고객사/점검 프로파일 전환·관리 팝업 (Project 시스템을 그대로 사용 — 완전 독립 저장) =====
document.getElementById('tb-context-selector')?.addEventListener('click', openCustomerProfileModal);

async function openCustomerProfileModal() {
  let tree = await call('get_customer_profiles') || [];
  const active = await call('get_active_project');
  const overlay = document.createElement('div');
  overlay.className = 'modal-overlay';
  overlay.innerHTML = `<div class="customer-modal card"><div class="customer-modal-header"><div><p class="card-title">고객사 / 정기점검</p><p class="card-desc">고객사별 정기점검 프로파일을 관리합니다.</p></div><button class="btn btn-outlined" id="customer-close">닫기</button></div><div class="customer-modal-body"><aside class="customer-nav"><div class="customer-nav-actions"><button class="btn btn-primary" id="customer-add">고객사 추가</button></div><div id="customer-list"></div></aside><section class="customer-detail" id="customer-detail"></section></div></div>`;
  document.body.appendChild(overlay);
  overlay.querySelector('#customer-close').onclick = () => overlay.remove();
  overlay.onclick = e => { if (e.target === overlay) overlay.remove(); };
  let selectedId = tree.find(item => item.profiles.some(profile => profile.id === active))?.id || tree[0]?.id || null;
  const render = () => {
    const list = overlay.querySelector('#customer-list');
    list.innerHTML = tree.map(customer => `<button class="customer-nav-item ${customer.id === selectedId ? 'selected' : ''}" data-customer-id="${customer.id}"><span class="material-symbols-rounded">business</span><span class="customer-nav-name">${customer.name}</span><span class="customer-nav-count">${customer.profiles.length}</span><span class="material-symbols-rounded customer-more" data-customer-menu="${customer.id}">more_vert</span></button>`).join('') || '<div class="empty-state">등록된 고객사가 없습니다.</div>';
    list.querySelectorAll('[data-customer-id]').forEach(item => item.onclick = e => { if (e.target.closest('[data-customer-menu]')) return; selectedId = item.dataset.customerId; render(); });
    list.querySelectorAll('[data-customer-menu]').forEach(item => item.onclick = async e => { e.stopPropagation(); const customer = tree.find(value => value.id === item.dataset.customerMenu); const name = prompt('새 고객사 이름', customer.name); if (name === null) return; const result = await call('rename_customer', customer.id, name); if (result?.error) alert(result.error); else { tree = await call('get_customer_profiles') || []; render(); } });
    const customer = tree.find(item => item.id === selectedId);
    const detail = overlay.querySelector('#customer-detail');
    if (!customer) { detail.innerHTML = '<div class="empty-state">고객사를 선택하세요.</div>'; return; }
    detail.innerHTML = `<div class="customer-detail-header"><div><h2>${customer.name}</h2><p>${customer.profiles.length}개 정기점검 프로파일</p></div><div><button class="btn btn-primary" id="profile-add">정기점검 추가</button><button class="btn btn-danger" id="customer-delete">고객사 삭제</button></div></div><div class="inspection-profile-list">${customer.profiles.map(profile => `<details class="inspection-profile-card"><summary><span class="material-symbols-rounded">folder</span><span>${profile.profile_name}</span><span class="profile-status">${profile.status}</span></summary><div class="inspection-profile-content"><p>${profile.description || '설명 없음'}</p><p class="profile-date">점검일: ${profile.inspection_date || '미정'}</p><button class="btn btn-primary" data-profile-select="${profile.id}">선택</button><button class="btn btn-outlined" data-profile-edit="${profile.id}">수정</button><button class="btn btn-danger" data-profile-delete="${profile.id}">삭제</button></div></details>`).join('') || '<div class="empty-state">정기점검 프로파일이 없습니다.</div>'}</div>`;
    detail.querySelector('#profile-add')?.addEventListener('click', async () => { const name = prompt('정기점검 이름'); if (!name) return; const result = await call('create_inspection_profile', customer.id, name, '', ''); if (result?.error) alert(result.error); else { tree = await call('get_customer_profiles') || []; render(); } });
    detail.querySelector('#customer-delete')?.addEventListener('click', async () => { if (!confirm(`고객사 '${customer.name}'와 모든 정기점검을 함께 삭제합니다. 계속할까요?`)) return; const result = await call('delete_customer', customer.id); if (result?.error) alert(result.error); else { tree = await call('get_customer_profiles') || []; selectedId = tree[0]?.id || null; render(); await refreshStatusBar(); } });
    detail.querySelectorAll('[data-profile-select]').forEach(button => button.onclick = async () => { await call('set_active_project', button.dataset.profileSelect); overlay.remove(); await refreshStatusBar(); navigate(currentPage); });
    detail.querySelectorAll('[data-profile-edit]').forEach(button => button.onclick = async () => { const profile = customer.profiles.find(item => item.id === button.dataset.profileEdit); const name = prompt('정기점검 이름', profile.profile_name); if (!name) return; const result = await call('rename_inspection_profile', profile.id, name, profile.description, profile.inspection_date); if (result?.error) alert(result.error); else { tree = await call('get_customer_profiles') || []; render(); } });
    detail.querySelectorAll('[data-profile-delete]').forEach(button => button.onclick = async () => { if (!confirm('이 정기점검 프로파일을 삭제할까요?')) return; await call('delete_inspection_profile', button.dataset.profileDelete); tree = await call('get_customer_profiles') || []; render(); });
  };
  overlay.querySelector('#customer-add').onclick = async () => { const name = prompt('고객사 이름'); if (!name) return; const result = await call('create_customer', name); if (result?.error) alert(result.error); else { tree = await call('get_customer_profiles') || []; selectedId = tree.at(-1)?.id || null; render(); } };
  render();
}

async function openProfileModal() {
  const tree = await call('list_customer_tree') || [];
  const projects = tree.flatMap(customer => customer.profiles);
  const active = await call('get_active_project');

  const overlay = document.createElement('div');
  overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.5);display:flex;align-items:center;justify-content:center;z-index:1000;';
  overlay.innerHTML = `
    <div class="card" style="width:520px;max-width:92vw;max-height:80vh;overflow-y:auto;">
      <div class="card-header">
        <div class="card-icon"><span class="material-symbols-rounded">business</span></div>
        <div><p class="card-title">고객사 / 점검 프로파일</p><p class="card-desc">프로파일마다 장비목록·점검기준·커맨드카탈로그·이력이 완전히 독립적으로 저장됩니다.</p></div>
      </div>
      <div id="profile-list" style="display:flex;flex-direction:column;gap:10px;margin-bottom:14px;"></div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:12px;">
        <select class="field" id="customer-select"></select>
        <select class="field" id="profile-select"></select>
      </div>
      <div style="display:flex;gap:8px;margin-bottom:10px;">
        <input class="field" id="customer-new-name" placeholder="고객사">
        <input class="field" id="profile-new-name" placeholder="점검 프로파일 (예: 26년07월 정기점검)">
        <button class="btn btn-primary" id="btn-profile-add"><span class="material-symbols-rounded">add</span>추가</button>
      </div>
      <div style="display:flex;gap:8px;justify-content:flex-end;">
        <button class="btn btn-outlined" id="btn-profile-import"><span class="material-symbols-rounded">upload_file</span>불러오기(zip)</button>
        <button class="btn btn-outlined" id="btn-profile-close">닫기</button>
      </div>
    </div>
  `;
  document.body.appendChild(overlay);
  overlay.addEventListener('click', (e) => { if (e.target === overlay) overlay.remove(); });
  overlay.querySelector('#btn-profile-close').addEventListener('click', () => overlay.remove());

  const customerSelect = overlay.querySelector('#customer-select');
  const profileSelect = overlay.querySelector('#profile-select');
  customerSelect.innerHTML = tree.map(c => `<option value="${c.name}">${c.name}</option>`).join('');
  function renderProfileSelect() {
    const customer = tree.find(c => c.name === customerSelect.value);
    profileSelect.innerHTML = (customer ? customer.profiles : []).map(p => `<option value="${p.id}">${p.profile_name}</option>`).join('');
  }
  customerSelect.addEventListener('change', renderProfileSelect);
  profileSelect.addEventListener('change', async () => {
    if (!profileSelect.value) return;
    await call('set_customer_context', customerSelect.value, profileSelect.value);
    overlay.remove(); await refreshStatusBar(); navigate(currentPage);
  });
  const context = await call('get_customer_context');
  customerSelect.value = context.customer || (tree[0] && tree[0].name) || '';
  renderProfileSelect();
  profileSelect.value = context.profile || '';

  function renderList() {
    const listEl = overlay.querySelector('#profile-list');
    listEl.innerHTML = tree.map(customer => `<div class="card" style="padding:10px 12px;"><div style="font-weight:700;margin-bottom:6px;"><span class="material-symbols-rounded" style="vertical-align:middle">business</span> ${customer.name}</div>${customer.profiles.map(p => `
      <div style="display:flex;align-items:center;gap:8px;background:var(--hover);border-radius:var(--radius-sm);padding:8px 12px;${p.id === active ? 'border:1px solid var(--primary);' : ''}">
        <span class="material-symbols-rounded" style="color:${p.id === active ? 'var(--primary)' : 'var(--sub)'};font-size:18px;">folder</span>
        <div style="flex:1;cursor:pointer;" data-select="${p.id}">
          <div style="font-size:13px;font-weight:600;">${p.profile_name}${p.id === active ? ' (사용 중)' : ''}</div>
          <div style="font-size:11px;color:var(--sub);">${p.created_at || ''}</div>
        </div>
        <button class="btn btn-outlined" style="height:28px;padding:2px 8px;" data-rename="${p.id}" title="이름 변경"><span class="material-symbols-rounded" style="font-size:15px">edit</span></button>
        <button class="btn btn-outlined" style="height:28px;padding:2px 8px;" data-export="${p.id}" title="내보내기(zip)"><span class="material-symbols-rounded" style="font-size:15px">download</span></button>
        <button class="btn btn-danger" style="height:28px;padding:2px 8px;" data-delete="${p.id}" title="삭제"><span class="material-symbols-rounded" style="font-size:15px">delete</span></button>
      </div>`).join('')}</div>`).join('') || `<p style="font-size:12px;color:var(--sub);">등록된 고객사가 없습니다.</p>`;

    listEl.querySelectorAll('[data-select]').forEach(el => {
      el.addEventListener('click', async () => {
        await call('set_active_project', el.dataset.select);
        overlay.remove();
        await refreshStatusBar();
        navigate(currentPage);
      });
    });
    listEl.querySelectorAll('[data-rename]').forEach(btn => {
      btn.addEventListener('click', async (e) => {
        e.stopPropagation();
        const proj = projects.find(p => p.id === btn.dataset.rename);
        const newName = prompt('새 프로파일 이름:', proj.display_name);
        if (!newName || !newName.trim()) return;
        await call('rename_project', proj.id, newName.trim());
        proj.display_name = newName.trim();
        renderList();
        if (proj.id === active) refreshStatusBar();
      });
    });
    listEl.querySelectorAll('[data-export]').forEach(btn => {
      btn.addEventListener('click', async (e) => {
        e.stopPropagation();
        const result = await call('export_project', btn.dataset.export);
        if (result && result.error) alert(result.error);
        else if (result) flashSaved(true);
      });
    });
    listEl.querySelectorAll('[data-delete]').forEach(btn => {
      btn.addEventListener('click', async (e) => {
        e.stopPropagation();
        const proj = projects.find(p => p.id === btn.dataset.delete);
        if (!confirm(`'${proj.display_name}' 프로파일을 완전히 삭제합니다. 되돌릴 수 없습니다. 계속할까요?`)) return;
        await call('delete_project', proj.id);
        overlay.remove();
        await refreshStatusBar();
        navigate(currentPage);
      });
    });
  }
  renderList();

  overlay.querySelector('#btn-profile-add').addEventListener('click', async () => {
    const customer = overlay.querySelector('#customer-new-name').value.trim();
    const name = overlay.querySelector('#profile-new-name').value.trim();
    if (!customer || !name) return;
    await call('create_customer_profile', customer, name);
    overlay.remove();
    await refreshStatusBar();
    navigate(currentPage);
  });

  overlay.querySelector('#btn-profile-import').addEventListener('click', async () => {
    const result = await call('import_project');
    if (result && result.error) { alert(result.error); return; }
    if (result) {
      await call('set_active_project', result.project_id);
      overlay.remove();
      await refreshStatusBar();
      navigate(currentPage);
    }
  });
}
