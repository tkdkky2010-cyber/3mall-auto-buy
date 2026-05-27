#!/bin/bash
# Step 2 한 번에 — check10.py (Chrome 자동 launch 포함) + inspect.
# 사용: bash step2.sh
cd "$(dirname "$0")"

echo "▶ Step 2 시작 (Hmall 10% 적립 체크) — $(date '+%Y-%m-%d %H:%M:%S')"
echo

# check10.py 가 9223 자동 launch + 23개 상품 체크 + 시트 입력
python3 cart/check10.py
CHECK_RC=$?
echo

echo "═════════ 빠른 확인 (cart/show.py) ═════════"
python3 cart/show.py
echo

# Step 2 종료 — Check10Chrome(9223)만 완전 종료 (개인 Chrome / 9222 HmallChrome 은 보존)
echo "═════════ Chrome 정리 ═════════"
if pkill -f "Check10Chrome" 2>/dev/null; then
  echo "[OK] 9223 Check10Chrome 종료"
else
  echo "[INFO] 종료할 9223 Chrome 없음"
fi

exit $CHECK_RC
