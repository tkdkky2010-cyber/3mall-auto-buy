"""2026-06-18: 전 19계정 카트 담기 — 조합14 (g2+h1) 단일. 결제 X.

사용자 지시: "g2+h1 모든 아이디에 장바구니 담아" (자정/탄력 라인 판매 부진 → e2+h1 에서 변경).
계정마다 clear_cart 선행 → 이전 e2+h1 카트는 자동으로 g2+h1 로 덮어써짐.
패턴은 _fill_combos_0606.py 와 동일 (run.py login/clear_cart/add_to_cart 재사용).
계정 일부만 담으려면: python3 buy/_fill_combos_0618.py 4 5 6  (계정 번호 나열)
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
    "14": [("g", 2), ("h", 1)],
}

PLAN: list[tuple[int, str]] = [(i, "14") for i in range(1, len(ACCOUNTS) + 1)]


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
    print("=== 2026-06-18 카트 담기 (조합14 g2+h1, 전 계정) — 결제 X ===")
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
