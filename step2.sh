#!/bin/bash
# Step 2 한 번에 — Chrome launch (idempotent) + check10.py + inspect 결과 표시.
# 사용: bash step2.sh
cd "$(dirname "$0")"

echo "▶ Step 2 시작 (Hmall 10% 적립 체크) — $(date '+%Y-%m-%d %H:%M:%S')"
echo

# 1) Chrome CDP 9223 (이미 떠있으면 즉시 OK)
bash launch-check10-chrome.sh
echo

# 2) check10 — 23개 상품 약 5-10분
python3 cart/check10.py
CHECK_RC=$?
echo

# 3) 빠른 확인 (today.json 표 형태)
echo "═════════ 빠른 확인 (cart/inspect.py) ═════════"
python3 cart/inspect.py

exit $CHECK_RC
