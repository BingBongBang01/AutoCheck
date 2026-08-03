// ===== 초기 진입 (모든 render*.js 로드가 끝난 뒤 마지막에 실행되도록 index.html에서 맨 뒤에 로드) =====
(async function init() {
  await waitForApiReady();
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
