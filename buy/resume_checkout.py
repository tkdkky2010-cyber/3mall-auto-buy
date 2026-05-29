"""Cart 이미 채워진 상태에서 checkout + 폰 자동결제만 진행 (login/clear/add 생략).

사용: python3 buy/resume_checkout.py
"""
from __future__ import annotations
import os, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "buy"))
os.environ["FLOW_USE_CAMERA"] = "1"

from buy.run import (
    do_checkout,
    trigger_phone_payment,
    apply_beauty_point_on_order_complete,
    DRY_PAYMENT,
    CDP_ENDPOINT,
)

from patchright.sync_api import sync_playwright


def _beauty_point_account_idx() -> int | None:
    raw = os.environ.get("BEAUTY_POINT_ACCOUNT_IDX", "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        print(f"[WARN] BEAUTY_POINT_ACCOUNT_IDX invalid: {raw!r}")
        return None


def main():
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(CDP_ENDPOINT)
        ctx = browser.contexts[0]
        # cart/order 페이지 우선
        page = None
        for pg in ctx.pages:
            if "hmall.com" in pg.url and ("order" in pg.url or "basket" in pg.url or "odb" in pg.url):
                page = pg; break
        if not page:
            page = ctx.pages[0]
        print(f"[INFO] cart page: {page.url}")
        page.bring_to_front()

        result = do_checkout(page)
        print(f"\n[INFO] checkout result: success={result['success']} code={result.get('code')} card_cd={result.get('card_cd')}")
        if result.get("error"):
            print(f"  error: {result['error']}")
            return 1

        if result["success"] and result.get("code") and not DRY_PAYMENT and result.get("card_cd"):
            phone = trigger_phone_payment(result["card_cd"], result["code"])
            print(f"\n[INFO] phone result: success={phone['success']}")
            if phone.get("error"):
                print(f"  error: {phone['error']}")
            if phone.get("log_tail"):
                print(f"  log tail:\n{phone['log_tail']}")
            if phone.get("success"):
                beauty = apply_beauty_point_on_order_complete(page, account_idx=_beauty_point_account_idx())
                print(f"\n[INFO] beauty point result: success={beauty['success']} clicked={beauty['clicked']}")
                if beauty.get("error"):
                    print(f"  error: {beauty['error']}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
