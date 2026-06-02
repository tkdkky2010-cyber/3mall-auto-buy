#!/usr/bin/env python3
"""현대몰 113조합 수식 스크리너 — 20조합 실측 preview 로 상품별 유효단가 역산 후
2·3종류 조합(+단일 f×5, h×3)을 수식으로 랭킹. (계정 #1 tkdkky2002 기준 — 쿠폰은 계정별 상이)

모델 (사용자 6/2 확정):
  preview(combo) = Σ unit[c]·q       # unit = 정가 × (1−합산쿠폰%) × (1−카드%), preview 에 카드% 이미 반영
  결제금액        = preview − 주문할인(--order-disc)   # 오늘=20,000원/주문 (월초 아이디별 1주문 한정)
  실비            = round(결제금액 × (1 − 페이백))      # 페이백 = 당일카드 stem (오늘 카카오페이+롯데 2%)
  공급률          = (실비 − 총샘플) / 소비자가          # 총샘플 = Σ추증 + GWP6

⚠️ 한계 (실측검증 2026-06-02):
- 잔차 ~0.5%(고수량 f 등) 비선형 잔존 → 스크리닝/랭킹용. 최종 채택 조합은 실측 권장(_remeasure_hmall.py).
- 미측정 상품(오늘 b)은 20조합에 없어 단가 미지 → --b4 로 b×4 preview 주입(오늘 690,023). 없으면 b 조합 제외.
- 쿠폰/할인은 *당일·계정별* 값 → 매일 fresh(시트 20조합 + b점). 코드에 daily 값 하드코딩 금지.

사용: python3 rate-check/_hmall_formula.py --order-disc 20000 --b4 690023        # 오늘 #1
      python3 rate-check/_hmall_formula.py --order-disc 20000 --b4 690023 --all  # 전체 115
"""
from __future__ import annotations
import sys, itertools
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "rate-check"))
import numpy as np  # noqa: E402
import _common as C  # noqa: E402

PAYBACK = C.CARD_PAYBACK["롯데카드"]   # 오늘 당일카드=카카오페이 롯데 → 2%. 다른 날은 당일카드 stem 으로.
LO, HI = 700_000, 810_000
SINGLES_ALLOWED = {("f", 5), ("h", 3)}  # 단일종류는 f×5, h×3 만 (사용자 6/2)


def _num(x):
    return int(str(x).replace(",", "").replace("%", "") or 0) if not isinstance(x, (int, float)) else int(x)


def _argval(argv, flag, default=0):
    return int(argv[argv.index(flag) + 1]) if flag in argv and argv.index(flag) + 1 < len(argv) else default


def main(argv=None):
    argv = argv or sys.argv[1:]
    show_all = "--all" in argv
    order_disc = _argval(argv, "--order-disc", 0)   # 오늘 20000
    b4 = _argval(argv, "--b4", 0)                    # b×4 preview (오늘 690023)

    ws = C.gs_client().open_by_key(C.RATE_SHEET_ID).worksheet(C.today_tab_name())
    START = C.HMALL_HEADER_ROW + 6
    block = ws.get(f"A{START}:M{START+len(C.COMBOS)-1}", value_render_option="UNFORMATTED_VALUE")
    comp = C.load_galleria_composition_from_sheet(ws)
    ADD = comp["add_gift_value"]; GWP6 = comp["gwp_6set"]

    codes = "bcdefgh"
    used = {c: 0 for c in codes}
    rows = []
    for i, combo in enumerate(C.COMBOS):
        preview = _num(block[i][9])
        qv = {c: 0 for c in codes}
        for c, q in combo:
            qv[c] += q; used[c] += q
        if preview:
            rows.append((qv, preview))
    present = [c for c in codes if used[c] > 0]

    # 선형 역산: preview = Σ unit[c]·q  (카드% 는 preview 에 이미 포함)
    A = np.array([[r[0][c] for c in present] for r in rows], float)
    y = np.array([r[1] for r in rows], float)
    sol, *_ = np.linalg.lstsq(A, y, rcond=None)
    unit = {c: float(sol[k]) for k, c in enumerate(present)}

    # 미측정 b: --b4 로 직접 주입 (unit_b = b4/4)
    if b4 and "b" not in unit:
        unit["b"] = b4 / 4.0
    have = set(unit)
    miss = [c for c in codes if c not in have]

    ratio = {c: unit[c] / C.PRODUCTS[c]["price"] for c in have}
    coup = {c: (1 - ratio[c] / (1 - 0.07)) * 100 for c in have}  # 합산쿠폰% (카드 7% 가정)
    print(f"[INFO] 계정 #1 기준 / 페이백 {PAYBACK*100:.0f}% / 주문할인 {order_disc:,} / GWP6 {GWP6:,}")
    print(f"[INFO] 역산 유효단가: { {c: round(unit[c]) for c in sorted(have)} }")
    print(f"[INFO] 합산쿠폰% 추정: { {c: round(coup[c],1) for c in sorted(have)} }  (b={('주입 b4=%d'%b4) if b4 else '미측정'})")
    if miss:
        print(f"[WARN] 미측정 {miss} → 해당 상품 포함 조합 제외 (--b4 로 b 주입 가능)")

    def consumer(combo): return sum(C.PRODUCTS[c]["price"] * q for c, q in combo)
    def rate(combo):
        preview = sum(unit[c] * q for c, q in combo)
        pay = preview - order_disc
        buy = round(pay * (1 - PAYBACK))
        총샘플 = sum(ADD.get(c, 0) * q for c, q in combo) + GWP6
        소 = consumer(combo)
        return ((buy - 총샘플) / 소 if 소 else 0), round(preview), buy

    cands = []
    for r in (1, 2, 3):
        for cs in itertools.combinations(codes, r):
            for qs in itertools.product(range(1, 7), repeat=r):
                combo = tuple(zip(cs, qs)); tot = consumer(combo)
                if not (LO <= tot <= HI):
                    continue
                if r == 1 and combo[0] not in SINGLES_ALLOWED:
                    continue
                if any(c not in have for c, _ in combo):
                    continue
                sr, prev, buy = rate(combo)
                hiq = any(q >= 5 for _, q in combo)
                cands.append({"combo": combo, "sr": sr, "소": tot, "prev": prev, "buy": buy, "hiq": hiq})
    cands.sort(key=lambda x: x["sr"])

    n2 = sum(1 for r in (2,) for cs in itertools.combinations(codes, r)
             for qs in itertools.product(range(1, 7), repeat=r)
             if LO <= consumer(tuple(zip(cs, qs))) <= HI)
    n3 = sum(1 for r in (3,) for cs in itertools.combinations(codes, r)
             for qs in itertools.product(range(1, 7), repeat=r)
             if LO <= consumer(tuple(zip(cs, qs))) <= HI)
    print(f"\n[INFO] 후보 {len(cands)}개 (2종류 {n2} + 3종류 {n3} + 단일 f5/h3 = 113+2, 미측정 제외분 빠짐)")
    n = len(cands) if show_all else 30
    print(f"\n=== 현대몰 #1 수식 공급률 랭킹 (상위 {n}) — ⚠️스크리닝(±~0.5%, ◆=고수량 불확실) ===")
    print(f"{'#':>3} {'공급률':>7} {'소비자':>8} {'preview':>8} {'실비':>8}  조합")
    for i, x in enumerate(cands[:n], 1):
        flag = "◆" if x["hiq"] else " "
        print(f"{i:>3} {x['sr']:>7.4f} {x['소']:>8,} {x['prev']:>8,} {x['buy']:>8,} {flag} {C.combo_label_ko(list(x['combo']))}")

    # --write ROW : 시트에 랭킹 기록 (A{ROW}부터). 롯데 섹션(~107) 아래 108부터 권장.
    write_row = _argval(argv, "--write", 0)
    if write_row:
        coup_s = ", ".join(f"{c}:{round(coup[c],1)}%" for c in sorted(have))
        grid = [
            [f"━━━━ 현대Hmall 수식 스크리너 — 계정 #1 / {C.today_tab_name()} / 주문할인 {order_disc:,}원 / 페이백 {PAYBACK*100:.0f}% ━━━━"],
            [f"합산쿠폰(정가기준): {coup_s}   ⚠️스크리닝 추정 ±~0.5% (◆고수량 불확실 / b=b4 주입)  공급률=((Σ유효단가·수량−주문할인)×(1−페이백)−총샘플)/소비자가"],
            ["순위", "공급률", "소비자가", "preview", "실비(2%후)", "조합", "비고"],
        ]
        for i, x in enumerate(cands, 1):
            bits = []
            if x["hiq"]:
                bits.append("고수량◆")
            if any(c == "b" for c, _ in x["combo"]):
                bits.append("b주입")
            grid.append([i, round(x["sr"], 4), x["소"], x["prev"], x["buy"],
                         C.combo_label_ko(list(x["combo"])), " ".join(bits)])
        ws.batch_clear([f"A{write_row}:G{write_row + 200}"])
        rng = C.write_grid(ws, write_row, grid)
        print(f"\n[WRITE] 시트 '{C.today_tab_name()}' {rng} 입력 ({len(cands)}조합)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
