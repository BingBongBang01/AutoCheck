// ===== Inspection Profile (Stage/Target State 구조 뷰) =====
async function renderInspection() {
  const content = document.getElementById('content');
  const profile = await call('get_inspection_profile');

  if (!profile) {
    content.innerHTML = `
      <h1 class="page-title">점검 항목</h1>
      <p class="page-sub">활성 프로파일 없음 — 워크스페이스에서 먼저 선택하세요.</p>`;
    return;
  }

  const rows = profile.map(s => {
    const depText = s.depends_on.length ? s.depends_on.join(', ') : '(없음, 최초 단계)';
    const cmdText = s.commands.length ? s.commands.map(c => `<span class="mono">${c}</span>`).join('<br>') : '<span style="color:var(--sub)">(미정의)</span>';
    const statusBadge = s.check_count > 0 ? '<span class="badge badge-pass">구성됨</span>' : '<span class="badge badge-neutral">미착수</span>';
    return `
      <tr>
        <td><b>${s.label}</b></td>
        <td style="font-size:12px;color:var(--sub);">${depText}</td>
        <td style="font-size:12px;">${cmdText}</td>
        <td>${s.check_count}개</td>
        <td>${statusBadge}</td>
      </tr>`;
  }).join('');

  content.innerHTML = `
    <h1 class="page-title">점검 항목</h1>
    <p class="page-sub">이번 회차에 어떤 Stage를 어떤 순서로 점검하는지 보여줍니다(읽기 전용). 실행할 커맨드는 '명령어 카탈로그' 탭에서 켜고 끕니다.</p>
    <div class="card">
      <table class="dtable">
        <thead><tr><th>Stage</th><th>선행 단계(depends_on)</th><th>실행 커맨드</th><th>체크 수</th><th>상태</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
    <p style="color:var(--sub);font-size:12px;margin-top:12px;">편집 기능(체크 추가/값 수정)은 다음 버전에서 지원 예정 — 지금은 구조 확인용 읽기 전용입니다.</p>
  `;
}
