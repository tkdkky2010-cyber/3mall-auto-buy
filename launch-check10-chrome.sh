#!/bin/bash
# check10용 Chrome 런처 — 포트 9223.
# ★기본 = 실제 Google Chrome 바이너리 (CfT 금지). 2026-07-05 실측: Hmall 안티봇이 Chrome for
#   Testing 바이너리를 감지해 ID/PW 로그인을 "다른 로그인 수단 이용바랍니다"로 차단함.
#   실제 Chrome + 별도 user-data-dir 이면 일상 Chrome 과 충돌 없이 로그인 정상.
#
# ▣ 보험(폴백): HMALL_USE_CFT=1 를 주면 예전 CfT 방식으로 즉시 되돌림.
#     예) HMALL_USE_CFT=1 bash step2.sh   또는   HMALL_USE_CFT=1 python3 cart/check10.py
#     실 Chrome / CfT 는 프로필 디렉토리가 분리돼 있어 서로 간섭 없음.
# Idempotent: 이미 9223 살아있으면 그대로 종료. 세션(쿠키)은 프로필에 유지됨.
set -e

PORT=9223

if [ "${HMALL_USE_CFT:-0}" = "1" ]; then
  # ── 폴백: 예전 Chrome for Testing 방식 ──
  USER_DATA_DIR="$HOME/Check10Chrome"
  CHROME_BIN=$(ls -d "$HOME/ChromeForTesting"/chrome/mac_*/chrome-mac-*/"Google Chrome for Testing.app"/Contents/MacOS/"Google Chrome for Testing" 2>/dev/null | sort -V | tail -1)
  [ -z "$CHROME_BIN" ] && { echo "[ERROR] CfT 바이너리 없음 — 'npx -y @puppeteer/browsers install chrome@stable --path \$HOME/ChromeForTesting'"; exit 1; }
  ENGINE="CfT(폴백)"
else
  # ── 기본: 실제 Google Chrome ──
  USER_DATA_DIR="$HOME/Check10RealChrome"
  CHROME_BIN="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
  [ -x "$CHROME_BIN" ] || { echo "[ERROR] 실제 Google Chrome 없음: $CHROME_BIN — 폴백: HMALL_USE_CFT=1"; exit 1; }
  ENGINE="실제 Chrome"
fi

# 1) 이미 살아있으면 OK
if curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:$PORT/json/version" | grep -q 200; then
  echo "[OK] CDP $PORT 이미 실행 중"
  exit 0
fi

# 2) Stale lock 정리
mkdir -p "$USER_DATA_DIR"
rm -f "$USER_DATA_DIR"/Singleton* 2>/dev/null || true

# 3) Launch (별도 프로필 → 일상 Chrome 과 분리)
"$CHROME_BIN" --remote-debugging-port=$PORT --remote-allow-origins=* \
  --user-data-dir="$USER_DATA_DIR" \
  --no-first-run --no-default-browser-check --disable-popup-blocking \
  --lang=ko-KR --window-size=1280,900 \
  "https://www.hmall.com" > /dev/null 2>&1 &
disown

# 4) 부팅 대기 (최대 12초)
for i in $(seq 1 12); do
  sleep 1
  if curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:$PORT/json/version" | grep -q 200; then
    echo "[OK] $i초 부팅 — CDP $PORT ($ENGINE, dir=$USER_DATA_DIR)"
    exit 0
  fi
done
echo "[ERROR] 부팅 실패 — ps aux | grep 9223 으로 확인"
exit 1
