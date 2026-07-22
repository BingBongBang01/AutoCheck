// ===== Collection (Pipeline 채점 실행) =====
async function renderCollection() {
  const content = document.getElementById('content');
  content.innerHTML = `
    <h1 class="page-title">수집</h1>
    <p class="page-sub">Pipeline(Collector→Parser→Rule Engine→Scorer→AI→Report) 실행</p>
    <div style="display:flex;align-items:center;gap:10px;margin-bottom:14px;">
      <input type="checkbox" id="mock-check" checked>
      <label for="mock-check" style="font-size:13px;">mock 모드(장비 접속 없이 파이프라인만 검증)</label>
      <button class="btn btn-primary" id="btn-run-collection"><span class="material-symbols-rounded">play_circle</span>실행</button>
      <button class="btn btn-danger" id="btn-clear-collection"><span class="material-symbols-rounded">delete_sweep</span>로그 지우기</button>
    </div>
    <div class="card">
      <div class="terminal" id="collection-output" style="height:420px;">실행 결과가 여기 표시됩니다.</div>
    </div>
  `;
  document.getElementById('btn-clear-collection').addEventListener('click', () => { document.getElementById('collection-output').textContent = ''; });
  document.getElementById('btn-run-collection').addEventListener('click', async (e) => {
    const btn = e.currentTarget;
    btn.classList.add('loading');
    const useMock = document.getElementById('mock-check').checked;
    const output = await call('run_grade', useMock);
    document.getElementById('collection-output').textContent = output;
    btn.classList.remove('loading');
  });
}
