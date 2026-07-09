#!/usr/bin/env python3
# ──────────────────────────────────────────────────────────────────
#  카카오페이 당일할인 날 전용 — PC 로 "결제하기 클릭"까지만 자동.
#  카카오페이 인증(QR/간편결제)은 카카오페이 본폰으로 사람이 수동 마무리.
#  (ADB 폰 자동화는 카카오페이 미지원 — hmall_hyundai_buy detect_card FAIL)
# ──────────────────────────────────────────────────────────────────
#  전제: 카트는 이미 담겨 있어야 함 (CART_ONLY=true python3 buy/run.py).
#  흐름: 로그인 → 장바구니 전체선택 → 구매하기 → 주문서 → 카카오페이 캐러셀 확인 → [결제하기]
#  배송지: 계정 기본주소 그대로 (변경 X).
#
#  사용:
#    python3 buy/checkout_kakao.py 1          # INSPECT: 결제하기 직전까지 (안 누름) — 검증용
#    python3 buy/checkout_kakao.py 1 --go     # 결제하기 클릭까지 (카카오페이 QR 뜸) → 본폰 스캔
#    python3 buy/checkout_kakao.py 1 6 10 --go
# ──────────────────────────────────────────────────────────────────
import re
import sys

import run  # buy/run.py — login/캐러셀 헬퍼/설정 재사용 (import 시 PROJECT_ROOT sys.path 등록)

# 장바구니 '일반상품' 전체선택 (옛 do_checkout 검증 셀렉터)
SELECT_ALL_JS = """
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
"""


def _pay_amount(page):
    """결제하기 버튼의 'N원 결제하기' 금액 int 반환 (없으면 None)."""
    pay = page.locator("button.btn-confirm").filter(has_text="결제하기").first
    if pay.count() == 0:
        pay = page.locator("button").filter(has_text="결제하기").first
    if pay.count() == 0:
        return None
    m = re.search(r"([\d,]+)\s*원", pay.inner_text() or "")
    return int(m.group(1).replace(",", "")) if m else None


def apply_hpoint_full(page) -> str:
    """주문서 H.Point '전액사용' 클릭 → 포인트 전액 차감.
    버튼 없으면(포인트 <100/미대상) skip. 금액 감소로 적용 검증 (폰 hmall_use_all_points 의 PC 판.)"""
    btn = page.get_by_role("button", name="전액사용").first
    if btn.count() == 0:
        btn = page.locator("button._2ivczp3").filter(has_text="전액사용").first
    if btn.count() == 0:
        return "H.Point 전액사용 버튼 없음 (포인트 부족/미대상) — skip"
    before = _pay_amount(page)
    try:
        btn.click()
    except Exception as e:
        return f"H.Point 전액사용 클릭 실패: {e}"
    page.wait_for_timeout(1500)
    after = _pay_amount(page)
    if before is not None and after is not None and after < before:
        return f"H.Point 전액사용 적용 ({before:,}→{after:,}, -{before - after:,}p)"
    return f"⚠️ 전액사용 눌렀으나 금액변화 미확인 (before={before}, after={after})"


def checkout_one(page, go: bool) -> dict:
    # 1) 장바구니 → 일반상품 전체선택
    page.goto(run.CART_URL, wait_until="domcontentloaded")
    page.wait_for_timeout(1500)
    page.evaluate(SELECT_ALL_JS)
    page.wait_for_timeout(500)

    # 2) 구매하기
    btn = page.locator("button.btn-purchase").filter(has_text="구매하기").first
    if btn.count() == 0:
        btn = page.locator("button").filter(has_text="구매하기").first
    if btn.count() == 0:
        return {"ok": False, "err": "구매하기 버튼 없음 (카트 비었을 수 있음)"}
    btn.click()
    page.wait_for_load_state("domcontentloaded", timeout=15000)
    page.wait_for_timeout(2500)

    # 3) H.Point 전액사용 (카드선택 전 — 폰 흐름과 동일 순서)
    print(f"    {apply_hpoint_full(page)}")
    page.wait_for_timeout(500)

    # 4) 카드할인 캐러셀에서 카카오페이 찾기
    slides = run.detect_carousel_slides(page)
    summary = [f"{s['brand']}({s['percent']}%)" + ("" if s["isCard"] else "[PAY]") for s in slides]
    print(f"    캐러셀 {len(slides)}개: {summary}")
    kakao = next((s for s in slides if "카카오" in s.get("brand", "")), None)
    if not kakao:
        return {"ok": False, "err": f"카카오페이 슬라이드 미발견 (슬라이드={summary})"}
    cardcd = kakao["cardCd"]

    # 이미 selected 면 재탭 금지 (토글 off 방지)
    already = page.evaluate(
        f"""() => {{
            const el = document.querySelector('div._32o920j:has(img[alt="{cardcd}"])');
            return el ? (el.className || '').includes('selected') : false;
        }}"""
    )
    if already:
        print(f"    카카오페이 이미 선택됨 (cardCd={cardcd}) — click 안 함")
    else:
        if not run.click_carousel_slide(page, cardcd):
            return {"ok": False, "err": "카카오페이 슬라이드 클릭 실패"}
        print(f"    카카오페이 선택 (cardCd={cardcd})")

    # 5) 결제하기 버튼
    page.wait_for_timeout(800)
    pay = page.locator("button.btn-confirm").filter(has_text="결제하기").first
    if pay.count() == 0:
        pay = page.locator("button").filter(has_text="결제하기").first
    if pay.count() == 0:
        return {"ok": False, "err": "결제하기 버튼 없음"}
    label = (pay.inner_text() or "").strip()

    if not go:
        print(f"    [INSPECT] 결제하기 버튼 확인: '{label}' — 누르지 않음 (--go 시 클릭)")
        return {"ok": True, "inspect": True, "pay_label": label}

    pay.click()
    page.wait_for_timeout(2500)
    print(f"    [GO] 결제하기 클릭 완료 ('{label}') — 카카오페이 인증 화면. 본폰으로 스캔/인증하세요.")
    return {"ok": True, "paid_click": True, "pay_label": label}


def _run_accounts(browser, accounts, idxs, go: bool, mode: str) -> int:
    ctx = browser.contexts[0] if browser.contexts else browser.new_context()
    for idx in idxs:
        acc = accounts[idx - 1]
        print(f"\n=== #{idx} {acc['id']} 카카오페이 체크아웃 ({mode}) ===")
        page = ctx.new_page()
        try:
            run._hmall_clean(ctx, page, deep=True)
            if not run.login(page, acc["id"], acc["pw"]):
                print("    ✗ 로그인 실패")
                continue
            r = checkout_one(page, go)
            print(f"    결과: {r}")
        except Exception as e:
            print(f"    [FATAL] #{idx}: {e}")
        finally:
            page.close()
    return 0


def main() -> int:
    args = sys.argv[1:]
    go = "--go" in args
    idxs = [int(a) for a in args if a.isdigit()]
    if not idxs:
        print("사용: python3 buy/checkout_kakao.py <계정번호...> [--go]")
        return 1

    accounts = run.load_json(run.ACCOUNTS_FILE)["accounts"]
    run.ensure_chrome(int(run.CDP_PORT))
    mode = "GO 결제하기클릭" if go else "INSPECT(안누름)"
    print(f"[checkout_kakao] {mode} — 계정 {idxs}")

    # CDP-attach 는 plain playwright 로 고정 — patchright 드라이버는 Chrome 148 connect 시
    # Network.setCacheDisabled 로 크래시(스텔스 패치 부작용). CDP attach 는 이미 떠있는 실 Chrome
    # 인스턴스에 붙는 것이라 스텔스 무관 (run.py 카트담기 19/19도 실제로 plain playwright 로 성공).
    from playwright.sync_api import sync_playwright as pw_plain
    with pw_plain() as p:
        browser = p.chromium.connect_over_cdp(run.CDP_ENDPOINT, slow_mo=500)
        return _run_accounts(browser, accounts, idxs, go, mode)


if __name__ == "__main__":
    sys.exit(main())
