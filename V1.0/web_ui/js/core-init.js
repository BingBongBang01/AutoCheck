// ===== 초기 진입 (모든 render*.js 로드가 끝난 뒤 마지막에 실행되도록 index.html에서 맨 뒤에 로드) =====
(async function init() {
  await waitForApiReady();
  // 상태바를 그리기 전에 활성 프로파일을 확정한다 — 저장된 id가 유효하면 그대로 쓰고, 폴더가
  // 사라졌거나 비어 있으면 고객사별 마지막 사용 기록으로 되살린다. 이걸 먼저 하지 않으면
  // 첫 화면이 '프로파일 없음'으로 떴다가 사용자가 팝업에서 다시 고를 때까지 그대로 남는다.
  await call('ensure_active_profile');
  await refreshStatusBar();

  // 고객사 및 정기점검 프로파일 존재 여부 검사
  const tree = await call('get_customer_profiles') || [];
  const hasNoCustomer = tree.length === 0;
  const hasNoProfile = tree.every(c => !c.profiles || c.profiles.length === 0);

  if (hasNoCustomer || hasNoProfile) {
    await navigate('workspace');
    openCustomerProfileModal();
  } else {
    await navigate('workspace');
  }
})();
