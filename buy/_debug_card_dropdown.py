"""'신용카드 선택' dropdown click → 카드사 list 검색."""
from __future__ import annotations
import sys, json
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    b = p.chromium.connect_over_cdp("http://127.0.0.1:9222")
    ctx = b.contexts[0]
    page = next((pg for pg in ctx.pages if "/oda/order" in pg.url or "/odb/order" in pg.url), None)
    if not page:
        page = next((pg for pg in ctx.pages if "hmall.com" in pg.url and "login" not in pg.url), None)
    if not page:
        print("[FAIL] checkout 페이지 없음")
        sys.exit(1)
    page.bring_to_front()
    print(f"[USE] {page.url[:120]}")

    # 신용카드 선택 dropdown 찾기 + click
    dd = page.get_by_text("신용카드 선택").first
    print(f"[DD] count={dd.count()}")
    if dd.count() == 0:
        print("[FAIL] 신용카드 선택 dropdown 없음")
        # 페이지에서 select/button 안에 'BC' 또는 '비씨' 가 있는지 dump
        elements = page.evaluate("""
            () => Array.from(document.querySelectorAll('select, button, [role="combobox"], [role="listbox"]'))
                .map(e => ({tag: e.tagName, text: e.textContent.trim().slice(0,80), role: e.getAttribute('role'), cls:(e.className||'').toString().slice(0,60)}))
                .slice(0, 40)
        """)
        for e in elements:
            print(f"  [{e['tag']}] role={e['role']} cls={e['cls']} text={e['text']}")
        sys.exit(2)
    dd.click()
    page.wait_for_timeout(2000)

    info = page.evaluate("""
        () => {
            const out = {};
            // 모든 li, option 카드사 후보
            out.options = Array.from(document.querySelectorAll('option, li, button, [role="option"]'))
                .filter(e => {
                    const t = e.textContent.trim();
                    return /비씨|BC|페이북|현대카드|신한|롯데카드|하나카드|KB|국민카드|삼성|NH|농협/.test(t) && t.length < 50;
                })
                .map(e => ({tag: e.tagName, value: e.getAttribute('value') || e.dataset?.value, text: e.textContent.trim(), cls: (e.className||'').toString().slice(0,60)}));
            // 가능한 select element 자체
            out.selects = Array.from(document.querySelectorAll('select')).map(s => ({
                name: s.name, id: s.id,
                opts: Array.from(s.options).map(o => ({val: o.value, txt: o.textContent.trim()})).slice(0, 30),
            }));
            return out;
        }
    """)
    print("\n=== options (filter on 카드 brand) ===")
    for e in info['options'][:40]:
        print(f"  [{e['tag']}] val={e['value']} cls={e['cls']} text={e['text']}")
    print("\n=== select elements ===")
    print(json.dumps(info['selects'], ensure_ascii=False, indent=2))

    out = PROJECT_ROOT / "buy" / "_debug_card_dropdown.png"
    page.screenshot(path=str(out), full_page=False)
    print(f"\n[SCREENSHOT] {out}")
