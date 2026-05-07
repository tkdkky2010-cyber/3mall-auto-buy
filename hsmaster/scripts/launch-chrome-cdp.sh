#!/bin/bash
# Hmall 자동화용 Chrome 디버그 모드 launcher
# Playwright bundled "Google Chrome for Testing" 사용 (메인 Chrome과 완전 분리)
#
# Usage:
#   ./scripts/launch-chrome-cdp.sh
#
# 처음 실행 시: 빈 프로필 → hmall 수동 로그인 1회
#   이후 cookies가 ~/HmallChrome/<profile>/ 에 저장되어 재실행 시 자동 로그인 유지

PORT="${CDP_PORT:-9222}"
USER_DATA_DIR="${HMALL_USER_DATA_DIR:-$HOME/HmallChrome}"
PROFILE_NAME="${HMALL_CHROME_PROFILE:-Default}"

# 이미 동일 포트로 떠 있으면 알림 + exit
if curl -s "http://127.0.0.1:$PORT/json/version" > /dev/null 2>&1; then
  echo "✓ Chrome for Testing이 이미 port $PORT로 실행 중입니다."
  echo "  $(curl -s http://127.0.0.1:$PORT/json/version | head -c 100)"
  exit 0
fi

# Playwright의 Chrome for Testing 위치 — 메인 "Google Chrome.app"과 별도 binary
# 가장 최신 버전 자동 선택 (chromium-NNNN/chrome-mac-{arm64,intel})
CFT_APP=$(ls -d "$HOME/Library/Caches/ms-playwright/chromium-"*/chrome-mac-*/"Google Chrome for Testing.app" 2>/dev/null | sort -V | tail -1)

if [ -z "$CFT_APP" ] || [ ! -d "$CFT_APP" ]; then
  echo "✗ Chrome for Testing 미발견. 'patchright install chromium' 또는 'playwright install chromium' 먼저 실행 필요"
  exit 1
fi

echo "▶ Chrome for Testing launch"
echo "  port    = $PORT"
echo "  data    = $USER_DATA_DIR"
echo "  profile = $PROFILE_NAME"
echo "  app     = $CFT_APP"

# Chrome for Testing은 메인 Chrome과 다른 .app이므로 open -na 충돌 X
open -na "$CFT_APP" --args \
  --remote-debugging-port="$PORT" \
  '--remote-allow-origins=*' \
  --user-data-dir="$USER_DATA_DIR" \
  --profile-directory="$PROFILE_NAME"

# 5초 대기 후 포트 확인
sleep 5
if curl -s "http://127.0.0.1:$PORT/json/version" > /dev/null 2>&1; then
  echo "✓ CDP 포트 활성화됨"
  echo ""
  echo "다음 단계 (첫 launch 시 한 번만):"
  echo "  1. 띄워진 Chrome for Testing 창에서 hmall 수동 로그인"
  echo "  2. 그대로 두고 자동화 진행"
  echo ""
  echo "메인 Chrome은 영향 받지 않음. 재시작 후에도 cookies 자동 유지."
else
  echo "✗ CDP 포트 응답 없음. Chrome for Testing 띄우는 데 실패."
  exit 1
fi
