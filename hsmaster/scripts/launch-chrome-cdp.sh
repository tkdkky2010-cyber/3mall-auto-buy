#!/bin/bash
# Hmall 자동화용 Chrome 디버그 모드 launcher
# 실제 Google Chrome binary 직접 실행 — Hmall 봇 차단 회피 (CFT user-agent X)
# 메인 Chrome과 다른 user-data-dir(~/HmallChrome) 사용 → 별도 process, 충돌 회피
#
# Usage:
#   ./scripts/launch-chrome-cdp.sh
#
# 데이터: ~/HmallChrome/Default 에 영구 저장 (이전 Profile 6 데이터 카피본)
#   Google 로그인 + Hmall 로그인 + 브라우징 이력 모두 보존
#   재실행 시 자동 로그인 유지

PORT="${CDP_PORT:-9222}"
USER_DATA_DIR="${HMALL_USER_DATA_DIR:-$HOME/HmallChrome}"
PROFILE_NAME="${HMALL_CHROME_PROFILE:-Default}"
CHROME_BIN="${CHROME_BIN:-/Applications/Google Chrome.app/Contents/MacOS/Google Chrome}"

# 이미 동일 포트로 떠 있으면 알림 + exit
if curl -s "http://127.0.0.1:$PORT/json/version" > /dev/null 2>&1; then
  echo "✓ Chrome이 이미 port $PORT로 실행 중입니다."
  echo "  $(curl -s http://127.0.0.1:$PORT/json/version | head -c 100)"
  exit 0
fi

if [ ! -x "$CHROME_BIN" ]; then
  echo "✗ Chrome binary 미발견: $CHROME_BIN"
  exit 1
fi

if [ ! -d "$USER_DATA_DIR/$PROFILE_NAME" ]; then
  echo "⚠️  $USER_DATA_DIR/$PROFILE_NAME 없음. 빈 프로필로 시작 (Hmall 봇 차단 가능)"
  echo "    필요 시 메인 Chrome의 'Profile 6' 폴더를 복사:"
  echo "    cp -r \"\$HOME/Library/Application Support/Google/Chrome/Profile 6\" $USER_DATA_DIR/$PROFILE_NAME"
fi

echo "▶ Chrome 직접 binary launch (메인 Chrome 충돌 회피)"
echo "  port    = $PORT"
echo "  data    = $USER_DATA_DIR"
echo "  profile = $PROFILE_NAME"
echo "  binary  = $CHROME_BIN"

# 직접 binary + background — open -na와 달리 macOS LaunchServices 우회
# user-data-dir이 메인 Chrome과 다르므로 SingletonLock 충돌 X
"$CHROME_BIN" \
  --remote-debugging-port="$PORT" \
  --remote-allow-origins=* \
  --user-data-dir="$USER_DATA_DIR" \
  --profile-directory="$PROFILE_NAME" \
  > /dev/null 2>&1 &
disown

# 5초 대기 후 포트 확인
sleep 5
if curl -s "http://127.0.0.1:$PORT/json/version" > /dev/null 2>&1; then
  echo "✓ CDP 포트 활성화됨"
  echo ""
  echo "  - 메인 Chrome 영향 받지 않음 (별도 user-data-dir + 별도 process)"
  echo "  - Hmall은 정상 Chrome user-agent로 보임 (CFT 아님)"
  echo "  - cookies/세션 ~/HmallChrome/$PROFILE_NAME 에 영구 저장"
else
  echo "✗ CDP 포트 응답 없음. Chrome 띄우는 데 실패."
  exit 1
fi
