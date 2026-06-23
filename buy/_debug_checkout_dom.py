"""hmall c 제품 add 후 구매하기 click 한 뒤 checkout 페이지 DOM dump.
캐러셀이 없는 이유 확인용."""
from __future__ import annotations
import sys, os, json
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

os.environ["DRY_PAYMENT"] = "true"

from buy.run import add_to_cart, clear_cart, CDP_ENDPOINT, CART_URL
from playwright.sync_api import sync_playwright

C_INFO = {
    "name": "설화수 자음2종",
    "slitmCd": "2228722509",
    "url_extra": "",
    "option_index": 1,
    "auto_coupon": False,
}

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp(CDP_ENDPOINT)
    ctx = browser.contexts[0]
    page = ctx.new_page()

    # cart 비어있다고 가정 (이미 dry 에서 c 1개 들어있음 — 한번 더 add 안 함)
    page.goto(CART_URL, wait_until="domcontentloaded")
    page.wait_for_timeout(1500)
    # 일반상품 체크 (do_checkout 과 동일)
    page.evaluate("""
        () => {
            const labels = Array.from(document.querySelectorAll('label.chklabel'));
            const target = labels.find(l => {
                const span = l.querySelector('span');
                return span && span.textContent.trim() === '일반상품';
            });
            if (target) {
                const cb = target.querySelector('input[type="checkbox"]');
                if (cb && !cb.checked) target.click();
            }
        }
    """)
    page.wait_for_timeout(500)
    btn = page.locator("button.btn-purchase").filter(has_text="구매하기").first
    if btn.count() == 0:
        btn = page.locator("button").filter(has_text="구매하기").first
    print(f"[BTN] 구매하기 count={btn.count()}")
    if btn.count() == 0:
        print("[FAIL] cart 비어있음 또는 button 없음")
        sys.exit(1)
    btn.click()
    page.wait_for_load_state("domcontentloaded", timeout=15000)
    page.wait_for_timeout(3500)

    print(f"\n[URL] {page.url}")
    print(f"[TITLE] {page.title()}")

    # 페이지 핵심 구조 dump
    info = page.evaluate("""
        () => {
            const out = {};
            out.h2_texts = Array.from(document.querySelectorAll('h2')).map(h => h.textContent.trim()).slice(0, 30);
            out.h3_texts = Array.from(document.querySelectorAll('h3')).map(h => h.textContent.trim()).slice(0, 30);
            out.btn_texts = Array.from(document.querySelectorAll('button')).map(b => b.textContent.trim()).filter(t => t.length > 0 && t.length < 50).slice(0, 40);
            out.img_alt_cardCd = Array.from(document.querySelectorAll('img[alt^="cardCd"]')).map(i => i.alt);
            out.has_carousel_class = Array.from(document.querySelectorAll('[class*="32o920"], [class*="swiper"]')).slice(0,5).map(e => ({tag:e.tagName, cls:(e.className||'').toString().slice(0,80)}));
            out.body_text_head = document.body.innerText.slice(0, 1500);
            return out;
        }
    """)
    print("\n=== H2 ===")
    for t in info['h2_texts']: print(f"  - {t}")
    print("\n=== H3 ===")
    for t in info['h3_texts']: print(f"  - {t}")
    print("\n=== Buttons ===")
    for t in info['btn_texts']: print(f"  - {t}")
    print("\n=== img alt^=cardCd ===")
    for t in info['img_alt_cardCd']: print(f"  - {t}")
    print("\n=== swiper/_32o920 ===")
    for t in info['has_carousel_class']: print(f"  - {t}")
    print("\n=== body text head ===")
    print(info['body_text_head'])

    # screenshot 저장
    out = PROJECT_ROOT / "buy" / "_debug_checkout_dom.png"
    page.screenshot(path=str(out), full_page=True)
    print(f"\n[SCREENSHOT] {out}")
