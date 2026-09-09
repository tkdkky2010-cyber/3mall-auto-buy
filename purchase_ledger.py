"""구매대장 — 결제 성공(주문완료) 1건을 기록한다.

저장 = ① 로컬 JSON(secrets/purchase_ledger.json) ② Google Sheet '구매대장' 탭 미러 (둘 다).
기록 항목 = 날짜 · 몰(채널) · 아이디 · 조합명 · 수량 · 최종결제금액 · 카드 · 주문번호.

★ 최종결제금액/조합명은 화면 OCR 대신 rate 시트(오늘 탭)의 몰별 섹션을 조합번호로 조회
   (사용자 결정 2026-06-16: pay 스크립트가 화면 금액을 안정적으로 못 읽음 → 시트 조합가 사용).

결제 스크립트(phone_auto/lotte_homeshopping_buy.py, hmall_hyundai_buy.py)가 DONE 직후 호출.
실패해도 결제 흐름엔 영향 없도록 모든 예외는 삼켜 경고만 출력.
"""
from __future__ import annotations
from pathlib import Path
import json
import os
import re
import time

ROOT = Path(__file__).resolve().parent
LEDGER_JSON = ROOT / "secrets" / "purchase_ledger.json"
TODAY_JSON = ROOT / "cart" / "today.json"   # 식품(Step2) 상품별 결제정보
RATE_SHEET_ID = "1fxB0UvLRy2iQfonCWn5U5mWnXbzSdn6l4e2XuQluhwo"
LEDGER_TAB = "구매대장"
GSPREAD_KEY = next(iter(ROOT.glob("gen-lang-*.json")), None)

# 몰 내부키 → rate 시트 섹션의 '최종결제금액' 컬럼 헤더 라벨 (섹션 구분에도 사용).
_AMOUNT_LABEL = {"lotte": "최종구매가", "hmall": "구매가격", "galleria": "네이버최종구매가"}
# 표기 → 내부키
_MALL_ALIAS = {
    "롯데홈쇼핑": "lotte", "롯데": "lotte", "lotte": "lotte",
    "현대Hmall": "hmall", "현대hmall": "hmall", "hmall": "hmall", "현대": "hmall",
    "갤러리아": "galleria", "galleria": "galleria",
}


def _gc():
    import gspread
    return gspread.service_account(filename=str(GSPREAD_KEY))


def _today_tab() -> str:
    t = time.localtime()
    return f"{t.tm_mon}.{t.tm_mday}"


def combo_info(mall: str, combo_idx, *, tab=None) -> tuple:
    """rate 시트 오늘 탭(tab 지정 시 그 탭)의 몰 섹션에서 combo_idx 행의 (조합명, 최종결제금액) 반환.
    오늘 탭이 없으면(=당일 Step1 미실행) 금액 미상으로 (None, None) — 다른 날 탭 금액을 잘못 쓰지 않음."""
    key = _MALL_ALIAS.get(mall, mall)
    label = _AMOUNT_LABEL.get(key)
    if not label or not GSPREAD_KEY or combo_idx is None:
        return (None, None)
    try:
        ws = _gc().open_by_key(RATE_SHEET_ID).worksheet(tab or _today_tab())
        vals = ws.get_all_values()
    except Exception:
        print(f"   [ledger] ⚠️ rate 탭 '{tab or _today_tab()}' 없음/조회실패 → 금액 미상 기록", flush=True)
        return (None, None)
    hdr_row = amt_col = name_col = None
    for ri, row in enumerate(vals):
        if "조합번호" in row and label in row:
            hdr_row = ri
            amt_col = row.index(label)
            name_col = row.index("조합") if "조합" in row else 1
            break
    if hdr_row is None:
        return (None, None)
    for row in vals[hdr_row + 1:]:
        first = row[0].strip() if row else ""
        if not first:
            continue
        if first == "조합번호":          # 다음 섹션 헤더 → 이 섹션 끝
            break
        if first == str(combo_idx):
            amt = row[amt_col].replace(",", "").strip() if amt_col < len(row) else ""
            nm = row[name_col].strip() if name_col < len(row) else ""
            return (nm or None, int(amt) if amt.lstrip("-").isdigit() else None)
    return (None, None)


# ════════════════════════════════════════════════════════════════
#  결제 직전 금액 가드 (2026-09-09 신설)
# ════════════════════════════════════════════════════════════════
#  기준 = rate 시트 오늘 탭의 그 조합 **'최종구매가'**(쿠폰 적용 후 결제금액).
#
#  ★상한 **한 방향만** 본다 — 적립금·L.POINT·H.Point 사용은 실제 결제액을 **낮추기만** 하므로
#    "예상보다 낮다"는 정상이다. 막아야 하는 건 "예상보다 높다"(= 혜택이 안 걸렸다) 뿐이다.
#
#  왜 만들었나: 종전 가드(`MAX_PAY`)는 **환경변수를 켜야만** 동작했다. 사람이 그날 안 켜면
#    쿠폰 0장이 조용히 정가로 결제된다 — 2026-08-25 602,000원(정상 541,800) /
#    2026-08-26 606,181원(정상 545,000대). 두 번 다 "MAX_PAY 가 막았다"고 기록돼 있지만
#    그건 **그날 사람이 켰기 때문**이고, 코드가 막은 게 아니다.
#    정답(시트 조합가)은 이미 이 파일 안에 있었는데 **결제가 끝난 뒤 대장 기록에만** 쓰고 있었다.
#
#  ⚠️ 시트를 네트워크로 읽는다(계정당 1회, 결제 직전). 조회 실패도 **차단**이다 —
#     "모르는 금액은 결제하지 않는다"(기존 MAX_PAY 의 AMOUNT_UNREADABLE 과 같은 원칙).
# 한도 = 결제화면 예상금액 + **정액 여유** (사용자 지시 2026-09-09: "max 를 좀 넉넉하게 잡아놔줘야해
# 너가 계산한 수치 + 2만원"). ★사용자가 정한 상수다 — "너무 빡빡/헐렁해 보인다"고 자동계산으로
# 바꾸지 말 것(READ_FIRST 「사용자가 정한 상수를 바꾸지 말 것」).
#   쿠폰 1장 누락은 보통 13,500~43,000원(상품 정가 135,000~270,000 × 10~15%)이라 2만원이면
#   대부분 걸린다. 다만 **최저가 상품의 10% 쿠폰 1장(≈13,500원)은 이 여유 안에 묻힌다.**
HEADROOM_WON = int(os.environ.get("AMOUNT_HEADROOM_WON", "20000"))

# ★★ 시트 금액 ≠ 결제화면 금액 — 몰마다 다르다 (2026-09-09 실측으로 잡은 초기 버그).
#    처음엔 세 몰 모두 대장용 컬럼(_AMOUNT_LABEL)을 그대로 썼는데, 롯데는 그 값이
#    **청구할인·페이백까지 미리 뺀** 값이라 결제화면보다 6~8% 낮다. 그대로 한도를 걸었더니
#    실측 로그(540,486~543,857원)가 전부 한도(518,409원) 초과 = **정상 결제가 전부 차단**됐다.
#    → 몰별로 '결제화면에 실제로 뜨는 금액' 에 해당하는 컬럼을 쓴다.
#      · 갤러리아 `네이버최종구매가` — 카드할인 미운영이라 그대로가 결제금액.
#      · 현대   `미리보기가`      — 카드 즉시할인 미리보기가 = 결제화면 금액.
#                                  (`구매가격` = 미리보기가 × (1-페이백) 이라 더 낮다. 쓰면 안 된다.)
#      · 롯데   `최종구매가` ÷ (1-청구할인%)(1-페이백%) — 섹션 위 '카드 청구할인: … N% (페이백 M%)'
#                                  줄에서 읽어 되돌린다. 청구할인은 결제화면에서 안 빠진다.
_PAY_COLUMN = {"lotte": "최종구매가", "hmall": "미리보기가", "galleria": "네이버최종구매가"}
_BILLING_RE = re.compile(r"카드\s*청구할인\s*:.*?(\d+(?:\.\d+)?)\s*%.*?페이백\s*(\d+(?:\.\d+)?)\s*%")


def expected_pay_amount(mall: str, combo_idx, *, tab=None) -> tuple:
    """그 조합이 **결제화면에 띄울** 예상 금액 → (금액|None, 설명).

    포인트(적립금·L.POINT·H.Point)는 여기 반영 안 된다 — 실제 결제액을 **낮추기만** 하므로
    상한 비교에는 무해하다.
    """
    key = _MALL_ALIAS.get(mall, mall)
    col = _PAY_COLUMN.get(key)
    if not col or not GSPREAD_KEY or combo_idx is None:
        return (None, "대상 아님")
    try:
        ws = _gc().open_by_key(RATE_SHEET_ID).worksheet(tab or _today_tab())
        vals = ws.get_all_values()
    except Exception as e:
        return (None, f"rate 탭 '{tab or _today_tab()}' 조회 실패({e})")
    hdr_row = amt_col = None
    for ri, row in enumerate(vals):
        if "조합번호" in row and col in row:
            hdr_row, amt_col = ri, row.index(col)
            break
    if hdr_row is None:
        return (None, f"시트에 '{col}' 컬럼이 있는 섹션을 못 찾음")
    # 롯데만 — 섹션 헤더 위쪽에서 청구할인/페이백을 찾아 되돌린다.
    factor = 1.0
    note = ""
    if key == "lotte":
        m = next((_BILLING_RE.search(" ".join(r)) for r in reversed(vals[:hdr_row])
                  if _BILLING_RE.search(" ".join(r))), None)
        if not m:
            return (None, "롯데 섹션에서 '카드 청구할인: N% (페이백 M%)' 줄을 못 찾음")
        pct, pb = float(m.group(1)), float(m.group(2))
        factor = (1 - pct / 100) * (1 - pb / 100)
        note = f" [청구할인 {pct:g}%·페이백 {pb:g}% 되돌림]"
    for row in vals[hdr_row + 1:]:
        first = row[0].strip() if row else ""
        if not first:
            continue
        if first == "조합번호":
            break
        if first == str(combo_idx):
            raw = row[amt_col].replace(",", "").strip() if amt_col < len(row) else ""
            if not raw.lstrip("-").isdigit():
                return (None, f"조합{combo_idx} '{col}' 이 비었다")
            return (round(int(raw) / factor), f"{col}{note}")
    return (None, f"시트 섹션에 조합{combo_idx} 행이 없다")


def check_amount(mall: str, combo_idx, actual: int | None, *, tab=None) -> dict:
    """결제 직전 금액 검증 → `{"ok", "expected", "limit", "reason"}`. **ok=False 면 결제하지 않는다.**

    - `SKIP_SHEET_GUARD=1`  → 검사 안 함 (탈출구; 시트가 없는 날 수동 진행용).
    - `combo_idx is None`   → 검사 안 함. 식품은 시트에 조합가가 없다.
    - 시트값 없음 / 금액 판독 실패 → **차단**.
    - 한도 = 예상금액 + `AMOUNT_HEADROOM_WON`(기본 20,000원).
    """
    if os.environ.get("SKIP_SHEET_GUARD") == "1":
        return {"ok": True, "expected": None, "limit": None,
                "reason": "SKIP_SHEET_GUARD=1 — 시트 대조 해제됨"}
    if combo_idx is None:
        return {"ok": True, "expected": None, "limit": None,
                "reason": "조합 미지정(식품) — 시트 대조 대상 아님"}
    expected, src = expected_pay_amount(mall, combo_idx, tab=tab)
    if not expected:
        return {"ok": False, "expected": None, "limit": None,
                "reason": (f"시트에서 조합{combo_idx} 결제예상금액을 못 읽었다 ({src}). "
                           f"모르는 금액은 결제하지 않는다. 확인 후 강행하려면 SKIP_SHEET_GUARD=1")}
    limit = expected + HEADROOM_WON
    if actual is None:
        return {"ok": False, "expected": expected, "limit": limit,
                "reason": (f"결제 예정 금액 판독 실패 (시트 기대 {expected:,}원). "
                           f"모르는 금액은 결제하지 않는다")}
    if actual > limit:
        return {"ok": False, "expected": expected, "limit": limit,
                "reason": (f"실제 {actual:,}원 > 한도 {limit:,}원 "
                           f"(시트 조합{combo_idx} 예상 {expected:,}원 +여유 {HEADROOM_WON:,}원) — 혜택 미적용 의심")}
    return {"ok": True, "expected": expected, "limit": limit,
            "reason": (f"시트 조합{combo_idx} {expected:,}원 / 한도 {limit:,}원 "
                       f"vs 실제 {actual:,}원 — 통과")}


def food_info(product_id) -> tuple:
    """cart/today.json 에서 식품 상품의 (이름, 최종결제금액=즉시할인가, 수량) 반환. 실패시 (None, None, None).
    최종결제금액 = kakao_price(카드 청구되는 즉시할인가). 실비(kakao_final_cost)는 적립 차감 후라 청구액 아님."""
    try:
        d = json.loads(TODAY_JSON.read_text(encoding="utf-8"))
        p = next((x for x in d.get("products", []) if str(x.get("id")) == str(product_id)), None)
        if not p:
            # today.json 미측정 상품(옵션 변형 등) — buy/products.json 이름만 폴백 (금액은 미상)
            try:
                prods = json.loads((ROOT / "buy" / "products.json").read_text(encoding="utf-8"))
                name = (prods.get(str(product_id)) or {}).get("name")
            except Exception:
                name = None
            return (name, None, None)
        pay = p.get("payment") or {}
        return (p.get("name"), pay.get("kakao_price"), pay.get("qty"))
    except Exception:
        return (None, None, None)


def _ledger_ws():
    sh = _gc().open_by_key(RATE_SHEET_ID)
    try:
        return sh.worksheet(LEDGER_TAB)
    except Exception:
        ws = sh.add_worksheet(title=LEDGER_TAB, rows=1000, cols=8)
        ws.append_row(["날짜", "몰", "아이디", "조합/상품", "수량", "최종결제금액", "카드", "주문번호"],
                      value_input_option="USER_ENTERED")
        return ws


def record(mall: str, account_id, combo, qty, amount, *,
           date=None, order_no=None, card=None) -> dict:
    """1건 기록 (JSON append + 시트 append). 모든 예외 삼킴."""
    date = date or time.strftime("%Y-%m-%d %H:%M")
    entry = {"date": date, "mall": mall, "id": account_id, "combo": combo,
             "qty": qty, "amount": amount, "card": card, "order_no": order_no}
    try:
        led = json.loads(LEDGER_JSON.read_text(encoding="utf-8")) if LEDGER_JSON.exists() else {"entries": []}
        led.setdefault("entries", []).append(entry)
        LEDGER_JSON.write_text(json.dumps(led, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"   [ledger] JSON 기록 실패(무시): {e}", flush=True)
    try:
        _ledger_ws().append_row(
            [date, mall, account_id or "", combo or "",
             qty if qty is not None else "", amount if amount is not None else "",
             card or "", order_no or ""],
            value_input_option="USER_ENTERED")
    except Exception as e:
        print(f"   [ledger] 시트 기록 실패(무시): {e}", flush=True)
    print(f"   [ledger] 기록: {mall} {account_id} {combo} x{qty} "
          f"{amount if amount is not None else '?'}원 (주문 {order_no or '-'})", flush=True)
    return entry


def record_combo(mall: str, account_id, combo_idx, *,
                 qty=1, order_no=None, card=None, date=None) -> dict:
    """조합번호로 rate 시트 조회 후 기록 (설화수 폰결제용). combo_idx None 이면 미지정으로 기록."""
    name, amount = combo_info(mall, combo_idx)
    combo = name or (f"조합{combo_idx}" if combo_idx is not None else "(조합 미지정)")
    return record(mall, account_id, combo, qty, amount,
                  order_no=order_no, card=card, date=date)


def record_food(mall: str, account_id, product_id, *,
                qty=None, order_no=None, card=None, date=None) -> dict:
    """식품 상품번호로 cart/today.json 조회 후 기록 (식품 폰결제용).
    실제 구매 qty가 today.json 측정 qty와 다르면 청구액을 비례 환산 (식품 카드할인은 쿠폰 없는 flat% → 수량 비례)."""
    name, t_amount, t_qty = food_info(product_id)
    use_qty = qty if qty is not None else t_qty
    amount = t_amount
    if t_amount is not None and t_qty and qty is not None and qty != t_qty:
        amount = round(t_amount / t_qty * qty)
    combo = name or f"식품#{product_id}"
    return record(mall, account_id, combo, use_qty if use_qty is not None else 1, amount,
                  order_no=order_no, card=card, date=date)


if __name__ == "__main__":
    import sys
    # 테스트: python3 purchase_ledger.py info lotte 23 [tab]  |  food 2
    if len(sys.argv) >= 4 and sys.argv[1] == "info":
        tab = sys.argv[4] if len(sys.argv) > 4 else None
        print(combo_info(sys.argv[2], int(sys.argv[3]), tab=tab))
    elif len(sys.argv) >= 3 and sys.argv[1] == "food":
        print(food_info(int(sys.argv[2])))
