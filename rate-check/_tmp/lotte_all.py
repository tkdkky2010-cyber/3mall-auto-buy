"""롯데홈쇼핑 stage — 7개 상품 쿠폰% + 카드할인 + 11개 조합 공급률 계산 + 시트 이어쓰기.

적립 데이터는 _check_lotte_reward.py 출력 _lotte_reward_dump.json 사용 (별도 실행).
이 스크립트는 쿠폰%/카드만 자동, 적립은 0으로 초기화 후 사용자 확인.
"""
import json, sys, time, re
from pathlib import Path
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
import gspread

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _common import combo_label_ko

opts = Options()
opts.add_experimental_option("debuggerAddress", "127.0.0.1:9222")
driver = webdriver.Chrome(options=opts)

IDS = json.load(open('/Users/jasonkim/Desktop/Vibe Coding/3mall auto buy/hsmaster/config/sulwhasoo-ids.json'))['ids']
PRICES = {'b':229000,'c':150000,'d':125000,'e':215000,'f':140000,'g':225000,'h':270000}

def block_dialogs():
    try:
        driver.execute_script("window.confirm=()=>true;window.alert=()=>{};window.prompt=()=>''")
    except: pass

# 7개 상품: 쿠폰 + 첫 상품에서 카드할인
coupons = {}
card_info = None

for code in 'bcdefgh':
    goods = IDS[code]['lotte']
    url = f"https://www.lotteimall.com/goods/viewGoodsDetail.lotte?goods_no={goods}"
    print(f"[{code}] {url}")
    driver.get(url)
    time.sleep(3)
    block_dialogs()
    # 스크롤
    for y in (0, 800, 1600, 2400, 3200, 0):
        driver.execute_script(f"window.scrollTo(0, {y})"); time.sleep(0.3)
    block_dialogs()

    # 쿠폰받기 클릭 → 가장 큰 쿠폰% 확인
    js_click_and_read = """
    // 쿠폰받기 버튼 클릭
    const all = Array.from(document.querySelectorAll('button, a, span, div'));
    let clicked = false;
    for (const el of all) {
      const t = el.textContent.trim();
      if (t === '쿠폰받기' || t === '쿠폰 받기') { try { el.click(); clicked = true; break; } catch(e){} }
    }
    return {clicked};
    """
    r = driver.execute_script(js_click_and_read)
    time.sleep(1.8)
    block_dialogs()

    # 팝업/리스트에서 쿠폰 %
    js_read_coupon = """
    // 페이지 전체 body에서 "N% 할인" 또는 N% 쿠폰 패턴 수집
    const body = document.body.innerText;
    const matches = body.match(/(\\d{1,2})\\s*%\\s*(?:할인|쿠폰|즉시)/g) || [];
    const nums = matches.map(m => parseInt(m.match(/\\d+/)[0])).filter(n => n >= 5 && n <= 30);
    return {matches: matches.slice(0,20), max: nums.length ? Math.max(...nums) : null};
    """
    rd = driver.execute_script(js_read_coupon)
    coupons[code] = rd.get('max')
    print(f"  쿠폰: max {rd.get('max')}%, samples={rd['matches'][:5]}")

    # 첫 상품(b)에서만 카드 청구할인 확인
    if code == 'b' and card_info is None:
        js_card = """
        // detail_list / benefit 영역에서 "청구할인" 텍스트 + N% 추출
        const body = document.body.innerText;
        const lines = body.split('\\n').filter(l => /청구할인/.test(l));
        const out = {lines: lines.slice(0,10)};
        for (const l of lines) {
          const m = l.match(/(\\d+)\\s*%\\s*청구할인/);
          if (m) { out.pct = parseInt(m[1]); break; }
        }
        // 카드 이름 추출 (롯데/비씨/삼성/하나/농협)
        for (const c of ['롯데카드', '비씨카드', '삼성카드', '하나카드', '농협카드']) {
          if (body.includes(c)) {
            if (!out.cards) out.cards = [];
            out.cards.push(c);
          }
        }
        return out;
        """
        cr = driver.execute_script(js_card)
        card_info = cr
        print(f"  카드 청구할인 정보: {cr}")

print(f"\n쿠폰: {coupons}")
print(f"카드: {card_info}")

# 적립 확인 — _lotte_reward_dump.json 필수 (구매사은혜택 클릭 진입으로 최대 적립금 확인됨)
# ⚠️ dump 없거나 적립이 모두 0이면 절대 진행 안 함 — 가이드 9-2.5 click-in 필수
import os
reward_path = '/Users/jasonkim/Desktop/Vibe Coding/3mall auto buy/rate-check/_lotte_reward_dump.json'
if not os.path.exists(reward_path):
    sys.exit(f"❌ {reward_path} 없음 — 먼저 `_check_lotte_reward.py all` 실행해서 "
             f"구매사은혜택(#eventBanner) 클릭 진입으로 최대 적립금을 추출해야 함.")
rd = json.load(open(reward_path))
rewards = {code: rd.get(code, {}).get('total_max', 0) for code in 'bcdefgh'}
missing = [c for c in 'bcdefgh' if c not in rd or 'total_max' not in rd[c]]
if missing:
    sys.exit(f"❌ 적립 dump에 누락 상품 {missing} — `_check_lotte_reward.py all` 재실행 필요.")
print(f"적립금 (dump, 클릭 검증된 최대 적립금): {rewards}")

# 카드 결정 (단일 카드 가정)
CARD_NAME = (card_info.get('cards') or ['미확인'])[0] if card_info else '미확인'
CARD_PCT = card_info.get('pct') or 0 if card_info else 0
PAYBACK = {
    '롯데카드': 0.02, '비씨카드': 0.015, '삼성카드': 0.01,
    '하나카드': 0.01, '농협카드': 0.01,
}.get(CARD_NAME, 0)

print(f"\n적용 카드: {CARD_NAME} {CARD_PCT}% (페이백 {PAYBACK*100:.1f}%)")

# 11개 조합
COMBOS = [
    [('g',2),('h',1)], [('d',2),('g',2)], [('d',4),('e',1)], [('e',2),('h',1)],
    [('b',2),('d',2)], [('e',2),('f',2)], [('c',3),('h',1)], [('c',3),('d',2)],
    [('c',1),('f',4)], [('c',2),('f',3)], [('f',5)],
]
ADD_GIFT = {'b':16700,'c':8300,'d':15100,'e':16700,'f':15800,'g':18800,'h':22500}
GWP_6 = 82_800

def compute(combo):
    소비자 = sum(PRICES[c]*q for c,q in combo)
    추증 = sum(ADD_GIFT[c]*q for c,q in combo)
    총샘플 = 추증 + GWP_6
    # 적립은 한 주문건(=1조합)당 최대 1회 적용. 본품 수량 무관.
    적립 = max((rewards.get(c, 0) for c, _ in combo), default=0)
    # 상품별 최종가
    final = 0
    for c,q in combo:
        cp = coupons.get(c) or 0
        item_final = PRICES[c] * q * 0.9 * (1 - cp/100) * (1 - CARD_PCT/100) * (1 - PAYBACK)
        final += item_final
    final = round(final)
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

# === gspread 이어쓰기: Hmall(65~81) 뒤 3행 띄우고 85~ ===
gc = gspread.service_account(filename='/Users/jasonkim/Desktop/Vibe Coding/3mall auto buy/gen-lang-client-0553550811-4b553902b0d0.json')
sh = gc.open_by_key('1fxB0UvLRy2iQfonCWn5U5mWnXbzSdn6l4e2XuQluhwo')
ws = sh.worksheet('5.14')

START = 85
data = []
data.append(["━━━━ 3단계: 롯데홈쇼핑 공급률 분석 ━━━━"])
data.append([])
coupon_str = ", ".join(f"{c}={coupons[c]}%" for c in 'bcdefgh')
data.append([f"상품별 쿠폰: {coupon_str}"])
data.append([f"카드 청구할인: {CARD_NAME} {CARD_PCT}% (페이백 {round(PAYBACK*100,1)}%)"])
data.append([f"적립금(상품별 max): {rewards}"])
data.append([])
data.append(['조합번호','조합','소비자가','추증','GWP','총샘플','적립','최종구매가','순구매가','공급률'])
for r in rows:
    cn = combo_label_ko(r['combo'])
    data.append([r['idx'], cn, r['소비자가'], r['추증'], GWP_6, r['총샘플'], r['적립'], r['최종'], r['순'], round(r['공급률'],4)])

maxc = max(len(r) for r in data)
for r in data:
    while len(r) < maxc: r.append('')
end_col = chr(ord('A')+maxc-1)
rng = f"A{START}:{end_col}{START+len(data)-1}"
ws.update(values=data, range_name=rng, value_input_option='USER_ENTERED')
print(f"\n롯데 섹션 입력: {rng}")
