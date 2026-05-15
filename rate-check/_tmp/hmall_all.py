"""Hmall stage — 11개 조합 × 카드별 결제페이지 진입해서 실제 즉시할인 금액 읽기.

신규 흐름 (구버전 '정가×0.9 수학 계산' 폐기):
  1. 임의 상품 1회 방문 → 카드 즉시할인 리스트 수집
  2. 11조합 반복:
     a. 장바구니 진입 → 조합 상품만 체크 + 수량 조정
     b. 구매하기 클릭 → 결제 페이지 진입
     c. 결제 페이지 카드 캐러셀 sections 파싱 → 카드별 즉시할인 금액 읽기
        (장바구니 단계엔 안 보이는 히든 추가쿠폰 반영)
     d. 페이백 카드면 페이백계수 추가 적용
  3. gspread 시트 입력

당일 추가증정가치/GWP는 sheet에서 직접 읽는다 (_common.load_galleria_composition_from_sheet).
**캐시 파일 절대 사용 X** — sheet가 SoT, 매 실행 fresh.
"""
import json, os, sys, time, re
from pathlib import Path
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
import gspread

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _common import combo_label_ko, load_galleria_composition_from_sheet, RATE_SHEET_ID, today_tab_name, gs_client

_CDP_PORT = os.environ.get("HMALL_CDP_PORT", "9222")
opts = Options()
opts.add_experimental_option("debuggerAddress", f"127.0.0.1:{_CDP_PORT}")
driver = webdriver.Chrome(options=opts)
print(f"CDP attach: 127.0.0.1:{_CDP_PORT}")

# 7상품 정가 — 본품 정가는 안정적
PRODUCTS = [
    ('b', '윤조3종',          '2228731835', 229_000),
    ('c', '자음2종',          '2228722509', 150_000),
    ('d', '본윤2종',          '2141738785', 125_000),
    ('e', '탄력3종',          '2228731377', 215_000),
    ('f', '윤조에센스90',     '2140423337', 140_000),
    ('g', '자음생2종',        '2215366254', 225_000),
    ('h', '자음생크림리치',   '2215367358', 270_000),
]
PRICE = {p[0]: p[3] for p in PRODUCTS}
SLITM = {p[0]: p[2] for p in PRODUCTS}
NAME = {p[0]: p[1] for p in PRODUCTS}

# 11개 조합 — 본품 구성 (고정)
COMBOS = [
    [('g',2),('h',1)], [('d',2),('g',2)], [('d',4),('e',1)], [('e',2),('h',1)],
    [('b',2),('d',2)], [('e',2),('f',2)], [('c',3),('h',1)], [('c',3),('d',2)],
    [('c',1),('f',4)], [('c',2),('f',3)], [('f',5)],
]

# === 당일 추가증정가치/GWP — sheet에서 직접 읽기 (캐시 X) ===
_gc = gs_client()
_sh = _gc.open_by_key(RATE_SHEET_ID)
_ws_read = _sh.worksheet(today_tab_name())
_comp = load_galleria_composition_from_sheet(_ws_read)
ADD_GIFT_VALUE = _comp["add_gift_value"]
GWP_6SET = _comp["gwp_6set"]
print(f"당일 추증가치 (sheet): {ADD_GIFT_VALUE}")
print(f"당일 GWP 6세트 (sheet): {GWP_6SET:,}원")

PAYBACK = {
    '롯데카드': 0.02, '비씨카드': 0.015, '삼성카드': 0.01,
    '하나카드': 0.01, '농협카드': 0.01,
}

# ==========================================================
# 1. 카드 즉시할인 리스트 (1회 수집) — 임의 상품 1개에서
# ==========================================================
def scrape_card_info():
    """첫 상품 페이지에서 적용 가능한 카드 즉시할인 목록 가져온다."""
    driver.get(f"https://www.hmall.com/md/pda/itemPtc?slitmCd={SLITM['b']}")
    time.sleep(2)
    js = """
    const cards = document.querySelectorAll('#crdImdDlTagTmp .crdImdDlTagTmp_1 .dc-card');
    return Array.from(cards).map(c => c.textContent.trim().replace(/\\s+/g, ''));
    """
    raw = driver.execute_script(js) or []
    cards = []
    for c in raw:
        m = re.match(r"(.+?카드|.*페이)\s*(\d+)\s*%", c)
        if m:
            nm = m.group(1)
            pct = int(m.group(2))
            cards.append({'name': nm, 'pct': pct, 'payback': PAYBACK.get(nm, 0)})
    return cards

cards_seen = scrape_card_info()
print(f"카드 즉시할인 후보: {cards_seen}")
if not cards_seen:
    print("⚠️ 카드 정보 못 가져옴 — 페이지 DOM/로그인 상태 확인 필요")
    sys.exit(1)

# ==========================================================
# 2. 장바구니 → 조합 체크 → 구매하기 → 결제 페이지에서 카드별 즉시할인 금액 읽기
# ==========================================================

def goto_cart():
    driver.get("https://www.hmall.com/mo/odb/basktList")
    time.sleep(2.5)

def cart_uncheck_all():
    """장바구니의 모든 상품 체크 해제."""
    js = """
    const cbs = document.querySelectorAll('input[name="backet"]');
    let unchecked = 0;
    cbs.forEach(cb => {
      if (cb.checked) { cb.click(); unchecked++; }
    });
    return unchecked;
    """
    driver.execute_script(js)
    time.sleep(0.5)

def cart_inspect():
    """장바구니 전체 아이템 메타 (name, qty, checked) 조회 — 매번 호출 (인덱스 변동 대응).

    실제 cart DOM (5/15 확정): 컨테이너 = `div.pdwrap.pdlist`, 상품명은 innerText 첫 라인.
    qty: "N개" 패턴 (가격 "X원"이 아님)
    """
    js = """
    const cbs = document.querySelectorAll('input[name="backet"]');
    return Array.from(cbs).map((cb, idx) => {
      // 컨테이너 — div.pdwrap (5/15 확정) 또는 옵션변경 텍스트 있는 상위
      let item = cb.closest('div.pdwrap, div.pdlist, li');
      if (!item) {
        let p = cb.parentElement;
        for (let i = 0; i < 8 && p; i++) {
          const t = p.innerText || '';
          if (t.indexOf('옵션변경') >= 0) { item = p; break; }
          p = p.parentElement;
        }
      }
      if (!item) item = cb.parentElement;

      // 상품명 — innerText 첫 라인 (보통 "[본사직영]설화수 [공통]XXX 세트")
      const lines = (item.innerText || '').split('\\n').map(s => s.trim()).filter(s => s);
      let name = lines[0] || '';
      // "[본사직영]" "[공통]" 등 brackets 제거 — 순수 상품명만
      name = name.replace(/^\\[[^\\]]+\\]\\s*/g, '').replace(/^설화수\\s*/, '').trim();

      // qty — "N개" 패턴 매칭 (가격 "X원" 제외)
      let qty = 1;
      for (const line of lines) {
        const m = line.match(/^(\\d+)개$/);
        if (m) { qty = parseInt(m[1]); break; }
      }
      return { idx, name, raw_first_line: lines[0] || '', qty, checked: cb.checked };
    });
    """
    return driver.execute_script(js) or []


# ★ Hmall cart 텍스트는 풀네임 — NAME 닉네임 substring 매칭 안 됨.
# 5/15 진단 확정: cart의 실제 상품명 첫 줄 키워드로 매핑
CART_KEYWORDS = {
    'b': '에센셜 퍼스트',         # 윤조3종 = 에센셜 퍼스트케어 세트
    'c': '에센셜 데일리',         # 자음2종 = 에센셜 데일리 세트
    'd': '본윤 데일리',           # 본윤2종 = 본윤 데일리 루틴 세트
    'e': '에센셜 탄력',           # 탄력3종 = 에센셜 탄력케어 세트
    'f': '윤조에센스 6세대',       # 윤조에센스90 = 윤조에센스 6세대 90ml
    'g': '자음생 2종',            # 자음생2종 = 자음생 2종 세트
    'h': '자음생크림 리치',        # 자음생크림리치세트
}

def cart_check_one(item_idx: int):
    """특정 인덱스의 체크박스 체크."""
    js = """
    const cbs = document.querySelectorAll('input[name="backet"]');
    if (cbs[arguments[0]] && !cbs[arguments[0]].checked) {
      cbs[arguments[0]].click();
      return true;
    }
    return false;
    """
    driver.execute_script(js, item_idx)
    time.sleep(0.2)

def cart_change_qty_via_popup(item_idx: int, target_qty: int, current_qty: int):
    """item_idx 상품의 옵션변경 팝업 → +/- N회 → 변경하기.

    DOM (사용자 제공):
    - 옵션변경 버튼: <button>옵션변경</button> (cart item 안)
    - 팝업 + 버튼: <button class="_1qe59fg4">증가</button>
    - 팝업 - 버튼: <button class="_1qe59fg3">감소</button>
    - 변경하기 버튼: <button class="change-btn">변경하기</button>
    ⚠️ 변경하기 클릭 후 cart가 다시 로드되며 인덱스/체크박스 리셋됨 → 호출 후 재 inspect 필수.
    """
    diff = target_qty - current_qty
    if diff == 0:
        return

    # 1. 옵션변경 클릭 (해당 cart item 안의 button)
    js_open = """
    const cbs = document.querySelectorAll('input[name="backet"]');
    const cb = cbs[arguments[0]];
    if (!cb) return false;
    let item = cb.closest('li') || cb.parentElement;
    for (let lvl = 0; lvl < 6 && item; lvl++) {
      const btns = item.querySelectorAll('button');
      for (const b of btns) {
        if (b.textContent.trim() === '옵션변경') { b.click(); return true; }
      }
      item = item.parentElement;
    }
    return false;
    """
    if not driver.execute_script(js_open, item_idx):
        print(f"    ⚠️ 옵션변경 버튼 못찾음 (idx={item_idx})")
        return
    time.sleep(1.5)  # popup 로딩 대기

    # 2. + 또는 - 버튼 N회 클릭
    label = "증가" if diff > 0 else "감소"
    n = abs(diff)
    js_step = """
    // 텍스트 매칭 (해시 클래스 _1qe59fg4 등은 변동 가능 → 텍스트로 안전)
    const btns = document.querySelectorAll('button');
    const tgt = Array.from(btns).find(b => b.textContent.trim() === arguments[0]);
    if (!tgt) return false;
    tgt.click();
    return true;
    """
    for _ in range(n):
        if not driver.execute_script(js_step, label):
            print(f"    ⚠️ {label} 버튼 못찾음 (popup)")
            break
        time.sleep(0.2)

    # 3. 변경하기 클릭 (class="change-btn" 우선, 없으면 텍스트 매칭)
    js_submit = """
    let btn = document.querySelector('button.change-btn');
    if (!btn) {
      btn = Array.from(document.querySelectorAll('button')).find(b => b.textContent.trim() === '변경하기');
    }
    if (btn) { btn.click(); return true; }
    return false;
    """
    driver.execute_script(js_submit)
    time.sleep(1.8)  # cart 리로드 대기

def cart_check_combo(combo):
    """조합 상품만 체크 + 수량 조정. combo = [(code, qty), ...].

    흐름:
    1. (반복) inspect → 수량 다른 첫 매칭 상품 발견 시 옵션변경 팝업 → 일치할 때까지 반복
    2. 모두 체크 해제 → inspect (인덱스 변동 대응) → 매칭 상품만 체크
    """
    # ★ NAME (윤조3종 등) 닉네임 X — CART_KEYWORDS (cart 풀네임 substring) 사용
    targets = {CART_KEYWORDS[c]: q for c, q in combo}

    # 1단계: 수량 조정 — 매번 inspect (변경하기 후 인덱스 리셋되므로)
    for _ in range(15):
        items = cart_inspect()
        to_change = None
        for item in items:
            for tname, tqty in targets.items():
                if tname in item['name'] and item['qty'] != tqty:
                    to_change = (item, tqty)
                    break
            if to_change:
                break
        if not to_change:
            break  # 모두 일치
        item, tqty = to_change
        print(f"    qty 조정: '{item['name'][:30]}' {item['qty']}→{tqty}")
        cart_change_qty_via_popup(item['idx'], tqty, item['qty'])

    # 2단계: 모두 체크 해제 → 매칭 상품만 체크 (재 inspect 필수)
    cart_uncheck_all()
    items = cart_inspect()
    matched = []
    for item in items:
        for tname in targets:
            if tname in item['name']:
                cart_check_one(item['idx'])
                matched.append(f"{item['name'][:25]} x{item['qty']}")
                break
    print(f"  cart 매칭/체크: {matched}")

def click_buy():
    """구매하기 버튼 (텍스트 '구매하기'를 포함한 button)."""
    js = """
    const btns = document.querySelectorAll('button');
    for (const b of btns) {
      const p = b.querySelector('p');
      const txt = p ? p.textContent.trim() : b.textContent.trim();
      if (txt === '구매하기') { b.click(); return true; }
    }
    return false;
    """
    return driver.execute_script(js)

def read_checkout_card_prices():
    """결제 페이지 카드 캐러셀 → 카드별 즉시할인 후 금액(int) 매핑.

    codegen 5/15 확정: 카드 클릭 셀렉터는 div with exact text "{카드명}{N}%즉시할인"
    (예: "현대5%즉시할인"). 클릭 후 선택된 영역에 최종할인가 표시됨.

    흐름:
    1. 카드 div 후보 모두 수집 (텍스트 패턴 매칭)
    2. 각 카드 div 클릭 → wait → 선택 영역 가격 읽기
    3. {카드풀네임: 최종금액} 반환
    """
    # 1. 카드 div 후보 수집
    js_collect = """
    const out = [];
    const divs = document.querySelectorAll('div');
    for (const d of divs) {
      const text = (d.textContent || '').trim();
      // exact: "{name}{N}%즉시할인"  e.g. "현대5%즉시할인"
      const m = text.match(/^(.+?)(\\d+)%즉시할인$/);
      if (m && d.children.length < 5) {  // direct text holder (depth 얕음)
        out.push({ name: m[1], pct: parseInt(m[2]), text: text });
      }
    }
    // 텍스트 중복 제거
    const seen = new Set();
    return out.filter(o => { if (seen.has(o.text)) return false; seen.add(o.text); return true; });
    """
    cards = driver.execute_script(js_collect) or []
    print(f"  결제 페이지 카드 후보 ({len(cards)}): {[c['text'] for c in cards]}")

    result = {}
    for card in cards:
        # 2. 해당 카드 클릭
        js_click = """
        const target = arguments[0];
        const divs = document.querySelectorAll('div');
        for (const d of divs) {
          if ((d.textContent || '').trim() === target && d.children.length < 5) {
            d.click(); return true;
          }
        }
        return false;
        """
        clicked = driver.execute_script(js_click, card['text'])
        if not clicked:
            continue
        time.sleep(0.6)  # 가격 영역 업데이트 대기

        # 3. 선택된 영역에서 최종할인가 읽기 (카드 section 안의 strong / 또는 총결제 영역)
        # codegen에서 본 형태: <p><strong>402,344</strong>원</p>
        # 우선 카드 section의 strong에서 읽고, 없으면 페이지 상의 큰 가격 텍스트
        js_read = """
        // 1순위: 클릭된 카드 주변 section 의 p > strong
        const target = arguments[0];
        const divs = document.querySelectorAll('div');
        for (const d of divs) {
          if ((d.textContent || '').trim() === target && d.children.length < 5) {
            // 가장 가까운 section 또는 부모로 올라가 가격 strong 찾기
            let scope = d.closest('section') || d.parentElement;
            for (let lvl = 0; lvl < 5 && scope; lvl++) {
              const strongs = scope.querySelectorAll('p strong, strong');
              for (const s of strongs) {
                const t = (s.textContent || '').trim();
                const m = t.match(/^([\\d,]{4,})$/);  // 4자리 이상 숫자만 (가격)
                if (m) return parseInt(m[1].replace(/,/g, ''));
              }
              scope = scope.parentElement;
            }
          }
        }
        // 2순위: 페이지 상의 "총 결제금액" 또는 "최종 결제" 영역
        const allText = document.body.innerText || '';
        const m2 = allText.match(/(?:총 ?결제|최종 ?결제|결제 ?금액)[^\\d]*?([\\d,]{5,})\\s*원/);
        return m2 ? parseInt(m2[1].replace(/,/g, '')) : null;
        """
        price = driver.execute_script(js_read, card['text'])
        if price:
            full = card['name'] + '카드' if not card['name'].endswith('카드') and not card['name'].endswith('페이') else card['name']
            result[full] = int(price)
            print(f"    ✓ {full} {card['pct']}% → {price:,}원")
        else:
            print(f"    ✗ {card['text']} 가격 못읽음")

    return result

def measure_combo(combo):
    """조합 1개 측정. {카드명: 즉시할인후 금액} 반환."""
    goto_cart()
    cart_check_combo(combo)
    if not click_buy():
        print("  ✗ 구매하기 버튼 못찾음")
        return {}
    time.sleep(3.5)  # 결제 페이지 로딩 대기
    return read_checkout_card_prices()

# ==========================================================
# 3. 11조합 전부 측정
# ==========================================================
results = []
for idx, combo in enumerate(COMBOS, start=1):
    cn = combo_label_ko(combo)
    print(f"\n[조합 {idx}] {cn}")
    소비자가 = sum(PRICE[c]*q for c,q in combo)
    추증 = sum(ADD_GIFT_VALUE[c]*q for c,q in combo)
    총샘플 = 추증 + GWP_6SET

    card_prices = measure_combo(combo)
    print(f"  카드별 즉시할인후: {card_prices}")
    card_rows = []
    for card in cards_seen:
        if card['name'] not in card_prices:
            continue
        immediate = card_prices[card['name']]
        # 페이백 추가 적용 (즉시할인 금액 × 페이백계수)
        final = round(immediate * (1 - card['payback']))
        net = final - 총샘플
        rate = net / 소비자가 if 소비자가 else 0
        card_rows.append({
            'card': card['name'], 'pct': card['pct'], 'payback': card['payback'],
            'immediate': immediate, 'final': final, 'net': net, 'rate': rate,
        })
    results.append({
        'idx': idx, 'combo': combo, '소비자가': 소비자가,
        '추증': 추증, '총샘플': 총샘플, 'cards': card_rows,
    })

# ==========================================================
# 4. 결과 출력
# ==========================================================
print(f"\n{'idx':3s} {'조합':22s} {'소비자가':>9s} {'총샘플':>8s} {'카드후':>9s} {'순구매가':>9s} {'공급률':>7s}")
for r in results:
    cn = combo_label_ko(r['combo'])
    c0 = r['cards'][0] if r['cards'] else {'final':0, 'net':0, 'rate':0}
    print(f"{r['idx']:3d} {cn:22s} {r['소비자가']:>9,d} {r['총샘플']:>8,d} {c0['final']:>9,d} {c0['net']:>9,d} {c0['rate']:>6.4f}")

# ==========================================================
# 5. gspread 이어쓰기
# ==========================================================
gc = gspread.service_account(filename='/Users/jasonkim/Desktop/Vibe Coding/3mall auto buy/gen-lang-client-0553550811-4b553902b0d0.json')
sh = gc.open_by_key('1fxB0UvLRy2iQfonCWn5U5mWnXbzSdn6l4e2XuQluhwo')
from datetime import datetime
ws = sh.worksheet(datetime.now().strftime('%-m.%-d'))

START = 44  # 사용자 layout: Galleria 1~41 다음 + 빈 2행 + Hmall 44~
data = []
data.append([f"━━━━ 2단계: 현대Hmall 공급률 분석 (결제페이지 실측) ━━━━"])
data.append([])
card_summary = ' / '.join(f"{c['name']} {c['pct']}%{'(페이백 ×'+str(round(1-c['payback'],3))+')' if c['payback'] else ''}" for c in cards_seen)
data.append([f"카드즉시할인: {card_summary}"])
data.append([f"※ 결제 페이지 캐러셀에서 카드별 실제 즉시할인 금액 읽음 (히든 추가쿠폰 반영)"])
data.append([])

# 조합 요약 (조합번호 1~11 순) — 우측 카드정보 블록
headers = ['조합번호', '조합', '소비자가', '추가증정', 'GWP가치', '총샘플가치']
for c in cards_seen:
    headers += [f"{c['name']}즉시할인후", f"{c['name']}최종(페이백)", f"{c['name']}순구매가", f"{c['name']}공급률"]
data.append(headers)
for r in results:
    cn = combo_label_ko(r['combo'])
    row = [r['idx'], cn, r['소비자가'], r['추증'], GWP_6SET, r['총샘플']]
    for cr in r['cards']:
        row += [cr['immediate'], cr['final'], cr['net'], round(cr['rate'], 4)]
    data.append(row)

maxcols = max(len(r) for r in data)
for r in data:
    while len(r) < maxcols:
        r.append('')
end_col = chr(ord('A') + maxcols - 1)
range_str = f"A{START}:{end_col}{START + len(data) - 1}"
ws.update(values=data, range_name=range_str, value_input_option='USER_ENTERED')
print(f"\nHmall 섹션 입력: {range_str}")
# ★ 로컬 결과파일(hmall_results.json) 저장 X — sheet가 SoT

# J~M 비교 차트 — Hmall 컬럼 (L) 만 채움. 첫 카드 공급률 사용.
chart_l = [["Hmall"]]
for r in results:
    c0_rate = r['cards'][0]['rate'] if r['cards'] else 0
    chart_l.append([round(c0_rate, 4)])
ws.update(values=chart_l, range_name="L1:L12", value_input_option='USER_ENTERED')
print("L1:L12 (Hmall 비교 차트 컬럼) 입력")
