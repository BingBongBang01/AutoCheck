// ===== 점검 로그 (원본 / 이상탐지 / 마스킹) =====
// 원래는 '수집/채점' 탭 안의 카드 하나에 3개 하위 탭으로 파묻혀 있었다.
// 로그를 읽는 일은 채점을 돌리는 일과 목적이 다르고(실행 vs 확인), 실제로는 채점을
// 돌리지 않고 세션 터미널로 모은 로그만 보는 경우도 많아서 독립 탭으로 뺐다.
// 하위 탭 각각의 실제 렌더링은 그대로 collection-log-viewer/-analysis/-masking.js가 담당한다.

let collectionLogSubTab = 'original'; // 'original' | 'analysis' | 'masking'

const INSPECTION_LOG_TABS = [
  { id: 'original', icon: 'description', label: '원본 로그', folder: 'original' },
  { id: 'analysis', icon: 'troubleshoot', label: '이상 탐지', folder: 'problem' },
  { id: 'masking', icon: 'visibility_off', label: '마스킹', folder: 'masking' },
];

function applyInspectionLogSubTab() {
  document.getElementById('log-viewer').style.display = collectionLogSubTab === 'original' ? 'flex' : 'none';
  document.getElementById('log-analysis').style.display = collectionLogSubTab === 'analysis' ? 'flex' : 'none';
  document.getElementById('log-masking').style.display = collectionLogSubTab === 'masking' ? 'flex' : 'none';
  document.querySelectorAll('[data-log-subtab]').forEach(el =>
    el.classList.toggle('active', el.dataset.logSubtab === collectionLogSubTab));
}

async function renderInspectionLog() {
  const content = document.getElementById('content');
  content.innerHTML = `
    <div class="term-page">
      <h1 class="page-title">점검 로그</h1>
      <p class="page-sub">장비에서 수집한 원본 CLI 출력을 확인하고, 이상 징후를 찾아내고, 외부 공유용으로 민감정보를 가립니다.</p>
  
      <div class="card">
        <div class="card-header">
          <div class="card-icon"><span class="material-symbols-rounded">folder_open</span></div>
          <div>
            <p class="card-title">수집된 로그</p>
            <p class="card-desc">세션 터미널 점검 또는 수집/채점 실행으로 쌓인 로그입니다.</p>
          </div>
          <span style="flex:1"></span>
          <button class="btn btn-outlined" id="btn-open-log-folder"><span class="material-symbols-rounded">folder_open</span>폴더 열기</button>
        </div>
        <div class="term-tabs" style="margin-bottom:10px;">
          ${INSPECTION_LOG_TABS.map(t => `
            <div class="term-tab ${collectionLogSubTab === t.id ? 'active' : ''}" data-log-subtab="${t.id}">
              <span class="material-symbols-rounded" style="font-size:14px;">${t.icon}</span>${t.label}
            </div>`).join('')}
        </div>
        <div class="log-viewer" id="log-viewer" style="flex:1; flex-direction:column; min-height:0;"></div>
        <div id="log-analysis" style="flex:1; flex-direction:column; min-height:0;"></div>
        <div id="log-masking" style="flex:1; flex-direction:column; min-height:0;"></div>
      </div>
    </div>
  `;

  document.getElementById('btn-open-log-folder').addEventListener('click', async () => {
    const tab = INSPECTION_LOG_TABS.find(t => t.id === collectionLogSubTab);
    const result = await call('open_inspection_log_folder', tab ? tab.folder : 'root');
    if (result && result.error) alert(result.error);
  });

  document.querySelectorAll('[data-log-subtab]').forEach(tabEl => {
    tabEl.addEventListener('click', async () => {
      collectionLogSubTab = tabEl.dataset.logSubtab;
      applyInspectionLogSubTab();
      // 하위 탭은 열릴 때 처음 로드한다 — 세 종류를 매번 다 읽으면 로그가 많을 때 느려진다.
      if (collectionLogSubTab === 'analysis') await refreshLogAnalysis();
      if (collectionLogSubTab === 'masking') await refreshLogMasking();
    });
  });

  applyInspectionLogSubTab();
  await refreshLogViewer(false);
  if (collectionLogSubTab === 'analysis') await refreshLogAnalysis();
  if (collectionLogSubTab === 'masking') await refreshLogMasking();
}
