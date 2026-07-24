#!/usr/bin/env python3
"""부족 상품 → 당일 적립 이벤트 구간을 '최소 초과'로 맞추는 수량 조합 계산.

사용:
    python3 cart/combo_plan.py 13,17,35            # 아이디당 각 상품 최대 2개
    python3 cart/combo_plan.py 13,17,35 --max 2

기준가 = **카드할인 후 결제금액** (혜택가 × (1 − 당일카드%)). 적립 판정이 최종결제가 기준이므로.
데이터원 = cart/today.json (check10 결과). 이벤트/구간은 상품별 events 에서 읽되,
멤버스데이 현대카드는 스크랩이 안 돼(구간 0) 사용자 제공값을 MANUAL_TIERS 로 보강.
"""
from __future__ import annotations
import json
import sys
from itertools import product as iproduct
from pathlib import Path

TODAY = Path(__file__).parent / "today.json"
LIMIT_PER_ID = 2          # 아이디당 동일상품 최대 구매수량 (사용자 지시)

# 스크랩 안 되는 이벤트 구간 — 사용자 제공(2026-07-23). [(임계금액, 적립)]
MANUAL_TIERS = {
    "멤버스데이": [(50_000, 5_000), (100_000, 10_000), (300_000, 30_000),
                (500_000, 50_000), (700_000, 70_000)],
}


def _event_key(name: str) -> str:
    """이벤트명 → 짧은 키."""
    if "데이즈온" in name:
        return "데이즈온"
    if "멤버스데이" in name:
        return "멤버스데이"
    if "건강식품" in name or "특별전" in name:
        return "건강식품특별전"
    return name.strip()[:14]


def load_products() -> tuple[dict, dict]:
    """{id: {...unit_card, events[]}} 와 {event_key: tiers|('rate',pct)} 반환."""
    d = json.loads(TODAY.read_text(encoding="utf-8"))
    prods, ev_rule = {}, {}
    for p in d["products"]:
        pay = p.get("payment") or {}
        cs = [c for c in (pay.get("card_slides") or []) if c.get("percent") and c.get("price")]
        bu = pay.get("benefit_unit_price")
        if not cs or not bu:
            continue                                   # 카드할인/단가 미확보 상품은 계산 불가
        best = max(cs, key=lambda c: c["percent"])
        unit_card = int(round(bu * (1 - best["percent"] / 100)))
        keys = []
        for e in (p.get("events") or []):
            k = _event_key(e.get("name") or "")
            keys.append(k)
            if k in ev_rule:
                continue
            if e.get("tiers"):
                ev_rule[k] = ("tier", [(t["min_won"], t["reward_pt"]) for t in e["tiers"]])
            elif e.get("simple_range"):
                sr = e["simple_range"]
                ev_rule[k] = ("rate", (sr["min_won"], sr["max_won"], sr["pct"]))
        prods[p["id"]] = {"id": p["id"], "name": p["name"], "unit_card": unit_card,
                          "card_pct": best["percent"], "events": keys}
    for k, tiers in MANUAL_TIERS.items():             # 스크랩 실패분 보강
        ev_rule.setdefault(k, ("tier", tiers))
    return prods, ev_rule


def reward_of(rule, total: int) -> tuple[int, int | None]:
    """(적립액, 걸린임계) — 미달이면 (0, None)."""
    kind, val = rule
    if kind == "rate":
        lo, hi, pct = val
        return (int(total * pct / 100), lo) if lo <= total <= hi else (0, None)
    hit = [(mw, rp) for mw, rp in val if total >= mw]
    if not hit:
        return 0, None
    mw, rp = max(hit, key=lambda x: x[0])
    return rp, mw


def next_threshold(rule, total: int) -> int | None:
    """아직 못 넘은 다음 임계 (있으면)."""
    kind, val = rule
    cands = [val[0]] if kind == "rate" else [mw for mw, _ in val]
    up = [c for c in cands if c > total]
    return min(up) if up else None


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    ids = [int(x) for x in sys.argv[1].replace(" ", "").split(",") if x.strip().isdigit()]
    mx = LIMIT_PER_ID
    if "--max" in sys.argv:
        mx = int(sys.argv[sys.argv.index("--max") + 1])

    prods, ev_rule = load_products()
    missing = [i for i in ids if i not in prods]
    if missing:
        print(f"[WARN] 카드할인/단가 미확보로 계산 불가: {missing} (바로구매 실패·판매중단 등)")
    sel = [prods[i] for i in ids if i in prods]
    if not sel:
        print("[ERR] 계산 가능한 상품이 없음")
        return 1

    print(f"■ 대상 상품 (아이디당 최대 {mx}개, 카드할인 {sel[0]['card_pct']}% 후 단가 기준)")
    for p in sel:
        print(f"   #{p['id']:>3} {p['name'][:30]:32s} 단가 {p['unit_card']:>8,}원   이벤트: {', '.join(p['events'])}")

    # 전 조합 (각 1..mx) 평가
    rows = []
    for combo in iproduct(*[range(1, mx + 1) for _ in sel]):
        per_ev: dict[str, int] = {}
        for p, q in zip(sel, combo):
            for k in p["events"]:
                per_ev[k] = per_ev.get(k, 0) + p["unit_card"] * q
        total_pay = sum(p["unit_card"] * q for p, q in zip(sel, combo))
        detail, total_rw = {}, 0
        for k, amt in per_ev.items():
            rule = ev_rule.get(k)
            if not rule:
                continue
            rw, hit = reward_of(rule, amt)
            total_rw += rw
            detail[k] = (amt, rw, hit, next_threshold(rule, amt))
        rows.append((combo, total_pay, total_rw, detail))

    # ★적립률(적립÷결제) 최대 → 결제액 최소. 정률이벤트(10%)는 금액이 커도 비율이 같아
    #   '총액 최대'로 정렬하면 무조건 최대수량이 1등이 됨 → 구간 '최소초과' 취지에 맞게 비율 기준.
    rows.sort(key=lambda r: (-(r[2] / r[1] if r[1] else 0), r[1]))

    TOPN = 6
    print(f"\n■ 조합별 결과 (적립률 최대 → 결제액 최소 순, 상위 {TOPN})")
    for combo, pay, rw, detail in rows[:TOPN]:
        qty_s = " + ".join(f"#{p['id']}×{q}" for p, q in zip(sel, combo))
        rate = rw / pay * 100 if pay else 0
        print(f"\n  ▶ {qty_s}\n     결제 {pay:,}원 → 적립 {rw:,}원 (적립률 {rate:.1f}%) / 실부담 {pay - rw:,}원")
        for k, (amt, r, hit, nxt) in sorted(detail.items()):
            hit_s = f"{hit:,} 구간" if hit else "미달"
            gap = ""
            if nxt:
                need = nxt - amt
                rule = ev_rule.get(k)
                gain = reward_of(rule, nxt)[0] - r if rule else 0
                mark = "  ⭐아쉬움" if (gain > 0 and need <= 30_000) else ""
                gap = f" | 다음 {nxt:,} 까지 {need:,}원 (+{gain:,}원){mark}"
            print(f"       - {k:12s} {amt:>9,}원 → {r:>7,}원 ({hit_s}){gap}")

    best = rows[0]
    qty_s = ", ".join(f"#{p['id']} {q}개" for p, q in zip(sel, best[0]))
    _rate = best[2] / best[1] * 100 if best[1] else 0
    print(f"\n★ 최고 효율: {qty_s}")
    print(f"   결제 {best[1]:,}원 / 적립 {best[2]:,}원 (적립률 {_rate:.1f}%) / 실부담 {best[1]-best[2]:,}원")
    mx_row = max(rows, key=lambda r: r[2])
    if mx_row[0] != best[0]:
        q2 = ", ".join(f"#{p['id']} {q}개" for p, q in zip(sel, mx_row[0]))
        print(f"   (적립 총액 최대는: {q2} → 적립 {mx_row[2]:,}원 / 결제 {mx_row[1]:,}원)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
