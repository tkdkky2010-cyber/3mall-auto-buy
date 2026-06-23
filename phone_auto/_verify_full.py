"""[스크래치] 전체 새 흐름 검증: 장바구니→주소→할인/플러스쿠폰→포인트→**카드선택(신규 그리드 루트)→현금영수증→동의**.
결제 직전(결제하기 전)에 정지. 카드=당일카드 자동감지, 팝업 0 확인. usage: python3 phone_auto/_verify_full.py 11
"""
from __future__ import annotations
import sys, time
sys.path.insert(0, "."); sys.path.insert(0, "phone_auto")

import lotte_homeshopping_buy as L
from phone_auto.adb_input import ADB
from phone_auto.hmall_hyundai_buy import _resolve_serial, cap
from phone_auto.flow_runner import _ocr_texts

POPUP_HITS = []
_orig = ADB.tap
def watched(self, x, y):
    _orig(self, x, y); time.sleep(0.7)
    t = " ".join(i["text"] for i in _ocr_texts(cap()))
    if any(k in t for k in ("초기화", "적용된 할인혜택")):
        POPUP_HITS.append((x, y)); print(f"   ⚠️ 팝업 (탭 {x},{y})", flush=True)
ADB.tap = watched


def main():
    idx = int(sys.argv[1]) if len(sys.argv) > 1 else 11
    _resolve_serial()
    print(f"[#{idx}] 전체 새 흐름 검증 — 장바구니 재출발", flush=True)
    L.reset_lotte_app(); L.dismiss_popups()
    if not L.logout(): print("LOGOUT_FAIL"); return 1
    if not L.login(idx).get("ok"): print("LOGIN_FAIL"); return 1
    cs = L.goto_cart_select_all(); print(f"cart: {cs}", flush=True)
    if not cs.get("ok"): print("CART_FAIL"); return 1

    print(f"addr:     {L.set_address()}", flush=True)
    print(f"discount: {L.set_discount_coupons()}", flush=True)
    print(f"plus:     {L.set_plus_coupons()}", flush=True)
    print(f"points:   {L.use_all_points()}", flush=True)
    print(f"CARD:     {L.select_card_lotte(day=None)}", flush=True)   # ★신규 그리드 루트
    print(f"cash:     {L.set_cash_receipt()}", flush=True)            # ★카드 뒤
    print(f"agree:    {L.agree_required()}", flush=True)

    time.sleep(1.0)
    its = sorted(_ocr_texts(cap()), key=lambda z: z["cy"])
    txt = " ".join(t["text"] for t in its)
    print("\n===== 검증 결과 =====")
    print(f"  팝업: {len(POPUP_HITS)}회  {'✅' if not POPUP_HITS else '❌'+str(POPUP_HITS)}")
    print("  주문서 카드/현금/금액:")
    for t in its:
        if any(k in t["text"] for k in ("카드 선택", "삼성카드", "롯데카드", "할부", "지출증빙", "사업자",
                                         "507", "청구할인", "동의", "결제하기", "할인쿠폰", "플러스")):
            print(f"     ({t['cx']:4d},{t['cy']:4d})  {t['text']}")
    print("\n▶ 결제하기 전 정지 (실결제 X).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
