// ===== Discovery =====
async function renderDiscovery() {
  const content = document.getElementById('content');
  content.innerHTML = `
    <h1 class="page-title">EVE 구성 불러오기</h1>
    <p class="page-sub">.unl 파일 분석 — 토폴로지 / 계층 / 이미지버전 / 인벤토리</p>
    <button class="btn btn-primary" id="btn-run-discovery"><span class="material-symbols-rounded">upload_file</span>.unl 파일 선택</button>
    <div style="display:flex;gap:8px;margin-top:12px;"><input class="field" id="discovery-range" placeholder="CIDR 또는 IP 범위 예: 10.10.1.0/24"><button class="btn btn-outlined" id="btn-scan-network">Ping/SSH 탐색</button></div>
    <button class="btn btn-outlined" id="btn-register-inv" style="display:none;margin-left:8px;"><span class="material-symbols-rounded">playlist_add</span>Inventory 등록</button>
    <div class="card section-gap">
      <div class="terminal" id="discovery-output" style="height:400px;">결과가 여기 표시됩니다.</div>
    </div>
  `;
  let discoveredNodes = [];
  document.getElementById('btn-scan-network').addEventListener('click', async () => {
    const target = document.getElementById('discovery-range').value.trim();
    if (!target) return;
    const result = await call('scan_network', target, 22);
    document.getElementById('discovery-output').textContent = JSON.stringify(result, null, 2);
  });
  document.getElementById('btn-run-discovery').addEventListener('click', async () => {
    if (!window.pywebview || !window.pywebview.api) {
      document.getElementById('discovery-output').textContent = '파일 선택 창은 데스크톱 앱(pywebview) 실행 환경에서만 열립니다. gui_web.py로 앱을 실행한 뒤 다시 시도하세요.';
      return;
    }
    let result;
    try {
      result = await call('run_discovery');
    } catch (error) {
      document.getElementById('discovery-output').textContent = `파일을 불러오지 못했습니다.\n${error}`;
      return;
    }
    if (!result) { document.getElementById('discovery-output').textContent = '(파일 선택 취소됨)'; return; }
    if (result.error) { document.getElementById('discovery-output').textContent = result.error; return; }
    document.getElementById('discovery-output').textContent = result.text;
    discoveredNodes = result.node_names || [];
    document.getElementById('btn-register-inv').style.display = discoveredNodes.length ? 'inline-flex' : 'none';
  });
  document.getElementById('btn-register-inv').addEventListener('click', async () => {
    const added = await call('register_discovered_devices', discoveredNodes);
    flashSaved(true);
    alert(`Device Inventory에 ${added}건 신규 등록됨(IP 미입력·비활성 상태 — Device Inventory 탭에서 채워주세요)`);
  });
}
