// ===== 점검 로그 (원본) =====
// 원본 로그 뷰어만 남깁니다. 이상탐지와 마스킹은 독립 메뉴로 분리되었습니다.

async function renderInspectionLog() {
  const content = document.getElementById('content');
  content.innerHTML = `
    <div class="term-page">
      <h1 class="page-title">점검 로그</h1>
      <p class="page-sub">장비에서 수집한 원본 CLI 출력을 확인합니다.</p>
  
      <div class="card" style="display:flex; flex-direction:column; min-height:0; flex:1;">
        <div class="card-header">
          <div class="card-icon"><span class="material-symbols-rounded">folder_open</span></div>
          <div>
            <p class="card-title">수집된 로그</p>
            <p class="card-desc">세션 터미널 점검 또는 수집/채점 실행으로 쌓인 로그입니다.</p>
          </div>
          <span style="flex:1"></span>
          <button class="btn btn-outlined" id="btn-open-log-folder"><span class="material-symbols-rounded">folder_open</span>폴더 열기</button>
        </div>
        <div class="log-viewer" id="log-viewer" style="flex:1; flex-direction:column; min-height:0; display:flex;"></div>
      </div>
    </div>
  `;

  document.getElementById('btn-open-log-folder').addEventListener('click', async () => {
    const result = await call('open_inspection_log_folder', 'original');
    if (result && result.error) alert(result.error);
  });

  await refreshLogViewer(false);
}

