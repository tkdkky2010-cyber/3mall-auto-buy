"""롯데 커스텀 조합 cart-only 로더 — 번호조합(COMBOS) 아닌 임의 e2+h1 등 담기.

sulwhasoo.py 의 lotte_login/clear/add 재사용. Chrome(CDP 9222) 연결 전제.

사용:
    python3 buy/_load_lotte_custom.py <idx> "e2+h1"     # e×2 + h×1
    python3 buy/_load_lotte_custom.py 9 "e2,h1"          # 쉼표도 허용
"""
from __future__ import annotations
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from playwright.sync_api import sync_playwright
from chrome_launcher import ensure_chrome
from buy.sulwhasoo import (
    lotte_login, lotte_clear_cart, lotte_add_product_by_url,
    LOTTE_PRODUCTS, LOTTE_ACCOUNTS, LOTTE_HOME, CDP_PORT, CDP_ENDPOINT, load_json,
)


def parse_spec(spec: str) -> list[tuple[str, int]]:
    """'e2+h1' / 'e2,h1' → [('e',2),('h',1)]."""
    out = []
    for tok in re.split(r"[+,]", spec):
        tok = tok.strip()
        m = re.fullmatch(r"([a-z]+)\s*(\d+)", tok)
        if not m:
            raise ValueError(f"조합 토큰 파싱 실패: {tok!r}")
        out.append((m.group(1), int(m.group(2))))
    return out


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 1
    idx = int(sys.argv[1])
    combo = parse_spec(sys.argv[2])
    accounts = load_json(LOTTE_ACCOUNTS)["accounts"]
    acc = accounts[idx - 1]
    print(f"[INFO] lotte #{idx} {acc['id']} 커스텀 조합 {combo} (cart-only)")

    ensure_chrome(int(CDP_PORT))
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(CDP_ENDPOINT)
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        page = context.new_page()
        page.goto(LOTTE_HOME, wait_until="domcontentloaded", timeout=15000)
        page.wait_for_timeout(2000)
        if not lotte_login(page, acc["id"], acc["pw"]):
            print("[FATAL] 로그인 실패")
            return 1
        print("[OK] 로그인")
        if not lotte_clear_cart(page):
            print("[FATAL] 카트 비우기 실패 — 중단(기존 상품 보호)")
            return 1
        for sku, qty in combo:
            prod = LOTTE_PRODUCTS.get(sku)
            if not prod:
                print(f"[FATAL] sku '{sku}' 상품 없음")
                return 1
            print(f"  [INFO] {sku} ({prod['name']}) × {qty}")
            if not lotte_add_product_by_url(page, prod["goods_no"], qty):
                print(f"[FATAL] {sku} 추가 실패")
                return 1
        print(f"\n✓ [lotte] #{idx} 커스텀 조합 {combo} 담기 완료 (cart-only)")
        print("  → 폰 앱에서 동일 계정 로그인 후 결제")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
