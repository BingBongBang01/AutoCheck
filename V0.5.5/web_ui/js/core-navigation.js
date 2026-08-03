// ===== 사이드바 네비게이션 + StatusBar =====
let currentPage = 'dashboard';

function setActiveNav(page) {
  document.querySelectorAll('.nav-item[data-page]').forEach(el => {
    el.classList.toggle('active', el.dataset.page === page);
  });
}

document.querySelectorAll('.nav-item[data-page]').forEach(el => {
  el.addEventListener('click', () => navigate(el.dataset.page));
  // 접힌 상태에서는 아이콘만 남는다 — 탭이 16개라 아이콘만으로는 못 찾으므로 툴팁을 붙인다.
  const label = el.querySelector('.nav-label');
  if (label && !el.title) el.title = label.textContent.trim();
});

// 접힘 상태는 #app에 건다 — #sidebar에만 걸면 그리드 열이 200px로 남아 본문이 안 넓어진다.
// 화면 공간이 부족하다는 요구로 v0.5.4부터 '접힌 상태'가 기본이고, 선택은 localStorage에 남는다.
// (접혀 있어도 사이드바에 마우스를 올리면 본문을 밀지 않고 겹쳐서 펼쳐진다 — style.css 참조)
const SIDEBAR_KEY = 'autocheck.sidebarCollapsed';

function applySidebarCollapsed(collapsed) {
  document.getElementById('app').classList.toggle('sidebar-collapsed', collapsed);
  const icon = document.querySelector('#nav-collapse .material-symbols-rounded');
  const label = document.querySelector('#nav-collapse .nav-label');
  if (icon) icon.textContent = collapsed ? 'chevron_right' : 'chevron_left';
  if (label) label.textContent = collapsed ? '펼치기' : '접기';
}

applySidebarCollapsed(localStorage.getItem(SIDEBAR_KEY) !== '0');

document.getElementById('nav-collapse').addEventListener('click', () => {
  const collapsed = !document.getElementById('app').classList.contains('sidebar-collapsed');
  localStorage.setItem(SIDEBAR_KEY, collapsed ? '1' : '0');
  applySidebarCollapsed(collapsed);
});

async function navigate(page) {
  currentPage = page;
  setActiveNav(page);
  const content = document.getElementById('content');
  content.style.opacity = 0;
  await new Promise(r => setTimeout(r, 120));

  const renderers = {
    dashboard: renderDashboard,
    inventory: renderInventory,
    connection: renderConnection,
    catalog: renderCatalog,
    // collection은 사이드바에 항목이 없다 — 채점 실행(run_grade) 화면이라 남겨 둔 라우터 키다.
    collection: renderCollection,
    realtimewatch: renderRealtimeWatch,
    inspectionlog: renderInspectionLog,
    loganalysis: renderLogAnalysis,
    logmasking: renderLogMasking,
    report: renderInspectionReport,
    workspace: renderWorkspace,
    knowledge: renderKnowledge,
    logs: renderLogs,
    settings: renderSettings,
  };
  await (renderers[page] || renderers.dashboard)();
  content.style.opacity = 1;
}

// ===== StatusBar clock =====
function tickClock() {
  document.getElementById('sb-clock').textContent = new Date().toLocaleString('ko-KR');
}
setInterval(tickClock, 1000);
tickClock();

async function refreshStatusBar() {
  // 5개 브리지 호출은 서로 의존하지 않는다 — 예전엔 하나씩 await해서 시작할 때
  // 왕복 5번을 순서대로 기다렸다. 한 번에 던지고 같이 기다린다.
  const [version, project, projectsRaw, contextRaw, treeRaw] = await Promise.all([
    call('get_app_version'),
    call('get_active_project'),
    call('list_projects'),
    call('get_customer_context'),
    call('get_customer_profiles'),
  ]);
  const projects = projectsRaw || [];
  document.getElementById('sb-version').textContent = 'v' + (version || '-');
  const meta = projects.find(p => p.id === project);
  const label = meta ? meta.display_name : (project || '프로파일 없음');
  document.getElementById('sb-project').textContent = '프로파일: ' + label;
  const context = contextRaw || {};
  const tree = treeRaw || [];
  const customer = tree.find(item => item.id === tree.find(value => value.profiles.some(profile => profile.id === context.profile))?.id);
  const profile = customer?.profiles.find(item => item.id === context.profile);
  document.getElementById('tb-customer-name').textContent = context.customer || customer?.name || '고객사 없음';
  document.getElementById('tb-profile-name').textContent = profile?.profile_name || label || '정기점검 없음';
}
