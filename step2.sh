#!/bin/bash
# Step 2 한 번에 — check10.py (Chrome 자동 launch 포함) + inspect.
# 사용: bash step2.sh
cd "$(dirname "$0")"

echo "▶ Step 2 시작 (Hmall 10% 적립 체크) — $(date '+%Y-%m-%d %H:%M:%S')"
echo

# check10.py 가 로그인된 CFT 9222 재사용 + 상품 체크 + 시트 입력
python3 cart/check10.py
CHECK_RC=$?
echo

echo "═════════ 빠른 확인 (cart/show.py) ═════════"
python3 cart/show.py
echo

# ★단일 CFT 원칙(2026-07-16): check10 도 로그인된 CFT 9222 사용 — 별도 Chrome 안 띄우고 안 죽임.
exit $CHECK_RC
