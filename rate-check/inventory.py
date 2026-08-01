"""재고관리 시트 비교 + 자동 새 버전 추가.

galleria.py가 공급률 시트(통합 탭)에 쓴 데이터를 sheet에서 직접 읽어 재고관리 시트와 비교.
**캐시 JSON 파일 절대 사용 X** — sheet가 SoT, 매 실행 fresh.

Dry-run (디폴트):
    python3 rate-check/inventory.py
    → 활성 버전 + 차이 보고만, 시트 미수정

자동 새 버전 추가:
    python3 rate-check/inventory.py --apply
    → 차이 발견 시 가이드 섹션 14-1 절차로 새 버전 (b→c→d→...) 추가:
       - 조합별입고수량 시트에 20개 행 추가
       - MAP A열에 라벨 추가
       - IN A+B열에 라벨+조합명 추가

가이드 섹션 14의 절대 규칙 준수:
    - 매핑 시트 건드리지 않음
    - 재고현황 D열·G열·H~열 수식 건드리지 않음
    - MAP B열~ SUMIFS 수식 건드리지 않음
    - IN 1행 날짜 헤더 건드리지 않음
    - 기존 데이터 절대 덮어쓰지 않음 (마지막 행 다음 빈 행에만 추가)
"""
from __future__ import annotations
import argparse
import json
import re
import string
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import _common as C


def load_composition_from_sheet() -> dict:
    """공급률 시트(통합 탭)에서 당일 composition 읽기 — 캐시 JSON 절대 사용 X.

    galleria가 sheet에 쓴 결과를 직접 파싱. galleria 미실행이면 ValueError.
    """
    gc = C.gs_client()
    sh = gc.open_by_key(C.RATE_SHEET_ID)
    ws = sh.worksheet(C.today_tab_name())
    products_samples = C.load_galleria_samples_from_sheet(ws)
    gwp_set = C.load_galleria_gwp_from_sheet(ws)
    if not gwp_set:
        raise ValueError("galleria sheet에서 GWP set 못찾음 — galleria 먼저 실행 필요")
    return {
        "products": {c: {"add_gifts": products_samples.get(c, [])} for c in C.PRODUCT_CODES},
        "gwp": {"set": gwp_set},
    }


# ============================================================
# 당일 구성 → 조합별 코드 dict 변환
# ============================================================
def composition_to_combo_codes(comp: dict, gwp_sets_per_combo: int = 4) -> dict[int, dict[str, int]]:
    """1조합 → {코드: 수량} dict.

    구성 = 본품 + (상품별 추가증정 × 본품수량) + (GWP 1세트 × gwp_sets_per_combo)
    같은 코드끼리 합산.

    gwp_sets_per_combo: 2026-07-28 프로모션 변경 — 본품가>700K → 4세트, 400K~700K → 2세트.
    실제로 전 조합 700K+ 가정 (Layer 1 조합 정의 기준) — 디폴트 4.
    """
    products = comp["products"]
    gwp_set = comp["gwp"]["set"]

    out: dict[int, dict[str, int]] = {}
    for idx, combo in enumerate(C.COMBOS, start=1):
        codes: dict[str, int] = defaultdict(int)
        # 본품
        for code, qty in combo:
            codes[code] += qty
        # 상품별 추가증정 (본품 수량만큼 곱)
        for code, qty in combo:
            for s in products.get(code, {}).get("add_gifts", []):
                if s.get("code"):  # s코드 있을 때만 (단가표 매칭된 것)
                    codes[s["code"]] += s["qty"] * qty
        # GWP (조합당 N세트)
        for s in gwp_set:
            if s.get("code"):
                codes[s["code"]] += s["qty"] * gwp_sets_per_combo
        out[idx] = dict(codes)
    return out


# ============================================================
# 시트에서 활성 버전 읽기
# ============================================================
SUFFIX_LETTERS = string.ascii_lowercase  # b, c, d, ...


def find_active_version(map_a_col: list[str]) -> str | None:
    """MAP A열에서 마지막 활성 버전의 접미사 반환.

    A열 값 예시:
      "1", "2", ..., "11"          (월초, 접미사 없음 → None 반환)
      "1b", "2b", ..., "11b"      ("b" 반환)
      "11d"                        ("d" 반환)

    숫자 + 알파벳 접미사 매치만 찾음. 마지막 매치의 접미사.
    """
    last_suffix = None
    for v in map_a_col:
        v = v.strip()
        m = re.fullmatch(r"(\d+)([a-z])", v)
        if m and 1 <= int(m.group(1)) <= 16:
            last_suffix = m.group(2)
        elif re.fullmatch(r"\d+", v):
            # 접미사 없는 행 — 월초
            if last_suffix is None and 1 <= int(v) <= 16:
                last_suffix = ""
    return last_suffix


def next_suffix(current: str | None) -> str:
    """현재 접미사 → 다음 접미사. None/'' → 'b', 'b' → 'c', ..."""
    if not current:
        return "b"
    idx = SUFFIX_LETTERS.index(current)
    if idx + 1 >= len(SUFFIX_LETTERS):
        raise ValueError(f"접미사 한계 도달: {current}")
    return SUFFIX_LETTERS[idx + 1]


def read_combo_quantities(rows: list[list[str]], suffix: str) -> dict[int, dict[str, int]]:
    """조합별입고수량 시트의 raw rows → {조합번호: {코드: 수량}}.

    suffix == "" → 접미사 없는 행 (월초)
    suffix == "d" → 1d~16d 행 (16조합 확장 2026-05-16, 과거 11조합 버전 행도 매칭됨)
    """
    out: dict[int, dict[str, int]] = defaultdict(dict)
    pattern = re.compile(rf"^(\d+){re.escape(suffix)}$")
    for row in rows:
        if len(row) < 4:
            continue
        a = row[0].strip()
        m = pattern.fullmatch(a)
        if not m:
            continue
        idx = int(m.group(1))
        if not (1 <= idx <= len(C.COMBOS)):
            continue
        code = row[1].strip()
        try:
            qty = int(row[3])
        except (ValueError, IndexError):
            continue
        if code:
            out[idx][code] = out[idx].get(code, 0) + qty
    return dict(out)


def find_last_data_row(rows: list[list[str]]) -> int:
    """A열 값이 있는 마지막 행 번호 (1-based)."""
    last = 0
    for i, row in enumerate(rows, start=1):
        if row and row[0].strip():
            last = i
    return last


# ============================================================
# 비교
# ============================================================
def diff_combos(today: dict[int, dict[str, int]],
                actual: dict[int, dict[str, int]]) -> dict[int, list[tuple[str, int, int]]]:
    """조합별 차이 (today - actual). 빈 dict == 차이 없음.

    반환: {조합번호: [(코드, expected, actual_qty), ...]}
    """
    out: dict[int, list] = {}
    for idx in range(1, len(C.COMBOS) + 1):
        e = today.get(idx, {})
        a = actual.get(idx, {})
        diffs = []
        for code in sorted(set(e) | set(a)):
            ev = e.get(code, 0)
            av = a.get(code, 0)
            if ev != av:
                diffs.append((code, ev, av))
        if diffs:
            out[idx] = diffs
    return out


# ============================================================
# 새 버전 추가 (--apply)
# ============================================================
def combo_label(idx: int) -> str:
    """조합 idx → "본윤2종×2 + 자음생2종×2" 형식."""
    parts = []
    for code, qty in C.COMBOS[idx - 1]:
        parts.append(f"{C.PRODUCTS[code]['name']}×{qty}")
    return " + ".join(parts)


def append_combo_rows(ws, today_combos: dict[int, dict[str, int]],
                     suffix: str, start_row: int) -> str:
    """조합별입고수량 시트에 새 버전 N조합 행 추가 (N = len(C.COMBOS)).

    각 코드별로 한 행. A=라벨, B=코드, C=상품명(빈), D=수량.
    """
    grid: list[list] = []
    for idx in range(1, len(C.COMBOS) + 1):
        for code in sorted(today_combos.get(idx, {})):
            qty = today_combos[idx][code]
            label = f"{idx}{suffix}"
            name = C.PRODUCTS[code]["name"] if code in C.PRODUCTS else ""  # 본품만 자동
            grid.append([label, code, name, qty])
    end_row = start_row + len(grid) - 1
    # grid 한도 초과 시 행 추가 (sheet 의 max row 가 데이터로 가득 차있는 경우)
    if end_row > ws.row_count:
        ws.add_rows(end_row - ws.row_count)
    rng = f"A{start_row}:D{end_row}"
    ws.update(values=grid, range_name=rng, value_input_option="USER_ENTERED")
    return rng


def append_map_in_labels(map_ws, in_ws, suffix: str) -> tuple[str, str]:
    """MAP·IN 시트 A열에 1{suffix}~N{suffix} 라벨 추가 (N = len(C.COMBOS)).

    각 시트의 A열 마지막 사용 행 다음부터 N행 추가.
    MAP은 A열만, IN은 A+B열 (B는 조합명).
    """
    n = len(C.COMBOS)
    # MAP
    map_a = [r[0] if r else "" for r in map_ws.get_all_values()]
    map_start = find_last_data_row([[v] for v in map_a]) + 1
    map_grid = [[f"{idx}{suffix}"] for idx in range(1, n + 1)]
    map_rng = f"A{map_start}:A{map_start + n - 1}"
    map_ws.update(values=map_grid, range_name=map_rng, value_input_option="USER_ENTERED")

    # IN
    in_rows = in_ws.get_all_values()
    in_a = [r[0] if r else "" for r in in_rows]
    in_start = find_last_data_row([[v] for v in in_a]) + 1
    in_grid = [[f"{idx}{suffix}", combo_label(idx)] for idx in range(1, n + 1)]
    in_rng = f"A{in_start}:B{in_start + n - 1}"
    in_ws.update(values=in_grid, range_name=in_rng, value_input_option="USER_ENTERED")

    return map_rng, in_rng


# ============================================================
# CLI
# ============================================================
def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--apply", action="store_true",
                   help="차이 발견 시 새 버전 자동 추가 (디폴트: dry-run)")
    p.add_argument("--gwp-sets", type=int, default=4,
                   help="조합당 GWP 세트 수 (2026-07-28~ 기본 4, 400K~700K 조합이면 2)")
    args = p.parse_args(argv)

    date = datetime.now().strftime("%Y-%m-%d")
    print(f"▶ 재고관리 비교 ({date}) — {'APPLY' if args.apply else 'DRY-RUN'}")

    # 1) 당일 구성 로드 — sheet에서 직접 (캐시 X)
    comp = load_composition_from_sheet()
    today_combos = composition_to_combo_codes(comp, args.gwp_sets)
    print(f"  당일 구성 로드 (sheet): {len(today_combos)} 조합, GWP {args.gwp_sets}세트/조합")

    # 2) 시트 접속 + 활성 버전 찾기
    gc = C.gs_client()
    sh = gc.open_by_key(C.INVENTORY_SHEET_ID)
    map_ws = sh.worksheet("MAP")
    qty_ws = sh.worksheet("조합별입고수량")
    in_ws = sh.worksheet("IN")

    map_a_col = [r[0] for r in map_ws.get_all_values() if r]
    suffix = find_active_version(map_a_col)
    print(f"  MAP 활성 버전 접미사: {suffix!r} (' ' = 월초 접미사 없음)")
    if suffix is None:
        print(f"  ⚠️ MAP A열에 1~{len(C.COMBOS)}번 행 없음 — 월초 리셋 필요할 수 있음")
        return 1

    # 3) 활성 버전 행 읽기
    qty_rows = qty_ws.get_all_values()
    actual_combos = read_combo_quantities(qty_rows, suffix)
    print(f"  활성 버전 {suffix!r} 조합 수: {len(actual_combos)}")

    # 4) 비교
    diffs = diff_combos(today_combos, actual_combos)
    if not diffs:
        print(f"  ✓ 전체 일치 — 변경 없음, 활성 버전 {suffix!r} 사용")
        return 0

    print(f"\n  ⚠️ 차이 발견: {len(diffs)}개 조합")
    for idx in sorted(diffs):
        print(f"    조합 {idx}{suffix}:")
        for code, e, a in diffs[idx]:
            print(f"      {code}: 기대(당일)={e}  현재(시트)={a}")

    # 5) Apply 여부
    new_suf = next_suffix(suffix)
    if not args.apply:
        print(f"\n  DRY-RUN — 새 버전 {new_suf!r} 추가하려면 --apply 추가")
        return 0

    print(f"\n  → 새 버전 {new_suf!r} 추가 중...")
    qty_start = find_last_data_row(qty_rows) + 1
    qty_rng = append_combo_rows(qty_ws, today_combos, new_suf, qty_start)
    print(f"    조합별입고수량: {qty_rng}")
    map_rng, in_rng = append_map_in_labels(map_ws, in_ws, new_suf)
    print(f"    MAP: {map_rng}")
    print(f"    IN:  {in_rng}")
    print(f"  ✓ 새 버전 {new_suf!r} 생성 완료. 오늘부터 IN 시트의 새 버전 행에 구매 입력.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
