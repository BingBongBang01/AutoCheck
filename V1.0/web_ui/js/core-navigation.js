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

// ===== 화면 수명주기 =====
// 화면은 그릴 때(mount) '떠날 때 정리할 것'을 onPageLeave() 로 맡겨 둔다 — 인터벌, document/window 전역
// 리스너처럼 화면 DOM 이 사라져도 남는 것들이다. 다른 화면으로 넘어가거나 같은 화면을 다시 그리기 직전에
// renderPage 가 leaveCurrentPage() 로 한 번에 정리한다(unmount). 예전엔 화면마다 '다음 tick 에 currentPage 를
// 보고 스스로 멈추기'를 따로 구현했고, 빠뜨린 곳(전체 로그 탭의 인터벌, 장비 목록·구성도의 전역 리스너)은
// 탭을 오갈 때마다 남거나 쌓였다(IMPROVEMENT_PLAN 3-7).
// 세션 터미널 출력·세션 점검 진행처럼 탭을 옮겨도 계속 돌아야 하는 폴링은 여기에 맡기지 않는다.
let pageLeaveCallbacks = [];

function onPageLeave(callback) {
  pageLeaveCallbacks.push(callback);
}

function leaveCurrentPage() {
  const callbacks = pageLeaveCallbacks;
  pageLeaveCallbacks = [];
  for (const callback of callbacks) {
    try {
      callback();
    } catch (err) {
      console.error('[AutoCheck] 화면 정리 실패:', err);
    }
  }
}

// 화면 전환은 한 번에 하나씩 그린다. 예전엔 앞 화면의 렌더(서버 응답 대기 중)가 뒤에 누른 화면을
// 늦게 덮어써서, 사이드바는 B 인데 내용은 A 인 상태가 생겼다. 기다리는 동안 또 누르면 마지막 것만 그린다.
let navigationTarget = null;
let navigationChain = Promise.resolve();

function navigate(page) {
  navigationTarget = page;
  navigationChain = navigationChain.then(() => {
    if (navigationTarget !== page) return undefined;    // 그 사이 다른 화면을 눌렀다
    return renderPage(page);
  }).catch((err) => {
    console.error(`[AutoCheck] '${page}' 화면을 그리지 못했습니다:`, err);
    reportClientError(err && (err.stack || err.message), `navigate:${page}`, 0);
  });
  return navigationChain;
}

async function renderPage(page) {
  leaveCurrentPage();
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
    realtimewatch: renderRealtimeWatch,
    topology: renderTopology,
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
  // allSettled를 쓰는 이유: Promise.all이면 5개 중 하나만 예외를 던져도 전체가 reject되어
  // 상태바·상단바가 통째로 초기 HTML('v-', '고객사 없음')에 남는다. 하나가 실패해도
  // 나머지는 표시돼야 한다.
  const settled = await Promise.allSettled([
    call('get_app_version'),
    call('get_active_project'),
    call('list_projects'),
    call('get_customer_context'),
    call('get_customer_profiles'),
  ]);
  const [version, project, projectsRaw, contextRaw, treeRaw] =
    settled.map(r => (r.status === 'fulfilled' ? r.value : null));
  settled.forEach((r, i) => {
    if (r.status === 'rejected') console.error('[AutoCheck] 상태바 갱신 실패(항목 ' + i + '):', r.reason);
  });
  const projects = projectsRaw || [];
  document.getElementById('sb-version').textContent = 'v' + (version || '-');
  const meta = projects.find(p => p.id === project);
  const label = meta ? meta.display_name : (project || '프로파일 없음');
  document.getElementById('sb-project').textContent = '프로파일: ' + label;
  // 상단바(고객사 / 정기점검) — 활성 프로파일은 config/active_project.yaml에 저장돼 있어서
  // 프로그램을 다시 켜도 '마지막에 쓰던 것'이 그대로 활성 상태다. 여기서는 그 이름을 찾아
  // 표시만 한다. context가 정본이고(get_customer_context가 활성 프로젝트로 직접 찾아 준다),
  // tree는 context에 이름이 비어 있을 때의 보조 수단이다.
  const context = contextRaw || {};
  const tree = treeRaw || [];
  const customer = tree.find(c => (c.profiles || []).some(p => p.id === context.profile));
  const profile = (customer?.profiles || []).find(p => p.id === context.profile);
  document.getElementById('tb-customer-name').textContent =
    context.customer || customer?.name || '고객사 없음';
  document.getElementById('tb-profile-name').textContent =
    context.profile_name || profile?.profile_name || label || '정기점검 없음';
}
