"""2026-06-05 저녁: 계정 1~18 카트 비우고 6조합 × 3계정씩 담기 (카트만, 결제 X).

조합(시트 번호): 1=f5 / 3=e2+f2 / 5=f2+g2 / 12=e2+h1 / 14=g2+h1 / 23=h3
배정: 1~3=조합1, 4~6=조합3, 7~9=조합5, 10~12=조합12, 13~15=조합14, 16~18=조합23
#6 이번 회차 사용 허용 (사용자 지시).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "buy"))

from chrome_launcher import ensure_chrome
from playwright.sync_api import sync_playwright

import run as hmall

try:
    sys.stdout.reconfigure(line_buffering=True)
except Exception:
    pass

IDS = json.loads((ROOT / "hsmaster" / "config" / "sulwhasoo-ids.json").read_text(encoding="utf-8"))["ids"]
ACCOUNTS = json.loads(Path(hmall.ACCOUNTS_FILE).read_text(encoding="utf-8"))["accounts"]

COMBOS: dict[str, list[tuple[str, int]]] = {
    "1": [("f", 5)],
    "3": [("e", 2), ("f", 2)],
    "5": [("f", 2), ("g", 2)],
    "12": [("e", 2), ("h", 1)],
    "14": [("g", 2), ("h", 1)],
    "23": [("h", 3)],
}

PLAN: list[tuple[int, str]] = [
    (1, "1"), (2, "1"), (3, "1"),
    (4, "3"), (5, "3"), (6, "3"),
    (7, "5"), (8, "5"), (9, "5"),
    (10, "12"), (11, "12"), (12, "12"),
    (13, "14"), (14, "14"), (15, "14"),
    (16, "23"), (17, "23"), (18, "23"),
]


def product_info(code: str) -> dict:
    entry = IDS[code]
    return {"name": entry["name"], "slitmCd": entry["hyundai"], "url_extra": "",
            "option_index": 1, "auto_coupon": True}


def combo_label(combo_key: str) -> str:
    return " + ".join(f"{code}{qty}" for code, qty in COMBOS[combo_key])


def fill_account(context, idx: int, combo_key: str) -> tuple[int, int, bool]:
    account = ACCOUNTS[idx - 1]
    page = context.new_page()
    try:
        hmall._hmall_clean(context, page, deep=True)
        if not hmall.login(page, account["id"], account["pw"]):
            print(f"  [FAIL] #{idx} 로그인 실패")
            return (0, len(COMBOS[combo_key]), False)

        hmall.clear_cart(page)
        ok = 0
        for code, qty in COMBOS[combo_key]:
            print(f"    add {code} x{qty} ({IDS[code]['name']})")
            if hmall.add_to_cart(page, code, product_info(code), qty):
                ok += 1
            else:
                print(f"    [FAIL] add {code} x{qty}")
                break
            page.wait_for_timeout(800)
        return (ok, len(COMBOS[combo_key]), True)
    finally:
        try:
            page.close()
        except Exception:
            pass


def main() -> int:
    wanted = {int(a) for a in sys.argv[1:] if a.isdigit()}
    plan = [(idx, key) for idx, key in PLAN if not wanted or idx in wanted]

    ensure_chrome(int(hmall.CDP_PORT))
    print("=== 6조합 × 3계정 카트 담기 (결제 X) ===")
    print(f"accounts: {[i for i, _ in plan]}\n")

    summary = []
    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp(hmall.CDP_ENDPOINT, slow_mo=400)
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        for pos, (idx, key) in enumerate(plan, 1):
            print(f"[{pos}/{len(plan)}] 계정 #{idx} — 조합{key}: {combo_label(key)}")
            try:
                ok, total, logged = fill_account(context, idx, key)
            except Exception as e:
                print(f"  [FATAL] #{idx}: {e}")
                ok, total, logged = 0, len(COMBOS[key]), False
            mark = "✓" if ok == total and logged else "✗"
            print(f"  → {mark} add {ok}/{total}")
            summary.append((idx, key, ok, total, logged))

    print("\n=== SUMMARY ===")
    fails = 0
    for idx, key, ok, total, logged in summary:
        mark = "OK" if ok == total and logged else "FAIL"
        if mark == "FAIL":
            fails += 1
        print(f"  {mark} #{idx:2d} 조합{key:>2s} ({combo_label(key)}) add={ok}/{total}")
    print(f"done: {len(summary) - fails}/{len(summary)} accounts")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
