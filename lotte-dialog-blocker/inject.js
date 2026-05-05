// lotteimall.com 전용 - 네이티브 다이얼로그 자동 OK 처리
// document_start + MAIN world에서 실행되므로 페이지 스크립트보다 먼저 적용됨

window.alert = function() {
  console.log('[Dialog Blocker] alert 자동 OK:', arguments[0]);
  return undefined;
};

window.confirm = function() {
  console.log('[Dialog Blocker] confirm 자동 OK:', arguments[0]);
  return true;
};

window.prompt = function(msg, def) {
  console.log('[Dialog Blocker] prompt 자동 OK:', msg);
  return def || '';
};

console.log('[Dialog Blocker] lotteimall.com 다이얼로그 차단 활성화됨');
