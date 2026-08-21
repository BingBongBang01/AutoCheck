// ===== 점검 로그 목록을 실행(run) 단위로 묶는 공용 헬퍼 =====
// data/<고객사>/<프로파일>/runs/<run_id>/ 아래 로그는 "같은 시간에 점검된 로그"다.
// list_log_files류 API(api/log_file_browser_api.py)는 모든 run을 합친 평평한 목록을 주는데,
// 회차가 몇 개만 쌓여도 파일이 수십~수백 개가 되어 한 화면에 다 펼쳐 놓으면 원하는 회차를
// 찾기 어렵다. 여기서는 run_id로 묶어 "N분 전 점검 로그" 같은 접이식 그룹으로 보여주고,
// 그룹을 눌러야 그 안의 개별 로그가 펼쳐지게 한다. '점검 로그'/'로그 분석'/'마스킹' 세 탭이
// 같은 파일 목록 모양({path, name/device, source, run_id, mtime, mtime_str})을 쓰므로 공유한다.

// files -> run_id(없으면 source) 기준 그룹 배열. 최근 회차가 먼저 오도록 정렬한다.
function groupLogsByRun(files) {
  const groups = new Map();
  (files || []).forEach(f => {
    const key = f.run_id || `legacy:${f.source || ''}`;
    let g = groups.get(key);
    if (!g) {
      g = { key, runId: f.run_id || null, source: f.source || '', files: [], maxMtime: 0 };
      groups.set(key, g);
    }
    g.files.push(f);
    if ((f.mtime || 0) > g.maxMtime) g.maxMtime = f.mtime || 0;
  });
  const list = [...groups.values()];
  list.sort((a, b) => b.maxMtime - a.maxMtime);
  list.forEach(g => {
    const label = runGroupRelativeLabel(g.maxMtime);
    g.title = label.main;
    g.sub = g.runId ? `Run ${g.runId}${label.date ? ` · ${label.date}` : ''}` : (g.source || '이전 버전 폴더');
  });
  return list;
}

// 그룹 대표 시각(그 안에서 가장 최근 파일의 mtime)을 사람이 읽는 상대 시각 문구로.
function runGroupRelativeLabel(mtimeSec) {
  if (!mtimeSec) return { main: '시각 미상 점검 로그', date: '' };
  const now = Date.now() / 1000;
  const diff = Math.max(0, now - mtimeSec);
  const d = new Date(mtimeSec * 1000);
  const pad = (n) => String(n).padStart(2, '0');
  const dateFull = `${d.getFullYear()}년 ${d.getMonth() + 1}월 ${d.getDate()}일 ${pad(d.getHours())}:${pad(d.getMinutes())}`;
  const dateShort = `${d.getMonth() + 1}월 ${d.getDate()}일`;
  if (diff < 60) return { main: '방금 점검한 로그', date: dateFull };
  if (diff < 3600) return { main: `${Math.floor(diff / 60)}분 전 점검 로그`, date: dateFull };
  if (diff < 86400) return { main: `${Math.floor(diff / 3600)}시간 전 점검 로그`, date: dateFull };
  const days = Math.floor(diff / 86400);
  if (days === 1) return { main: '어제 점검 로그', date: dateFull };
  if (days < 30) return { main: `${days}일 전 점검 로그`, date: dateShort };
  if (days < 365) return { main: `${Math.floor(days / 30)}달 전 점검 로그`, date: dateShort };
  return { main: `${Math.floor(days / 365)}년 전 점검 로그`, date: dateFull };
}

// expandedSet은 호출부(각 탭)가 모듈 전역 Set으로 들고 있는다 — 폴링으로 다시 그려도 사용자가
// 펼쳐/접어 둔 상태가 유지돼야 한다. 데이터가 처음 생겼을 때 딱 한 번만 최신 회차를 자동으로
// 펼친다(그 뒤 사용자가 전부 접어도 다시 펴지지 않도록 __autoExpanded 플래그로 1회만 동작).
function ensureDefaultExpandedRun(groups, expandedSet) {
  if (expandedSet.__autoExpanded) return;
  if (groups.length) {
    expandedSet.add(groups[0].key);
    expandedSet.__autoExpanded = true;
  }
}

// rowHtml(file) -> 그 파일 한 줄의 HTML. 접힌 그룹은 본문을 아예 렌더링하지 않는다 — 그래야
// 기존의 Shift/드래그 범위선택(wireSelectableFileList, collection-log-viewer.js의 자체 구현)이
// "화면에 실제로 보이는 행"만 대상으로 그대로 동작한다(별도 대응 코드가 필요 없다).
function renderLogRunGroupsHtml(files, expandedSet, rowHtml, emptyHtml) {
  const groups = groupLogsByRun(files);
  if (!groups.length) return emptyHtml || '';
  ensureDefaultExpandedRun(groups, expandedSet);
  return groups.map(g => {
    const open = expandedSet.has(g.key);
    return `
      <div class="log-run-group">
        <div class="log-run-group-head ${open ? 'open' : ''}" data-run-group="${escapeAttr(g.key)}"
             title="클릭 → 이때 점검된 로그 펼치기/접기">
          <span class="material-symbols-rounded log-run-group-caret">arrow_drop_down</span>
          <div class="log-run-group-labels">
            <span class="log-run-group-title">${escapeHtml(g.title)}</span>
            <span class="log-run-group-sub">${escapeHtml(g.sub)}</span>
          </div>
          <span class="log-run-group-count">${g.files.length}개</span>
        </div>
        ${open ? `<div class="log-run-group-body">${g.files.map(rowHtml).join('')}</div>` : ''}
      </div>`;
  }).join('');
}

// 2단계 하이라이트의 '2단계' — 펼친 회차 안에서 지금 우측 뷰어/분석창에 읽혀 보이는 파일.
// 세 탭(점검 로그/로그 분석/마스킹)이 각자 다른 상태 변수로 활성 파일을 들고 있어서,
// 클래스 이름만 여기서 한 곳으로 모아 세 탭의 하이라이트 모양이 갈라지지 않게 한다.
// isActive = 지금 열려서 보이는 파일, isSelected = 삭제 등 다중선택 대상(서로 다른 정보다).
function logFileRowClass(isActive, isSelected) {
  return ['connection-device', 'log-file-row', 'log-file-item',
    isActive ? 'active session-active' : '', isSelected ? 'selected' : '']
    .filter(Boolean).join(' ');
}

// 활성 파일 우측 배지 — 배경색만으로는 다중선택(.selected)과 헷갈리므로 글자로도 못 박는다.
function logFileActiveBadge(isActive) {
  return isActive
    ? '<span class="log-file-open-badge" title="지금 오른쪽에 열려 있는 로그입니다">\u{1F441} 열림</span>'
    : '';
}

function wireLogRunGroupToggles(containerEl, expandedSet, rerender) {
  if (!containerEl) return;
  containerEl.querySelectorAll('[data-run-group]').forEach((head) => {
    head.addEventListener('click', () => {
      const key = head.dataset.runGroup;
      if (expandedSet.has(key)) expandedSet.delete(key);
      else expandedSet.add(key);
      rerender();
    });
  });
}
