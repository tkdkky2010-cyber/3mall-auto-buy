"""btn-purchase 어느 버튼이 first 인지 + 클릭 후 popup 등장 검증.
사용: python3 cart/_debug_btn_purchase.py <slitmCd>
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

    # btn-purchase 다 살펴보기 (visible + class)
    info = page.evaluate("""() => {
        const btns = Array.from(document.querySelectorAll('button.btn-purchase'));
        return btns.map((b, i) => ({
            i, text: b.textContent.trim().slice(0, 20),
            visible: b.offsetParent !== null,
            disabled: b.disabled,
            cls: b.className,
            rect: b.getBoundingClientRect().top
        }));
    }""")
    print(f"[1] button.btn-purchase 후보 {len(info)}")
    for b in info:
        print(f"   [{b['i']}] {b['text']!r} visible={b['visible']} cls={b['cls']!r} top={b['rect']:.0f}")

    # "구매하기" 명시적 클릭 시도
    print("\n[2] '구매하기' 텍스트 명시적 클릭")
    purchase = page.evaluate("""() => {
        const btn = Array.from(document.querySelectorAll('button.btn-purchase'))
            .find(b => b.offsetParent !== null && b.textContent.trim() === '구매하기');
        if (btn) { btn.click(); return true; }
        return false;
    }""")
    print(f"   클릭 결과: {purchase}")
    page.wait_for_timeout(3000)  # 더 길게 wait

    # 팝업/옵션 상태 재확인
    opts_visible = page.evaluate("""() => {
        const opts = Array.from(document.querySelectorAll("strong.opt-num"))
            .filter(s => s.offsetParent !== null);
        return opts.map(o => ({
            text: o.textContent.trim(),
            parent: ((o.parentElement || {}).tagName || '') + '.' + ((o.parentElement || {}).className || '').slice(0,60)
        }));
    }""")
    print(f"\n[3] visible opt-num: {len(opts_visible)}")
    for o in opts_visible[:6]:
        print(f"   {o['text']!r} parent={o['parent']!r}")

    # 모든 visible 가능 element 중 [선택 텍스트 검색
    sun = page.evaluate("""() => {
        const all = Array.from(document.querySelectorAll('*'));
        return all.filter(el =>
            el.offsetParent !== null &&
            el.children.length === 0 &&
            (el.textContent || '').trim().includes('[선택')
        ).slice(0, 10).map(el => ({
            tag: el.tagName, cls: (el.className || '').toString().slice(0, 60),
            text: el.textContent.trim().slice(0, 80)
        }));
    }""")
    print(f"\n[4] visible [선택 텍스트: {len(sun)}")
    for s in sun:
        print(f"   <{s['tag']} cls={s['cls']!r}> {s['text']!r}")
