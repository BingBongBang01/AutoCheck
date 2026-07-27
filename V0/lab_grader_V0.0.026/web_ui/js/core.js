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

// 브리지가 없으면(= 데스크톱 앱이 아니라 브라우저로 index.html을 직접 연 경우)
// 가짜 값을 만들어내지 않고 null을 돌려준다 — 화면에 실제와 다른 수치가 뜨는 게 더 위험함.
// 브리지는 있는데 그런 이름의 메서드가 없으면 "undefined is not a function" 대신
// 어떤 메서드가 없는지 바로 알 수 있게 이름을 찍는다(api/*.py mixin 누락 진단용).
async function call(fn, ...args) {
  const api = window.pywebview && window.pywebview.api;
  if (!api) {
    console.warn(`[AutoCheck] pywebview 브리지 없음 — '${fn}' 호출을 건너뜀. 'python main.py'로 실행하세요.`);
    return null;
  }
  if (typeof api[fn] !== 'function') {
    console.error(`[AutoCheck] API 메서드 '${fn}' 없음 — api/*.py의 mixin이 Api 클래스에 합성됐는지 확인하세요.`);
    return null;
  }
  return await api[fn](...args);
}

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
