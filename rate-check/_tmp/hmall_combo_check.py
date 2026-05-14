"""Hmall 1개 조합 실제 체크아웃 가격 측정 (rate-check 신규 방식 PoC).

흐름:
1) CDP 9222 attach
2) hmall_config.json 첫 계정 로그인 (이미 로그인 상태면 skip)
3) 장바구니 비우기
4) 인자로 받은 (code, qty) 조합 add_to_cart
5) cart → "구매하기" 클릭 (do_checkout 전반부)
6) 카드할인 캐러셀 detect → 최고% 카드 pick → click
7) 최종 결제예정금액 DOM 추출
8) 카드페이백 대상이면 페이백계수 적용
9) 결제하기 클릭 X — rate check 용도, 절대 결제 X

usage:
    python rate-check/_tmp/hmall_combo_check.py f 5
    python rate-check/_tmp/hmall_combo_check.py g 2 h 1    # g×2 + h×1
"""
from __future__ import annotations
import json, os, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "buy"))
sys.path.insert(0, str(ROOT / "rate-check"))

# buy/run.py helpers
import run as buy_run  # type: ignore
from run import (  # type: ignore
    CART_URL, CDP_ENDPOINT, CARD_CD_TO_NAME,
    login, clear_cart, add_to_cart,
    detect_carousel_slides, pick_carousel_slide, click_carousel_slide,
)
import _common as C

# 카드명 → 페이백계수 (rate-check/_common.py)
CARD_PAYBACK_BY_NAME = {
    "롯데카드": C.CARD_PAYBACK["롯데카드"],
    "비씨카드(페이북)": C.CARD_PAYBACK["비씨카드"],
    "삼성카드": C.CARD_PAYBACK["삼성카드"],
    "하나카드": C.CARD_PAYBACK["하나카드"],
    "NH농협카드": C.CARD_PAYBACK["농협카드"],
}

# 본품 b~h → Hmall slitmCd
IDS = json.load(open(ROOT / "hsmaster" / "config" / "sulwhasoo-ids.json"))["ids"]


def build_combo(argv: list[str]) -> list[tuple[str, int]]:
    """argv = ['f', '5'] or ['g', '2', 'h', '1'] → [('f',5)] or [('g',2),('h',1)]"""
    if len(argv) % 2:
        sys.exit("usage: <code> <qty> [<code> <qty> ...]")
    return [(argv[i], int(argv[i+1])) for i in range(0, len(argv), 2)]


def read_final_price(page) -> int | None:
    """체크아웃 페이지에서 '결제예정금액' 부근 가장 큰 원 단위 숫자를 추출."""
    js = r"""
    () => {
        const out = {candidates: [], labelHits: []};
        // 1) "결제예정금액" 라벨 근처의 숫자 찾기
        const labels = Array.from(document.querySelectorAll('*'))
            .filter(el => el.children.length === 0 &&
                          /결제\s*예정\s*금액|총\s*결제\s*금액|최종\s*결제\s*금액/.test(el.textContent || ''));
        for (const lbl of labels.slice(0, 5)) {
            let n = lbl;
            for (let lvl = 0; lvl < 6 && n; lvl++) {
                const numEl = Array.from(n.querySelectorAll('*'))
                    .find(e => /^[\d,]{4,}원?$/.test((e.textContent || '').trim()));
                if (numEl) {
                    const txt = (numEl.textContent || '').replace(/[,원]/g, '').trim();
                    const val = parseInt(txt);
                    if (val >= 10000) {
                        out.labelHits.push({label: lbl.textContent.trim().slice(0,40), value: val});
                        break;
                    }
                }
                n = n.parentElement;
            }
        }
        // 2) 전체 body에서 \d{3},\d{3},?\d{0,3} 패턴 모두 수집 (큰 순)
        const body = document.body ? document.body.innerText : '';
        const matches = Array.from(body.matchAll(/[\d,]{6,}\s*원/g))
            .map(m => parseInt(m[0].replace(/[,원\s]/g, '')))
            .filter(v => v >= 100000 && v < 10000000);
        out.candidates = [...new Set(matches)].sort((a,b) => b-a).slice(0, 10);
        return out;
    }
    """
    res = page.evaluate(js) or {}
    print(f"    [PRICE] labelHits: {res.get('labelHits', [])}")
    print(f"    [PRICE] candidates (큰순): {res.get('candidates', [])}")
    if res.get("labelHits"):
        return res["labelHits"][0]["value"]
    if res.get("candidates"):
        return res["candidates"][0]
    return None


def run(combo: list[tuple[str, int]]):
    # patchright → fallback playwright
    try:
        from patchright.sync_api import sync_playwright
        backend = "patchright"
    except ImportError:
        from playwright.sync_api import sync_playwright
        backend = "playwright"
    print(f"[INFO] PW backend: {backend}")
    print(f"[INFO] CDP endpoint: {CDP_ENDPOINT}")
    print(f"[INFO] 조합: {combo}")

    소비자가 = sum(C.PRODUCTS[c]["price"] * q for c, q in combo)
    print(f"[INFO] 소비자가: {소비자가:,}원")

    with sync_playwright() as pw:
        try:
            browser = pw.chromium.connect_over_cdp(CDP_ENDPOINT)
        except Exception as e:
            sys.exit(f"❌ CDP attach 실패: {e}")
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        page = context.new_page()
        page.set_default_timeout(20000)

        # 로그인 — 마이페이지 접근으로 검증 (로그인 풀려있으면 redirect)
        page.goto("https://www.hmall.com/mo/mma/myhome", wait_until="domcontentloaded")
        page.wait_for_timeout(1500)
        cur_url = page.url
        if "loginForm" in cur_url or "/login" in cur_url:
            print(f"[INFO] 로그인 풀림 (현재 URL: {cur_url[:80]}) — hmall_config.json[0]으로 로그인")
            cfg = json.load(open(ROOT / "hmall_config.json"))
            acc = cfg["accounts"][0]
            if not login(page, acc["id"], acc["pw"]):
                sys.exit("❌ 로그인 실패")
            page.wait_for_timeout(1500)
        else:
            print(f"[INFO] 이미 로그인 상태 (URL: {cur_url[:60]})")

        # 장바구니 비우기
        print("[STEP] clear_cart")
        clear_cart(page)
        page.wait_for_timeout(800)

        # 본품 add_to_cart
        for c, q in combo:
            hyundai_cd = IDS[c]["hyundai"]
            info = {
                "name": IDS[c]["name"],
                "slitmCd": hyundai_cd,
                "url_extra": "",
                "option_index": 1,
                "auto_coupon": True,
            }
            print(f"[STEP] add_to_cart {c} × {q} (slitmCd={hyundai_cd})")
            ok = add_to_cart(page, c, info, q)
            if not ok:
                sys.exit(f"❌ add_to_cart 실패: {c} × {q}")
            page.wait_for_timeout(500)

        # cart → 구매하기
        print("[STEP] cart 진입 + 일반상품 체크 + 구매하기")
        page.goto(CART_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(1500)
        page.evaluate("""
            () => {
                const labels = Array.from(document.querySelectorAll('label.chklabel'));
                const target = labels.find(l => {
                    const s = l.querySelector('span');
                    return s && s.textContent.trim() === '일반상품';
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
            sys.exit("❌ 구매하기 버튼 미발견")
        purchase_btn.click()
        page.wait_for_load_state("domcontentloaded", timeout=15000)
        page.wait_for_timeout(2800)

        # 카드 캐러셀
        print("[STEP] 카드할인 캐러셀 detect")
        slides = detect_carousel_slides(page)
        if not slides:
            sys.exit("❌ 캐러셀 슬라이드 미발견")
        for s in slides:
            print(f"    · {s['brand']} ({s['percent']}%) cardCd={s['cardCd']} isCard={s['isCard']}")
        pick = pick_carousel_slide(slides, override="")
        if not pick:
            sys.exit("❌ 슬라이드 pick 실패")
        cardcd = pick["cardCd"]
        card_full_name = CARD_CD_TO_NAME.get(cardcd, pick["brand"])
        print(f"[OK] 자동 선택: {card_full_name} ({pick['percent']}%) cardCd={cardcd}")

        # 클릭 (가격 갱신 트리거)
        print("[STEP] 캐러셀 슬라이드 클릭")
        if not click_carousel_slide(page, cardcd):
            sys.exit("❌ 슬라이드 클릭 실패")
        page.wait_for_timeout(1500)

        # 가격 추출
        print("[STEP] 최종 결제예정금액 추출")
        final_price = read_final_price(page)
        if final_price is None:
            sys.exit("❌ 가격 추출 실패")

        # 페이백 계산
        payback_pct = CARD_PAYBACK_BY_NAME.get(card_full_name, 0)
        if payback_pct:
            after_payback = round(final_price * (1 - payback_pct))
            print(f"[INFO] 페이백 카드 {card_full_name} × (1 - {payback_pct}) = {after_payback:,}원")
        else:
            after_payback = final_price
            print(f"[INFO] 페이백 없음 (현대카드 등) — 최종가 그대로 {final_price:,}원")

        # 공급률 (총샘플가치는 추가증정·GWP 합산. 여기서는 1조합만 — composition file 활용해야 정확.
        # PoC 단계: 추가증정은 따로 안 빼고 최종가만 출력)
        print()
        print("=" * 60)
        print(f"  조합: {combo}")
        print(f"  소비자가: {소비자가:,}원")
        print(f"  선택 카드: {card_full_name} {pick['percent']}% 즉시할인")
        print(f"  최종 결제예정금액 (페이지): {final_price:,}원")
        print(f"  페이백 적용 후: {after_payback:,}원")
        print(f"  (총샘플가치 차감 전) 임시공급률: {after_payback / 소비자가:.4f}")
        print("=" * 60)
        print()
        print("⚠️ 결제하기 클릭 안 함 (rate check 전용). cart 상태로 종료.")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    combo = build_combo(sys.argv[1:])
    run(combo)
