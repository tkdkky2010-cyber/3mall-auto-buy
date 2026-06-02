"""갤러리아 공급률 분석 — 1단계.

CDP 9222 attach (~/bin/launch-hmall-chrome.sh로 미리 실행).
실행:
    python3 rate-check/galleria.py
    python3 rate-check/galleria.py --gwp-config rate-check/_tmp/gwp_2026-05-14.json

GWP resume 패턴:
- 첫 실행 → GWP 이미지 다운로드 + stdout에 PENDING 안내 + exit 2
- 사용자/PM이 이미지 판독 → JSON 작성
- 같은 명령 재실행 → 자동으로 _tmp/gwp_{date}.json 로드 (또는 --gwp-config 명시)

산출물:
- gspread "{M.DD}" 탭의 갤러리아 섹션 (행 1~45) — **유일한 결과 저장소**
- ★ 로컬 캐시/JSON 파일 절대 생성 X — 재실행 시 옛 캐시 따라쓰는 버그 방지 (sheet가 SoT)
"""
from __future__ import annotations
import argparse
import io
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import requests
from selenium import webdriver
from selenium.webdriver.chrome.options import Options

sys.path.insert(0, str(Path(__file__).parent))
import _common as C

PENDING_EXIT = 2

GALLERIA_URL = "https://www.galleria.co.kr/goods/initDetailGoods.action?goods_no={}"


# ============================================================
# Selenium attach
# ============================================================
def attach_chrome() -> webdriver.Chrome:
    opts = Options()
    opts.add_experimental_option("debuggerAddress", f"127.0.0.1:{C.CDP_PORT}")
    return webdriver.Chrome(options=opts)


# ============================================================
# 페이지 1개 처리: 기본할인%, 쿠폰%, 추가증정 raw 텍스트, GWP 이미지 src
# ============================================================
EXTRACT_JS = r"""
const out = {};
const fullText = document.body.innerText;
out.length = fullText.length;

// ★ 쿠폰 추출 — button.down em 안의 텍스트에서만 (page-wide regex 절대 X):
//   <button class="down" onclick="...couponListLayer..."><em>[카테고리] 쿠폰명 N%</em></button>
//   이 button 안의 <em> 텍스트에서만 추출. Q&A 영역 / 배너 텍스트 / 기타 영역 모두 사용 X.
out.coupon_text = null;
const couponBtns = document.querySelectorAll('button.down em, button[onclick*="couponListLayer"] em');
for (const em of couponBtns) {
    const t = (em.textContent || '').trim();
    if (t.includes('쿠폰') && /\d+\s*%/.test(t)) {
        out.coupon_text = t;  // "[카테고리] 쿠폰명 N%" 형태
        break;
    }
}

// 기본할인 — GOODS.info 가격 비교로 산출 (sale_price 대비 cust_sale_price)
out.goods_data = (typeof GOODS !== 'undefined' && GOODS.info && GOODS.info.price) ? {
    sale_price: GOODS.info.price.sale_price || null,
    cust_sale_price: GOODS.info.price.cust_sale_price || null,
} : null;

// 1) "[추가 증정]" 또는 "[추가증정]" 다음에 나오는 텍스트 ~600자
const addPattern = /\[\s*추가\s*증정\s*\][^\[]*/g;
const addMatches = fullText.match(addPattern) || [];
out.add_blocks = addMatches.map(s => s.substring(0, 600));

// 4) GWP 이미지 (src에 galleria_md/gwp 포함, naturalHeight >= 100)
const imgs = Array.from(document.querySelectorAll('img'));
const gwpImgs = imgs.filter(i =>
    /galleria_md|gwp/i.test(i.src) && i.naturalHeight >= 100
).map(i => ({src: i.src, h: i.naturalHeight, w: i.naturalWidth}));
out.gwp_imgs = gwpImgs;

// 5) "본윤2종" "윤조3종" 등 상품 코드 검증
out.title = document.title;
return out;
"""


def _is_product_page(driver, goods_no: str) -> bool:
    """상품 페이지 정상 로드 여부. URL에 goods_no 가 있으면 OK.

    리디렉트(예: f → TUDOR 브랜드) 감지용.
    """
    cur = driver.current_url
    return goods_no in cur


def scrape_product(driver, code: str, goods_no: str, *, max_retry: int = 1) -> dict:
    url = GALLERIA_URL.format(goods_no)
    print(f"[{code}] → {url}")
    for attempt in range(max_retry + 1):
        driver.get(url)
        time.sleep(2.5)
        if _is_product_page(driver, goods_no):
            break
        if attempt < max_retry:
            print(f"  ⚠️  리디렉트 감지 ({driver.current_url}) — 재시도 {attempt + 1}/{max_retry}")
            time.sleep(1.5)
    else:
        print(f"  ⚠️  리디렉트 지속 — fallback 검색 모드 시도 (현재 URL: {driver.current_url})")
        driver.get(f"https://www.galleria.co.kr/search/searchTotal.action?searchKwd=설화수")
        time.sleep(2.0)
        # 검색 결과에서 goods_no 링크 클릭은 broad — 실패 시 그냥 raw 진행

    # 스크롤 — 추가증정/GWP 영역까지
    for y in (0, 800, 1600, 2400, 3200, 4000, 4800, 0):
        driver.execute_script(f"window.scrollTo(0, {y});")
        time.sleep(0.25)
    raw = driver.execute_script(EXTRACT_JS)
    raw["redirected"] = not _is_product_page(driver, goods_no)
    return {"code": code, "url": url, **raw}


# ============================================================
# raw → ProductDay 변환
# ============================================================
SET_COMBINE_RULES = [
    # (요구되는 raw 키워드 set, 결합 결과 (정식이름, 가격, s코드))
    ({"자음생수", "자음생유액"}, ("자음생수25ml자음생유액25ml", 4_900, "s07")),
    ({"자음수", "자음유액"}, ("자음수15ml자음유액15ml", 3_300, "s04")),
]

# 번들 라인 패턴 — "X종 GWP", "X종 포함", "구성품" 등
BUNDLE_LINE = re.compile(r"(\d+\s*종\s*GWP|\d+\s*종\s*포함|구성품|기획세트)")


def _split_inline_items(text: str) -> list[str]:
    """한 줄 안의 다중 품목 → 개별 단편.

    구분자: 콤마, 슬래시 (단위 뒤), ml/g/ea 뒤 공백+한글.
    """
    parts = re.split(r"[,，]|(?<=ml)\s+(?=[가-힣])|(?<=g)\s+(?=[가-힣])|(?<=\bea\b)\s+(?=[가-힣])", text)
    return [p.strip(" ·,;:()") for p in parts if p and p.strip()]


def parse_add_gifts(blocks: list[str]) -> tuple[list[C.Sample], list[str]]:
    """[추가 증정] 블록 → Sample 리스트 + 신규 품목 raw 리스트.

    처리 순서:
    1. 라인 단위 split, "[추가 증정]" 헤더 제거
    2. 번들 라인 ("X종 GWP (...)") → 괄호 안 단품들만 추출
    3. 일반 라인 → 인라인 split
    4. 각 단편 lookup_sample
    5. SET combine pass (자음생수+자음생유액 등)
    """
    raw_items: list[str] = []  # raw 텍스트만 모음
    new_items: list[str] = []

    for block in blocks:
        text = re.sub(r"\[\s*추가\s*증정\s*\]", "", block)
        for line in re.split(r"[\n\r]+", text):
            line = line.strip(" ·,;:")
            if not line or len(line) < 4:
                continue
            # 번들 라인 — 괄호 안만
            if BUNDLE_LINE.search(line):
                m = re.search(r"\(([^)]+)\)", line)
                if m:
                    for sub in _split_inline_items(m.group(1)):
                        if sub:
                            raw_items.append(sub)
                # 번들 라인 자체는 매칭 시도 안 함
                continue
            # 일반 라인 — 인라인 split
            for sub in _split_inline_items(line):
                if sub and len(sub) >= 4:
                    raw_items.append(sub)

    # SET combine — 결합 가능한 단품 쌍 처리
    samples: list[C.Sample] = []
    seen_keys: set[tuple] = set()
    consumed = [False] * len(raw_items)

    for kw_set, (name, price, scode) in SET_COMBINE_RULES:
        # 각 키워드를 포함하는 raw 인덱스 찾기
        matched_idx = []
        for kw in kw_set:
            for i, raw in enumerate(raw_items):
                if consumed[i]:
                    continue
                if kw in raw:
                    matched_idx.append(i)
                    break  # 키워드당 첫 매칭만
        if len(matched_idx) == len(kw_set):
            # 전부 매칭 — 결합
            key = (name, price)
            if key not in seen_keys:
                seen_keys.add(key)
                samples.append(C.Sample(
                    name=name, qty=1, price=price, code=scode,
                    raw_text=" + ".join(raw_items[i] for i in matched_idx),
                ))
            for i in matched_idx:
                consumed[i] = True

    # 결합 안 된 나머지 단품 매칭
    for i, raw in enumerate(raw_items):
        if consumed[i]:
            continue
        hit = C.lookup_sample(raw)
        if hit:
            name, price, scode = hit
            key = (name, price)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            samples.append(C.Sample(name=name, qty=1, price=price, code=scode, raw_text=raw))
        else:
            if any(unit in raw for unit in ("ml", "g", "ea", "팩", "마스크")):
                new_items.append(raw)

    return samples, new_items


def parse_basic_and_coupon(raw: dict) -> tuple[float, float]:
    """기본할인% + 쿠폰% 추출.

    ★ 사용자 5/15 확정 룰:
    - 쿠폰: <button class="down"><em>[화장] 더블쿠폰 N%</em></button> 의 em 텍스트에서만.
      페이지 전체 regex / Q&A 영역 / 배너 / GOODS.info 모두 사용 X.
    - 기본할인: GOODS.info.price.sale_price 대비 cust_sale_price 비율로 산출.
    """
    # 쿠폰 — coupon_text 에서 N% 추출 (button.down em)
    coupon = 0.0
    ctext = raw.get("coupon_text") or ""
    m = re.search(r"(\d+)\s*%", ctext)
    if m:
        coupon = float(m.group(1))

    # 기본할인 — GOODS.info 가격 비교
    gd = raw.get("goods_data") or {}
    sale = gd.get("sale_price")
    cust = gd.get("cust_sale_price")
    if sale and cust and sale > 0:
        basic = round((1 - cust / sale) * 100, 1)
    else:
        basic = 10.0

    return basic, coupon


# ============================================================
# GWP — 이미지 다운로드 + resume
# ============================================================
def download_gwp_image(src: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    r = requests.get(src, timeout=10)
    r.raise_for_status()
    dest.write_bytes(r.content)
    return dest


def load_gwp_config(path: Path) -> C.GwpDay:
    """GWP JSON → GwpDay. JSON 형식:
    {
      "period": "5.8 - 5.31",
      "set": [{"text": "순행클렌징오일 50ml", "qty": 1}, ...]
    }
    각 text 는 lookup_sample 로 단가 + s코드 매칭.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    items = []
    for entry in data["set"]:
        text = entry["text"]
        qty = int(entry["qty"])
        hit = C.lookup_sample(text)
        if hit:
            name, price, scode = hit
            items.append(C.Sample(name=name, qty=qty, price=price, code=scode, raw_text=text))
        else:
            print(f"  ⚠️  GWP 신규품목: {text!r} — 단가미정 (단가 0 처리)")
            items.append(C.Sample(name=text, qty=qty, price=0, code=None, raw_text=text))
    return C.GwpDay(set_items=items, period=data.get("period", ""))


def emit_gwp_pending(date: str, image_path: Path) -> int:
    json_path = C.TMP_DIR / f"gwp_{date}.json"
    print()
    print("=" * 60)
    print(f"▶ GWP_PENDING — 40/70만 GWP 이미지 판독 필요")
    print(f"   이미지: {image_path}")
    print(f"   판독 후 작성: {json_path}")
    print(f"   JSON 형식 예시:")
    print(f"     {{")
    print(f'       "period": "5.8 - 5.31",')
    print(f'       "set": [')
    print(f'         {{"text": "순행클렌징오일 50ml", "qty": 1}},')
    print(f'         {{"text": "여윤팩 35ml", "qty": 2}}')
    print(f'       ]')
    print(f"     }}")
    print(f"   그 다음 이 명령 재실행.")
    print("=" * 60)
    return PENDING_EXIT


# ============================================================
# gspread 입력 — 갤러리아 섹션 (행 1~C.GALLERIA_DATA_END_ROW)
# ============================================================
def write_galleria_section(ws, products: dict[str, C.ProductDay], gwp: C.GwpDay,
                           combos: list[dict], tab: str) -> str:
    # ★ galleria 영역 = 행 1~C.GALLERIA_DATA_END_ROW.
    # C.HMALL_HEADER_ROW~ Hmall, C.LOTTE_HEADER_ROW~ 롯데 침범 금지 (RULES §13).
    rows: list[list] = [[""] for _ in range(C.GALLERIA_DATA_END_ROW)]

    def setrow(idx, *vals):
        rows[idx] = list(vals)

    setrow(0, f"{tab} 공급률 분석 리포트 (갤러리아몰)")
    coupon_summary = ", ".join(f"{c}={products[c].coupon_pct:.0f}%" for c in C.PRODUCT_CODES)
    setrow(2, f"할인정보: 기본할인 + 쿠폰 ({coupon_summary}) + 네이버구매할인 ×{C.GALLERIA_NAVER_MULT}")
    setrow(3, "카드페이백: 없음 (갤러리아몰 카드할인 미운영)")

    setrow(5, f"[40/70만원 구매혜택 구성] {gwp.period}")
    for i, s in enumerate(gwp.set_items):
        setrow(6 + i, s.name, f"x{s.qty}", f"{s.price:,}원" if s.price else "단가미정")
    setrow(11, "1세트 합계", "", f"{gwp.set_value:,}원")
    setrow(12, "6세트 합계 (70만+)", "", f"{gwp.set_value * 6:,}원")

    # 상품별 추가증정 (행 15~)
    setrow(14, "[상품별 추가증정 구성]")
    header = ["", *[f"{c} {C.PRODUCTS[c]['name']}" for c in C.PRODUCT_CODES]]
    setrow(15, *header)
    max_samples = max(len(products[c].add_gifts) for c in C.PRODUCT_CODES) if products else 0
    for i in range(max_samples):
        row = [f"샘플{i + 1}"]
        for c in C.PRODUCT_CODES:
            items = products[c].add_gifts
            if i < len(items):
                s = items[i]
                row.append(f"{s.name} x{s.qty} ({s.price:,}원)")
            else:
                row.append("")
        setrow(16 + i, *row)
    setrow(16 + max_samples, "추가증정가치", *[f"{products[c].add_gift_value:,}원" for c in C.PRODUCT_CODES])
    # 신규 품목 알림
    new_alerts = []
    for c in C.PRODUCT_CODES:
        if products[c].new_items:
            new_alerts.append(f"{c}: {', '.join(products[c].new_items)}")
    if new_alerts:
        setrow(17 + max_samples, "⚠️ 신규품목 (단가미정)", *new_alerts)

    # 20개 조합 공급률 요약 (타이틀 28→행29, 헤더 29→행30, 데이터 30~49→행31~50)
    # A~G열: 조합 요약 / I~K열: 상품별 쿠폰율 (b~h, 첫 7행)
    setrow(28, f"[{len(C.COMBOS)}개 조합 공급률 요약 - 갤러리아몰]", "", "", "", "", "", "",
           "", "[상품별 쿠폰율]")
    setrow(29, "조합번호", "조합", "소비자가", "총샘플가치", "네이버최종구매가", "순구매가", "공급률",
           "", "상품", "기본할인%", "쿠폰%")
    for i, r in enumerate(combos):
        row_data = [r["idx"], r["name"], r["소비자가"], r["총샘플"],
                    r["최종구매가"], r["순구매가"], round(r["공급률"], 4)]
        if i < len(C.PRODUCT_CODES):
            code = C.PRODUCT_CODES[i]
            p = products[code]
            row_data += ["",
                         f"{code} {C.PRODUCTS[code]['name']}",
                         f"{p.basic_discount_pct:.0f}%",
                         f"{p.coupon_pct:.0f}%"]
        setrow(30 + i, *row_data)

    rng = C.write_grid(ws, 1, rows)

    # ★ J~M 비교 차트 (3사 공급률 비교 영역) — 갤러리아 컬럼만 채움
    # 사용자 layout: J1="조합", K1="갤러리아몰", L1="Hmall", M1="롯데"
    # 행 2~(1+N): 조합번호 1~N + 각 몰 공급률 (hmall/lotte는 자기 스크립트에서 채움)
    chart_data = [["조합", "갤러리아몰"]]
    for r in combos:
        chart_data.append([r["idx"], round(r["공급률"], 4)])
    chart_end = 1 + len(C.COMBOS)
    ws.update(values=chart_data, range_name=f"J1:K{chart_end}", value_input_option="USER_ENTERED")

    return rng


# ============================================================
# CLI
# ============================================================
def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--gwp-config", help="GWP 구성 JSON 경로 (생략 시 _tmp/gwp_{date}.json 자동)")
    p.add_argument("--tab", help="시트 탭 이름 override (기본: today)")
    p.add_argument("--skip-sheet", action="store_true", help="시트 입력 건너뛰기 (드라이런)")
    p.add_argument("--only", help="특정 코드만 (b/c/.../h) — 디버그용")
    args = p.parse_args(argv)

    today = datetime.now().strftime("%Y-%m-%d")
    tab = args.tab or C.today_tab_name()
    gwp_image_path = C.TMP_DIR / f"gwp_{today}.jpg"
    gwp_json_path = Path(args.gwp_config) if args.gwp_config else C.TMP_DIR / f"gwp_{today}.json"

    ids = C.load_ids()
    codes = [args.only] if args.only else C.PRODUCT_CODES

    print(f"▶ 갤러리아 공급률 분석 ({today}) — 탭 {tab!r}")

    # 샘플 단가 체크 — 재고현황 시트(SoT)에서 동적 로드 + 하드코딩 SAMPLE_TABLE drift 가시화.
    sheet_prices = C.sample_prices_from_sheet()
    print(f"  [단가체크] 재고현황 시트에서 {len(sheet_prices)}건 로드 (시트 우선 적용)")
    drift = C.check_sample_price_drift()
    if drift:
        print(f"  [단가체크] ⚠️ 하드코딩 SAMPLE_TABLE와 {len(drift)}건 불일치 → 시트값 사용:")
        for code, name, old, new in drift:
            print(f"            {code} {name}: 코드 {old:,} → 시트 {new:,}")
    elif sheet_prices:
        print("  [단가체크] ✓ 하드코딩과 일치 (drift 0)")

    print(f"  CDP {C.CDP_PORT} attach...")
    driver = attach_chrome()

    raw_results: dict[str, dict] = {}
    products: dict[str, C.ProductDay] = {}
    gwp_image_src: str | None = None

    try:
        for code in codes:
            goods_no = ids[code]["galleria"]
            r = scrape_product(driver, code, goods_no)
            raw_results[code] = r
            samples, new_items = parse_add_gifts(r["add_blocks"])
            basic, coupon = parse_basic_and_coupon(r)
            products[code] = C.ProductDay(
                code=code, basic_discount_pct=basic, coupon_pct=coupon,
                add_gifts=samples, new_items=new_items,
            )
            redirect_flag = " ⚠️REDIRECTED" if r.get("redirected") else ""
            print(f"  [{code}] basic={basic:.0f}%, coupon={coupon:.0f}%, "
                  f"추가증정 {len(samples)}개 ({products[code].add_gift_value:,}원), "
                  f"신규 {len(new_items)}개{redirect_flag}")
            if not gwp_image_src and r.get("gwp_imgs"):
                gwp_image_src = r["gwp_imgs"][0]["src"]
                print(f"      GWP 이미지 후보: {gwp_image_src} ({r['gwp_imgs'][0]['w']}x{r['gwp_imgs'][0]['h']})")
    finally:
        # CDP attach 한 driver 는 close 안 함 (Chrome 자체는 살려둠)
        try:
            driver.quit()
        except Exception:
            pass

    # GWP 처리
    if gwp_json_path.exists():
        print(f"  GWP 구성 로드: {gwp_json_path}")
        gwp = load_gwp_config(gwp_json_path)
        print(f"      1세트 = {gwp.set_value:,}원, 6세트 = {gwp.set_value * 6:,}원")
        # ★ JSON 로드 성공 시 다운로드된 jpg 더 이상 불필요 → 정리 (결과파일 X)
        if gwp_image_path.exists():
            try:
                gwp_image_path.unlink()
                print(f"      gwp 이미지 정리: {gwp_image_path.name}")
            except Exception:
                pass
    else:
        if gwp_image_src:
            print(f"  GWP 이미지 다운로드 → {gwp_image_path}")
            try:
                download_gwp_image(gwp_image_src, gwp_image_path)
            except Exception as e:
                print(f"  ⚠️ 이미지 다운로드 실패: {e}")
        else:
            print(f"  ⚠️ GWP 이미지 fast-path 못 찾음 — 페이지 스크린샷 필요할 수 있음")
        return emit_gwp_pending(today, gwp_image_path)

    # --only 디버그 모드면 20조합 계산 스킵
    if args.only:
        print(f"\n  --only {args.only} → 20조합 계산/시트 입력 생략")
        return 0

    # 20개 조합 계산 (조합번호 1~20 순)
    rows_list = [C.galleria_combo(i + 1, c, products, gwp) for i, c in enumerate(C.COMBOS)]

    # 표 출력
    print()
    print(f"  {'idx':>3} {'조합':40s} {'소비자가':>10s} {'추증':>8s} {'GWP':>8s} {'총샘플':>8s} {'최종':>10s} {'순':>10s} {'공급률':>7s}")
    for r in rows_list:
        print(f"  {r['idx']:>3d} {r['name'][:40]:40s} {r['소비자가']:>10,d} {r['추가증정']:>8,d} {r['gwp_value']:>8,d} {r['총샘플']:>8,d} {r['최종구매가']:>10,d} {r['순구매가']:>10,d} {r['공급률']:>6.4f}")

    # ★ today_composition.json 등 로컬 캐시 파일 생성 X — sheet가 SoT.
    # hmall/lotte/inventory는 _common.load_galleria_composition_from_sheet(ws) /
    # load_galleria_samples_from_sheet(ws) 로 직접 sheet에서 읽는다.

    # 시트 입력
    if args.skip_sheet:
        print("  --skip-sheet 지정됨 → gspread 입력 생략")
        return 0

    print(f"  gspread 입력 → 탭 {tab!r}")
    gc = C.gs_client()
    sh = gc.open_by_key(C.RATE_SHEET_ID)
    ws = C.get_or_create_tab(sh, tab, leftmost=True)
    # 갤러리아 영역(행 1~C.GALLERIA_DATA_END_ROW)만 비우고 쓰기.
    # Hmall(C.HMALL_HEADER_ROW~), 롯데(C.LOTTE_HEADER_ROW~)는 보존.
    # ★ 시트 layout은 RULES.md §13 + _common.py 상수 참고.
    ws.batch_clear([f"A1:I{C.GALLERIA_DATA_END_ROW}"])
    rng = write_galleria_section(ws, products, gwp, rows_list, tab)
    print(f"  → {rng}")
    print(f"  URL: https://docs.google.com/spreadsheets/d/{C.RATE_SHEET_ID}/edit#gid={ws.id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
