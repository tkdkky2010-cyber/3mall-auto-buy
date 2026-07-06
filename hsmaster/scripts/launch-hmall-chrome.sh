#!/bin/bash
# Hmall 자동화용 Chrome launcher — 포트 9222, ~/HmallChrome/Profile 6.
# ★기본 = 실제 Google Chrome 바이너리 (CfT 금지). 2026-07-05 실측: Hmall 안티봇이 Chrome for
#   Testing 바이너리를 감지해 ID/PW 로그인을 "다른 로그인 수단 이용바랍니다"로 차단함.
#   실제 Chrome + 별도 user-data-dir(Profile 6) 이면 일상 Chrome 과 충돌 없이 로그인 정상.
#   세션(쿠키)은 프로필에 유지되므로 재실행 시 재로그인 거의 불필요.
#
# ▣ 보험(폴백): HMALL_USE_CFT=1 를 주면 예전 CfT 방식으로 즉시 되돌림.
#     예) HMALL_USE_CFT=1 python3 buy/run.py 3
set -e

PORT=9222
USER_DATA_DIR="$HOME/HmallChrome"
PROFILE_DIR="Profile 6"

if [ "${HMALL_USE_CFT:-0}" = "1" ]; then
  # ── 폴백: 예전 Chrome for Testing 방식 ──
  CHROME_BIN=$(ls -d "$HOME/ChromeForTesting"/chrome/mac_*/chrome-mac-*/"Google Chrome for Testing.app"/Contents/MacOS/"Google Chrome for Testing" 2>/dev/null | sort -V | tail -1)
  [ -z "$CHROME_BIN" ] && { echo "[ERROR] CfT 바이너리 없음 — 'npx -y @puppeteer/browsers install chrome@stable --path \$HOME/ChromeForTesting'"; exit 1; }
  ENGINE="CfT(폴백)"
else
  # ── 기본: 실제 Google Chrome ──
  CHROME_BIN="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
  [ -x "$CHROME_BIN" ] || { echo "[ERROR] 실제 Google Chrome 없음: $CHROME_BIN — 폴백: HMALL_USE_CFT=1"; exit 1; }
  ENGINE="실제 Chrome"
fi

if curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:$PORT/json/version" | grep -q 200; then
  echo "[OK] CDP $PORT 이미 실행 중"
  exit 0
fi

mkdir -p "$USER_DATA_DIR"
rm -f "$USER_DATA_DIR"/Singleton* 2>/dev/null || true

"$CHROME_BIN" --remote-debugging-port=$PORT --remote-allow-origins=* \
  --user-data-dir="$USER_DATA_DIR" --profile-directory="$PROFILE_DIR" \
  --no-first-run --no-default-browser-check --disable-popup-blocking \
  "https://www.hmall.com" > /dev/null 2>&1 &
disown

for i in $(seq 1 12); do
  sleep 1
  if curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:$PORT/json/version" | grep -q 200; then
    echo "[OK] $i초 부팅 — Profile 6 ($ENGINE)"
    exit 0
  fi
done
echo "[ERROR] 부팅 실패"
exit 1
