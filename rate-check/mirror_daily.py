#!/usr/bin/env python3
"""당일 출력물을 **재고관리 시트의 고정 탭에 미러**한다 (사용자 지시 2026-08-06).

기존 출력(공급률 시트의 날짜 탭 `M.D` / `M.DD 식품`)은 **그대로 두고**, 같은 내용을
재고관리 시트의 고정 탭에 **덮어쓴다**. 날짜별 탭을 새로 만들지 않는다 —
"다음날 결과도 이전날에 덮어써서" (사용자 문장 그대로).

  당일 설화수 = 공급률 시트 `M.D`      탭 (step1: 3사 조합표 + 카트플랜)
  당일 식품   = 공급률 시트 `M.DD 식품` 탭 (step2: 10% 적립 체크표)

★값은 **표시값(FORMATTED_VALUE)** 으로 복사한다. 수식을 그대로 옮기면 원본 시트의 다른 탭을
  가리키는 참조가 깨져 딴 값이 뜬다(조용한 오염).
★배경색도 같이 옮긴다 — step1 의 조합번호 색칠이 정보이기 때문(색 빠지면 어느 조합인지 안 보인다).

사용:
  python3 rate-check/mirror_daily.py sulwhasoo        # 오늘자 M.D → '당일 설화수'
  python3 rate-check/mirror_daily.py food             # 오늘자 M.DD 식품 → '당일 식품'
  python3 rate-check/mirror_daily.py both [--date 8.6]
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import INVENTORY_SHEET_ID, RATE_SHEET_ID, gs_client  # noqa: E402

DST_TAB = {"sulwhasoo": "당일 설화수", "food": "당일 식품"}


def _src_tab(kind: str, now: datetime) -> str:
    return f"{now.month}.{now.day}" if kind == "sulwhasoo" else f"{now.month}.{now.day:02d} 식품"


def _read_grid(sh, tab: str) -> tuple[list[list[str]], list[list[dict]]]:
    """탭의 (표시값, 배경색) 격자. gspread 만 사용 — googleapiclient 의존성 추가 안 함."""
    got = sh.fetch_sheet_metadata(params={
        "includeGridData": True, "ranges": [tab],
        "fields": "sheets(data(rowData(values(formattedValue,effectiveFormat/backgroundColor))))"})
    rows = got["sheets"][0]["data"][0].get("rowData", [])
    vals, bgs = [], []
    for r in rows:
        cells = r.get("values", [])
        vals.append([c.get("formattedValue", "") for c in cells])
        bgs.append([(c.get("effectiveFormat") or {}).get("backgroundColor") or {} for c in cells])
    return vals, bgs


def _white(bg: dict) -> bool:
    """흰색/무색이면 굳이 칠하지 않는다(요청 크기 축소)."""
    return all(abs(bg.get(k, 1) - 1) < 0.01 for k in ("red", "green", "blue")) or not bg


def mirror(kind: str, now: datetime | None = None) -> bool:
    now = now or datetime.now()
    src_tab, dst_tab = _src_tab(kind, now), DST_TAB[kind]
    gc = gs_client()
    try:
        vals, bgs = _read_grid(gc.open_by_key(RATE_SHEET_ID), src_tab)
    except Exception as e:
        print(f"  [mirror] ✗ 원본 탭 '{src_tab}' 읽기 실패 — {e}", flush=True)
        return False
    if not any(any(r) for r in vals):
        print(f"  [mirror] ✗ 원본 탭 '{src_tab}' 이 비어있다 — 덮어쓰지 않는다(기존 내용 보존)", flush=True)
        return False
    dst = gc.open_by_key(INVENTORY_SHEET_ID).worksheet(dst_tab)
    dst.clear()
    dst.update(vals, "A1", value_input_option="RAW")   # RAW = 수식 재해석 금지(표시값 그대로)
    _paint(dst, bgs)
    print(f"  [mirror] ✓ '{src_tab}' → 재고관리 '{dst_tab}' "
          f"({len(vals)}행 덮어씀)", flush=True)
    return True


def _paint(dst, bgs: list[list[dict]]) -> None:
    """배경색만 별도 batchUpdate. 흰색은 건너뛴다(요청 수 축소)."""
    reqs = []
    for ri, row in enumerate(bgs):
        for ci, bg in enumerate(row):
            if _white(bg):
                continue
            reqs.append({"repeatCell": {
                "range": {"sheetId": dst.id, "startRowIndex": ri, "endRowIndex": ri + 1,
                          "startColumnIndex": ci, "endColumnIndex": ci + 1},
                "cell": {"userEnteredFormat": {"backgroundColor": bg}},
                "fields": "userEnteredFormat.backgroundColor"}})
    if not reqs:
        return
    try:
        dst.spreadsheet.batch_update({"requests": reqs})
        print(f"  [mirror] 배경색 {len(reqs)}칸 복사", flush=True)
    except Exception as e:
        print(f"  [mirror] ⚠️ 배경색 복사 실패(값은 정상): {e}", flush=True)


def main() -> int:
    args = sys.argv[1:]
    kind = next((a for a in args if a in ("sulwhasoo", "food", "both")), "both")
    d = next((a.split("=", 1)[1] for a in args if a.startswith("--date=")), None)
    now = datetime.now()
    if d:                                        # '8.6' 형식 override (하루 늦게 돌릴 때)
        m, dd = d.split("."); now = now.replace(month=int(m), day=int(dd))
    kinds = ["sulwhasoo", "food"] if kind == "both" else [kind]
    return 0 if all(mirror(k, now) for k in kinds) else 1


if __name__ == "__main__":
    sys.exit(main())
