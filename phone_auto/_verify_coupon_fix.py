"""[스크래치] 쿠폰 비활성-필터 수정본 검증. 장바구니부터 재출발 → 결제설정(주소/할인/플러스/포인트/현금)까지만.
플러스쿠폰 3제품 전부 적용 + '할인혜택 초기화' 팝업 없음을 확인하고 **카드/결제 전에 정지**.
매 탭에 팝업 출현 감시(WARN만, 멈추진 않음). usage: python3 phone_auto/_verify_coupon_fix.py 11
"""
from __future__ import annotations
import sys, time
sys.path.insert(0, ".")
sys.path.insert(0, "phone_auto")

import lotte_homeshopping_buy as L
from phone_auto.adb_input import ADB
from phone_auto.hmall_hyundai_buy import _resolve_serial, cap
from phone_auto.flow_runner import _ocr_texts

POPUP_HITS = []
_orig_tap = ADB.tap


def watched_tap(self, x, y):
    _orig_tap(self, x, y)
    time.sleep(0.8)
    txt = " ".join(t["text"] for t in _ocr_texts(cap()))
    if any(k in txt for k in ("초기화", "적용된 할인혜택")):
        POPUP_HITS.append((x, y))
        print(f"   ⚠️ 팝업 출현 (탭 {x},{y} 직후)", flush=True)


ADB.tap = watched_tap


def main():
    idx = int(sys.argv[1]) if len(sys.argv) > 1 else 11
    _resolve_serial()
    print(f"[#{idx}] 장바구니 재출발 — 쿠폰 수정본 검증", flush=True)
    L.reset_lotte_app(); L.dismiss_popups()
    if not L.logout():
        print("LOGOUT_FAIL"); return 1
    if not L.login(idx).get("ok"):
        print("LOGIN_FAIL"); return 1
    cs = L.goto_cart_select_all()
    print(f"cart: {cs}", flush=True)
    if not cs.get("ok"):
        print("CART_FAIL"); return 1

    print(f"addr:     {L.set_address()}", flush=True)
    print(f"discount: {L.set_discount_coupons()}", flush=True)
    print(f"plus:     {L.set_plus_coupons()}", flush=True)
    print(f"points:   {L.use_all_points()}", flush=True)
    print(f"cash:     {L.set_cash_receipt()}", flush=True)

    time.sleep(1.0)
    its = sorted(_ocr_texts(cap()), key=lambda z: z["cy"])
    txt = " ".join(t["text"] for t in its)
    print("\n===== 검증 결과 =====")
    print(f"  팝업 출현 횟수: {len(POPUP_HITS)}  {'✅ 없음' if not POPUP_HITS else '❌ ' + str(POPUP_HITS)}")
    print(f"  '초기화' 현재화면: {'❌ 있음' if '초기화' in txt else '✅ 없음'}")
    print("  주문서 쿠폰/금액:")
    for t in its:
        if any(k in t["text"] for k in ("할인쿠폰", "플러스쿠폰", "적립금", "L.POINT", "주문금액", "결제하기", "합계")):
            print(f"     ({t['cx']:4d},{t['cy']:4d})  {t['text']}")
    print("\n▶ 카드/결제 전 정지. 통과 시 삼성 결제 관찰로.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
