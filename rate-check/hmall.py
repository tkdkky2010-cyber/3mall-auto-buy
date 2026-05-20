"""Hmall 공급률 분석 — 20조합 실제 체크아웃 페이지 가격 기반 (active).

Step 1 substep #4 의 active script. buy/run.py 의 로그인 + cart 자동 fill 사용 →
사용자 수동 cart 세팅 불필요. 완전 자동.

흐름: 조합별로 실제 cart→checkout 진입 → 카드할인 캐러셀에서
가장 높은 할인율 카드의 미리보기 가격을 구매가격으로 사용. 페이백 카드면 계수 곱.

결제하기 절대 클릭 X — rate check 전용.

레이아웃 (RULES.md §13): A{HMALL_HEADER_ROW}:M{HMALL_COMBO_END_ROW}
                       — galleria 1~{GALLERIA_DATA_END_ROW}, lotte {LOTTE_HEADER_ROW}~ 와 분리.

사용:
    python3 rate-check/hmall.py             # 20조합 전체
    python3 rate-check/hmall.py 11          # 11번 조합만 (테스트용)
    python3 rate-check/hmall.py --dry-sheet # 시트 입력 skip
"""
from __future__ import annotations
import json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "buy"))
sys.path.insert(0, str(ROOT / "rate-check"))

import run as buy_run  # type: ignore
from run import CART_URL, CDP_ENDPOINT, login, clear_cart, add_to_cart  # type: ignore
import _common as C
from chrome_launcher import ensure_chrome

IDS = json.load(open(ROOT / "hsmaster" / "config" / "sulwhasoo-ids.json"))["ids"]

# 캐러셀 카드명 → 페이백계수
CARD_NAME_TO_PAYBACK = {
    "롯데카드": C.CARD_PAYBACK["롯데카드"],
    "비씨카드": C.CARD_PAYBACK["비씨카드"],
    "비씨카드(페이북)": C.CARD_PAYBACK["비씨카드"],
    "삼성카드": C.CARD_PAYBACK["삼성카드"],
    "하나카드": C.CARD_PAYBACK["하나카드"],
    "농협카드": C.CARD_PAYBACK["농협카드"],
    "NH농협카드": C.CARD_PAYBACK["농협카드"],
    "KB국민카드": C.CARD_PAYBACK["KB국민카드"],
    "국민카드": C.CARD_PAYBACK["KB국민카드"],
}

CHECKOUT_URL_FRAG = "/mo/oda/order"


def detect_card_offer(page) -> dict | None:
    """체크아웃 페이지 '카드할인' 헤더 다음 캐러셀에서 카드 1개 정보 추출.

    헤더 룰: <div class="tarvxz0"><h2>카드할인</h2></div>
    당일 최대 할인율 카드 1개만 뜨므로 그대로 사용 (사용자 5/18 확정).

    Returns: {cardCd, brand, percent, preview_price} | None
    """
    js = r"""
    () => {
        const hdr = Array.from(document.querySelectorAll('.tarvxz0'))
            .find(d => {
                const h2 = d.querySelector('h2');
                return h2 && h2.textContent.trim() === '카드할인';
            });
        if (!hdr) return null;
        const sec = hdr.nextElementSibling;
        if (!sec) return null;
        const img = sec.querySelector('img[alt^="cardCd"]');
        if (!img) return null;
        const txt = (sec.textContent || '').replace(/\s+/g, ' ').trim();
        const pctMatch = txt.match(/(\d+)\s*%/);
        const percent = pctMatch ? parseInt(pctMatch[1]) : 0;
        const namePart = txt.split(/\d+\s*%/)[0].trim();
        const priceMatch = txt.match(/([\d,]{4,})\s*원/);
        const preview_price = priceMatch ? parseInt(priceMatch[1].replace(/,/g, '')) : null;
        return { cardCd: img.alt, brand: namePart, percent, preview_price };
    }
    """
    return page.evaluate(js)


def lookup_payback(brand: str) -> float:
    """카드명 → 페이백계수 (없으면 0)."""
    for key, pct in CARD_NAME_TO_PAYBACK.items():
        if key in brand or brand in key:
            return pct
    return 0.0


def process_combo(page, idx: int, combo: list[tuple[str, int]],
                  add_gift_value: dict[str, int], gwp_6set: int) -> dict:
    """조합 1개 처리 — cart 비우기 → 담기 → 체크아웃 → 캐러셀 → 가격 추출."""
    소비자가 = sum(C.PRODUCTS[c]["price"] * q for c, q in combo)
    추증 = sum(add_gift_value.get(c, 0) * q for c, q in combo)
    총샘플 = 추증 + gwp_6set

    print(f"\n=== 조합 {idx}: {combo} 소비자가 {소비자가:,}원 ===")

    # 1) cart 비우기
    print(f"  [STEP] clear_cart")
    clear_cart(page)
    page.wait_for_timeout(800)

    # 2) 본품 담기
    for c, q in combo:
        info = {"name": IDS[c]["name"], "slitmCd": IDS[c]["hyundai"],
                "url_extra": "", "option_index": 1, "auto_coupon": True}
        print(f"  [STEP] add_to_cart {c} × {q}")
        if not add_to_cart(page, c, info, q):
            return {"idx": idx, "error": f"add_to_cart 실패: {c}×{q}"}
        page.wait_for_timeout(400)

    # 3) cart → 일반상품 체크 → 구매하기
    page.goto(CART_URL, wait_until="domcontentloaded")
    page.wait_for_timeout(1500)
    page.evaluate("""() => {
        const labels = Array.from(document.querySelectorAll('label.chklabel'));
        const t = labels.find(l => l.querySelector('span')?.textContent.trim() === '일반상품');
        if (t) {
            const cb = t.querySelector('input[type="checkbox"]');
            if (cb && !cb.checked) t.click();
        }
    }""")
    page.wait_for_timeout(500)
    btn = page.locator("button.btn-purchase").filter(has_text="구매하기").first
    if btn.count() == 0:
        btn = page.locator("button").filter(has_text="구매하기").first
    if btn.count() == 0:
        return {"idx": idx, "error": "구매하기 버튼 없음"}
    btn.click()
    page.wait_for_load_state("domcontentloaded", timeout=15000)
    page.wait_for_timeout(2800)

    # 4) 체크아웃 도달 확인
    if CHECKOUT_URL_FRAG not in page.url:
        return {"idx": idx, "error": f"체크아웃 페이지 미도달 (cur={page.url[:60]})"}

    # 5) 카드할인 캐러셀 (당일 최대 할인율 카드 1개)
    best = detect_card_offer(page)
    if not best:
        return {"idx": idx, "error": "카드할인 캐러셀 없음 (.tarvxz0 h2='카드할인' 또는 다음 캐러셀 카드 없음)"}
    if not best.get("percent") or not best.get("preview_price"):
        return {"idx": idx, "error": f"카드 정보 추출 실패 (brand={best.get('brand')!r}, %={best.get('percent')}, price={best.get('preview_price')})"}
    print(f"  [INFO] 카드할인: {best['brand']} {best['percent']}% → {best['preview_price']:,}원 (cardCd={best['cardCd']})")

    # 6) 페이백 적용
    payback = lookup_payback(best["brand"])
    if payback:
        구매가격 = round(best["preview_price"] * (1 - payback))
        print(f"  [OK] {best['brand']} {best['percent']}% {best['preview_price']:,}원 × (1-{payback}) = {구매가격:,}원")
    else:
        구매가격 = best["preview_price"]
        print(f"  [OK] {best['brand']} {best['percent']}% → {구매가격:,}원 (페이백 없음)")

    순 = 구매가격 - 총샘플
    공급률 = 순 / 소비자가 if 소비자가 else 0

    return {
        "idx": idx, "combo": combo,
        "소비자가": 소비자가, "추증": 추증, "GWP": gwp_6set, "총샘플": 총샘플,
        "card_brand": best["brand"], "card_pct": best["percent"], "payback_pct": payback,
        "preview_price": best["preview_price"], "구매가격": 구매가격,
        "순구매가": 순, "공급률": round(공급률, 4),
    }


def write_sheet(results: list[dict], tab: str):
    """시트 "{M.DD}" 탭 Hmall 영역 입력 (RULES.md §13 layout)."""
    gc = C.gs_client()
    sh = gc.open_by_key(C.RATE_SHEET_ID)
    ws = C.get_or_create_tab(sh, tab, leftmost=True)
    ws.batch_clear([f"A{C.HMALL_HEADER_ROW}:M{C.HMALL_COMBO_END_ROW}"])

    rows = [
        ["━━━━ 2단계: 현대Hmall 공급률 분석 (체크아웃 페이지 캐러셀 가격) ━━━━"],
        [],
        ["선택 룰: 카드할인 캐러셀에서 가장 높은 할인율 카드 미리보기 가격. 페이백 카드면 계수 곱."],
        ["Hmall 기본할인 + 쿠폰까지 페이지 표시가에 자동 반영됨."],
        [],
        ["조합번호", "조합", "소비자가", "추가증정", "GWP가치", "총샘플가치",
         "선택카드", "카드%", "페이백%", "미리보기가", "구매가격", "순구매가", "공급률"],
    ]
    for r in sorted(results, key=lambda x: x["idx"]):
        if r.get("error"):
            rows.append([r["idx"], r["error"], "", "", "", "", "", "", "", "", "", "", ""])
            continue
        cn = C.combo_label_ko(r["combo"])
        rows.append([
            r["idx"], cn, r["소비자가"], r["추증"], r["GWP"], r["총샘플"],
            r["card_brand"], f"{r['card_pct']}%", f"{r['payback_pct']*100:.1f}%",
            r["preview_price"], r["구매가격"], r["순구매가"], r["공급률"],
        ])
    C.write_grid(ws, C.HMALL_HEADER_ROW, rows)
    print(f"  → 시트 입력: A{C.HMALL_HEADER_ROW}:M{C.HMALL_HEADER_ROW + len(rows) - 1}")

    # J~M 비교 차트 — Hmall 컬럼 (L) 채움. K=galleria/M=lotte 는 각 스크립트가.
    chart_l: list[list] = [["Hmall"]]
    for r in sorted(results, key=lambda x: x["idx"]):
        if r.get("error"):
            chart_l.append([""])
        else:
            chart_l.append([round(r["공급률"], 4)])
    chart_end = 1 + len(C.COMBOS)
    ws.update(values=chart_l, range_name=f"L1:L{chart_end}", value_input_option="USER_ENTERED")
    print(f"  → L1:L{chart_end} (Hmall 비교 차트 컬럼) 입력")


def main(argv=None):
    argv = argv or sys.argv[1:]
    dry_sheet = "--dry-sheet" in argv
    only_idx = None
    for a in argv:
        if a.isdigit():
            only_idx = int(a)

    # composition 로드 — sheet에서 직접 (캐시 X, sheet가 SoT, 사용자 5/15 지시)
    gc_read = C.gs_client()
    sh_read = gc_read.open_by_key(C.RATE_SHEET_ID)
    try:
        ws_read = sh_read.worksheet(C.today_tab_name())
    except Exception:
        sys.exit(f"❌ '{C.today_tab_name()}' 탭 없음 — 먼저 galleria.py 실행 필요")
    comp = C.load_galleria_composition_from_sheet(ws_read)
    add_gift_value = comp["add_gift_value"]
    gwp_6set = comp["gwp_6set"]
    print(f"[INFO] composition (sheet): GWP 6세트={gwp_6set:,}원")
    print(f"[INFO] 추가증정가치: {add_gift_value}")

    combos = list(enumerate(C.COMBOS, start=1))
    if only_idx:
        combos = [(i, c) for i, c in combos if i == only_idx]
    print(f"[INFO] 처리 조합 {len(combos)}개")

    try:
        from patchright.sync_api import sync_playwright
    except ImportError:
        from playwright.sync_api import sync_playwright

    results: list[dict] = []
    ensure_chrome(C.CDP_PORT)
    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp(CDP_ENDPOINT)
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        page = context.new_page()
        page.set_default_timeout(25000)

        # 로그인 검증
        page.goto("https://www.hmall.com/mo/mma/myhome", wait_until="domcontentloaded")
        page.wait_for_timeout(1500)
        if "loginForm" in page.url or "/login" in page.url:
            print("[INFO] 로그인 필요")
            cfg = json.load(open(ROOT / "hmall_config.json"))
            acc = cfg["accounts"][0]
            if not login(page, acc["id"], acc["pw"]):
                sys.exit("❌ 로그인 실패")
            page.wait_for_timeout(1500)
        else:
            print(f"[INFO] 이미 로그인 상태")

        for idx, combo in combos:
            try:
                r = process_combo(page, idx, combo, add_gift_value, gwp_6set)
            except Exception as e:
                r = {"idx": idx, "error": f"예외: {e}"}
            results.append(r)

    print("\n=== 요약 ===")
    print(f"{'idx':>3} {'조합':40s} {'카드':10s} {'%':>4s} {'미리보기':>10s} {'구매':>10s} {'순':>10s} {'공급률':>7s}")
    valid = [r for r in results if not r.get("error")]
    for r in sorted(results, key=lambda x: x["idx"]):
        if r.get("error"):
            print(f"  {r['idx']:>3d} ERR: {r['error']}")
            continue
        cn = C.combo_label_ko(r["combo"])
        print(f"  {r['idx']:>3d} {cn[:40]:40s} {r['card_brand'][:10]:10s} {r['card_pct']:>3d}% "
              f"{r['preview_price']:>10,d} {r['구매가격']:>10,d} {r['순구매가']:>10,d} {r['공급률']:>7.4f}")

    if valid and not dry_sheet:
        tab = C.today_tab_name()
        print(f"\n[INFO] 시트 입력 → 탭 {tab!r} (--dry-sheet 로 skip 가능)")
        write_sheet(results, tab)
    elif dry_sheet:
        print("\n[INFO] --dry-sheet — 시트 입력 생략")

    return 0


if __name__ == "__main__":
    sys.exit(main())
