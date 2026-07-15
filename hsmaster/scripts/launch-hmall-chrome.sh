#!/bin/bash
# Hmall / rate-check 자동화용 Chrome launcher — 포트 9222.
# ★기본 = 구글 로그인된 Chrome for Testing (CFT).
#   프로필 = ~/Library/Application Support/Google/Chrome for Testing / "Profile 1"
#            (tkdkky2021@gmail.com 구글 로그인 상태).
#   사용자 지시(2026-07-15): 이 CFT 항상 사용 — 구글 로그인되어 있어 롯데/몰에서 안 튕김.
#   (예: 롯데 goods 페이지 콜드 세션 403 회피. rate-check 는 몰 로그인 불필요.)
#
# ▣ 보험(폴백): HMALL_USE_REAL_CHROME=1 → 예전 "실제 Chrome Profile 6" 방식으로 되돌림.
#     예) HMALL_USE_REAL_CHROME=1 python3 buy/run.py 3
#   (참고: 2026-07-05 실측상 로그인 안 된 CFT 는 Hmall 안티봇에 로그인 차단됐으나,
#    구글 로그인된 이 프로필은 사용자 환경에서 정상. Hmall 로그인 재확인 필요 시 폴백 사용.)
# Idempotent: 이미 9222 살아있으면 그대로 종료. 세션(쿠키)은 프로필에 유지됨.
set -e

PORT=9222

# 1) 이미 포트 살아있으면 그대로 재사용
if curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:$PORT/json/version" | grep -q 200; then
  echo "[OK] CDP $PORT 이미 실행 중"
  exit 0
fi

if [ "${HMALL_USE_REAL_CHROME:-0}" = "1" ]; then
  # ── 폴백: 실제 Google Chrome (구 기본) ──
  CHROME_BIN="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
  [ -x "$CHROME_BIN" ] || { echo "[ERROR] 실제 Google Chrome 없음: $CHROME_BIN"; exit 1; }
  USER_DATA_DIR="$HOME/HmallChrome"
  PROFILE_DIR="Profile 6"
  START_URL="https://www.hmall.com"
  ENGINE="실제 Chrome(폴백)"
else
  # ── 기본: 구글 로그인된 CFT ──
  CHROME_BIN=$(ls -d "$HOME/ChromeForTesting"/chrome/mac_*/chrome-mac-*/"Google Chrome for Testing.app"/Contents/MacOS/"Google Chrome for Testing" 2>/dev/null | sort -V | tail -1)
  [ -z "$CHROME_BIN" ] && { echo "[ERROR] CFT 바이너리 없음 — 'npx -y @puppeteer/browsers install chrome@stable --path \$HOME/ChromeForTesting'"; exit 1; }
  USER_DATA_DIR="$HOME/Library/Application Support/Google/Chrome for Testing"
  PROFILE_DIR="Profile 1"   # tkdkky2021@gmail.com 구글 로그인
  START_URL="about:blank"
  ENGINE="CFT(구글로그인)"

  # 포트 없이 수동 실행된 CFT 가 이 프로필을 잡고 있으면 CDP attach 불가 →
  # graceful quit 후 포트 붙여 재실행 (같은 프로필 → 로그인 유지).
  if pgrep -f "Google Chrome for Testing" >/dev/null 2>&1; then
    echo "[INFO] 포트없는 CFT 감지 → graceful quit 후 포트 붙여 재실행"
    osascript -e 'quit app "Google Chrome for Testing"' >/dev/null 2>&1 || true
    for i in $(seq 1 6); do pgrep -f "Google Chrome for Testing" >/dev/null 2>&1 || break; sleep 1; done
    pgrep -f "Google Chrome for Testing" >/dev/null 2>&1 && { pkill -f "Google Chrome for Testing"; sleep 2; }
  fi
fi

mkdir -p "$USER_DATA_DIR"
rm -f "$USER_DATA_DIR"/Singleton* 2>/dev/null || true

"$CHROME_BIN" --remote-debugging-port=$PORT "--remote-allow-origins=*" \
  --user-data-dir="$USER_DATA_DIR" --profile-directory="$PROFILE_DIR" \
  --no-first-run --no-default-browser-check --disable-popup-blocking \
  "$START_URL" > /dev/null 2>&1 &
disown

for i in $(seq 1 12); do
  sleep 1
  if curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:$PORT/json/version" | grep -q 200; then
    echo "[OK] $i초 부팅 — $PROFILE_DIR ($ENGINE)"
    exit 0
  fi
done
echo "[ERROR] 부팅 실패"
exit 1
