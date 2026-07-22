// ===== Knowledge (Explorer 스타일 — Folder/Document/Search) =====
let knowledgeSearch = '';

async function renderKnowledge() {
  const content = document.getElementById('content');
  const docs = await call('list_knowledge_docs') || [];

  content.innerHTML = `
    <h1 class="page-title">지식베이스</h1>
    <p class="page-sub">프로젝트별 지식 문서(Markdown) — 트러블슈팅 노트, 표준 절차 등</p>
    <div style="display:flex;gap:8px;margin-bottom:12px;">
      <input class="field" id="kb-search" placeholder="문서 검색" style="width:220px;">
      <button class="btn btn-outlined" id="btn-new-doc"><span class="material-symbols-rounded">note_add</span>새 문서</button>
    </div>
    <div style="display:grid;grid-template-columns:220px 1fr;gap:16px;">
      <div class="card" id="kb-list" style="padding:12px;max-height:500px;overflow-y:auto;"></div>
      <div class="card">
        <textarea id="kb-editor" class="field" style="width:100%;height:400px;font-family:var(--font-mono);font-size:12px;resize:vertical;" placeholder="문서를 선택하거나 새로 만드세요"></textarea>
        <div style="display:flex;gap:8px;margin-top:10px;">
          <button class="btn btn-primary" id="btn-save-doc"><span class="material-symbols-rounded">save</span>저장</button>
          <button class="btn btn-danger" id="btn-delete-doc"><span class="material-symbols-rounded">delete</span>삭제</button>
        </div>
      </div>
    </div>
  `;

  let currentDoc = null;
  const renderList = () => {
    const filtered = docs.filter(d => d.name.toLowerCase().includes(knowledgeSearch));
    document.getElementById('kb-list').innerHTML = filtered.length
      ? filtered.map(d => `<div class="nav-item" data-doc="${d.name}" style="color:var(--text);"><span class="material-symbols-rounded" style="font-size:16px;">description</span><span class="nav-label">${d.name}</span></div>`).join('')
      : '<p style="color:var(--sub);font-size:12px;">문서 없음</p>';
    document.querySelectorAll('#kb-list [data-doc]').forEach(el => {
      el.addEventListener('click', async () => {
        currentDoc = el.dataset.doc;
        document.getElementById('kb-editor').value = await call('get_knowledge_doc', currentDoc);
      });
    });
  };
  renderList();

  document.getElementById('kb-search').addEventListener('input', (e) => {
    knowledgeSearch = e.target.value.toLowerCase();
    renderList();
  });

  document.getElementById('btn-new-doc').addEventListener('click', () => {
    const name = prompt('문서 이름 (예: stp-troubleshooting.md)');
    if (!name) return;
    currentDoc = name.endsWith('.md') ? name : name + '.md';
    document.getElementById('kb-editor').value = `# ${currentDoc.replace('.md', '')}\n\n`;
  });

  document.getElementById('btn-save-doc').addEventListener('click', async () => {
    if (!currentDoc) { alert('먼저 문서를 선택하거나 새로 만드세요'); return; }
    const content_ = document.getElementById('kb-editor').value;
    await call('save_knowledge_doc', currentDoc, content_);
    flashSaved(true);
    const refreshed = await call('list_knowledge_docs') || [];
    docs.length = 0; docs.push(...refreshed);
    renderList();
  });

  document.getElementById('btn-delete-doc').addEventListener('click', async () => {
    if (!currentDoc) return;
    await call('delete_knowledge_doc', currentDoc);
    currentDoc = null;
    document.getElementById('kb-editor').value = '';
    const refreshed = await call('list_knowledge_docs') || [];
    docs.length = 0; docs.push(...refreshed);
    renderList();
  });
}
