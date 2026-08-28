#!/usr/bin/env python3
"""쿠폰만 다시 받기 — 장바구니는 건드리지 않는다.

주문을 취소하면 그 주문에 물렸던 상품쿠폰이 함께 사라진다(2026-08-28 #2·#3 취소 건).
카트는 그대로 두고 **상품 상세페이지만 다시 방문**해 '쿠폰받기 → 쿠폰 전체 다운로드' 만 재실행한다.
롯데는 쿠폰을 담기 시점의 상품페이지에서 받으므로, 카트 화면엔 다시 받을 자리가 없다.

사용:
    python3 buy/recollect_coupons.py 2 3 4 5 6 7 --codes e,h
"""
from __future__ import annotations
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "buy"))

import sulwhasoo as S  # noqa: E402


def recollect(page, goods_no: str) -> str:
    """상품 상세 진입 → 쿠폰받기 → 전체 다운로드 → 닫기. 반환 = 상태 문자열."""
    page.goto(f"https://www.lotteimall.com/goods/viewGoodsDetail.lotte?goods_no={goods_no}",
              wait_until="domcontentloaded", timeout=15000)
    page.wait_for_timeout(2000)
    S.dismiss_popup(page)
    try:
        page.get_by_role("button", name="쿠폰받기").click(timeout=3000)
    except Exception as e:
        return f"쿠폰받기 없음({type(e).__name__})"
    page.wait_for_timeout(800)
    status = "팝업만"
    try:
        page.get_by_role("button", name="쿠폰 전체 다운로드").click(timeout=2500)
        page.wait_for_timeout(900)
        status = "전체다운로드"
    except Exception as e:
        status = f"다운로드실패({type(e).__name__})"
    for name in ("닫기",):
        try:
            page.get_by_role("button", name=name, exact=True).click(timeout=2000)
            page.wait_for_timeout(400)
        except Exception:
            pass
    # 쿠폰 레이어 잔존 방어 (담기 코드와 동일 이유 — 다음 상품 클릭을 가로챈다)
    try:
        page.evaluate("() => { const l = document.querySelector('#layer_down_coupon');"
                      " if (l) l.style.display = 'none'; }")
    except Exception:
        pass
    return status


def main() -> int:
    args = sys.argv[1:]
    codes = next((a.split("=", 1)[1] for a in args if a.startswith("--codes=")), "e,h").split(",")
    idxs = [int(a) for a in args if a.isdigit()]
    if not idxs:
        print(__doc__)
        return 1

    accounts = S.load_json(S.LOTTE_ACCOUNTS)["accounts"]
    goods = {}
    for c in codes:
        c = c.strip()
        p = S.LOTTE_PRODUCTS.get(c)
        if not p:
            print(f"[FATAL] 상품코드 {c} 없음")
            return 1
        goods[c] = p

    port = S.resolve_cdp_port(int(S.CDP_PORT))
    with S.sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        usable = [pg for pg in ctx.pages
                  if not pg.is_closed() and S.LOTTE_PW_CAMPAIGN_URL not in (pg.url or "")]
        page = usable[-1] if usable else ctx.new_page()

        fails = []
        for idx in idxs:
            acc = accounts[idx - 1]
            print(f"\n▶ #{idx} {acc['id']} — 쿠폰 재수령")
            page.goto(S.LOTTE_HOME, wait_until="domcontentloaded", timeout=15000)
            page.wait_for_timeout(1500)
            if not S.lotte_login(page, acc["id"], acc["pw"]):
                print(f"   ✗ 로그인 실패")
                fails.append(idx)
                continue
            for c, p in goods.items():
                print(f"   · {c} {p['name']} ({p['goods_no']}): {recollect(page, p['goods_no'])}")
            # 카트가 그대로인지 확인 — 쿠폰만 받았으니 담긴 건수가 유지돼야 한다
            page.goto("https://www.lotteimall.com/cart/searchCartList.lotte",
                      wait_until="domcontentloaded", timeout=15000)
            page.wait_for_timeout(1800)
            n = page.evaluate(
                "() => Array.from(document.querySelectorAll('.c_item'))"
                ".filter(r => !r.querySelector('input[id*=\"AllChk\" i], input[name*=\"AllChk\" i]')).length")
            print(f"   ✓ 카트 {n}건 유지")
            if n == 0:
                fails.append(idx)
                print(f"   ⚠️ 카트가 비었다 — 결제 전 확인 필요")
        print(f"\n===== 완료: {len(idxs) - len(fails)}/{len(idxs)} "
              f"{'(실패 ' + ','.join(map(str, fails)) + ')' if fails else ''}")
        return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
