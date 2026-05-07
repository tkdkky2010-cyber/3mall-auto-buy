#!/bin/bash
# Hmall 자동화용 Chrome 디버그 모드 launcher
# Hmall10 검증된 flags 그대로 — 봇 검출 우회의 핵심
#
# Usage:
#   ./scripts/launch-chrome-cdp.sh
#   HMALL_CHROME_PROFILE="Profile 6" ./scripts/launch-chrome-cdp.sh   # 프로필 명시
#
# 처음 실행 시: 빈 프로필 → Google + hmall 수동 로그인 1회
#   이후 cookies가 ~/HmallChrome/<profile>/ 에 저장되어 재실행 시 자동 로그인 유지
#   Chrome 재시작/리부팅 후에도 보존 (가이드 A.1)

PORT="${CDP_PORT:-9222}"
USER_DATA_DIR="${HMALL_USER_DATA_DIR:-$HOME/HmallChrome}"
PROFILE_NAME="${HMALL_CHROME_PROFILE:-Default}"

# 이미 동일 포트로 떠 있으면 알림 + exit
if curl -s "http://127.0.0.1:$PORT/json/version" > /dev/null 2>&1; then
  echo "✓ Chrome이 이미 port $PORT로 실행 중입니다."
  echo "  $(curl -s http://127.0.0.1:$PORT/json/version | head -c 100)"
  exit 0
fi

echo "▶ Chrome 디버그 모드 launch"
echo "  port    = $PORT"
echo "  data    = $USER_DATA_DIR"
echo "  profile = $PROFILE_NAME"

# Hmall10 검증 명령: open -na "Google Chrome" --args ...
open -na "Google Chrome" --args \
  --remote-debugging-port="$PORT" \
  '--remote-allow-origins=*' \
  --user-data-dir="$USER_DATA_DIR" \
  --profile-directory="$PROFILE_NAME"

# 5초 대기 후 포트 확인
sleep 5
if curl -s "http://127.0.0.1:$PORT/json/version" > /dev/null 2>&1; then
  # 창이 0개면 강제로 새 탭 열기 (--profile-directory 변경 시 첫 launch에서 가끔 발생)
  WINDOW_COUNT=$(osascript -e 'tell application "Google Chrome" to count of windows' 2>/dev/null || echo 0)
  if [ "$WINDOW_COUNT" = "0" ]; then
    curl -sX PUT "http://127.0.0.1:$PORT/json/new?https://www.hmall.com/" > /dev/null
    sleep 1
  fi
  osascript -e 'tell application "Google Chrome" to activate' 2>/dev/null
  echo "✓ CDP 포트 활성화됨"
  echo ""
  echo "다음 단계 (첫 launch 시 한 번만):"
  echo "  1. 띄워진 Chrome에서 Google 로그인 (자동화 전용 계정 권장)"
  echo "  2. hmall 수동 로그인"
  echo "  3. Chrome 그냥 닫기 (또는 자동화 계속)"
  echo ""
  echo "재시작 후에도 cookies 자동 유지됨."
else
  echo "✗ CDP 포트 응답 없음. Chrome 띄우는 데 실패."
  exit 1
fi
