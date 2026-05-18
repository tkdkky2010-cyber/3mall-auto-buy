"""1회용 디버그 — #2 (하루견과 초록) 결제 페이지 카드할인 DOM 덤프.

CDP 9223 에 이미 연결된 Chrome 가정 (launch-check10-chrome.sh 로 띄운 것).
사용: python3 cart/_debug_order_dom.py
"""
from __future__ import annotations
import sys
import os
from pathlib import Path

ROOT = Path(__file__).parent
PROJECT_ROOT = ROOT.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(ROOT))

os.environ["DEBUG_ORDER"] = "1"

from patchright.sync_api import sync_playwright
import importlib.util
spec = importlib.util.spec_from_file_location("check10", ROOT / "check10.py")
check10 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check10)

URL = check10.ITEM_URL_FMT.format(slitmCd="2151046312", extra="&sectId=3059445")  # #2

with sync_playwright() as pw:
    browser = pw.chromium.connect_over_cdp("http://127.0.0.1:9223")
    ctx = browser.contexts[0]
    page = ctx.pages[0] if ctx.pages else ctx.new_page()

    print(f"[1] navigate: {URL}")
    page.goto(URL)
    page.wait_for_timeout(2000)

    print("[2] coupon click")
    check10._click_coupon(page)

    print("[3] buy now (qty=1, no option keyword for base)")
    ok, options = check10._execute_buy_now(page, 1, option_keyword=None)
    print(f"   bn={ok}, options={len(options)}")
    if not ok:
        sys.exit(1)

    print("[4] extract_order_page (DEBUG_ORDER=1 → dom dump)")
    page.wait_for_timeout(2000)
    info = check10._extract_order_page(page)
    print(f"\n=== RESULT ===")
    print(f"slides: {info.get('slides')}")
    print(f"list_price: {info.get('list_price')}")
    print(f"member_price: {info.get('member_price')}")
    print(f"qty: {info.get('qty')}")

    # 추가 — order 페이지에서 inf-discount / dc-card / crdImdDlTagTmp 비슷한 패턴 검색
    extra = page.evaluate("""() => {
        const out = {};
        out.url = location.href;
        out.title = document.title;
        // dl with discount classes
        const dls = Array.from(document.querySelectorAll('dl')).filter(d =>
            /discount|inf-/.test(d.className) || /즉시\\s*할인/.test(d.textContent || ''));
        out.discount_dls = dls.slice(0, 5).map(d => ({
            cls: (d.className || '').slice(0, 100),
            id: d.id || '',
            text: (d.innerText || '').slice(0, 300).replace(/\\n/g, ' | ')
        }));
        // strong.dc-price
        const prices = Array.from(document.querySelectorAll('strong.dc-price, [class*="dc-price"]'));
        out.dc_prices = prices.slice(0, 5).map(p => ({
            cls: (p.className || '').slice(0, 80),
            text: (p.innerText || '').slice(0, 60)
        }));
        // dc-card
        const cards = Array.from(document.querySelectorAll('[class*="dc-card"]'));
        out.dc_cards = cards.slice(0, 5).map(p => ({
            cls: (p.className || '').slice(0, 80),
            text: (p.innerText || '').slice(0, 100)
        }));
        return out;
    }""")
    print("\n=== ORDER PAGE EXTRA SCAN ===")
    import json
    print(json.dumps(extra, ensure_ascii=False, indent=2))
