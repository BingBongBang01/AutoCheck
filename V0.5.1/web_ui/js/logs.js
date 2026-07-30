// ===== 전체 로그 (아키텍처 탭 아래) — 200ms DOM Throttling + 500줄 DOM 제한 + 레벨 필터링 =====

let logsPollTimer = null;
let logsThrottleTimer = null;
let logsPendingLines = [];
let logsDomLines = [];
let logsLastIndex = 0;
let logsTotalCount = 0;
let logsFullCount = 0;
let logsCurrentLevel = localStorage.getItem('autocheck_logs_level') || 'INFO';
let logsIsPaused = false;
let logsIsThrottling = false;

const MAX_DOM_LINES = 500;
const THROTTLE_INTERVAL_MS = 200; // 200ms DOM Update Throttling
const POLL_INTERVAL_MS = 1000;

function stopLogsPolling() {
  if (logsPollTimer) {
    clearInterval(logsPollTimer);
    logsPollTimer = null;
  }
  if (logsThrottleTimer) {
    clearTimeout(logsThrottleTimer);
    logsThrottleTimer = null;
  }
}

async function renderLogs() {
  stopLogsPolling();
  logsDomLines = [];
  logsPendingLines = [];
  logsLastIndex = 0;
  logsTotalCount = 0;
  logsFullCount = 0;
  logsIsPaused = false;
  logsIsThrottling = false;
  logsCurrentLevel = localStorage.getItem('autocheck_logs_level') || 'INFO';

  const content = document.getElementById('content');
  content.innerHTML = `
    <h1 class="page-title">전체 로그</h1>
    <p class="page-sub">프로그램의 모든 동작/오류 로그입니다. 랙 방지를 위해 화면에는 최근 500줄만 200ms 속도제어(Throttling)로 표출되며, 전체 로그는 내보내기(.txt)로 보존됩니다.</p>
    <div class="card">
      <div class="card-header" style="justify-content:space-between;flex-wrap:wrap;gap:8px;">
        <div style="display:flex;gap:8px;align-items:center;">
          <div class="card-icon"><span class="material-symbols-rounded">terminal</span></div>
          <div><p class="card-title">최근 로그 스트리밍</p><p class="card-desc" id="logs-meta">로그 로딩 중...</p></div>
        </div>
        <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;">
          <!-- Level Filter Dropdown -->
          <div style="display:flex;align-items:center;gap:4px;font-size:12px;color:var(--sub);">
            <span>레벨 필터:</span>
            <select id="logs-level-filter" class="form-input" style="height:32px;padding:2px 8px;font-size:12px;width:120px;">
              <option value="INFO" ${logsCurrentLevel === 'INFO' ? 'selected' : ''}>INFO 이상</option>
              <option value="WARNING" ${logsCurrentLevel === 'WARNING' ? 'selected' : ''}>WARN 이상</option>
              <option value="ERROR" ${logsCurrentLevel === 'ERROR' ? 'selected' : ''}>ERROR 이상</option>
              <option value="DEBUG" ${logsCurrentLevel === 'DEBUG' ? 'selected' : ''}>ALL (DEBUG 포함)</option>
            </select>
          </div>
          <!-- Pause/Resume Toggle -->
          <button class="btn btn-outlined" id="btn-toggle-logs-pause" style="height:32px;padding:0 10px;font-size:12px;">
            <span class="material-symbols-rounded" style="font-size:16px;" id="icon-logs-pause">pause</span>
            <span id="text-logs-pause">일시정지</span>
          </button>
          <button class="btn btn-outlined" id="btn-refresh-logs" style="height:32px;padding:0 10px;font-size:12px;">
            <span class="material-symbols-rounded" style="font-size:16px;">refresh</span>새로고침
          </button>
          <button class="btn btn-primary" id="btn-export-logs" style="height:32px;padding:0 10px;font-size:12px;">
            <span class="material-symbols-rounded" style="font-size:16px;">download</span>전체 내보내기(.txt)
          </button>
        </div>
      </div>
      <pre id="logs-view" style="max-height:520px;min-height:240px;overflow:auto;font-size:12px;line-height:1.5;white-space:pre-wrap;background:var(--hover);padding:12px;border-radius:8px;margin-top:12px;font-family:var(--font-mono, monospace);"></pre>
    </div>
  `;

  // Event handlers
  const filterEl = document.getElementById('logs-level-filter');
  filterEl.value = logsCurrentLevel;
  filterEl.addEventListener('change', (e) => {
    logsCurrentLevel = e.target.value;
    localStorage.setItem('autocheck_logs_level', logsCurrentLevel);
    resetAndFetchLogs();
  });


  document.getElementById('btn-toggle-logs-pause').addEventListener('click', () => {
    logsIsPaused = !logsIsPaused;
    const textEl = document.getElementById('text-logs-pause');
    const iconEl = document.getElementById('icon-logs-pause');
    if (logsIsPaused) {
      if (textEl) textEl.textContent = '재개';
      if (iconEl) iconEl.textContent = 'play_arrow';
    } else {
      if (textEl) textEl.textContent = '일시정지';
      if (iconEl) iconEl.textContent = 'pause';
      pollLogsStream();
    }
  });

  document.getElementById('btn-refresh-logs').addEventListener('click', resetAndFetchLogs);

  document.getElementById('btn-export-logs').addEventListener('click', async () => {
    const btn = document.getElementById('btn-export-logs');
    btn.classList.add('loading');
    const result = await call('export_full_log');
    btn.classList.remove('loading');
    if (result === null || result === undefined) return;
    if (result.error) { alert(result.error); return; }
    if (result.path) alert(`전체 로그를 저장했습니다:\n${result.path}`);
  });

  await resetAndFetchLogs();
  startLogsPolling();
}

async function resetAndFetchLogs() {
  logsDomLines = [];
  logsPendingLines = [];
  logsLastIndex = 0;
  
  const view = document.getElementById('logs-view');
  if (view) view.textContent = '로그를 불러오는 중...';

  const result = await call('get_recent_logs', 500, null, logsCurrentLevel) || {
    lines: [], total_count: 0, full_log_count: 0, truncated: false
  };

  logsLastIndex = result.total_count || 0;
  logsTotalCount = result.total_count || 0;
  logsFullCount = result.full_log_count || 0;
  logsDomLines = (result.lines || []).slice(-MAX_DOM_LINES);

  updateLogsMeta(result);
  renderDomThrottled(true);
}

function startLogsPolling() {
  stopLogsPolling();
  logsPollTimer = setInterval(pollLogsStream, POLL_INTERVAL_MS);
}

async function pollLogsStream() {
  if (logsIsPaused || currentPage !== 'logs') return;

  const result = await call('get_recent_logs', 500, logsLastIndex, logsCurrentLevel);
  if (!result || currentPage !== 'logs') return;

  logsLastIndex = result.total_count || logsLastIndex;
  logsTotalCount = result.total_count || logsTotalCount;
  logsFullCount = result.full_log_count || logsFullCount;

  if (result.lines && result.lines.length > 0) {
    logsPendingLines.push(...result.lines);
    scheduleThrottledDomUpdate();
  }
  updateLogsMeta(result);
}

function scheduleThrottledDomUpdate() {
  if (logsIsThrottling) return;
  logsIsThrottling = true;

  logsThrottleTimer = setTimeout(() => {
    logsIsThrottling = false;
    if (logsPendingLines.length === 0) return;

    // Append pending lines and trim DOM buffer to MAX_DOM_LINES (500)
    logsDomLines.push(...logsPendingLines);
    logsPendingLines = [];

    if (logsDomLines.length > MAX_DOM_LINES) {
      logsDomLines = logsDomLines.slice(-MAX_DOM_LINES);
    }

    renderDomThrottled(false);
  }, THROTTLE_INTERVAL_MS); // 200ms Throttling
}

function renderDomThrottled(forceScroll) {
  const view = document.getElementById('logs-view');
  if (!view) return;

  const isAtBottom = forceScroll || (view.scrollHeight - view.scrollTop - view.clientHeight < 40);
  view.textContent = logsDomLines.length ? logsDomLines.join('\n') : '(로그 없음)';
  
  if (isAtBottom) {
    view.scrollTop = view.scrollHeight;
  }
}

function updateLogsMeta(result) {
  const meta = document.getElementById('logs-meta');
  if (!meta) return;

  const levelText = logsCurrentLevel === 'DEBUG' ? 'ALL (DEBUG)' : `${logsCurrentLevel} 이상`;
  const pauseStatus = logsIsPaused ? ' [일시정지됨]' : '';
  
  meta.textContent = `화면 표출: 최근 ${logsDomLines.length}줄 (필터: ${levelText}) | 세션 전체: ${logsFullCount || logsTotalCount}줄${pauseStatus}`;
}
