"""결제수단변경 modal DOM dump — BC/현대/신한/롯데/하나/KB 선택 가능한지 확인."""
from __future__ import annotations
import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    b = p.chromium.connect_over_cdp("http://127.0.0.1:9222")
    ctx = b.contexts[0]
    # 현재 열려있는 checkout 페이지 reuse
    page = next((pg for pg in ctx.pages if "/oda/order" in pg.url or "/odb/order" in pg.url), None)
    if not page:
        # 가장 최근 hmall 페이지
        page = next((pg for pg in ctx.pages if "hmall.com" in pg.url and "login" not in pg.url), None)
    if not page:
        print("[FAIL] checkout 페이지 없음 — 먼저 cycle 한번 돌려서 checkout 페이지 열어둘 것")
        sys.exit(1)
    print(f"[USE] {page.url[:120]}")
    page.bring_to_front()

    # 결제수단변경 button 찾고 click
    btn = page.locator("button, a").filter(has_text="결제수단변경").first
    print(f"[BTN] count={btn.count()}")
    if btn.count() == 0:
        # 더 큰 범위 검색
        btn = page.get_by_text("결제수단변경").first
        print(f"[BTN-text] count={btn.count()}")
    if btn.count() == 0:
        print("[FAIL] 결제수단변경 button 없음")
        sys.exit(1)
    btn.click()
    page.wait_for_timeout(2500)

    # 모달 DOM dump
    info = page.evaluate("""
        () => {
            const out = {};
            // 모달 컨테이너 후보 — role=dialog 또는 큰 z-index modal
            const dialogs = Array.from(document.querySelectorAll('[role="dialog"], [aria-modal="true"]'));
            out.dialogs_count = dialogs.length;
            // li[value], button list with 카드명
            out.li_with_value = Array.from(document.querySelectorAll('li[value]')).map(li => ({
                value: li.getAttribute('value'),
                text: li.textContent.trim().slice(0, 80),
            }));
            // 카드명 텍스트가 있는 button들
            out.card_buttons = Array.from(document.querySelectorAll('button, label, li, div'))
                .filter(e => {
                    const t = e.textContent.trim();
                    return /비씨|BC|페이북|현대카드|신한|롯데카드|하나카드|KB|국민카드/.test(t) && t.length < 50;
                })
                .map(e => ({tag: e.tagName, text: e.textContent.trim(), cls: (e.className||'').toString().slice(0,80)}))
                .slice(0, 30);
            // body inner text 일부
            out.body_head = document.body.innerText.slice(0, 1500);
            return out;
        }
    """)
    print("\n=== dialogs ===")
    print(info['dialogs_count'])
    print("\n=== li[value] ===")
    for li in info['li_with_value'][:30]:
        print(f"  value={li['value']} text={li['text']}")
    print("\n=== card-name elements ===")
    for e in info['card_buttons']:
        print(f"  [{e['tag']}] cls={e['cls'][:40]} text={e['text']}")
    print("\n=== body head ===")
    print(info['body_head'])

    out = PROJECT_ROOT / "buy" / "_debug_change_card_modal.png"
    page.screenshot(path=str(out), full_page=False)
    print(f"\n[SCREENSHOT] {out}")
