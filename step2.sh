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

exit $CHECK_RC
