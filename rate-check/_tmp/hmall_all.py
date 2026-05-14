"""Hmall stage — 7개 상품 일괄 확인 + 11개 조합 가격 계산 + gspread "5.14" 탭 이어쓰기."""
import json, sys, time
from pathlib import Path
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
import gspread

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _common import combo_label_ko

opts = Options()
opts.add_experimental_option("debuggerAddress", "127.0.0.1:9222")
driver = webdriver.Chrome(options=opts)

PRODUCTS = [
    ('b', '윤조3종',          '2228731835', 229_000),
    ('c', '자음2종',          '2228722509', 150_000),
    ('d', '본윤2종',          '2141738785', 125_000),
    ('e', '탄력3종',          '2228731377', 215_000),
    ('f', '윤조에센스90',     '2140423337', 140_000),
    ('g', '자음생2종',        '2215366254', 225_000),
    ('h', '자음생크림리치',   '2215367358', 270_000),
]

# 7개 상품에서 정가 × 0.9 일관성 + 카드할인 정보 일괄 확인
results = {}
cards_collected = []
for code, name, slitm, price in PRODUCTS:
    driver.get(f"https://www.hmall.com/md/pda/itemPtc?slitmCd={slitm}")
    time.sleep(1.8)
    js = """
    const out = {};
    // 정가
    const list = document.querySelectorAll('[class*="price"]');
    out.all_texts = Array.from(list).slice(0,10).map(e => e.textContent.trim().replace(/\\s+/g,' ').substring(0,80));
    // 카드 즉시할인 DOM
    const cards = document.querySelectorAll('#crdImdDlTagTmp .crdImdDlTagTmp_1 .dc-card');
    out.cards = Array.from(cards).map(c => c.textContent.trim().replace(/\\s+/g,' '));
    // 카드할인 후 가격
    const after = document.querySelectorAll('#crdImdDlTagTmp .crdImdDlTagTmp_1 .dc-price');
    out.cards_after_price = Array.from(after).map(a => a.textContent.trim().replace(/\\s+/g,''));
    return out;
    """
    r = driver.execute_script(js)
    # 정가 × 0.9 확인: 정가 + "10%" + 할인후가 텍스트 찾기
    expected_after_basic = round(price * 0.9)
    found = False
    for t in r.get('all_texts', []):
        if f"{expected_after_basic:,}원" in t:
            found = True; break
    r['basic_discount_10pct_verified'] = found
    r['expected_after_basic'] = expected_after_basic
    results[code] = r
    cards_collected.extend(r['cards'])
    print(f"[{code}] {name} | 정가 {price:,} → 10%할인 {expected_after_basic:,}: {'✓' if found else '✗'} | cards: {r['cards']}")

# 카드 정보 통합 (중복 제거, 첫 출현 순서)
unique_cards = []
for c in cards_collected:
    if c and c not in unique_cards:
        unique_cards.append(c)
print(f"\n전체 카드(unique): {unique_cards}")

# 카드 파싱: "삼성카드5%" → ("삼성", 5)
import re
parsed_cards = []
for c in unique_cards:
    m = re.match(r"(.+?카드|.*페이)\s*(\d+)\s*%", c.replace(' ', ''))
    if m:
        nm = m.group(1)
        pct = int(m.group(2))
        # 페이백 대상 매핑
        payback = {
            '롯데카드': 0.02, '비씨카드': 0.015, '삼성카드': 0.01,
            '하나카드': 0.01, '농협카드': 0.01,
        }.get(nm, 0)
        parsed_cards.append({'name': nm, 'pct': pct, 'payback': payback})
print(f"파싱된 카드: {parsed_cards}")

# === 11개 조합 ===
COMBOS = [
    [('g',2),('h',1)],
    [('d',2),('g',2)],
    [('d',4),('e',1)],
    [('e',2),('h',1)],
    [('b',2),('d',2)],
    [('e',2),('f',2)],
    [('c',3),('h',1)],
    [('c',3),('d',2)],
    [('c',1),('f',4)],
    [('c',2),('f',3)],
    [('f',5)],
]
PRICE = {p[0]: p[3] for p in PRODUCTS}

# 갤러리아 stage에서 계산한 총샘플가치 (재사용)
ADD_GIFT_VALUE = {
    'b': 16_700, 'c': 8_300, 'd': 15_100, 'e': 16_700,
    'f': 15_800, 'g': 18_800, 'h': 22_500,
}
GWP_6SET = 82_800

def combo_data(idx, combo):
    소비자가 = sum(PRICE[c]*q for c, q in combo)
    추증 = sum(ADD_GIFT_VALUE[c]*q for c, q in combo)
    총샘플 = 추증 + GWP_6SET
    # Hmall 할인전금액 = 정가 × 0.9 × 수량 (가이드: 장바구니에서 표시되는 값)
    할인전 = sum(PRICE[c] * 0.9 * q for c, q in combo)
    # 카드별 최종가
    card_results = []
    for card in parsed_cards:
        final = 할인전 * (1 - card['pct']/100) * (1 - card['payback'])
        final = round(final)
        net = final - 총샘플
        rate = net / 소비자가
        card_results.append({'card': card['name'], 'pct': card['pct'], 'payback': card['payback'],
                             'final': final, 'net': net, 'rate': rate})
    return {
        'idx': idx, 'combo': combo,
        '소비자가': 소비자가, '추증': 추증, '총샘플': 총샘플, '할인전': round(할인전),
        'cards': card_results,
    }

rows = [combo_data(i+1, c) for i, c in enumerate(COMBOS)]
# 순위는 첫 번째 카드 기준
if rows[0]['cards']:
    rows_sorted = sorted(rows, key=lambda r: r['cards'][0]['rate'])
else:
    rows_sorted = rows
for rk, r in enumerate(rows_sorted, start=1):
    r['rank'] = rk

# 결과 출력
print(f"\n{'idx':3s} {'조합 본품':22s} {'소비자가':>9s} {'할인전':>9s} {'카드후':>9s} {'순구매가':>9s} {'공급률':>7s} {'순위':>3s}")
for r in rows:
    cn = combo_label_ko(r['combo'])
    c0 = r['cards'][0] if r['cards'] else {'final':0, 'net':0, 'rate':0}
    print(f"{r['idx']:3d} {cn:22s} {r['소비자가']:>9,d} {r['할인전']:>9,d} {c0['final']:>9,d} {c0['net']:>9,d} {c0['rate']:>6.4f} {r['rank']:>3d}")

# === gspread 이어쓰기: Hmall 섹션을 "5.14" 탭 행 65~ 부터 ===
gc = gspread.service_account(filename='/Users/jasonkim/Desktop/Vibe Coding/3mall auto buy/gen-lang-client-0553550811-4b553902b0d0.json')
sh = gc.open_by_key('1fxB0UvLRy2iQfonCWn5U5mWnXbzSdn6l4e2XuQluhwo')
ws = sh.worksheet('5.14')

# Hmall 섹션 시작 행
START = 65
data = []
data.append([f"━━━━ 2단계: 현대Hmall 공급률 분석 ━━━━"])
data.append([])
card_summary = ' / '.join(f"{c['name']} {c['pct']}%{'(페이백 ×'+str(round(1-c['payback'],3))+')' if c['payback'] else ''}" for c in parsed_cards)
data.append([f"카드즉시할인: {card_summary}"])
data.append([f"Hmall 기본할인 10% (정가 × 0.9)"])
data.append([])

# 순위표
headers = ['순위', '조합', '소비자가', '추가증정', 'GWP가치', '총샘플가치', 'Hmall할인전']
for c in parsed_cards:
    headers += [f"{c['name']}최종", f"{c['name']}순구매가", f"{c['name']}공급률"]
data.append(headers)
for r in rows_sorted:
    cn = combo_label_ko(r['combo'])
    row = [r['rank'], cn, r['소비자가'], r['추증'], GWP_6SET, r['총샘플'], r['할인전']]
    for cr in r['cards']:
        row += [cr['final'], cr['net'], round(cr['rate'], 4)]
    data.append(row)

# 비어있는 행 padding
maxcols = max(len(r) for r in data)
for r in data:
    while len(r) < maxcols:
        r.append('')
end_col = chr(ord('A') + maxcols - 1)
range_str = f"A{START}:{end_col}{START + len(data) - 1}"
ws.update(values=data, range_name=range_str, value_input_option='USER_ENTERED')
print(f"\nHmall 섹션 입력: {range_str}")

with open('/Users/jasonkim/Desktop/Vibe Coding/3mall auto buy/rate-check/_tmp/hmall_results.json', 'w') as f:
    json.dump({'cards': parsed_cards, 'rows': rows_sorted}, f, ensure_ascii=False, indent=2, default=str)
