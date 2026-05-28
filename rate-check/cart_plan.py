"""rate-check/cart_plan.py — Step 1 (3사 공급률 분석) 완료 후 자동 실행.

자동 채널 선택 + N개 단순 분배 (sort + round-robin + 재고 50 스킵) + 시트 입력.
LP 미사용 (scipy 의존 X). 표준 라이브러리만.

분배 로직 (make_cart_plan):
- 가중 수익 점수 (Σ PRODUCT_PROFIT × PRODUCT_VELOCITY × qty) 내림차순 정렬
- 재고 OVERSTOCK (>50) 코드 포함 조합 스킵
- N개 슬롯을 available 조합에 round-robin (slot % len(available))
- **available < N 이면 같은 조합 2~3개씩 자동 누적** (N=14/36 친구 카드 케이스 핵심).
  예: available=7, N=14 → 각 조합 2개씩 / available=10, N=36 → 평균 3.6개씩.

사용:
    python3 rate-check/cart_plan.py                       # 자동 채널
    python3 rate-check/cart_plan.py --channel lotte       # override
    python3 rate-check/cart_plan.py --channel lotte --n 14  # 친구카드

채널별 디폴트 N: galleria=36, hmall=36, lotte=7

데이터 소스 (캐시 X, sheet가 SoT, RULES §1-3):
- 공급률: rate 시트 오늘 탭 K2:M{1+N} (galleria/hmall/lotte × N조합, N=len(COMBOS))
- 재고:   INVENTORY 시트 '재고현황' 탭 D6:D12 (b~h)

출력:
- stdout: '=== CART_PLAN_BEGIN === {JSON} === CART_PLAN_END ===' 마커 + product_totals
- 시트:   오늘 탭 O1:U{end} — 카트플랜 (O~R) + b~h 제품별 총 수량 (O~U, 하단)
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import _common as C


CHANNEL_DEFAULT_N = {"galleria": 36, "hmall": 36, "lotte": 7}
OVERSTOCK_THRESHOLD = 50


def read_supply_rates(ws) -> list[list[float | None]]:
    """K2:M{1+N} → N × 3 (galleria, hmall, lotte). 빈셀 = None. N = len(C.COMBOS)."""
    n = len(C.COMBOS)
    rows = ws.get(f"K2:M{1 + n}")
    out: list[list[float | None]] = []
    for r in rows:
        row: list[float | None] = []
        padded = r + ["", "", ""]
        for v in padded[:3]:
            try:
                row.append(float(v) if v != "" else None)
            except (ValueError, TypeError):
                row.append(None)
        out.append(row)
    while len(out) < n:
        out.append([None, None, None])
    return out[:n]


def select_channel(rates: list[list[float | None]]) -> str:
    """조합별 최저 공급률 채널 win count → 가장 많이 이긴 채널. 동률 시 평균 최저."""
    channels = ["galleria", "hmall", "lotte"]
    wins = {c: 0 for c in channels}
    sums = {c: 0.0 for c in channels}
    counts = {c: 0 for c in channels}
    for row in rates:
        present = [(c, v) for c, v in zip(channels, row) if v is not None]
        if not present:
            continue
        winner = min(present, key=lambda p: p[1])
        wins[winner[0]] += 1
        for c, v in present:
            sums[c] += v
            counts[c] += 1
    max_wins = max(wins.values())
    candidates = [c for c in channels if wins[c] == max_wins]
    if len(candidates) == 1:
        return candidates[0]
    return min(
        candidates,
        key=lambda c: sums[c] / counts[c] if counts[c] else float("inf"),
    )


def read_stocks(gc) -> dict[str, int]:
    """재고관리 시트 '재고현황' 탭 D6:D12 → {b~h: 재고}."""
    sh = gc.open_by_key(C.INVENTORY_SHEET_ID)
    ws = sh.worksheet("재고현황")
    cells = ws.get("D6:D12")
    stocks: dict[str, int] = {}
    padded = cells + [[""]] * 7
    for code, row in zip("bcdefgh", padded[:7]):
        v = row[0] if row else ""
        try:
            stocks[code] = int(str(v).replace(",", "")) if v else 0
        except (ValueError, TypeError):
            stocks[code] = 0
    return stocks


def is_overstocked(combo: list[tuple[str, int]], stocks: dict[str, int]) -> bool:
    return any(stocks.get(code, 0) > OVERSTOCK_THRESHOLD for code, _ in combo)


def has_excluded_code(combo: list[tuple[str, int]], exclude: set[str]) -> bool:
    """판매중지 등 사용자 지정 제외 코드 포함 시 True."""
    return any(code in exclude for code, _ in combo)


def _combo_score(combo: list[tuple[str, int]]) -> int:
    """조합의 가중 수익 점수 = Σ (PRODUCT_PROFIT[c] × PRODUCT_VELOCITY[c] × qty).

    상품별 수익금 (개당 남는 금액) × 회전율 (월환산 판매수량) × 수량.
    회전 빠른 상품을 더 많이 포함하는 조합이 우선 선택됨 → 악성재고 회피 + 수익 극대화.
    """
    return sum(
        C.PRODUCT_PROFIT[code] * C.PRODUCT_VELOCITY[code] * q
        for code, q in combo
    )


def make_cart_plan(
    channel_rates: list[float | None],
    combos: list[list[tuple[str, int]]],
    n: int,
    stocks: dict[str, int],
    exclude_codes: set[str] | None = None,
    last_resort_codes: set[str] | None = None,
    cap_per_combo: int | None = None,
    max_per_product: dict[str, int] | None = None,
) -> dict[int, int]:
    """sort + tiered round-robin + 재고 OVERSTOCK 스킵. 반환: {조합번호(1-based): 수량}.

    정렬 기준 (5/19): **상품별 수익금 × 회전율 가중합** 내림차순.

    Tier 분리 (5/28 추가):
    - `last_resort_codes` 포함 조합 = tier_b (보조). 미포함 = tier_a (주력).
    - tier_a 우선 round-robin (각 조합 `cap_per_combo` 까지) → 부족 시 tier_b 추가.
    - `max_per_product` (5/28 추가) — {code: max} 도달한 product 포함 조합은 skip (round-robin 다른 조합 시도).
    - 예: last_resort={d}, cap=3, tier_a 9개, max={f:20}, N=36 → tier_a 채울 때 f cap 도달 시 해당 조합 건너뜀.

    사용 이유:
    - d profit=-359 적자 → d 포함 조합 보조 자원
    - f 누적이 많은 조합 위주로 COMBOS 가 구성되어 있어 plan 의 f 본품 수가 과다해질 수 있음 → max f 로 상한.
    """
    indexed = [i for i, rate in enumerate(channel_rates) if rate is not None]
    exc = exclude_codes or set()
    lr = last_resort_codes or set()
    mpp = dict(max_per_product or {})
    if exc:
        indexed = [i for i in indexed if not has_excluded_code(combos[i], exc)]
    indexed.sort(key=lambda i: -_combo_score(combos[i]))
    available = [i for i in indexed if not is_overstocked(combos[i], stocks)]
    if not available:
        available = indexed
    if not available:
        return {}

    tier_a = [i for i in available if not has_excluded_code(combos[i], lr)]
    tier_b = [i for i in available if has_excluded_code(combos[i], lr)]

    cart: dict[int, int] = {}
    running: dict[str, int] = {}

    def _would_exceed_cap(idx: int) -> bool:
        for code, q in combos[idx]:
            if code in mpp and running.get(code, 0) + q > mpp[code]:
                return True
        return False

    def _apply(idx: int):
        cart[idx + 1] = cart.get(idx + 1, 0) + 1
        for code, q in combos[idx]:
            running[code] = running.get(code, 0) + q

    def _fill_tier(tier: list[int], remaining: int, cap: int | None) -> int:
        """tier round-robin. per-combo cap + per-product cap 둘 다 적용. 한 라운드 동안 아무것도 못 담으면 종료."""
        if not tier:
            return remaining
        rounds = cap if cap is not None else 10**6  # virtual unlimited (실질 round-robin)
        for _ in range(rounds):
            added = False
            for idx in tier:
                if remaining <= 0:
                    return 0
                if _would_exceed_cap(idx):
                    continue
                _apply(idx)
                remaining -= 1
                added = True
            if not added:
                # 한 라운드 동안 모든 조합이 cap 초과 → 종료 (tier 더 진행 불가)
                break
        return remaining

    remaining = _fill_tier(tier_a, n, cap_per_combo)
    # tier_a 만으로 N 부족 시 tier_b 추가 (per-combo cap 적용 X — last-resort 라 자유)
    if remaining > 0:
        remaining = _fill_tier(tier_b, remaining, None)
    return cart


def write_sheet_section(
    ws,
    channel: str,
    n: int,
    plan_rows: list[dict],
    total_qty: int,
    total_pay: int,
    avg_rate: float,
    today_label: str,
    product_totals: dict[str, int],
) -> str:
    """O1:U{end} 카트플랜 + b~h 총 수량 섹션 1회 batch update."""
    grid: list[list] = []
    grid.append(["카트 플랜", channel, f"N={n}", today_label])
    grid.append(["조합번호", "조합", "구매수", "공급률", "수익금"])
    for r in plan_rows:
        grid.append([r["combo_idx"], r["label"], r["qty"], round(r["supply_rate"], 4), r["profit"]])
    # 합계 행: 채널, 총 수량, 총 결제, 평균 공급률, 총 수익금
    total_profit_sum = sum(r["profit"] for r in plan_rows)
    grid.append([channel, total_qty, total_pay, round(avg_rate, 4), total_profit_sum])
    # 하단: b~h 제품별 총 수량 (오늘 카트플랜대로 구매 시 받게 되는 본품 합계)
    grid.append([])  # 빈 행 (분리)
    grid.append(["[b~h 제품별 총 수량 (본)]"])
    grid.append([f"{c} {C.SHORT_NAME[c]}" for c in "bcdefgh"])
    grid.append([product_totals.get(c, 0) for c in "bcdefgh"])
    # 행 너비 정규화 (mixed width → 최대 너비로 padding)
    maxw = max(len(row) for row in grid)
    norm = [row + [""] * (maxw - len(row)) for row in grid]
    end_col = chr(ord("O") + maxw - 1)
    rng = f"O1:{end_col}{len(norm)}"
    ws.update(values=norm, range_name=rng, value_input_option="USER_ENTERED")
    return rng


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--channel", choices=["galleria", "hmall", "lotte"],
                   help="override 자동 채널 선택")
    p.add_argument("--n", type=int,
                   help=f"override 채널별 디폴트 N {CHANNEL_DEFAULT_N}")
    p.add_argument("--tab", help="시트 탭 override (기본: today, M.DD)")
    p.add_argument("--exclude", default="",
                   help="제외 코드 (콤마 구분, 예: 'c,d' 판매중지 시)")
    p.add_argument("--last-resort", default="d",
                   help="보조 자원 코드 (콤마 구분). 포함 조합은 tier_b — N 초과 분만 추가. 기본 'd' (적자 코드, 5/28~)")
    p.add_argument("--cap-per-combo", type=int, default=3,
                   help="tier_a 조합당 최대 반복 회수 (기본 3 = 계정당 최대 3회 구매 제약 반영)")
    p.add_argument("--max-product", default="",
                   help="제품 상한 (콤마 구분, 예: 'f=20,d=10') — 누적 본품 수 초과 시 해당 조합 skip")
    args = p.parse_args(argv)
    exclude_codes = set(c.strip() for c in args.exclude.split(",") if c.strip())
    last_resort_codes = set(c.strip() for c in args.last_resort.split(",") if c.strip())
    max_per_product: dict[str, int] = {}
    for tok in args.max_product.split(","):
        tok = tok.strip()
        if not tok or "=" not in tok:
            continue
        code, val = tok.split("=", 1)
        try:
            max_per_product[code.strip()] = int(val.strip())
        except ValueError:
            pass

    gc = C.gs_client()
    sh = gc.open_by_key(C.RATE_SHEET_ID)
    tab = args.tab or C.today_tab_name()
    try:
        ws = sh.worksheet(tab)
    except Exception:
        print(f"❌ '{tab}' 탭 없음 — Step 1 (galleria/hmall/lotte) 먼저 실행 필요")
        return 1

    rates = read_supply_rates(ws)
    channel = args.channel or select_channel(rates)
    n = args.n if args.n is not None else CHANNEL_DEFAULT_N[channel]
    print(f"▶ cart_plan: channel={channel}, N={n}")

    channel_idx = {"galleria": 0, "hmall": 1, "lotte": 2}[channel]
    channel_rates = [row[channel_idx] for row in rates]

    stocks = read_stocks(gc)
    print(f"  재고 (b~h): {stocks}")
    over = sorted(c for c, q in stocks.items() if q > OVERSTOCK_THRESHOLD)
    if over:
        print(f"  ⚠️ 재고 > {OVERSTOCK_THRESHOLD}: {over} → 해당 코드 포함 조합 스킵")

    if exclude_codes:
        print(f"  ⊘ 제외 코드: {sorted(exclude_codes)} (포함 조합 스킵)")
    if last_resort_codes:
        print(f"  ▼ 보조(last-resort) 코드: {sorted(last_resort_codes)} — tier_a {args.cap_per_combo}회 cap 초과 분만 추가")
    if max_per_product:
        print(f"  ↧ 제품 상한: {max_per_product} — 누적 본품 cap 도달 조합 skip")
    cart = make_cart_plan(channel_rates, C.COMBOS, n, stocks,
                          exclude_codes=exclude_codes,
                          last_resort_codes=last_resort_codes,
                          cap_per_combo=args.cap_per_combo,
                          max_per_product=max_per_product)
    if not cart:
        print(f"❌ 공급률 데이터 없음 — Step 1 K2:M{1+len(C.COMBOS)} 비어있는지 확인")
        return 1

    plan_rows: list[dict] = []
    total_pay = 0
    total_profit = 0
    weighted_rate_sum = 0.0
    total_qty = 0
    product_totals: dict[str, int] = {code: 0 for code in "bcdefgh"}
    for combo_no in sorted(cart):
        qty = cart[combo_no]
        rate = channel_rates[combo_no - 1] or 0.0
        combo = C.COMBOS[combo_no - 1]
        label = C.combo_label_ko(combo)
        소비자가 = sum(C.PRODUCTS[code]["price"] * q for code, q in combo)
        pay_per_combo = round(소비자가 * rate)
        # 조합 1회 구매 시 수익금 = Σ PRODUCT_PROFIT[code] × q
        profit_per_combo = sum(C.PRODUCT_PROFIT[code] * q for code, q in combo)
        total_pay += pay_per_combo * qty
        total_profit += profit_per_combo * qty
        weighted_rate_sum += rate * qty
        total_qty += qty
        # b~h 제품 누적 (qty × 조합 내 개수)
        for code, q in combo:
            product_totals[code] += q * qty
        plan_rows.append({
            "combo_idx": combo_no,
            "qty": qty,
            "supply_rate": rate,
            "label": label,
            "profit": profit_per_combo * qty,
        })
    avg_rate = weighted_rate_sum / total_qty if total_qty else 0.0

    print()
    print("=== CART_PLAN_BEGIN ===")
    print(json.dumps({
        "date": tab,
        "channel": channel,
        "n": n,
        "plan": [
            {"combo_idx": r["combo_idx"], "qty": r["qty"],
             "supply_rate": round(r["supply_rate"], 4)}
            for r in plan_rows
        ],
        "total_pay": total_pay,
        "avg_supply_rate": round(avg_rate, 4),
        "product_totals": product_totals,
    }, ensure_ascii=False))
    print("=== CART_PLAN_END ===")
    print(f"\nb~h 제품별 총 수량: {product_totals}")

    rng = write_sheet_section(
        ws, channel, n, plan_rows, total_qty, total_pay, avg_rate, tab,
        product_totals,
    )
    print(f"\n→ 시트 입력: {rng}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
