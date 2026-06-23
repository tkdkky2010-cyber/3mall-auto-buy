"""갤러리아 e2+h1 실결제 테스트 (1계정). ⚠️실돈 — 사용자 취소 전제. DRY_PAYMENT=false 로 실행.
사용: DRY_PAYMENT=false python3 buy/_galleria_test_buy.py [idx=1]
"""
from __future__ import annotations
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from playwright.sync_api import sync_playwright
from chrome_launcher import ensure_chrome
from buy.sulwhasoo import (
    galleria_login, galleria_clear_cart, galleria_add_product_by_url, galleria_checkout,
    GALLERIA_PRODUCTS, GALLERIA_ACCOUNTS, GALLERIA_HOME, CDP_PORT, CDP_ENDPOINT,
    load_json, CREDENTIALS_FILE, DRY_PAYMENT,
)


def main() -> int:
    idx = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    combo = [("e", 2), ("h", 1)]
    acc = load_json(GALLERIA_ACCOUNTS)["accounts"][idx - 1]
    cred = load_json(CREDENTIALS_FILE)
    print(f"[INFO] galleria #{idx} {acc['id']} | combo {combo} | DRY_PAYMENT={DRY_PAYMENT}")
    if DRY_PAYMENT:
        print("[WARN] DRY_PAYMENT=true → 실결제 안 됨. 실결제하려면 'DRY_PAYMENT=false' 로 실행.")
    ensure_chrome(int(CDP_PORT))
    with sync_playwright() as p:
        b = p.chromium.connect_over_cdp(CDP_ENDPOINT)
        ctx = b.contexts[0] if b.contexts else b.new_context()
        page = ctx.new_page()
        page.goto(GALLERIA_HOME, wait_until="domcontentloaded", timeout=15000)
        page.wait_for_timeout(2000)
        if not galleria_login(page, acc["id"], acc["pw"]):
            print("[FATAL] 로그인 실패"); return 1
        print("[OK] 로그인")
        galleria_clear_cart(page)
        for sku, qty in combo:
            prod = GALLERIA_PRODUCTS[sku]
            print(f"  [INFO] {sku} ({prod['name']}) x{qty}")
            if not galleria_add_product_by_url(page, prod["goods_no"], qty):
                print(f"[FATAL] {sku} 추가 실패"); return 1
        print("[INFO] 카트 담기 완료 → 결제(Naver Pay)")
        r = galleria_checkout(page, naver_id=cred.get("naver_id", ""),
                              naver_pw=cred.get("naver_pw", ""), naver_pay_pw=cred.get("naver_pay_pw", ""))
        print("[RESULT]", r)
        return 0 if r.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
