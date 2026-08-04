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

// 이벤트만 기다리면 안 된다 — pywebview는 window.pywebview 객체를 먼저 만들고 api 메서드를
// 잠시 뒤에 주입하는데, 그 사이에 이 함수가 호출되면 (1) window.pywebview.api가 아직 없어서
// 즉시 반환하지 못하고 (2) pywebviewready는 이미 발생해 버려 두 번 오지 않으므로 리스너가
// 영원히 안 불린다. 그러면 timeoutMs(5초)를 통째로 기다린 뒤 '준비됨'으로 치고 진행하는데,
// 그때도 api가 없으면 call()이 전부 null을 반환해서 버전이 'v-', 고객사/정기점검이
// '없음'으로 굳어 버렸다(실제로는 마지막 프로파일이 정상적으로 선택돼 있는데도).
// 그래서 이벤트와 폴링을 함께 쓴다 — 어느 쪽이든 먼저 준비되면 즉시 진행한다.
function waitForApiReady(timeoutMs = 5000) {
  if (window.pywebview && window.pywebview.api) {
    API_READY = true;
    return Promise.resolve();
  }
  return new Promise((resolve) => {
    const finish = () => {
      if (window.pywebview && window.pywebview.api) API_READY = true;
      clearTimeout(timer);
      clearInterval(poll);
      window.removeEventListener('pywebviewready', finish);
      resolve();
    };
    const poll = setInterval(() => {
      if (window.pywebview && window.pywebview.api) finish();
    }, 40);
    const timer = setTimeout(finish, timeoutMs);
    window.addEventListener('pywebviewready', finish, { once: true });
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


// ===== 드래그 정렬 중 가장자리 자동 스크롤 =====
// 목록이 화면보다 길어도, 드래그한 채 콘텐츠 영역의 위·아래 가장자리로 가져가면 계속 이동할 수 있다.
// onTick: 한 프레임 스크롤한 뒤에 호출된다 — 커서가 멈춘 상태로 콘텐츠만 흐를 때
// 커서 밑에 새로 들어온 행을 다시 판정하려면 이 콜백이 필요하다(createDragRangeSelect 참고).
function createDragAutoScroller(scrollElement = document.getElementById('content'), edgeSize = 72, maxSpeed = 24, onTick = null) {
  let frameId = null;
  let speed = 0;

  const tick = () => {
    if (!speed || !scrollElement) { frameId = null; return; }
    const before = scrollElement.scrollTop;
    scrollElement.scrollTop += speed;
    if (scrollElement.scrollTop === before) { frameId = null; return; }
    if (onTick) onTick();
    frameId = requestAnimationFrame(tick);
  };

  return {
    update(event) {
      if (!scrollElement) return;
      const rect = scrollElement.getBoundingClientRect();
      const topDistance = event.clientY - rect.top;
      const bottomDistance = rect.bottom - event.clientY;
      if (topDistance >= 0 && topDistance < edgeSize) {
        speed = -Math.ceil(((edgeSize - topDistance) / edgeSize) * maxSpeed);
      } else if (bottomDistance >= 0 && bottomDistance < edgeSize) {
        speed = Math.ceil(((edgeSize - bottomDistance) / edgeSize) * maxSpeed);
      } else {
        speed = 0;
      }
      if (speed && !frameId) frameId = requestAnimationFrame(tick);
    },
    stop() {
      speed = 0;
      if (frameId) cancelAnimationFrame(frameId);
      frameId = null;
    },
  };
}

// ===== 목록 드래그 범위선택 (자동 스크롤 포함) — 로그 목록 3곳 + 세션 터미널 장비 목록 공용 =====
// 이 helper가 해결하는 것 3가지:
//  1) 목록이 스크롤 영역보다 길 때, 드래그한 채 위/아래 가장자리로 가져가면 자동으로 스크롤된다.
//  2) 자동 스크롤 중에는 커서가 멈춰 있어서 mousemove가 안 온다 — 그래서 매 프레임
//     elementFromPoint()로 커서 밑의 행을 다시 찾아 선택 범위를 이어서 넓힌다.
//     (이게 없으면 스크롤만 되고 선택은 화면 끝 행에서 멈춘다.)
//  3) mousemove/mouseup을 드래그 중에만 document에 걸고 끝나면 떼어낸다. 예전엔 목록을
//     다시 그릴 때마다(클릭 한 번에 renderLogViewer() 호출) 리스너가 계속 쌓였다.
//
// container: 스크롤되는 목록 컨테이너, rowSelector: 행 셀렉터, rows: 행 엘리먼트 배열,
// applyTo(idx): 시작행~idx 범위를 선택/해제하는 콜백, onEnd(dragged, startIdx): 드래그 종료 콜백.
function createDragRangeSelect({ container, rowSelector, rows, applyTo, onEnd }) {
  let startIdx = null;
  let dragged = false;
  let pointer = null;
  const scroller = createDragAutoScroller(container, 36, 14, () => resolveAndApply());

  function resolveAndApply() {
    if (startIdx === null || !pointer) return;
    const el = document.elementFromPoint(pointer.x, pointer.y);
    const row = el && el.closest ? el.closest(rowSelector) : null;
    if (!row || !container.contains(row)) return;
    const idx = rows.indexOf(row);
    if (idx === -1 || idx === startIdx && !dragged) return;
    if (idx !== startIdx) dragged = true;
    applyTo(idx);
  }

  const onMove = (e) => {
    if (startIdx === null || !e.buttons) return;
    pointer = { x: e.clientX, y: e.clientY };
    scroller.update(e);
    resolveAndApply();
  };

  const onUp = () => {
    document.removeEventListener('mousemove', onMove);
    document.removeEventListener('mouseup', onUp);
    scroller.stop();
    const wasDragged = dragged;
    const from = startIdx;
    startIdx = null;
    pointer = null;
    if (onEnd) onEnd(wasDragged, from);
    // 드래그 직후 click 이벤트가 한 번 더 오므로, 그걸 무시할 시간을 준다.
    setTimeout(() => { dragged = false; }, 0);
  };

  return {
    begin(idx) {
      startIdx = idx;
      dragged = false;
      pointer = null;
      document.addEventListener('mousemove', onMove);
      document.addEventListener('mouseup', onUp);
    },
    isDragging() { return dragged; },
  };
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
