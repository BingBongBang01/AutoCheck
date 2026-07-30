// ===== 사이드바 네비게이션 + StatusBar =====
let currentPage = 'dashboard';

function setActiveNav(page) {
  document.querySelectorAll('.nav-item[data-page]').forEach(el => {
    el.classList.toggle('active', el.dataset.page === page);
  });
}

document.querySelectorAll('.nav-item[data-page]').forEach(el => {
  el.addEventListener('click', () => navigate(el.dataset.page));
});

document.getElementById('nav-collapse').addEventListener('click', () => {
  document.getElementById('sidebar').classList.toggle('collapsed');
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
