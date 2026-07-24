// ===== 테마 (라이트/다크) =====
function applyTheme(mode) {
  document.documentElement.setAttribute('data-theme', mode);
  const icon = document.getElementById('tb-theme-icon');
  if (icon) icon.textContent = mode === 'dark' ? 'dark_mode' : 'light_mode';
  localStorage.setItem('theme', mode);
}
(function initTheme() {
  const saved = localStorage.getItem('theme');
  const mode = saved || (window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark');
  applyTheme(mode);
})();
document.getElementById('tb-theme-toggle').addEventListener('click', () => {
  const current = document.documentElement.getAttribute('data-theme') || 'dark';
  applyTheme(current === 'dark' ? 'light' : 'dark');
});

// ===== pywebview 브리지 준비 대기 =====
let API_READY = false;
window.addEventListener('pywebviewready', () => { API_READY = true; });

function waitForApiReady(timeoutMs = 5000) {
  if (window.pywebview && window.pywebview.api) {
    API_READY = true;
    return Promise.resolve();
  }
  return new Promise((resolve) => {
    const onReady = () => { clearTimeout(timer); resolve(); };
    const timer = setTimeout(() => {
      window.removeEventListener('pywebviewready', onReady);
      resolve();
    }, timeoutMs);
    window.addEventListener('pywebviewready', onReady, { once: true });
  });
}

async function call(fn, ...args) {
  if (window.pywebview && window.pywebview.api) {
    return await window.pywebview.api[fn](...args);
  }
  return MOCK[fn] ? MOCK[fn](...args) : null;
}

const MOCK = {
  get_app_version: () => '0.0.15-mock',
  list_projects: () => [],
  get_active_project: () => null,
  get_dashboard: () => ({
    kpi: { health: 66, critical: 3, warning: 8, devices: 7, sessions: 2 },
    stages: [
      { label: 'VLAN', pass: 18, total: 18, status: 'COMPLETE' },
      { label: 'STP', pass: 3, total: 14, status: 'IN_PROGRESS' },
      { label: 'LACP', pass: 0, total: 0, status: 'SKIPPED' },
    ],
    ai_summary: '전체 21/32건 PASS(66%). 완료 단계: VLAN. 미해결 단계: STP.',
  }),
};

// ===== 리플 효과 =====
document.addEventListener('click', (e) => {
  const btn = e.target.closest('.btn');
  if (!btn) return;
  const rect = btn.getBoundingClientRect();
  const ripple = document.createElement('span');
  ripple.className = 'ripple';
  const size = Math.max(rect.width, rect.height);
  ripple.style.width = ripple.style.height = size + 'px';
  ripple.style.left = (e.clientX - rect.left - size / 2) + 'px';
  ripple.style.top = (e.clientY - rect.top - size / 2) + 'px';
  btn.appendChild(ripple);
  setTimeout(() => ripple.remove(), 500);
});

// ===== 저장 완료 토스트 (여러 페이지에서 공용) =====
function flashSaved(ok) {
  const el = document.createElement('div');
  el.textContent = ok ? '저장됨' : '저장 실패';
  el.style.cssText = `position:fixed;bottom:40px;right:24px;background:${ok ? 'var(--success)' : 'var(--critical)'};color:#0B1220;padding:8px 16px;border-radius:10px;font-size:13px;font-weight:600;z-index:999;transition:opacity 300ms;`;
  document.body.appendChild(el);
  setTimeout(() => { el.style.opacity = 0; setTimeout(() => el.remove(), 300); }, 1500);
}

// ===== 커스텀 문구 토스트 (여러 페이지에서 공용 — flashSaved()는 고정 문구라 재사용 불가) =====
function showToast(message, tone = 'success') {
  const color = tone === 'error' ? 'var(--critical)' : (tone === 'warn' ? 'var(--warning)' : 'var(--success)');
  const el = document.createElement('div');
  el.textContent = message;
  el.style.cssText = `position:fixed;bottom:40px;right:24px;background:${color};color:#0B1220;padding:8px 16px;border-radius:10px;font-size:13px;font-weight:600;z-index:999;transition:opacity 300ms;`;
  document.body.appendChild(el);
  setTimeout(() => { el.style.opacity = 0; setTimeout(() => el.remove(), 300); }, 1800);
}

function renderComingSoon(title, desc) {
  document.getElementById('content').innerHTML = `
    <h1 class="page-title">${title}</h1>
    <p class="page-sub">${desc}</p>
    <div class="card">
      <div class="card-header">
        <div class="card-icon"><span class="material-symbols-rounded">construction</span></div>
        <div>
          <p class="card-title">준비 중</p>
          <p class="card-desc">이 탭은 UI 스캐폴드만 완성되어 있고, 실제 기능 연결은 다음 버전에서 진행 예정입니다.</p>
        </div>
      </div>
    </div>`;
}
