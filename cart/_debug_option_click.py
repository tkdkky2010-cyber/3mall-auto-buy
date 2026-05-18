"""옵션 span 클릭 후 popup 변화 + 가격 위치 + 바로구매 등장 검증.
사용: python3 cart/_debug_option_click.py <slitmCd>
"""
import sys
from pathlib import Path
ROOT = Path(__file__).parent
PROJECT_ROOT = ROOT.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(ROOT))

from patchright.sync_api import sync_playwright

slitm = sys.argv[1]
url = f"https://www.hmall.com/md/pda/itemPtc?slitmCd={slitm}&sectId=3059445"

with sync_playwright() as pw:
    browser = pw.chromium.connect_over_cdp("http://127.0.0.1:9223")
    ctx = browser.contexts[0]
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.goto(url, wait_until="domcontentloaded")
    page.wait_for_timeout(1500)

    # 구매하기 click
    page.evaluate("""() => {
        const btn = Array.from(document.querySelectorAll('button.btn-purchase'))
            .find(b => b.offsetParent !== null && b.textContent.trim() === '구매하기');
        if (btn) btn.click();
    }""")
    page.wait_for_timeout(2500)

    # 옵션 span 의 부모/형제 dump (가격 위치 찾기)
    dump = page.evaluate("""() => {
        const spans = Array.from(document.querySelectorAll('span.choice-num.title'))
            .filter(s => s.offsetParent !== null);
        return spans.map(s => {
            const parent = s.parentElement;
            const grand = parent ? parent.parentElement : null;
            return {
                idx_text: s.textContent.trim().slice(0, 50),
                parent_tag: parent ? parent.tagName : '',
                parent_cls: parent ? (parent.className || '').toString().slice(0, 60) : '',
                parent_text: parent ? (parent.innerText || '').slice(0, 200).replace(/\\n/g, ' | ') : '',
                grand_text: grand ? (grand.innerText || '').slice(0, 200).replace(/\\n/g, ' | ') : ''
            };
        });
    }""")
    print("[1] 옵션 span 부모 dump:")
    for d in dump[:3]:
        print(f"  {d['idx_text']!r}")
        print(f"    parent <{d['parent_tag']} cls={d['parent_cls']!r}>")
        print(f"    parent text: {d['parent_text']!r}")

    # 옵션 1 클릭 (span 기준)
    print("\n[2] span[선택 1] 클릭 시도")
    import re
    sp = page.locator("span.choice-num.title").filter(has_text=re.compile(r"\[선택\s*1\]")).first
    print(f"   count: {sp.count()}")
    if sp.count() > 0:
        try:
            sp.click()
            print("   ✓ click 완료")
        except Exception as e:
            print(f"   ❌ {e}")
        page.wait_for_timeout(1500)

    # 클릭 후 popup 안의 바로구매 / 장바구니 검사
    after = page.evaluate("""() => {
        const btns = Array.from(document.querySelectorAll('button'))
            .filter(b => b.offsetParent !== null);
        return {
            buy_now: btns.filter(b => b.textContent.trim() === '바로구매').length,
            cart: btns.filter(b => b.textContent.trim() === '장바구니').length,
            buy_now_classes: btns.filter(b => b.textContent.trim() === '바로구매').map(b => b.className.slice(0, 80))
        };
    }""")
    print(f"\n[3] 옵션 클릭 후 visible: 바로구매={after['buy_now']}, 장바구니={after['cart']}")
    for c in after['buy_now_classes']:
        print(f"   - {c!r}")

    # 바로구매 클릭
    print("\n[4] 바로구매 클릭")
    clicked = page.evaluate("""() => {
        const btn = Array.from(document.querySelectorAll('button'))
            .find(b => b.offsetParent !== null && b.textContent.trim() === '바로구매');
        if (btn) { btn.click(); return true; }
        return false;
    }""")
    print(f"   클릭 결과: {clicked}")
    page.wait_for_timeout(3500)
    print(f"   final URL: {page.url}")
    print(f"   /order in URL: {'/order' in (page.url or '')}")
