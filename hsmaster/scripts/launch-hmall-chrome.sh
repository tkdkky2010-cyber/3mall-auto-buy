#!/bin/bash
# Hmall 자동화용 Chrome for Testing launcher
# Profile 6 (~/HmallChrome/Profile 6) 데이터로 자동 로그인 유지
# 메인 Chrome 과 완전 분리된 별도 .app — 충돌 X, Hmall 봇 차단 회피
set -e

PORT=9222
USER_DATA_DIR="$HOME/HmallChrome"
PROFILE_DIR="Profile 6"

# Chrome for Testing binary 자동 발견 (npx puppeteer/browsers 설치 위치)
CHROME_BIN=$(ls -d "$HOME/ChromeForTesting"/chrome/mac_*/chrome-mac-*/"Google Chrome for Testing.app"/Contents/MacOS/"Google Chrome for Testing" 2>/dev/null | sort -V | tail -1)
[ -z "$CHROME_BIN" ] && { echo "[ERROR] Chrome for Testing 바이너리 없음 — 'npx -y @puppeteer/browsers install chrome@stable --path \$HOME/ChromeForTesting' 실행"; exit 1; }

if curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:$PORT/json/version" | grep -q 200; then
  echo "[OK] CDP $PORT 이미 실행 중"
  exit 0
fi

"$CHROME_BIN" --remote-debugging-port=$PORT --remote-allow-origins=* \
  --user-data-dir="$USER_DATA_DIR" --profile-directory="$PROFILE_DIR" \
  --no-first-run --no-default-browser-check "https://www.hmall.com" > /dev/null 2>&1 &
disown

for i in 1 2 3 4 5 6 7 8 9 10; do
  sleep 1
  if curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:$PORT/json/version" | grep -q 200; then
    echo "[OK] $i초 부팅 — Profile 6 (CFT)"
    exit 0
  fi
done
echo "[ERROR] 부팅 실패"
exit 1
