"""롯데홈쇼핑 stage — 7개 상품 쿠폰% + 카드할인 + 11개 조합 공급률 계산 + 시트 이어쓰기.

흐름:
- 쿠폰%: 상품 페이지 쿠폰받기 클릭 → 팝업 안의 가장 위 (다운로드 가능 최대) 쿠폰만 읽음 (배너 "할인" X)
- 카드 청구할인: 상품 페이지 "청구할인" 텍스트 추출
- 적립금: _check_lotte_reward.py를 subprocess로 호출 → stdout JSON_DUMP 파싱 (캐시 파일 X)
- 추증/GWP: galleria가 sheet에 쓴 결과를 load_galleria_composition_from_sheet(ws)로 직접 읽음 (캐시 X)
- 적립금은 조합당 1회 적용 (sum × qty 잘못 — 결제 1회 = 이벤트 1회)
"""
import json, sys, time, re
from pathlib import Path
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
import gspread

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _common import combo_label_ko, load_galleria_composition_from_sheet, RATE_SHEET_ID, today_tab_name, gs_client

import os as _os
_LOTTE_PORT = _os.environ.get("RATE_CHECK_CDP_PORT", "9222")
opts = Options()
opts.add_experimental_option("debuggerAddress", f"127.0.0.1:{_LOTTE_PORT}")
driver = webdriver.Chrome(options=opts)
print(f"CDP attach: 127.0.0.1:{_LOTTE_PORT}")

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

    # 쿠폰받기 클릭 — 팝업/레이어 등장
    # ★ 사용자 5/15 지시: body 전체 max % 수집 X (배너 "15% 할인" 같은 행사 표시 포함됨).
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

# 적립 확인 — _check_lotte_reward.py 를 subprocess로 호출 + stdout JSON 파싱.
# ★ 결과파일(_lotte_reward_dump.json) 절대 사용 X — sheet가 SoT.
import os, subprocess
rewards = {code: 0 for code in 'bcdefgh'}
try:
    reward_script = '/Users/jasonkim/Desktop/Vibe Coding/3mall auto buy/rate-check/_check_lotte_reward.py'
    if os.path.exists(reward_script):
        proc = subprocess.run(
            ['python3', reward_script, 'all'],
            capture_output=True, text=True, timeout=300,
        )
        out = proc.stdout
        # JSON_DUMP_BEGIN ... JSON_DUMP_END 사이 한 줄 파싱
        if 'JSON_DUMP_BEGIN' in out and 'JSON_DUMP_END' in out:
            jstr = out.split('JSON_DUMP_BEGIN')[1].split('JSON_DUMP_END')[0].strip().strip('=').strip()
            rd = json.loads(jstr)
            for code in 'bcdefgh':
                rewards[code] = rd.get(code, {}).get('total_max', 0)
            print(f"적립금 (subprocess): {rewards}")
        else:
            print("⚠️ _check_lotte_reward stdout JSON_DUMP 못 찾음 — 적립금 0으로 처리")
    else:
        print(f"⚠️ {reward_script} 없음 — 적립금 0으로 처리")
except Exception as e:
    print(f"⚠️ 적립금 subprocess 실패 ({e}) — 0으로 처리")

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
# 당일 추가증정가치/GWP — sheet에서 직접 읽기 (캐시 X, sheet가 SoT)
_gc_lotte = gs_client()
_sh_lotte = _gc_lotte.open_by_key(RATE_SHEET_ID)
_ws_lotte = _sh_lotte.worksheet(today_tab_name())
_comp = load_galleria_composition_from_sheet(_ws_lotte)
ADD_GIFT = _comp["add_gift_value"]
GWP_6 = _comp["gwp_6set"]
print(f"당일 추증가치 (sheet): {ADD_GIFT}")
print(f"당일 GWP 6세트 (sheet): {GWP_6:,}원")

def compute(combo):
    소비자 = sum(PRICES[c]*q for c,q in combo)
    추증 = sum(ADD_GIFT[c]*q for c,q in combo)
    총샘플 = 추증 + GWP_6
    # ★ 적립금: 조합 1개당 1회 적용 (사용자 5/14 지시).
    # 옛 sum(reward × qty)는 잘못 — 결제는 한 번이라 이벤트 1회 발생.
    # 조합 내 상품별 적립 후보 중 최대값 1회 (모두 동일 이벤트면 그 값 = max).
    applicable = [rewards.get(c, 0) for c, _ in combo if rewards.get(c, 0) > 0]
    적립 = max(applicable) if applicable else 0
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

# === gspread 이어쓰기: 행 100~ ===
gc = gs_client()
sh = gc.open_by_key(RATE_SHEET_ID)
ws = sh.worksheet(today_tab_name())

START = 62  # 사용자 layout: Hmall 44~60 다음 + 빈 1행 + Lotte 62~
data = []
data.append(["━━━━ 3단계: 롯데홈쇼핑 공급률 분석 ━━━━"])
data.append([])
coupon_str = ", ".join(f"{c}={coupons[c]}%" for c in 'bcdefgh')
data.append([f"상품별 쿠폰: {coupon_str}"])
data.append([f"카드 청구할인: {CARD_NAME} {CARD_PCT}% (페이백 {round(PAYBACK*100,1)}%)"])
data.append([f"적립금(상품별 max): {rewards}"])
data.append([])
data.append(['조합번호','조합','소비자가','추증','GWP','총샘플','적립','최종구매가','순구매가','공급률',
             '', '상품', '쿠폰%'])
for i, r in enumerate(rows):
    cn = combo_label_ko(r['combo'])
    row = [r['idx'], cn, r['소비자가'], r['추증'], GWP_6, r['총샘플'], r['적립'], r['최종'], r['순'], round(r['공급률'],4)]
    if i < 7:
        code = 'bcdefgh'[i]
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
ws.update(values=chart_m, range_name="M1:M12", value_input_option='USER_ENTERED')
print("M1:M12 (롯데 비교 차트 컬럼) 입력")

# 조건부 서식: K2:M12 행별 최저값 셀 → 연두색 배경 (3사 중 가장 좋은 deal 강조)
# 이미 같은 규칙이 있으면 중복 추가 — Sheets는 허용. 깨끗하게 하려면 미리 제거 가능.
try:
    sh.batch_update({
        "requests": [{
            "addConditionalFormatRule": {
                "rule": {
                    "ranges": [{
                        "sheetId": ws.id,
                        "startRowIndex": 1,    # 0-indexed row 2
                        "endRowIndex": 12,     # row 12 (exclusive 13)
                        "startColumnIndex": 10,  # K (0-indexed)
                        "endColumnIndex": 13,    # N (exclusive)
                    }],
                    "booleanRule": {
                        "condition": {
                            "type": "CUSTOM_FORMULA",
                            "values": [{"userEnteredValue": "=AND(ISNUMBER(K2),K2=MIN($K2:$M2))"}],
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
    print("조건부 서식 추가 — K2:M12 행별 최저 공급률 셀 연두색")
except Exception as e:
    print(f"⚠️ 조건부 서식 실패: {e}")
