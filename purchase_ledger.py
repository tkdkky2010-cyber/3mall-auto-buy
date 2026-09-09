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
