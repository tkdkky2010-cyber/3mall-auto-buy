#!/usr/bin/env python3
"""Hmall 특정 조합만 재측정 + 해당 행/차트셀만 패치 (batch_clear 없음).

hmall.py 의 단일조합 실행(`hmall.py 10`)은 write_sheet 가 A53:M78 전체를 batch_clear
한 뒤 그 1조합만 쓰므로 나머지 19조합이 날아간다. 이 헬퍼는 지정한 조합만 재측정하고
그 조합의 행(A{58+idx}:M{58+idx})과 비교차트 L 셀(L{1+idx})만 update → 나머지 무손상.

사용: python3 rate-check/_remeasure_hmall.py 10 16 18 19
      (인자 없으면 10 16 18 19 기본)
"""
from __future__ import annotations
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "buy"))
sys.path.insert(0, str(ROOT / "rate-check"))

import hmall  # noqa: E402  (top-level sys.path 셋업 재사용)
import _common as C  # noqa: E402
from chrome_launcher import ensure_chrome  # noqa: E402

# 데이터 행: header 6행(53~58) 다음부터 → 조합 idx 의 시트 행 = 58 + idx
DATA_ROW0 = C.HMALL_HEADER_ROW + 6  # 59 (= combo 1)


def _row_values(r: dict) -> list:
    if r.get("error"):
        return [r["idx"], r["error"], "", "", "", "", "", "", "", "", "", "", ""]
    cn = C.combo_label_ko(r["combo"])
    return [
        r["idx"], cn, r["소비자가"], r["추증"], r["GWP"], r["총샘플"],
        r["card_brand"], f"{r['card_pct']}%", f"{r['payback_pct']*100:.1f}%",
        r["preview_price"], r["구매가격"], r["순구매가"], r["공급률"],
    ]


def main(argv=None):
    argv = argv or sys.argv[1:]
    targets = [int(a) for a in argv if a.isdigit()] or [10, 16, 18, 19]
    n_combos = len(C.COMBOS)
    targets = [i for i in targets if 1 <= i <= n_combos]
    print(f"[INFO] 재측정 대상 조합: {targets}")

    # composition — sheet 에서 직접 (캐시 X, sheet=SoT)
    gc = C.gs_client()
    sh = gc.open_by_key(C.RATE_SHEET_ID)
    tab = C.today_tab_name()
    ws = sh.worksheet(tab)
    comp = C.load_galleria_composition_from_sheet(ws)
    add_gift_value = comp["add_gift_value"]
    gwp_6set = comp["gwp_6set"]
    print(f"[INFO] composition (sheet '{tab}'): GWP 6세트={gwp_6set:,}원")

    ensure_chrome(C.CDP_PORT)
    sync_playwright = hmall._pick_cdp_backend(hmall.CDP_ENDPOINT)
    results: list[dict] = []
    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp(hmall.CDP_ENDPOINT, timeout=20000)
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        page = context.new_page()
        page.set_default_timeout(25000)

        # 로그인 검증 (hmall.main 과 동일)
        page.goto("https://www.hmall.com/mo/mma/myhome", wait_until="domcontentloaded")
        page.wait_for_timeout(1500)
        if "loginForm" in page.url or "/login" in page.url:
            import json
            cfg = json.load(open(ROOT / "hmall_config.json"))
            acc = cfg["accounts"][0]
            if not hmall.login(page, acc["id"], acc["pw"]):
                sys.exit("❌ 로그인 실패")
            page.wait_for_timeout(1500)
        else:
            print("[INFO] 이미 로그인 상태")

        for idx in targets:
            combo = C.COMBOS[idx - 1]
            try:
                r = hmall.process_combo(page, idx, combo, add_gift_value, gwp_6set)
            except Exception as e:
                r = {"idx": idx, "error": f"예외: {e}"}
            results.append(r)

    # 결과 요약 + 해당 행/L셀만 패치
    print("\n=== 재측정 결과 ===")
    for r in results:
        row = DATA_ROW0 + (r["idx"] - 1)          # 시트 행 = 58 + idx
        lcell = 1 + r["idx"]                        # L1=header, L{1+idx}=combo idx
        vals = _row_values(r)
        ws.update(values=[vals], range_name=f"A{row}:M{row}", value_input_option="USER_ENTERED")
        if r.get("error"):
            ws.update(values=[[""]], range_name=f"L{lcell}", value_input_option="USER_ENTERED")
            print(f"  {r['idx']:>3d} (row {row}) ERR: {r['error']}")
        else:
            ws.update(values=[[round(r["공급률"], 4)]], range_name=f"L{lcell}", value_input_option="USER_ENTERED")
            print(f"  {r['idx']:>3d} (row {row}) {r['card_brand']} {r['card_pct']}% "
                  f"구매 {r['구매가격']:,} 순 {r['순구매가']:,} 공급률 {r['공급률']:.4f}  → L{lcell} 패치")

    ok = [r for r in results if not r.get("error")]
    print(f"\n[DONE] {len(ok)}/{len(results)} 성공 — 해당 행/L셀만 갱신 (나머지 조합 무손상)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
