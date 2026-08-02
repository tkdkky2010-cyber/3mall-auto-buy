"""Hmall 공급률 분석 — 전 조합(len(COMBOS)) 실제 체크아웃 페이지 가격 기반 (active).

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
from chrome_launcher import resolve_cdp_port


def _pick_cdp_backend(endpoint: str):
    """CDP connect 백엔드 선택 — patchright 우선. Chrome 147+ 에서 patchright connect_over_cdp 가
    행(무한대기)/에러 가능(run.py 와 동일 이슈) → 12s 프로브로 확인, 실패/행이면 plain playwright fallback.
    (2026-06-01 step1 Hmall 이 patchright connect 에서 15분 무한대기한 사건 재발방지.)"""
    import signal
    try:
        from patchright.sync_api import sync_playwright as _patch
    except ImportError:
        from playwright.sync_api import sync_playwright as _plain
        return _plain

    def _on_alarm(_s, _f):
        raise TimeoutError("patchright connect_over_cdp 12s 초과 (행 의심)")
    old = signal.signal(signal.SIGALRM, _on_alarm)
    try:
        signal.alarm(12)
        with _patch() as pw:
            pw.chromium.connect_over_cdp(endpoint, timeout=10000).close()
        signal.alarm(0)
        print("[INFO] CDP backend: patchright")
        return _patch
    except Exception as e:
        signal.alarm(0)
        print(f"[WARN] patchright CDP 연결 실패/행 ({str(e)[:100]}) → plain playwright fallback")
        from playwright.sync_api import sync_playwright as _plain
        return _plain
    finally:
        signal.signal(signal.SIGALRM, old)

IDS = json.load(open(ROOT / "hsmaster" / "config" / "sulwhasoo-ids.json"))["ids"]

# 캐러셀 카드 brand → 페이백계수.
# brand 는 '롯데카드' 같은 단독형뿐 아니라 '카카오페이 롯데' / '토스페이 삼성' 처럼
# 간편결제로 래핑된 형태로도 나옴. 간편결제(카카오/토스)도 실제 underlying 카드로 청구되므로
# 일반 카드결제와 동일 페이백 적용 → underlying 카드 stem 으로 매칭 (사용자 6/2 지시).
CARD_STEM_TO_PAYBACK = {
    "롯데": C.CARD_PAYBACK["롯데카드"],
    "비씨": C.CARD_PAYBACK["비씨카드"],
    "BC":   C.CARD_PAYBACK["비씨카드"],
    "삼성": C.CARD_PAYBACK["삼성카드"],
    "하나": C.CARD_PAYBACK["하나카드"],
    "농협": C.CARD_PAYBACK["농협카드"],
    "NH":   C.CARD_PAYBACK["농협카드"],
    "국민": C.CARD_PAYBACK["KB국민카드"],
    "KB":   C.CARD_PAYBACK["KB국민카드"],
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


# 장바구니 '담긴 상품' 컨테이너 — '일반상품' 체크박스가 들어있는 div (2026-08-02 DOM 실측).
# ★이 스코프 밖(페이지 하단 '최근 본 상품'/추천 캐러셀)에도 상품명이 널려 있다.
CART_SECTION_SEL = "div.shipping-listwrap"

_JS_CART_ITEMS = r"""() => {
    // '일반상품' 체크박스를 가진 label 의 조상 = 카트 목록 컨테이너. 없으면 클래스 폴백.
    let sec = null;
    const lab = Array.from(document.querySelectorAll('label.chklabel'))
        .find(l => l.querySelector('span') && l.querySelector('span').textContent.trim() === '일반상품');
    if (lab) {
        sec = lab;
        for (let i = 0; i < 10 && sec.parentElement; i++) {
            sec = sec.parentElement;
            if (sec.querySelectorAll('input[type=checkbox]').length >= 2) break;
        }
    }
    if (!sec) sec = document.querySelector('div.shipping-listwrap');
    if (!sec) return [];
    const names = [];
    for (const el of sec.querySelectorAll('span,a,strong')) {
        const t = (el.innerText || '').trim();
        if (t.length < 8 || t.length > 90 || t.includes('\n')) continue;
        if (!/설화수|기획세트|\d+ml|\d+개$/.test(t)) continue;
        if (!names.includes(t)) names.push(t);
        if (names.length >= 30) break;
    }
    return names;
}"""


def cart_items(page) -> list[str]:
    """실제 장바구니에 담긴 상품명 목록.

    ★페이지 전체(document)를 긁으면 하단 **'최근 본 상품' 캐러셀까지 딸려온다** —
      2026-08-02 그렇게 읽어서 카트에 없는 상품(에스트라 등)을 '담겨있다'고 오독했다.
      반드시 카트 섹션 안에서만 읽는다.
    """
    try:
        return page.evaluate(_JS_CART_ITEMS) or []
    except Exception as e:
        print(f"    [cart] 목록 읽기 실패: {e}")
        return []


def _cart_is_empty(page) -> bool:
    """카트가 실제로 비었는지 확인 (clear_cart 검증용). 판정 불가 시 False = 안전측.
    빈카트 문구 + **카트 섹션 내 실제 품목 수** 둘 다로 판정."""
    try:
        page.goto(CART_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(1200)
        body = page.inner_text("body")
        if ("장바구니가 비어" in body) or ("담긴 상품이 없" in body):
            return True
        return len(cart_items(page)) == 0
    except Exception as e:
        print(f"    [cart] 빈카트 확인 실패: {e}")
        return False


def lookup_payback(brand: str) -> float:
    """카드 brand → 페이백계수 (없으면 0).
    brand 는 '롯데카드' 또는 간편결제 래핑형 '카카오페이 롯데'/'토스페이 삼성' 등.
    underlying 카드 stem 으로 매칭 (간편결제도 실제 카드 청구 → 일반결제와 동일 페이백).

    ★미등재 카드는 0 이지만 **조용히 넘어가지 않고 경고**한다 (2026-08-02: 당일카드가 현대로
      바뀌었는데 카탈로그에 현대가 없어 페이백 0% 로 계산됐고, 그게 맞는지 알 길이 없었다).
    """
    for stem, pct in CARD_STEM_TO_PAYBACK.items():
        if stem in brand:
            return pct
    print(f"  [WARN] 페이백 카탈로그에 없는 카드 '{brand}' → 0% 적용. "
          f"실제 페이백이 있으면 _common.CARD_PAYBACK 에 추가할 것 (등재: {list(CARD_STEM_TO_PAYBACK)})")
    return 0.0


def process_combo(page, idx: int, combo: list[tuple[str, int]],
                  add_gift_value: dict[str, int], gwp_70tier: int) -> dict:
    """조합 1개 처리 — cart 비우기 → 담기 → 체크아웃 → 캐러셀 → 가격 추출."""
    소비자가 = sum(C.PRODUCTS[c]["price"] * q for c, q in combo)
    추증 = sum(add_gift_value.get(c, 0) * q for c, q in combo)
    총샘플 = 추증 + gwp_70tier

    print(f"\n=== 조합 {idx}: {combo} 소비자가 {소비자가:,}원 ===")

    # 1) cart 비우기 — ★비운 뒤 실제로 비었는지 검증 (2026-08-02 사고).
    #    clear_cart 는 '일반상품 체크박스 없음'/'선택삭제 버튼 없음' 이어도 조용히 return 한다.
    #    조합23(h×3) 잔여물 위에 조합24(g2+n2)를 담아 소비자가 720,000 짜리가 1,126,548원으로
    #    측정되고 그 값이 그대로 시트에 기록됐다. 감지하고도 진행 = 조용한 오염 → 여기서 끊는다.
    print(f"  [STEP] clear_cart")
    for attempt in (1, 2):
        clear_cart(page)
        page.wait_for_timeout(800)
        if _cart_is_empty(page):
            break
        left = cart_items(page)
        print(f"  [WARN] clear_cart 후에도 카트 비어있지 않음 (시도 {attempt}/2) — 잔여 {left}")
    else:
        return {"idx": idx, "combo": combo,
                "error": f"clear_cart 실패 — 카트 잔여물 {cart_items(page)} (2회 시도). 합산 오염 방지로 중단"}

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
        "소비자가": 소비자가, "추증": 추증, "GWP": gwp_70tier, "총샘플": 총샘플,
        "card_brand": best["brand"], "card_pct": best["percent"], "payback_pct": payback,
        "preview_price": best["preview_price"], "구매가격": 구매가격,
        "순구매가": 순, "공급률": round(공급률, 4),
    }


def ensure_logged_in(page) -> bool:
    """로그인 상태 확인, 로그아웃이면 accounts[0] 으로 재로그인.
    반환 True = 로그인됨(이미 또는 재로그인 성공) / False = 재로그인 실패.
    세션은 측정 중 끊길 수 있어(구매하기→비회원폼/장바구니 바운스) 매 조합 실패 시 재호출한다.

    ★2026-08-02: 옛 판정은 `mo/mma/myhome` 로 이동해 URL 에 loginForm 이 없으면 True 였는데,
      **그 URL 이 죽어서 '페이지를 찾을 수 없습니다' 404 를 반환**한다. 404 에도 loginForm 이
      없으니 로그아웃 상태여도 무조건 True → 세션 끊김을 영영 못 잡는 오판이었다
      (자동메모리 hmall-session-drop-preflight 가 경고한 바로 그 형태).
      → 홈에서 '로그아웃' 링크 존재로 판정한다(로그인 시에만 노출). 실측 확인.
    """
    page.goto("https://www.hmall.com/", wait_until="domcontentloaded")
    page.wait_for_timeout(1500)
    body = page.inner_text("body")
    if "로그아웃" in body:
        return True
    print(f"[INFO] 미로그인 감지 (홈에 '로그아웃' 없음, url={page.url[:60]})")
    cfg = json.load(open(ROOT / "hmall_config.json"))
    acc = cfg["accounts"][0]
    print(f"[INFO] 세션 로그아웃 감지 → 재로그인 {acc['id']}")
    return login(page, acc["id"], acc["pw"])


def _result_row(r: dict) -> list:
    """results dict → 시트 한 행(13컬럼). error면 idx+에러만."""
    if r.get("error"):
        return [r["idx"], r["error"], "", "", "", "", "", "", "", "", "", "", ""]
    cn = C.combo_label_ko(r["combo"])
    return [r["idx"], cn, r["소비자가"], r["추증"], r["GWP"], r["총샘플"],
            r["card_brand"], f"{r['card_pct']}%", f"{r['payback_pct']*100:.1f}%",
            r["preview_price"], r["구매가격"], r["순구매가"], r["공급률"]]


def write_sheet(results: list[dict], tab: str, only_idx: int | None = None):
    """시트 "{M.DD}" 탭 Hmall 영역 입력 (RULES.md §13 layout).
    only_idx 면 그 조합 행만 targeted 갱신 — 전체 batch_clear/재작성 안 함 → 나머지 22행 보존."""
    gc = C.gs_client()
    sh = gc.open_by_key(C.RATE_SHEET_ID)
    ws = C.get_or_create_tab(sh, tab, leftmost=True)

    if only_idx is not None:
        r = next((x for x in results if x["idx"] == only_idx), None)
        if not r:
            print(f"  ⚠️ 조합{only_idx} 결과 없음 — 시트 미갱신"); return
        target_row = C.HMALL_HEADER_ROW + 6 + (only_idx - 1)   # 헤더/안내 6행 아래, idx 순 데이터
        ws.update(values=[_result_row(r)], range_name=f"A{target_row}:M{target_row}",
                  value_input_option="USER_ENTERED")
        lcell = 1 + only_idx                                    # L1=헤더 'Hmall', L(1+idx)=조합idx
        lval = "" if r.get("error") else round(r["공급률"], 4)
        ws.update(values=[[lval]], range_name=f"L{lcell}:L{lcell}", value_input_option="USER_ENTERED")
        print(f"  → 조합{only_idx} 단일행만 갱신: A{target_row}:M{target_row} + L{lcell} (나머지 행 보존)")
        return

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
        rows.append(_result_row(r))
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
    gwp_70tier = comp["gwp_70tier"]
    print(f"[INFO] composition (sheet): GWP(70만↑ 4세트)={gwp_70tier:,}원")
    print(f"[INFO] 추가증정가치: {add_gift_value}")

    combos = list(enumerate(C.COMBOS, start=1))
    if only_idx:
        combos = [(i, c) for i, c in combos if i == only_idx]
    print(f"[INFO] 처리 조합 {len(combos)}개")

    results: list[dict] = []
    _port = resolve_cdp_port(C.CDP_PORT)   # 9222 막히면 9223→9224 (같은 CFT)
    _endpoint = f"http://127.0.0.1:{_port}"
    sync_playwright = _pick_cdp_backend(_endpoint)   # patchright 우선, Chrome 147+ 행/에러 시 plain playwright (12s 프로브)
    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp(_endpoint, timeout=20000)
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        page = context.pages[-1] if context.pages else context.new_page()   # 기존 탭 재사용(포커스 강탈 방지)
        page.set_default_timeout(25000)

        # 로그인 검증
        if not ensure_logged_in(page):
            sys.exit("❌ 로그인 실패")

        for idx, combo in combos:
            try:
                r = process_combo(page, idx, combo, add_gift_value, gwp_70tier)
                # 측정 중 세션이 끊기면 구매하기가 장바구니/비회원폼으로 바운스됨
                # → 재로그인 후 그 조합만 1회 재시도 (이후 조합 줄줄이 실패 방지).
                if str(r.get("error", "")).startswith("체크아웃 페이지 미도달"):
                    if ensure_logged_in(page):
                        print(f"  [RETRY] 조합 {idx} 재로그인 후 재시도")
                        r = process_combo(page, idx, combo, add_gift_value, gwp_70tier)
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
        scope = f"조합{only_idx} 단일" if only_idx else "전체"
        print(f"\n[INFO] 시트 입력({scope}) → 탭 {tab!r} (--dry-sheet 로 skip 가능)")
        write_sheet(results, tab, only_idx)
    elif dry_sheet:
        print("\n[INFO] --dry-sheet — 시트 입력 생략")

    return 0


if __name__ == "__main__":
    sys.exit(main())
