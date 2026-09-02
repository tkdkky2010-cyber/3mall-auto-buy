"""롯데홈쇼핑 공급률 분석 — 7개 상품 쿠폰% + 카드할인 + 20개 조합 (active).

Step 1 substep #5 의 active script. 2026-05-17 _tmp/lotte_all.py 에서 승격 (rate-check/lotte.py).

흐름:
- 쿠폰%: 상품 페이지 쿠폰받기 클릭 → 팝업 안의 가장 위 (다운로드 가능 최대) 쿠폰만 읽음 (배너 "할인" X)
- 카드 청구할인: 상품 페이지 "청구할인" 텍스트 추출
- 적립금: store-wide 이벤트라 상품 1개(b)의 #eventBanner→이벤트페이지를 **같은 driver로 in-process** 조회.
          최고 tier(최대 임계↔최대 적립) 1개를 전 조합 공통 적용. (subprocess 방식=드라이버 2개 9222 충돌로 폐기)
- 추증/GWP: galleria가 sheet에 쓴 결과를 load_galleria_composition_from_sheet(ws)로 직접 읽음 (캐시 X)
- 적립금은 조합당 1회 적용 (sum × qty 잘못 — 결제 1회 = 이벤트 1회)
"""
import json, sys, time, re
from pathlib import Path
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
import gspread

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import (
    combo_label_ko, load_galleria_composition_from_sheet, RATE_SHEET_ID,
    today_tab_name, gs_client, COMBOS, CARD_PAYBACK, PRODUCT_CODES, PRODUCTS,
    LOTTE_HEADER_ROW, LOTTE_COMBO_END_ROW, CHART_RANGE,
    matched_chromedriver_service,
    id_candidates as C_id_candidates,
    use_content_tab,
)

import os as _os
_LOTTE_PORT = _os.environ.get("RATE_CHECK_CDP_PORT", "9222")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from chrome_launcher import resolve_cdp_port
_LOTTE_PORT = str(resolve_cdp_port(int(_LOTTE_PORT)))  # 9222 막히면 9223→9224 (같은 CFT)
opts = Options()
opts.add_experimental_option("debuggerAddress", f"127.0.0.1:{_LOTTE_PORT}")
_svc = matched_chromedriver_service(_LOTTE_PORT)  # 버전 mismatch 회피
driver = webdriver.Chrome(options=opts, service=_svc) if _svc else webdriver.Chrome(options=opts)
print(f"CDP attach: 127.0.0.1:{_LOTTE_PORT}")
# ★Gemini webview 를 잡으면 driver.get 이 안 먹어 전 상품이 빈 값으로 읽힌다 (2026-08-19 실사고)
use_content_tab(driver)
# ★캐시 비활성화 — 장기 실행 CFT 의 stale 페이지 방지 (갤러리아 쿠폰 사고 2026-07-16, 동일 리스크)
try:
    driver.execute_cdp_cmd("Network.setCacheDisabled", {"cacheDisabled": True})
except Exception as _e:
    print(f"  [WARN] 캐시 비활성화 실패 ({_e}) — stale 값 주의")

IDS = json.load(open(Path(__file__).resolve().parent.parent / "hsmaster" / "config" / "sulwhasoo-ids.json",
                     encoding="utf-8"))['ids']   # ★encoding 필수 — 윈도우 기본 cp949 로 한글에서 죽는다
PRICES = {c: p["price"] for c, p in PRODUCTS.items()}   # 하드코딩 금지 — 본품 추가 시 자동 반영

def block_dialogs():
    try:
        driver.execute_script("window.confirm=()=>true;window.alert=()=>{};window.prompt=()=>''")
    except: pass

# 7개 상품: 쿠폰 + 첫 상품에서 카드할인
coupons = {}
card_info = None

# 세션 워밍 — goods 딥링크로 직행하면 WAF가 403(콜드 세션) 반환.
# 홈을 먼저 찍어 쿠키/세션을 확보해야 goods 페이지가 200으로 열림.
driver.get("https://www.lotteimall.com/main/viewMain.lotte")
time.sleep(2)
block_dialogs()

def _sold_out() -> bool:
    """현재 열린 롯데 상품페이지가 **일시품절**인지. (2026-08-19 실측 기반)

    ★왜 필요한가: 죽은 상품번호도 페이지가 열리고 **가격까지 멀쩡히 표시된다**
      (구 n 2923418727 이 13% 117,450원 표시). 리디렉트도 404도 아니라 감지 수단이 없어서
      step1 롯데가 판매불가 상품을 조용히 측정했다(2026-08-19 발견).
    ★신호 = 옵션(타입 선택) 영역의 **`재입고알림` 버튼**. 품절일 때만 나타난다.
      body 전체 검색 금지(RULES §1-2) — 버튼/링크 텍스트만 좁게 본다.
    ⚠️ 문구는 `판매중단` 이 아니라 **`일시품절`** 이다. 워크로그(8/18)엔 '판매중단'으로
      적혀 있었는데 실제 페이지 문구가 달라, 그 단어로 짰으면 못 잡았다.
    """
    try:
        return bool(driver.execute_script("""
            return Array.from(document.querySelectorAll('button, a'))
                        .some(e => /재입고알림|일시품절/.test((e.innerText||'').trim()));
        """))
    except Exception:
        return False


for code in PRODUCT_CODES:
    # ★2026-08-19: lotte 도 **후보 리스트** 가능. 품절이면 다음 후보로.
    _cands = C_id_candidates(IDS[code], "lotte")
    goods, url = None, None
    for _ci, _gno in enumerate(_cands):
        goods = _gno
        url = f"https://www.lotteimall.com/goods/viewGoodsDetail.lotte?goods_no={_gno}"
        print(f"[{code}] {url}")
        driver.get(url)
        time.sleep(3)
        block_dialogs()
        if not _sold_out():
            if _ci:
                print(f"  [WARN] {code} 1순위 {_cands[0]} 일시품절 → 후보 {_ci+1} {_gno} 사용. "
                      f"sulwhasoo-ids.json 순서 갱신 검토")
            break
        if _ci + 1 < len(_cands):
            print(f"  [WARN] {code} 후보 {_ci+1} {_gno} **일시품절** → 다음 후보 시도")
        else:
            print(f"  [WARN] {code} 후보 {len(_cands)}개 전부 일시품절 — 마지막 후보로 측정 진행(값 신뢰 주의)")
    # 스크롤 불필요 — 쿠폰받기는 아래 JS el.click() 로 스크롤 위치 무관하게 클릭됨. 바로 쿠폰 단계로.

    # 쿠폰받기 클릭 — 팝업/레이어 등장
    # ★ 사용자 5/15 지시: body 전체 max % 수집 X (배너 "N% 할인" 같은 행사 표시 포함됨).
    # 쿠폰받기 팝업 안의 **가장 위 (첫번째) 쿠폰** 만 본다 = 가장 높은 할인율의 다운로드 가능 쿠폰.
    js_click = """
    const all = Array.from(document.querySelectorAll('button, a, span, div'));
    let clicked = false;
    for (const el of all) {
      const t = el.textContent.trim();
      if (t === '쿠폰받기' || t === '쿠폰 받기') { try { el.click(); clicked = true; break; } catch(e){} }
    }
    return {clicked};
    """
    r = driver.execute_script(js_click)
    time.sleep(1.8)
    block_dialogs()

    # 쿠폰 팝업/레이어 안의 첫 쿠폰 %
    js_read_coupon = """
    // 1순위: 쿠폰 팝업 컨테이너 후보 (modal/layer/popup 클래스 또는 z-index 높은 것)
    const popupSelectors = [
      '[class*="coupon"][class*="popup"]', '[class*="coupon"][class*="modal"]',
      '[class*="coupon"][class*="layer"]', '[id*="coupon"][id*="popup"]',
      '[id*="coupon"][id*="layer"]', '.layer_pop', '.popup_layer', '.modal-coupon',
      // 일반 모달
      '[role="dialog"]', '[class*="modal"]:not([class*="hidden"])', '[class*="layer"]:not([style*="display:none"])'
    ];
    let popup = null;
    for (const sel of popupSelectors) {
      const els = document.querySelectorAll(sel);
      for (const el of els) {
        if (el.offsetParent === null) continue; // hidden
        const t = (el.innerText || '').trim();
        if (t.length > 10 && t.includes('쿠폰') && /\\d+\\s*%/.test(t)) {
          popup = el; break;
        }
      }
      if (popup) break;
    }

    // 팝업 못찾으면: 페이지 전체에서 "쿠폰" 단어 근처 % 만 추출 (배너 "할인" 텍스트 제외)
    let scope = popup ? (popup.innerText || '') : '';
    if (!scope) {
      const body = document.body.innerText || '';
      // "쿠폰" 단어 앞뒤 30자 내의 N% 만 수집
      const couponMatches = body.match(/[^\\n]{0,30}쿠폰[^\\n]{0,30}/g) || [];
      scope = couponMatches.join('\\n');
    }

    // 가장 위 (먼저 등장하는) % 추출 — max 아님, 첫 항목
    const lines = scope.split('\\n').map(s => s.trim()).filter(s => s);
    const couponLines = lines.filter(l => l.includes('쿠폰') && /\\d+\\s*%/.test(l));
    let firstPct = null;
    if (couponLines.length > 0) {
      const m = couponLines[0].match(/(\\d{1,2})\\s*%/);
      if (m) firstPct = parseInt(m[1]);
    }
    // fallback: scope 첫 % 패턴
    if (firstPct === null) {
      const m = scope.match(/(\\d{1,2})\\s*%/);
      if (m) firstPct = parseInt(m[1]);
    }

    return {
      popup_found: !!popup,
      coupon_lines: couponLines.slice(0, 5),
      first_pct: firstPct,
    };
    """
    rd = driver.execute_script(js_read_coupon)
    coupons[code] = rd.get('first_pct')
    print(f"  쿠폰: 첫 항목 {rd.get('first_pct')}% (popup={rd.get('popup_found')}, lines={rd.get('coupon_lines', [])[:3]})")

    # 첫 상품(b)에서만 카드 청구할인 확인
    if code == 'b' and card_info is None:
        js_card = """
        // "청구할인" 라인 + N% 추출. brand 는 % 가 잡힌 그 라인을 Python 이 stem 매칭.
        const body = document.body.innerText;
        const lines = body.split('\\n').filter(l => /청구할인/.test(l));
        const out = {lines: lines.slice(0,10)};
        for (const l of lines) {
          const m = l.match(/(\\d+)\\s*%\\s*청구할인/);
          if (m) { out.pct = parseInt(m[1]); out.cardline = l.trim(); break; }
        }
        return out;
        """
        cr = driver.execute_script(js_card)
        card_info = cr
        print(f"  카드 청구할인 정보: {cr}")

print(f"\n쿠폰: {coupons}")
print(f"카드: {card_info}")

# 적립 확인 — store-wide 이벤트(설화수 일반상품 전체 공통)라 상품 1개(b)만 조회 후 전 조합에 동일 적용.
# ★ subprocess(2번째 selenium 드라이버) 대신 이 스크립트의 driver 재사용 = 드라이버 1개 → 9222 윈도우 충돌 회피.
#   (subprocess 방식은 부모 driver 가 9222에 붙어있는 채로 자식이 2번째 driver 를 띄워 같은 윈도우를 두고 싸워 0개 오판.)
# GLOBAL_TIERS = [{'threshold': int, 'reward': int}, ...] 오름차순. compute() 가 결제금액 vs threshold 로 max 1회 적립 (RULES §7-3).
# ★ 결과파일 절대 사용 X — sheet가 SoT.
IGNORE_KW = [l.strip() for l in open(
    Path(__file__).resolve().parent.parent / "lotte_ignore_keywords.txt",
    encoding='utf-8') if l.strip()]

_COLLECT_JS = r"""
    const banner = document.querySelector('#eventBanner');
    if (!banner) return [];
    const out = [];
    for (const li of banner.querySelectorAll('li.swiper_slide, li.swiper-slide, li')) {
        const a = li.querySelector('a[data-url], a[href]');
        const img = li.querySelector('img[alt]');
        const strong = li.querySelector('strong');
        const p = li.querySelector('p');
        const text = li.textContent.replace(/\s+/g, ' ').trim();
        if (!text || text.length < 3) continue;
        out.push({text, alt: img?img.alt:'', title: strong?strong.textContent.trim():'',
                  subtitle: p?p.textContent.trim():'',
                  data_url: a?(a.getAttribute('data-url')||a.getAttribute('href')):null});
    }
    return out;
"""
_VERIFY_JS = r"""
    const body = document.body ? document.body.innerText : '';
    // 임계값/적립금 독립 matchAll 후 페어링 (lazy regex 누락 방지, RULES §7-2/§P8).
    const reThr = /([\d,]{3,})\s*원\s*이상/g, reRwd = /([\d,]{2,})\s*(?:원|P)\s*적립(?!금)/g, reRwdAlt = /적립금\s*([\d,]{2,})\s*원/g;
    const thr = [], rwd = []; let m;
    while ((m = reThr.exec(body)) !== null) { const v = parseInt(m[1].replace(/,/g,'')); if (v>=1000) thr.push(v); if (thr.length>=20) break; }
    while ((m = reRwd.exec(body)) !== null) { const v = parseInt(m[1].replace(/,/g,'')); if (v>=100) rwd.push(v); if (rwd.length>=20) break; }
    while ((m = reRwdAlt.exec(body)) !== null) { const v = parseInt(m[1].replace(/,/g,'')); if (v>=100 && !rwd.includes(v)) rwd.push(v); if (rwd.length>=20) break; }
    return {thr, rwd};
"""

def fetch_global_tiers():
    """상품 b 1개의 #eventBanner 에서 적립 이벤트 발견 → 이벤트 페이지 tier표 파싱. driver 재사용(단일)."""
    url0 = f"https://www.lotteimall.com/goods/viewGoodsDetail.lotte?goods_no={C_id_candidates(IDS['b'], 'lotte')[0]}"
    driver.get(url0); time.sleep(4); block_dialogs()
    for y in range(0, 10000, 700):
        driver.execute_script(f"window.scrollTo(0, {y})"); time.sleep(0.2)
    driver.execute_script("window.scrollTo(0, 0)"); time.sleep(0.6)
    items = driver.execute_script(_COLLECT_JS) or []
    print(f"  #eventBanner 슬라이드 {len(items)}개")
    cands = []
    for it in items:
        blob = f"{it.get('text','')} {it.get('alt','')} {it.get('subtitle','')} {it.get('title','')}"
        if '적립' not in blob:
            continue
        if any(kw in blob for kw in IGNORE_KW):
            continue
        if it.get('data_url'):
            cands.append(it)
    print(f"  적립 후보 {len(cands)}건 (ignore 제외 후)")
    for c in cands:
        du = c['data_url']
        ev_url = du if du.startswith('http') else 'https://www.lotteimall.com' + du
        driver.get(ev_url); time.sleep(3.5); block_dialogs()
        # ★ 이벤트 페이지 스크롤 필수 — 안 하면 tier표가 덜 렌더돼 body 순서가 어긋나 reward 페어링 뒤섞임.
        for y in range(0, 6000, 600):
            driver.execute_script(f"window.scrollTo(0, {y})"); time.sleep(0.2)
        driver.execute_script("window.scrollTo(0, 0)"); time.sleep(0.5)
        res = driver.execute_script(_VERIFY_JS) or {}
        thr = sorted({int(x) for x in (res.get('thr') or []) if int(x) > 0})
        rwd = sorted({int(x) for x in (res.get('rwd') or []) if int(x) > 0})
        if thr and rwd:
            # 임계값/적립금 독립 matchAll 의 순서 페어링은 본문 텍스트 순서가 어긋나 불안정.
            # store-wide 이벤트는 결제액↑ = 적립↑ 구조라 '최고 임계 ↔ 최고 적립'만 신뢰 = 최고 tier 1개.
            # 오늘 전 조합 결제액 > 최고 임계라 모든 조합에 최대 적립 적용 (사용자 지시 6/23, RULES §7-3 1회).
            top = {'threshold': max(thr), 'reward': max(rwd)}
            print(f"    ✓ {c.get('subtitle') or c.get('title')} → 최고 tier {top} (raw 임계 {thr}, 적립 {rwd})")
            return [top]
    return []

try:
    GLOBAL_TIERS = fetch_global_tiers()
except Exception as e:
    print(f"⚠️ 적립 조회 실패 ({e}) — GLOBAL_TIERS 빈 리스트")
    GLOBAL_TIERS = []
print(f"적립 tiers (store-wide, 전 조합 공통): {GLOBAL_TIERS}")

# 카드 결정 (단일 카드 가정). brand = "청구할인" 라인에서 stem 매칭.
# 롯데홈쇼핑 결제수단 카드: 현대 / 하나 / NHPay / KBPay / 롯데 / 삼성 / BC(ISP·페이북) (사용자 6/4).
# 페이백은 카탈로그 CARD_PAYBACK 단일소스 (없는 brand=0). 5%할인 등 pct 는 라인에서 추출.
LOTTE_CARD_STEMS = [
    ("현대카드", ("현대",)),
    ("하나카드", ("하나",)),
    ("농협카드", ("NH", "농협")),       # NHPay
    ("KB국민카드", ("KB", "국민")),     # KBPay
    ("롯데카드", ("롯데",)),
    ("삼성카드", ("삼성",)),
    ("비씨카드", ("BC", "비씨", "ISP", "페이북")),
]
_cardline = ((card_info or {}).get('cardline')
             or " ".join((card_info or {}).get('lines', []) or []))
CARD_NAME = '미확인'
for _name, _stems in LOTTE_CARD_STEMS:
    if any(s in _cardline for s in _stems):
        CARD_NAME = _name
        break
CARD_PCT = (card_info.get('pct') or 0) if card_info else 0
PAYBACK = CARD_PAYBACK.get(CARD_NAME, 0)

print(f"\n적용 카드: {CARD_NAME} {CARD_PCT}% (페이백 {PAYBACK*100:.1f}%)")

# 조합 = _common.COMBOS 20개 (DRY, 중복 정의 X)

# 당일 추가증정가치/GWP — sheet에서 직접 읽기 (캐시 X, sheet가 SoT)
_gc_lotte = gs_client()
_sh_lotte = _gc_lotte.open_by_key(RATE_SHEET_ID)
_ws_lotte = _sh_lotte.worksheet(today_tab_name())
_comp = load_galleria_composition_from_sheet(_ws_lotte)
ADD_GIFT = _comp["add_gift_value"]
GWP_70 = _comp["gwp_70tier"]
print(f"당일 추증가치 (sheet): {ADD_GIFT}")
print(f"당일 GWP(70만↑ 4세트) (sheet): {GWP_70:,}원")

def compute(combo):
    소비자 = sum(PRICES[c]*q for c,q in combo)
    추증 = sum(ADD_GIFT[c]*q for c,q in combo)
    총샘플 = 추증 + GWP_70
    # 1) 결제금액 (적립 적용 전) 먼저 계산
    final = 0
    for c,q in combo:
        cp = coupons.get(c) or 0
        item_final = PRICES[c] * q * 0.9 * (1 - cp/100) * (1 - CARD_PCT/100) * (1 - PAYBACK)
        final += item_final
    final = round(final)
    # 2) ★ 적립금: 조합당 1회 적용 (RULES §7-3). 결제 1회 = 이벤트 1회 발생.
    # store-wide 이벤트라 GLOBAL_TIERS 1개를 모든 조합에 공통 적용 — final >= threshold 인 tier 중 max reward.
    적립 = max([t['reward'] for t in GLOBAL_TIERS if final >= t['threshold']], default=0)
    순 = final - 총샘플 - 적립
    rate = 순 / 소비자
    return {'소비자가':소비자,'추증':추증,'총샘플':총샘플,'적립':적립,'최종':final,'순':순,'공급률':rate}

rows = []
for i, combo in enumerate(COMBOS, start=1):
    r = compute(combo)
    r['idx'] = i; r['combo'] = combo
    rows.append(r)

print(f"\n{'idx':3s} {'조합':22s} {'소비자':>8s} {'쿠폰합':>7s} {'카드후':>8s} {'적립':>5s} {'순':>8s} {'공급률':>7s}")
for r in rows:
    cn = combo_label_ko(r['combo'])
    # rough coupon avg display
    print(f"{r['idx']:3d} {cn:22s} {r['소비자가']:>8,d} {'':>7s} {r['최종']:>8,d} {r['적립']:>5,d} {r['순']:>8,d} {r['공급률']:>6.4f}")

# === gspread 이어쓰기: Lotte 영역 (RULES.md §13 layout) ===
gc = gs_client()
sh = gc.open_by_key(RATE_SHEET_ID)
ws = sh.worksheet(today_tab_name())

START = LOTTE_HEADER_ROW  # Lotte 헤더 시작행 (_common.py 정의)
data = []
data.append(["━━━━ 3단계: 롯데홈쇼핑 공급률 분석 ━━━━"])
data.append([])
coupon_str = ", ".join(f"{c}={coupons[c]}%" for c in PRODUCT_CODES)
data.append([f"상품별 쿠폰: {coupon_str}"])
data.append([f"카드 청구할인: {CARD_NAME} {CARD_PCT}% (페이백 {round(PAYBACK*100,1)}%)"])
data.append([f"적립금 tiers (전 조합 공통): {GLOBAL_TIERS}"])
data.append([])
data.append(['조합번호','조합','소비자가','추증','GWP','총샘플','적립','최종구매가','순구매가','공급률',
             '', '상품', '쿠폰%'])
for i, r in enumerate(rows):
    cn = combo_label_ko(r['combo'])
    row = [r['idx'], cn, r['소비자가'], r['추증'], GWP_70, r['총샘플'], r['적립'], r['최종'], r['순'], round(r['공급률'],4)]
    if i < len(PRODUCT_CODES):
        code = PRODUCT_CODES[i]
        cp = coupons.get(code) or 0
        row += ['', code, f"{cp}%"]
    data.append(row)

maxc = max(len(r) for r in data)
for r in data:
    while len(r) < maxc: r.append('')
end_col = chr(ord('A')+maxc-1)
rng = f"A{START}:{end_col}{START+len(data)-1}"
ws.update(values=data, range_name=rng, value_input_option='USER_ENTERED')
print(f"\n롯데 섹션 입력: {rng}")

# J~M 비교 차트 — Lotte 컬럼 (M) 채움
chart_m = [["롯데"]]
for r in rows:
    chart_m.append([round(r['공급률'], 4)])
chart_end = 1 + len(COMBOS)
ws.update(values=chart_m, range_name=f"M1:M{chart_end}", value_input_option='USER_ENTERED')
print(f"M1:M{chart_end} (롯데 비교 차트 컬럼) 입력")

# 조건부 서식: K2:M{N+1} 행별 최저값 셀 → 연두색 배경 (3사 중 가장 좋은 deal 강조)
# ★같은 탭에 step1 을 재실행하면 규칙이 계속 쌓인다(2026-08-07: '8.7' 에 동일 규칙 2개).
#   → 우리 수식과 같은 규칙을 먼저 지우고 추가한다. 색칠만 하고 지우지 않는 것과 같은 계열 결함.
_CF_FORMULA = "=AND(ISNUMBER(K2),K2=MIN($K2:$M2))"
try:
    _meta = sh.fetch_sheet_metadata({"includeGridData": False})
    _dels = []
    for _s in _meta["sheets"]:
        if _s["properties"]["sheetId"] != ws.id:
            continue
        for _i, _r in enumerate(_s.get("conditionalFormats", [])):
            _vals = _r.get("booleanRule", {}).get("condition", {}).get("values", [])
            if any(v.get("userEnteredValue") == _CF_FORMULA for v in _vals):
                _dels.append(_i)
    # 인덱스가 밀리지 않게 뒤에서부터 삭제
    for _i in sorted(_dels, reverse=True):
        sh.batch_update({"requests": [{
            "deleteConditionalFormatRule": {"sheetId": ws.id, "index": _i}}]})
    if _dels:
        print(f"기존 동일 조건부서식 {len(_dels)}개 제거")
except Exception as e:
    print(f"⚠️ 기존 조건부서식 조회/제거 실패(무시): {e}")

try:
    sh.batch_update({
        "requests": [{
            "addConditionalFormatRule": {
                "rule": {
                    "ranges": [{
                        "sheetId": ws.id,
                        "startRowIndex": 1,    # 0-indexed row 2
                        "endRowIndex": 1 + len(COMBOS),  # exclusive — N조합
                        "startColumnIndex": 10,  # K (0-indexed)
                        "endColumnIndex": 13,    # N (exclusive)
                    }],
                    "booleanRule": {
                        "condition": {
                            "type": "CUSTOM_FORMULA",
                            "values": [{"userEnteredValue": _CF_FORMULA}],
                        },
                        "format": {
                            "backgroundColor": {"red": 0.72, "green": 0.92, "blue": 0.72},
                        },
                    },
                },
                "index": 0,
            }
        }]
    })
    print(f"조건부 서식 추가 — K2:M{1+len(COMBOS)} 행별 최저 공급률 셀 연두색")
except Exception as e:
    print(f"⚠️ 조건부 서식 실패: {e}")
