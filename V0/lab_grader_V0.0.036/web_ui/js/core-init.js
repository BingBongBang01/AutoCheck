// ===== 초기 진입 (모든 render*.js 로드가 끝난 뒤 마지막에 실행되도록 index.html에서 맨 뒤에 로드) =====
(async function init() {
  await waitForApiReady();
  await refreshStatusBar();
  navigate('settings');
})();
