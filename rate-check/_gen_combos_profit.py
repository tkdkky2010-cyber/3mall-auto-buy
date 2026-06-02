#!/usr/bin/env python3
"""20조합 재선별 — 수익×회전 가중 기준 (사용자 6/2).

후보 = 2·3종류, 소비자가 70~81만, qty 1~6 (= 113개).
점수 = Σ PRODUCT_PROFIT[c] × PRODUCT_VELOCITY[c] × q   (cart_plan _combo_score 와 동일).
상위 20 + 단일 f×5(#21) · h×3(#22) 번외(20 밖).

출력: 랭킹표 + _common.py COMBOS 형식(22개). 코드 자동수정 X — 검토 후 채택.
사용: python3 rate-check/_gen_combos_profit.py
"""
from __future__ import annotations
import sys, itertools
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "rate-check"))
import _common as C  # noqa: E402

LO, HI = 700_000, 810_000
SINGLES = [(("f", 5),), (("h", 3),)]  # 번외 #21, #22


def consumer(combo): return sum(C.PRODUCTS[c]["price"] * q for c, q in combo)
def score(combo): return sum(C.PRODUCT_PROFIT[c] * C.PRODUCT_VELOCITY[c] * q for c, q in combo)


def main():
    cands = []
    for r in (2, 3):
        for cs in itertools.combinations("bcdefgh", r):
            for qs in itertools.product(range(1, 7), repeat=r):
                combo = tuple(zip(cs, qs))
                if LO <= consumer(combo) <= HI:
                    cands.append(combo)
    cands.sort(key=lambda c: -score(c))
    top20 = cands[:20]

    print(f"[INFO] 2·3종류 후보 {len(cands)}개 → 수익×회전 점수 상위 20 + 단일 f5/h3 번외")
    print(f"\n제품 단위점수(PROFIT×VELOCITY): " +
          ", ".join(f"{c}:{C.PRODUCT_PROFIT[c]*C.PRODUCT_VELOCITY[c]:,}" for c in "bcdefgh"))
    print(f"\n{'#':>3} {'점수':>11} {'소비자가':>9}  조합")
    rows = list(top20) + [s for s in SINGLES]
    for i, combo in enumerate(rows, 1):
        tag = "  (번외 단일)" if i > 20 else ""
        print(f"{i:>3} {score(combo):>11,} {consumer(combo):>9,}  {C.combo_label_ko(list(combo))}{tag}")

    print("\n=== _common.py COMBOS 형식 (22개: 1~20 본선 + 21·22 번외) ===")
    print("COMBOS = [")
    for combo in rows:
        print("    " + repr([list(t) for t in combo]) + ",")
    print("]")


if __name__ == "__main__":
    main()
