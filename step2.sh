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

# 재고관리 시트 '당일 식품' 탭에 미러 (사용자 지시 2026-08-06 — 날짜탭 신설 없이 덮어쓰기).
# 기존 출력(공급률 시트 'M.DD 식품' 탭)은 그대로. 실패해도 step2 rc 는 check10 결과를 따른다.
echo "═════════ 당일 식품 미러 ═════════"
python3 rate-check/mirror_daily.py food || echo "⚠️ 미러 실패 — 원본('M.DD 식품' 탭)은 정상. 수동: python3 rate-check/mirror_daily.py food"
echo

# ★단일 CFT 원칙(2026-07-16): check10 도 로그인된 CFT 9222 사용 — 별도 Chrome 안 띄우고 안 죽임.
exit $CHECK_RC
