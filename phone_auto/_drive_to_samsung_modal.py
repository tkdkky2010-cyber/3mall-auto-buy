"""[스크래치] 롯데홈쇼핑 #N 계정을 **코드화된 함수 재사용**으로 삼성 SDK 결제 모달 직전까지 빠르게 주행.
원 '결제하기' 탭(=삼성 SDK 모달 트리거)까지만 하고 정지. 이후 삼성 결제 구간은 라이브 관찰/코드화.
usage: python3 phone_auto/_drive_to_samsung_modal.py 11
"""
from __future__ import annotations
import sys
import time

import lotte_homeshopping_buy as L
from lotte_homeshopping_buy import (
    reset_lotte_app, dismiss_popups, logout, login, goto_cart_select_all,
    set_address, set_discount_coupons, set_plus_coupons, use_all_points,
    set_cash_receipt, select_card_lotte, agree_required, _all_text,
)
from phone_auto.hmall_hyundai_buy import cap, _adb, ocr_tap, screen_has, _resolve_serial
from phone_auto.flow_runner import _ocr_texts


def dump(tag: str) -> None:
    print(f"\n===== OCR [{tag}] =====", flush=True)
    for t in sorted(_ocr_texts(cap()), key=lambda z: z["cy"]):
        print(f"  ({t['cx']:4d},{t['cy']:4d})  {t['text']}", flush=True)


def main() -> int:
    idx = int(sys.argv[1]) if len(sys.argv) > 1 else 11
    _resolve_serial()
    print(f"[#{idx}] 삼성 SDK 모달 직전까지 주행 시작", flush=True)

    reset_lotte_app(); dismiss_popups()
    if not logout():
        print("LOGOUT_FAIL"); return 1
    lr = login(idx)
    print(f"  login: {lr}", flush=True)
    if not lr.get("ok"):
        print(f"LOGIN_FAIL:{lr.get('err')}"); return 1
    cs = goto_cart_select_all()
    print(f"  cart: {cs}", flush=True)
    if not cs.get("ok"):
        print(f"CART_FAIL:{cs.get('err')}"); return 1

    print(f"  addr: {set_address()}", flush=True)
    print(f"  discount: {set_discount_coupons()}", flush=True)
    print(f"  plus: {set_plus_coupons()}", flush=True)
    print(f"  points: {use_all_points()}", flush=True)
    print(f"  cash: {set_cash_receipt()}", flush=True)

    sc = select_card_lotte(day=None)      # 자동감지 (오늘=삼성 예상)
    print(f"  card: {sc}", flush=True)
    if not sc.get("ok"):
        print(f"CARD_FAIL:{sc.get('err')}"); return 1
    print(f"  agree: {agree_required()}", flush=True)

    # 원 '결제하기' → 삼성 SDK 모달 트리거 (pay_lotte_payco 앞부분 동일)
    if screen_has("다음에도") or screen_has("사용할까요"):
        ocr_tap("사용할게요", contains=True, retries=2)
    pay = next((it for it in _ocr_texts(cap()) if "결제하기" in it["text"] and it["cy"] > 2000), None)
    if pay:
        print(f"  원결제하기 탭 ({pay['cx']},{pay['cy']})", flush=True)
        _adb().tap(pay["cx"], pay["cy"])
    elif not ocr_tap("결제하기", contains=True):
        print("원결제하기 실패"); dump("결제하기 실패 화면"); return 1
    time.sleep(4.0)

    dump("삼성 SDK 모달(추정)")
    print("\n▶ 여기서 정지. 삼성 결제 구간 라이브 관찰 시작.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
