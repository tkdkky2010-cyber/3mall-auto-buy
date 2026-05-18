"""구매하기 클릭 후 팝업 DOM 덤프 — 옵션 패널 셀렉터 진단.

사용: python3 cart/_debug_popup_dom.py <slitmCd>
"""
from __future__ import annotations
import sys, json
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

    print(f"navigate: {url}")
    page.goto(url, wait_until="domcontentloaded")
    page.wait_for_timeout(1500)

    btn = page.locator("button.btn-purchase").first
    print(f"btn-purchase count: {btn.count()}")
    btn.click()
    page.wait_for_timeout(2000)

    dump = page.evaluate("""() => {
        const out = { popups: [], all_sun_text: [], buy_buttons: [] };
        // 1. [선택 텍스트 포함 모든 element (visible)
        const all = Array.from(document.querySelectorAll('*'));
        for (const el of all) {
            const txt = (el.textContent || '').trim();
            if (txt.includes('[선택') && el.children.length === 0) {
                out.all_sun_text.push({
                    tag: el.tagName,
                    cls: (el.className || '').toString().slice(0, 80),
                    id: el.id || '',
                    visible: el.offsetParent !== null,
                    text: txt.slice(0, 100)
                });
            }
        }
        // 2. 모든 visible popup-like containers
        const popupLike = Array.from(document.querySelectorAll('.popup, .modal, .layer, [role="dialog"], .popup-layerwrap, [class*="popup"], [class*="modal"], [class*="layer"]'));
        for (const p of popupLike) {
            if (p.offsetParent !== null && (p.innerText || '').length > 30) {
                out.popups.push({
                    tag: p.tagName,
                    cls: (p.className || '').toString().slice(0, 100),
                    text: (p.innerText || '').slice(0, 400).replace(/\\n/g, ' | ')
                });
            }
        }
        // 3. visible 바로구매/장바구니 버튼
        const btns = Array.from(document.querySelectorAll('button'));
        for (const b of btns) {
            if (b.offsetParent !== null) {
                const t = b.textContent.trim();
                if (t === '바로구매' || t === '장바구니' || t === '구매하기' || t === '주문하기') {
                    out.buy_buttons.push({
                        text: t,
                        cls: (b.className || '').slice(0, 80),
                        parentCls: ((b.parentElement || {}).className || '').toString().slice(0, 80)
                    });
                }
            }
        }
        return out;
    }""")
    print(f"\n=== [선택 ... ] 텍스트 ({len(dump['all_sun_text'])}) ===")
    for x in dump['all_sun_text'][:10]:
        print(f"  <{x['tag']} cls={x['cls']!r} visible={x['visible']}> {x['text']!r}")
    print(f"\n=== popup containers ({len(dump['popups'])}) ===")
    for p in dump['popups'][:5]:
        print(f"  <{p['tag']} cls={p['cls']!r}>")
        print(f"    text: {p['text']!r}")
    print(f"\n=== visible 바로구매/장바구니 버튼 ({len(dump['buy_buttons'])}) ===")
    for b in dump['buy_buttons']:
        print(f"  '{b['text']}' cls={b['cls']!r}")
