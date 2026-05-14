#!/bin/bash
# check10용 Chrome for Testing 런처 — launch-hmall-chrome.sh 패턴 미러링
# 포트 9223, ~/Check10Chrome (절대 경로 user-data-dir, Default 프로필).
# Idempotent: 이미 9223 살아있으면 그대로 종료.
set -e

PORT=9223
USER_DATA_DIR="$HOME/Check10Chrome"

# Chrome for Testing binary 자동 발견
CHROME_BIN=$(ls -d "$HOME/ChromeForTesting"/chrome/mac_*/chrome-mac-*/"Google Chrome for Testing.app"/Contents/MacOS/"Google Chrome for Testing" 2>/dev/null | sort -V | tail -1)
[ -z "$CHROME_BIN" ] && {
  echo "[ERROR] Chrome for Testing 바이너리 없음"
  echo "  설치: 'npx -y @puppeteer/browsers install chrome@stable --path \$HOME/ChromeForTesting'"
  exit 1
}

# 1) 이미 살아있으면 OK
if curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:$PORT/json/version" | grep -q 200; then
  echo "[OK] CDP $PORT 이미 실행 중"
  exit 0
fi

# 2) Stale lock 정리
mkdir -p "$USER_DATA_DIR"
rm -f "$USER_DATA_DIR"/Singleton*

# 3) Launch — hmall 패턴: --remote-allow-origins, --disable-popup-blocking, lang, window-size, 시작 URL
"$CHROME_BIN" --remote-debugging-port=$PORT --remote-allow-origins=* \
  --user-data-dir="$USER_DATA_DIR" \
  --no-first-run --no-default-browser-check --disable-popup-blocking \
  --lang=ko-KR --window-size=1280,900 \
  "https://www.hmall.com" > /dev/null 2>&1 &
disown

# 4) 부팅 대기 (최대 10초)
for i in 1 2 3 4 5 6 7 8 9 10; do
  sleep 1
  if curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:$PORT/json/version" | grep -q 200; then
    echo "[OK] $i초 부팅 — CDP $PORT (Check10Chrome)"
    exit 0
  fi
done
echo "[ERROR] 부팅 실패 — Chrome 프로세스 살았는지 ps aux | grep 9223 으로 확인"
exit 1
