"""삼성 캐러셀(당일 할인카드 자동감지+할인) 검증 — 결제 직전(삼성 PAYCO 팝업)까지만. ⚠️실결제 X.
buy_one 의 A(공통전)~B(카드선택) + 원결제하기 까지만 재사용하고, PAYCO 팝업 도달 확인 후 즉시 종료.
PIN/PAYCO결제하기 는 절대 안 누름 → 돈 안 나감.
사용: python3 -m phone_auto._verify_samsung_carousel <idx>
"""
import os
import subprocess
import sys

from phone_auto import hmall_hyundai_buy as m
from phone_auto import hmall_webview as hw


def main() -> int:
    if len(sys.argv) < 2 or not sys.argv[1].isdigit():
        print("사용: python3 -m phone_auto._verify_samsung_carousel <idx>"); return 2
    idx = int(sys.argv[1])
    m._resolve_serial()
    serial = hw._serial()
    print(f"\n[검증] 계정 #{idx} — 삼성 캐러셀 자동감지+할인, 결제 직전까지만 (⚠️실결제 X)\n", flush=True)

    hw.reset_to_main(serial)
    m.close_home_popup()
    lr = hw.login_account(idx, serial)
    if not lr.get("success"):
        print(f"LOGIN_FAIL: {lr.get('error')}"); return 1

    cs = hw.cart_state(serial)
    if cs.get("empty"):
        print("❌ 카트 비어있음 — 장바구니에 상품 담은 계정으로 해주세요"); return 1

    if not m.goto_cart():
        print("CART_NAV_FAIL"); return 1
    ok, sel = m.cdp_select_all()
    print(f"전체선택 {sel} ok={ok}", flush=True)
    if not ok:
        print(f"SELECT_ALL_FAIL:{sel}"); return 1

    if not m.ocr_tap("구매하기"):
        print("BUY_FAIL(구매하기)"); return 1
    if not m.wait_text("결제하기", timeout=15):
        print("ORDER_PAGE_FAIL(주문서 미도달)"); return 1

    # ── 핵심: 당일카드 자동감지 → 캐러셀 선택 ──
    day = m.detect_card()
    print(f"\n★ 당일카드 자동감지 = {day}", flush=True)
    if day != "삼성":
        print(f"⚠️ 당일카드가 삼성이 아님(={day}) → 캐러셀 검증 불가. 종료(결제 안 함)."); return 1
    sc = m.select_card("삼성", day)          # day==card → select_card_discount (캐러셀)
    print(f"★ select_card 결과 = {sc}", flush=True)
    if not sc.get("ok"):
        print(f"❌ 캐러셀 선택 실패: {sc.get('err')}"); return 1
    print("✅ 캐러셀(삼성 할인) 선택 성공", flush=True)

    # 원결제하기 (= 삼성 PAYCO 팝업 띄우기. 아직 결제 아님)
    if not m.ocr_tap("결제하기", contains=True):
        print("원결제하기(결제하기) 탭 실패"); return 1
    reached = m.wait_text("PAYCO", timeout=12)

    out = os.path.join("phone_auto", "_tmp", "_samsung_carousel_popup.png")
    with open(out, "wb") as f:
        subprocess.run([hw.ADB, "-s", serial, "exec-out", "screencap", "-p"], stdout=f)

    print("\n" + "=" * 54)
    if reached:
        print(f"✅✅ 삼성 결제 팝업(PAYCO) 도달 — 캐러셀+자동감지+할인 경로 검증 OK")
    else:
        print(f"⚠️ PAYCO 팝업 미감지 — 캡처로 화면 확인 필요")
    print(f"   캡처: {out}")
    print("   [종료] PIN/PAYCO결제하기 진행 안 함 — 돈 안 나감.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
