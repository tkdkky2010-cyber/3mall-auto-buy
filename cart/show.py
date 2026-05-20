"""cart/today.json 빠른 확인 — Step 2 결과 한눈에.

사용:
    python3 cart/show.py                     # 기본 (전체)
    python3 cart/show.py --only-10pct        # 10% 적립 상품만
    python3 cart/show.py --raw 1             # 특정 ID raw JSON 덤프

표시 항목:
- # / 제품명 / 10%적립 / qty / 우수가 / 즉시할인가(H) / 실비(I) / tier수 / simple_range / 페이백카드
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TODAY = ROOT / "today.json"


def fmt_int(v) -> str:
    if v is None or v == "" or v == 0:
        return "—"
    try:
        return f"{int(v):,}"
    except (ValueError, TypeError):
        return str(v)


def fmt_simple_range(sr_list: list[dict]) -> str:
    if not sr_list:
        return "—"
    sr = sr_list[0]
    return f"{sr['min_won']:,} ~ {sr['max_won']:,} {sr['pct']}%"


def fmt_paybacks(pb: dict | None) -> str:
    if not pb:
        return "—"
    return ", ".join(f"{k}={v['final_cost']:,}" for k, v in pb.items())


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--only-10pct", action="store_true", help="10%% 적립 상품만")
    p.add_argument("--raw", type=int, metavar="ID", help="특정 상품 ID raw JSON 덤프")
    args = p.parse_args(argv)

    if not TODAY.exists():
        print(f"[ERR] {TODAY} 없음 — 먼저 'python3 cart/check10.py' 실행")
        return 1

    d = json.loads(TODAY.read_text(encoding="utf-8"))

    # raw 모드
    if args.raw is not None:
        prod = next((r for r in d["products"] if r["id"] == args.raw), None)
        if not prod:
            print(f"[ERR] ID {args.raw} 없음")
            return 1
        print(json.dumps(prod, ensure_ascii=False, indent=2))
        return 0

    # 헤더
    print(f"날짜: {d.get('date')}  계정: {d.get('account_used')}")
    print(f"10% 적립 상품 ID: {d.get('ten_percent_ids')}")
    print()

    # 표
    hdr = (
        f"{'#':>3} {'제품명':30s} {'10%':>3} {'qty':>3} "
        f"{'혜택가':>8} {'즉시할인가':>10} {'실비':>9} "
        f"{'tier':>4} {'simple_range':28s} {'페이백':30s}"
    )
    print(hdr)
    print("-" * len(hdr))

    counts = {"ok": 0, "no_10pct": 0, "err": 0, "skip": 0}
    for r in d["products"]:
        if r.get("error"):
            counts["err"] += 1
            if args.only_10pct:
                continue
            print(f"{r['id']:>3} {r['name'][:28]:30s} {'ERR':>3} "
                  f"{'—':>3} {'—':>8} {'—':>10} {'—':>9} {'—':>4} "
                  f"{r.get('error', '')[:28]:28s}")
            continue
        if not r.get("ten_percent"):
            counts["no_10pct"] += 1
            if args.only_10pct:
                continue
            print(f"{r['id']:>3} {r['name'][:28]:30s} {'✗':>3}")
            continue
        counts["ok"] += 1
        p_d = r.get("payment") or {}
        if not p_d:
            counts["skip"] += 1
        ts = len(r.get("tiers") or [])
        sr = fmt_simple_range(r.get("simple_ranges") or [])
        pb = fmt_paybacks(p_d.get("paybacks"))
        print(
            f"{r['id']:>3} {r['name'][:28]:30s} {'✓':>3} "
            f"{str(p_d.get('qty') or '—'):>3} "
            f"{fmt_int(p_d.get('benefit_unit_price') or p_d.get('list_price')):>8} "
            f"{fmt_int(p_d.get('kakao_price')):>10} {fmt_int(p_d.get('kakao_final_cost')):>9} "
            f"{ts:>4} {sr[:28]:28s} {pb[:30]:30s}"
        )

    print()
    print(f"요약: 10%적립 {counts['ok']} / 적립없음 {counts['no_10pct']} / 에러 {counts['err']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
