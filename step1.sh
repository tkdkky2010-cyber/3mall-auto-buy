#!/bin/bash
# Step 1 한 번에 — 3사 공급률 분석 + 재고관리 + 카트플랜(O열).
# galleria → inventory(--apply) → hmall → lotte → cart_plan 순차 실행.
# ★ inventory(사은품 재고버전) + cart_plan(O열) 누락 방지: 항상 자동 실행 (2026-05-29 step1.sh 신설 이유).
# 사용: bash step1.sh
cd "$(dirname "$0")"

echo "▶ Step 1 시작 (3사 공급률 + 재고관리 + 카트플랜) — $(date '+%Y-%m-%d %H:%M:%S')"

# 1) 갤러리아 — GWP 첫 실행 시 이미지 다운로드 후 PENDING(exit 2).
#    안내대로 _tmp/gwp_{date}.json 판독 작성 후 'bash step1.sh' 재실행하면 이어서 진행.
echo
echo "═════════ 1/5 갤러리아 ═════════"
python3 rate-check/galleria.py
RC=$?
if [ $RC -eq 2 ]; then
  echo
  echo "⏸ GWP 판독 필요 — 위 안내대로 JSON 작성 후 'bash step1.sh' 재실행하세요."
  exit 2
fi
if [ $RC -ne 0 ]; then echo "❌ 갤러리아 실패 (rc=$RC)"; exit $RC; fi

# 2) 재고관리 비교 (galleria 구성 기반). 사은품 구성 바뀌면 새 버전 자동 추가(append-only),
#    변경 없으면 no-op. galleria 직후 = 의존성(galleria sheet) 충족 위치.
echo
echo "═════════ 2/5 재고관리 (inventory) ═════════"
python3 rate-check/inventory.py --apply || { echo "❌ inventory 실패"; exit 1; }

# 3) Hmall (20조합 실측)
echo
echo "═════════ 3/5 Hmall ═════════"
python3 rate-check/hmall.py || { echo "❌ Hmall 실패"; exit 1; }

# 4) 롯데
echo
echo "═════════ 4/5 롯데 ═════════"
python3 rate-check/lotte.py || { echo "❌ 롯데 실패"; exit 1; }

# 5) 카트플랜 (O열) — 절대 빠지면 안 됨
echo
echo "═════════ 5/5 카트플랜 (O열) ═════════"
python3 rate-check/cart_plan.py || { echo "❌ cart_plan 실패 — O열 미입력"; exit 1; }

# 6) 재고관리 시트 '당일 설화수' 탭에 미러 (사용자 지시 2026-08-06 — 날짜탭 신설 없이 덮어쓰기).
#    기존 출력(공급률 시트 M.D 탭)은 그대로 두고 한 벌 더 남기는 것 → 실패해도 step1 은 성공 처리.
echo
echo "═════════ 6/6 당일 설화수 미러 ═════════"
python3 rate-check/mirror_daily.py sulwhasoo || echo "⚠️ 미러 실패 — 원본(M.D 탭)은 정상. 수동: python3 rate-check/mirror_daily.py sulwhasoo"

echo
echo "✅ Step 1 완료 — 3사 공급률 + 재고관리(n버전) + 카트플랜(O열) 모두 입력됨 — $(date '+%H:%M:%S')"
