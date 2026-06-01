"""Hmall Sulwhasoo first-pass cart fill.

Each account gets only the first assigned combo from the current plan.
No checkout/payment is performed.
"""
from __future__ import annotations

import json
import sys
import time
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

ACCOUNT_DELAY_SEC = 0  # 설화수 = 계정 간 대기 없음 (7분 추적회피 정책은 Hmall 식품 buy/run.py 전용)

COMBOS: dict[str, list[tuple[str, int]]] = {
    "2": [("d", 1), ("f", 1), ("g", 2)],
    "4": [("e", 1), ("f", 2), ("g", 1)],
    "8": [("e", 2), ("f", 2)],
    "9": [("c", 1), ("f", 1), ("g", 2)],
    "13": [("c", 2), ("d", 1), ("f", 2)],
    "18": [("c", 1), ("d", 1), ("e", 2)],
    "22": [("c", 1), ("e", 2), ("f", 1)],
    "23": [("c", 2), ("g", 2)],
    "24": [("e", 1), ("g", 1), ("h", 1)],
    "h3": [("h", 3)],
}

FIRST_PASS_PLAN: list[tuple[int, str]] = [
    (1, "2"),
    (2, "13"),
    (3, "18"),
    (4, "h3"),
    (5, "4"),
    (7, "9"),
    (8, "22"),
    (9, "23"),
    (10, "22"),
    (11, "23"),
    (12, "22"),
    (13, "8"),
    (14, "24"),
    (15, "23"),
    (16, "22"),
    (17, "8"),
    (18, "24"),
    (19, "23"),
]


def mask_id(account_id: str) -> str:
    if "@" in account_id:
        user, domain = account_id.split("@", 1)
        return f"{user[:2]}***@{domain}"
    return account_id if len(account_id) <= 3 else f"{account_id[:2]}***{account_id[-1]}"


def product_info(code: str) -> dict:
    entry = IDS[code]
    return {
        "name": entry["name"],
        "slitmCd": entry["hyundai"],
        "url_extra": "",
        "option_index": 1,
        "auto_coupon": True,
    }


def combo_label(combo_key: str) -> str:
    return " + ".join(f"{code}{qty}" for code, qty in COMBOS[combo_key])


def fill_account(context, idx: int, combo_key: str) -> tuple[int, int, bool]:
    account = ACCOUNTS[idx - 1]
    page = context.new_page()
    try:
        hmall._hmall_clean(context, page, deep=True)
        if not hmall.login(page, account["id"], account["pw"]):
            print(f"  [FAIL] #{idx} {mask_id(account['id'])} 로그인 실패")
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
    plan = [(idx, combo_key) for idx, combo_key in FIRST_PASS_PLAN if not wanted or idx in wanted]
    if not plan:
        print(f"no matching accounts: {sorted(wanted)}")
        return 1

    ensure_chrome(int(hmall.CDP_PORT))
    print("=== Hmall Sulwhasoo first cart pass ===")
    print("checkout/payment: disabled")
    print(f"accounts: {[idx for idx, _ in plan]}")
    print("")

    summary: list[tuple[int, str, int, int, bool]] = []
    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp(hmall.CDP_ENDPOINT, slow_mo=400)
        context = browser.contexts[0] if browser.contexts else browser.new_context()

        for pos, (idx, combo_key) in enumerate(plan, 1):
            account = ACCOUNTS[idx - 1]
            print(f"\n[{pos}/{len(plan)}] #{idx} {mask_id(account['id'])} combo {combo_key}: {combo_label(combo_key)}")
            try:
                ok, total, logged = fill_account(context, idx, combo_key)
            except Exception as e:
                print(f"  [FATAL] #{idx}: {e}")
                ok, total, logged = 0, len(COMBOS[combo_key]), False
            summary.append((idx, combo_key, ok, total, logged))
            if pos < len(plan):
                print(f"  [WAIT] {ACCOUNT_DELAY_SEC}s")
                time.sleep(ACCOUNT_DELAY_SEC)

    print("\n=== SUMMARY ===")
    failures = 0
    for idx, combo_key, ok, total, logged in summary:
        mark = "OK" if ok == total and logged else "FAIL"
        if mark != "OK":
            failures += 1
        print(f"  {mark} #{idx:2d} combo={combo_key:>2s} add={ok}/{total}")
    print(f"done: {len(summary) - failures}/{len(summary)} accounts")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
