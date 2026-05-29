"""Single hmall card cycle — account #1, product c (설화수 자음2종 slitmCd=2228722509).

HYUNDAI_6CARD_GOAL.md 전용 driver. 기존 buy/run.py 의 add_to_cart / do_checkout /
trigger_phone_payment / clear_cart 함수 재사용 (surgical — products.json 변경 없음).

사용:
    python3 -m buy.hmall_card_cycle --card "비씨카드(페이북)"         # full cycle
    python3 -m buy.hmall_card_cycle --card "비씨카드(페이북)" --dry   # 7자리 코드 추출까지만

전제:
- CDP 9222 살아있음 + account 1 로그인 상태 (사용자가 직접 세션 띄움)
- adb devices 1대 연결
"""
from __future__ import annotations
import argparse, os, sys, json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

# TODAY_BRAND 는 buy.run import 전에 set — module-level 상수가 import 시점에 stamp 됨
def _set_brand_env(card_name: str):
    os.environ["TODAY_BRAND"] = card_name
    os.environ.setdefault("DRY_PAYMENT", "false")

C_INFO = {
    "name": "설화수 자음2종 (에센셜 데일리 세트)",
    "slitmCd": "2228722509",
    "url_extra": "",
    "option_index": 1,
    "auto_coupon": False,
}


def _is_logged_in(page) -> bool:
    """mypage 진입 시도 — 로그인 페이지로 redirect 되면 false."""
    page.goto("https://www.hmall.com/mo/mp/mainView", wait_until="domcontentloaded", timeout=15000)
    page.wait_for_timeout(1500)
    url = page.url
    return "loginForm" not in url and "/login" not in url.lower()


def _close_stale_payment_tabs(ctx) -> int:
    """stale checkout/vpay popup tab close — 매 cycle 시작 시 호출.
    vpay popup 누적 시 다음 카드 cycle 에서 직전 코드를 stale grab 함 (메모리 가드레일).
    """
    closed = 0
    for page in list(ctx.pages):
        url = page.url
        if ("vpay.co.kr" in url
            or "/oda/order" in url
            or "/odb/order" in url
            or "orderRequest" in url):
            try:
                page.close()
                closed += 1
            except Exception:
                pass
    if closed:
        print(f"[CLEAN] stale checkout/vpay tab {closed}개 close")
    return closed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--card", required=True, help="TODAY_BRAND override e.g. '비씨카드(페이북)', '현대카드'")
    ap.add_argument("--dry", action="store_true", help="7자리 코드 추출까지만, 폰 트리거 X")
    ap.add_argument("--account", type=int, default=1)
    args = ap.parse_args()

    _set_brand_env(args.card)
    if args.dry:
        os.environ["DRY_PAYMENT"] = "true"

    # buy.run 은 import 시점에 TODAY_BRAND / DRY_PAYMENT 읽음 — env 세팅 후 import
    from buy.run import (
        clear_cart, add_to_cart, do_checkout, trigger_phone_payment,
        apply_beauty_point_on_order_complete,
        ACCOUNTS_FILE, CDP_ENDPOINT,
    )
    from playwright.sync_api import sync_playwright

    accounts = json.loads(Path(ACCOUNTS_FILE).read_text())["accounts"]
    acc = accounts[args.account - 1]
    print(f"[INFO] account #{args.account} {acc['id']} → card='{args.card}' dry={args.dry}")

    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(CDP_ENDPOINT, slow_mo=400)
        except Exception as e:
            print(f"[FATAL] CDP {CDP_ENDPOINT} connect 실패: {e}")
            return 1

        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        _close_stale_payment_tabs(ctx)
        page = ctx.new_page()

        if not _is_logged_in(page):
            print(f"[FATAL] account #{args.account} 로그인 안 된 상태 — Chrome 에서 직접 로그인 후 재실행")
            page.close()
            return 1
        print(f"[OK] 로그인 상태 확인")

        clear_cart(page)
        if not add_to_cart(page, "c", C_INFO, 1):
            print("[FAIL] add_to_cart")
            page.close()
            return 1

        result = do_checkout(page)
        print("[CHECKOUT RESULT]")
        print(json.dumps(result, ensure_ascii=False, indent=2))

        if not result.get("success"):
            page.close()
            return 1

        if result.get("is_pay"):
            print("[SKIP] PAY brand 선택됨 — 폰 트리거 안 함")
            page.close()
            return 0

        if not result.get("code") or not result.get("card_cd"):
            print("[FAIL] code 또는 card_cd 없음")
            page.close()
            return 1

        print(f"[CODE] {result['card_brand']} 7자리: {result['code']}")
        if args.dry:
            print("[DRY] 폰 트리거 skip")
            page.close()
            return 0

        # 폰 트리거 (camera 모드 — trigger_phone_payment 내부에서 FLOW_USE_CAMERA=1 set)
        phone_res = trigger_phone_payment(result["card_cd"], result["code"], timeout_sec=300)
        print("[PHONE RESULT]")
        print(json.dumps(phone_res, ensure_ascii=False, indent=2))
        if phone_res.get("success"):
            beauty = apply_beauty_point_on_order_complete(page, account_idx=args.account)
            print("[BEAUTY POINT RESULT]")
            print(json.dumps(beauty, ensure_ascii=False, indent=2))
        page.close()
        # 결제 끝나든 실패하든 stale tab 정리 (사용자 가드레일 — 안 헷갈리게)
        _close_stale_payment_tabs(ctx)
        return 0 if phone_res.get("success") else 2


if __name__ == "__main__":
    sys.exit(main())
