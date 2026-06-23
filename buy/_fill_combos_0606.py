"""2026-06-06: 가용 13계정(#4~15, #19) 카트 담기 — 6조합 ×2 + 조합12 ×1(#19). 결제 X.

전제: #1·2·3·16 쿠폰 제한(+14,250), #17·18 오늘 h3 구매 완료 → 제외 (사용자 확정).
조합: 1=f5 / 3=e2+f2 / 5=f2+g2 / 12=e2+h1 / 14=g2+h1 / 23=h3
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
    (4, "1"), (5, "1"),
    (6, "3"), (7, "3"),
    (8, "5"), (9, "5"),
    (10, "12"), (11, "12"), (19, "12"),
    (12, "14"), (13, "14"),
    (14, "23"), (15, "23"),
]


def product_info(code: str) -> dict:
    entry = IDS[code]
    return {"name": entry["name"], "slitmCd": entry["hyundai"], "url_extra": "",
            "option_index": 1, "auto_coupon": True}


def combo_label(key: str) -> str:
    return " + ".join(f"{c}{q}" for c, q in COMBOS[key])


def fill_account(context, idx: int, key: str) -> tuple[int, int, bool]:
    account = ACCOUNTS[idx - 1]
    page = context.new_page()
    try:
        hmall._hmall_clean(context, page, deep=True)
        if not hmall.login(page, account["id"], account["pw"]):
            print(f"  [FAIL] #{idx} 로그인 실패")
            return (0, len(COMBOS[key]), False)
        hmall.clear_cart(page)
        ok = 0
        for code, qty in COMBOS[key]:
            print(f"    add {code} x{qty} ({IDS[code]['name']})")
            if hmall.add_to_cart(page, code, product_info(code), qty):
                ok += 1
            else:
                print(f"    [FAIL] add {code} x{qty}")
                break
            page.wait_for_timeout(800)
        return (ok, len(COMBOS[key]), True)
    finally:
        try:
            page.close()
        except Exception:
            pass


def main() -> int:
    wanted = {int(a) for a in sys.argv[1:] if a.isdigit()}
    plan = [(i, k) for i, k in PLAN if not wanted or i in wanted]

    ensure_chrome(int(hmall.CDP_PORT))
    print("=== 2026-06-06 카트 담기 (6조합×2 + 조합12@19) — 결제 X ===")
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
            print(f"  → {'✓' if ok == total and logged else '✗'} add {ok}/{total}")
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
