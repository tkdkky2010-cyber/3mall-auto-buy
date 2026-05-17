"""rate-check/cart_plan.py — Step 1 (3사 공급률 분석) 완료 후 자동 실행.

자동 채널 선택 + N개 단순 분배 (sort + round-robin + 재고 50 스킵) + 시트 입력.
LP 미사용 (scipy 의존 X). 표준 라이브러리만.

분배 로직 (make_cart_plan):
- 공급률 오름차순 정렬 → 재고 OVERSTOCK (>50) 코드 포함 조합 스킵
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


def make_cart_plan(
    channel_rates: list[float | None],
    combos: list[list[tuple[str, int]]],
    n: int,
    stocks: dict[str, int],
) -> dict[int, int]:
    """sort + round-robin + 재고 OVERSTOCK 스킵. 반환: {조합번호(1-based): 수량}."""
    # 공급률 있는 조합만 선택 + 오름차순 정렬 (인덱스 0-based 유지)
    indexed = [i for i, rate in enumerate(channel_rates) if rate is not None]
    indexed.sort(key=lambda i: channel_rates[i])  # type: ignore[arg-type]
    available = [i for i in indexed if not is_overstocked(combos[i], stocks)]
    # 다 스킵되면 정렬 순서대로 fallback (사용자 spec)
    if not available:
        available = indexed
    if not available:
        return {}
    cart: dict[int, int] = {}
    for slot in range(n):
        idx = available[slot % len(available)]
        combo_no = idx + 1  # 1-based
        cart[combo_no] = cart.get(combo_no, 0) + 1
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
    grid.append(["조합번호", "조합", "구매수", "공급률"])
    for r in plan_rows:
        grid.append([r["combo_idx"], r["label"], r["qty"], round(r["supply_rate"], 4)])
    grid.append([channel, total_qty, total_pay, round(avg_rate, 4)])
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
    args = p.parse_args(argv)

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

    cart = make_cart_plan(channel_rates, C.COMBOS, n, stocks)
    if not cart:
        print(f"❌ 공급률 데이터 없음 — Step 1 K2:M{1+len(C.COMBOS)} 비어있는지 확인")
        return 1

    plan_rows: list[dict] = []
    total_pay = 0
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
        total_pay += pay_per_combo * qty
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
