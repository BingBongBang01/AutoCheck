// ===== 우클릭 메뉴 / 바로 붙여넣기 =====
function closeTermCtxMenu() {
  document.querySelectorAll('.term-ctx-menu').forEach(el => el.remove());
  document.removeEventListener('click', closeTermCtxMenu);
}

async function onTermContextMenu(e, session, term) {
  e.preventDefault();
  if (termCtxMenuMode === 'paste') {
    try {
      const text = await navigator.clipboard.readText();
      if (text) await call('send_terminal_input', session.session_id, text + '\r');
    } catch (err) { /* 클립보드 접근 거부 — 조용히 무시 */ }
    return;
  }
  closeTermCtxMenu();
  const hasSelection = term.hasSelection();
  const menu = document.createElement('div');
  menu.className = 'term-ctx-menu';
  menu.style.left = e.clientX + 'px';
  menu.style.top = e.clientY + 'px';
  const items = [
    { label: '복사', icon: 'content_copy', disabled: !hasSelection, action: async () => {
      await navigator.clipboard.writeText(term.getSelection());
    } },
    { label: '잘라내기', icon: 'content_cut', disabled: !hasSelection, action: async () => {
      await navigator.clipboard.writeText(term.getSelection());
    } },
    { label: '붙여넣기', icon: 'content_paste', disabled: false, action: async () => {
      const text = await navigator.clipboard.readText();
      if (text) await call('send_terminal_input', session.session_id, text);
    } },
    { sep: true },
    { label: '전체 선택', icon: 'select_all', disabled: false, action: () => term.selectAll() },
    { label: '실행 취소', icon: 'undo', disabled: true, action: () => {} }, // 원격 셸은 undo 개념이 없음 — 항상 비활성
  ];
  menu.innerHTML = items.map((it, i) => it.sep
    ? '<div class="term-ctx-menu-sep"></div>'
    : `<div class="term-ctx-menu-item ${it.disabled ? 'disabled' : ''}" data-idx="${i}"><span class="material-symbols-rounded" style="font-size:16px;">${it.icon}</span>${it.label}</div>`
  ).join('');
  menu.querySelectorAll('[data-idx]').forEach(el => {
    const it = items[parseInt(el.dataset.idx, 10)];
    if (it.disabled) return;
    el.addEventListener('click', async (ev) => {
      ev.stopPropagation();
      closeTermCtxMenu();
      await it.action();
    });
  });
  document.body.appendChild(menu);
  const rect = menu.getBoundingClientRect();
  if (rect.right > window.innerWidth) menu.style.left = (window.innerWidth - rect.width - 8) + 'px';
  if (rect.bottom > window.innerHeight) menu.style.top = (window.innerHeight - rect.height - 8) + 'px';
  setTimeout(() => document.addEventListener('click', closeTermCtxMenu), 0);
}
