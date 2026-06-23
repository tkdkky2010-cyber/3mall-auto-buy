"""조합1 (f×5 윤조에센스90ml) 결제금액 확인 — 현대카드 선택 후 '결제하기' 버튼 금액만 읽음.

결제 클릭 X (금액 확인 only). 카트는 f×5 담긴 채로 유지.
기대값: 507,680원 (현대 5%).

사용: python3 buy/_check_combo1_price.py          # 1~19 전체 (INACTIVE 없음, #6 포함)
      python3 buy/_check_combo1_price.py 1 2 3   # 지정 계정
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "buy"))

from chrome_launcher import ensure_chrome
from playwright.sync_api import sync_playwright

import run as hmall

try:
    sys.stdout.reconfigure(line_buffering=True)
except Exception:
    pass

IDS = json.loads((ROOT / "hsmaster" / "config" / "sulwhasoo-ids.json").read_text(encoding="utf-8"))["ids"]
ACCOUNTS = json.loads(Path(hmall.ACCOUNTS_FILE).read_text(encoding="utf-8"))["accounts"]

COMBO = [("f", 5)]          # 조합1
TARGET_CARD = "롯데카드"     # cardCd08 — 2026-06-06 당일 할인카드 (롯데 5% 즉시할인)
EXPECTED = 507_680           # 2만원 쿠폰 기준선 — 이 금액이어야 쿠폰 발급 의미 있음

SKIP = set()  # 2026-06-06: 전계정 (#6 포함 — 어제 정상 사용 확인)


def product_info(code: str) -> dict:
    entry = IDS[code]
    return {"name": entry["name"], "slitmCd": entry["hyundai"], "url_extra": "",
            "option_index": 1, "auto_coupon": True}


def check_account(context, idx: int) -> dict:
    """Returns {idx, amount, card, error}"""
    out = {"idx": idx, "amount": None, "card": None, "error": None}
    account = ACCOUNTS[idx - 1]
    page = context.new_page()
    try:
        hmall._hmall_clean(context, page, deep=True)
        if not hmall.login(page, account["id"], account["pw"]):
            out["error"] = "로그인 실패"
            return out

        hmall.clear_cart(page)
        for code, qty in COMBO:
            if not hmall.add_to_cart(page, code, product_info(code), qty):
                out["error"] = f"카트담기 실패 ({code}×{qty})"
                return out
            page.wait_for_timeout(800)

        # cart → 일반상품 체크 → 구매하기
        page.goto(hmall.CART_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(1500)
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
        purchase_btn = page.locator("button.btn-purchase").filter(has_text="구매하기").first
        if purchase_btn.count() == 0:
            purchase_btn = page.locator("button").filter(has_text="구매하기").first
        if purchase_btn.count() == 0:
            out["error"] = "구매하기 버튼 없음"
            return out
        purchase_btn.click()
        page.wait_for_load_state("domcontentloaded", timeout=15000)
        page.wait_for_timeout(2500)

        # 현대카드 선택 (캐러셀 → modal fallback)
        slides = hmall.detect_carousel_slides(page)
        summary = [s["brand"] + f"({s['percent']}%)" for s in slides]
        print(f"    [INFO] 캐러셀: {summary}")
        pick = hmall.pick_carousel_slide(slides, override=TARGET_CARD)
        if pick:
            cardcd = pick.get("cardCd", "")
            already = page.evaluate(f"""() => {{
                const el = document.querySelector('div._32o920j:has(img[alt="{cardcd}"])');
                return el ? (el.className || '').includes('selected') : false;
            }}""")
            if already:
                print(f"    [SKIP] {cardcd} 이미 selected")
            elif not hmall.click_carousel_slide(page, cardcd):
                out["error"] = "캐러셀 클릭 실패"
                return out
        else:
            pick = hmall.select_card_via_change_modal(page, TARGET_CARD)
            if not pick:
                out["error"] = f"'{TARGET_CARD}' 캐러셀+모달 모두 실패"
                return out
        out["card"] = TARGET_CARD

        # '결제하기' 버튼 텍스트에서 금액만 읽음 — 클릭 X
        page.wait_for_timeout(1500)
        pay_btn = page.locator("button.btn-confirm").filter(has_text="결제하기").first
        if pay_btn.count() == 0:
            pay_btn = page.locator("button").filter(has_text="결제하기").first
        if pay_btn.count() == 0:
            out["error"] = "결제하기 버튼 없음"
            return out
        text = pay_btn.inner_text().strip()
        m = re.search(r"([\d,]+)\s*원", text)
        if not m:
            out["error"] = f"금액 파싱 실패: '{text}'"
            return out
        out["amount"] = int(m.group(1).replace(",", ""))
        return out
    except Exception as e:
        out["error"] = f"예외: {e}"
        return out
    finally:
        try:
            page.close()
        except Exception:
            pass


def main() -> int:
    wanted = [int(a) for a in sys.argv[1:] if a.isdigit()]
    targets = wanted or [i for i in range(1, len(ACCOUNTS) + 1) if i not in SKIP]

    ensure_chrome(int(hmall.CDP_PORT))
    print(f"=== 조합1 f×5 결제금액 확인 ({TARGET_CARD}, 기대 {EXPECTED:,}원) ===")
    print(f"accounts: {targets}\n")

    results = []
    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp(hmall.CDP_ENDPOINT, slow_mo=400)
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        for pos, idx in enumerate(targets, 1):
            print(f"[{pos}/{len(targets)}] 계정 #{idx}")
            r = check_account(context, idx)
            if r["amount"] is not None:
                mark = "✓" if r["amount"] == EXPECTED else "✗ 불일치"
                print(f"  → {r['amount']:,}원 {mark}")
            else:
                print(f"  → FAIL: {r['error']}")
            results.append(r)

    print("\n=== 결과표 ===")
    print(f"{'계정':>4} | {'결제금액':>10} | 판정")
    print("-" * 34)
    for r in results:
        if r["amount"] is not None:
            mark = "OK" if r["amount"] == EXPECTED else f"MISMATCH ({r['amount'] - EXPECTED:+,})"
            print(f"  #{r['idx']:>2} | {r['amount']:>9,}원 | {mark}")
        else:
            print(f"  #{r['idx']:>2} | {'—':>10} | FAIL: {r['error']}")
    ok = sum(1 for r in results if r["amount"] == EXPECTED)
    print(f"\n일치 {ok}/{len(results)} (기대 {EXPECTED:,}원)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
