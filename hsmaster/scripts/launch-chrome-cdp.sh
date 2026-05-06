#!/bin/bash
# Hmall 자동화용 Chrome 디버그 모드 launcher
# 가이드 A.1: hmall 봇 검출 우회의 핵심 — 사용자 Chrome에 자동화 attach
#
# Usage:
#   ./scripts/launch-chrome-cdp.sh
#   (그 후 다른 터미널에서: hsm hmall add-cart --attach-cdp ...)
#
# 처음 실행 시: 빈 프로필이라 hmall에 수동 로그인 1회 필요
#   → 이후 cookies가 ~/HmallChrome 에 저장되어 재실행 시 로그인 유지

PORT=9222
USER_DATA_DIR="$HOME/HmallChrome"

# 이미 동일 포트로 떠 있으면 알림
if curl -s "http://127.0.0.1:$PORT/json/version" > /dev/null 2>&1; then
  echo "✓ Chrome이 이미 port $PORT로 실행 중입니다."
  echo "  $(curl -s http://127.0.0.1:$PORT/json/version | head -c 100)"
  exit 0
fi

echo "▶ Chrome 디버그 모드 launch (port $PORT, profile=$USER_DATA_DIR)"

open -na "Google Chrome" --args \
  --remote-debugging-port="$PORT" \
  '--remote-allow-origins=*' \
  --user-data-dir="$USER_DATA_DIR"

# 5초 대기 후 포트 확인
sleep 5
if curl -s "http://127.0.0.1:$PORT/json/version" > /dev/null 2>&1; then
  echo "✓ CDP 포트 활성화됨"
  echo "  버전: $(curl -s http://127.0.0.1:$PORT/json/version | head -c 100)"
  echo ""
  echo "다음 단계:"
  echo "  1. 띄워진 Chrome에서 hmall 수동 로그인 (계정별 첫 1회만)"
  echo "  2. 별도 터미널에서:"
  echo "       hsm hmall add-cart --attach-cdp --url '<URL>' --qty <N> --accounts 1-19"
else
  echo "✗ CDP 포트 응답 없음. Chrome 다시 띄워주세요."
  exit 1
fi
