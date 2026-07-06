#!/usr/bin/env python3
"""우수스토어 식품 단독결제 오케스트레이터 (실돈).

계정별로 각 상품을 **단독 결제**(쿠폰상품 1주문 1상품) 한다.
1상품 = [PC 카트=그 상품 1종] → [폰 force-stop+재실행(광고클리어)+flow_payment 결제]
        → [폰 구매후 광고 닫기] → [PC H.Point 적립신청(prmo 있으면)].
계정 간 7분 대기 / 계정 내 상품 간 텀 없음.

resume: 완료된 (계정,상품)을 _food_done.json 에 기록 → 재실행 시 스킵(중복결제 방지).
실패 시 그 상품에서 중단(이후 진행 X) — 틀린 부분부터 재개 가능.

사용:
    python3 buy/food_buy.py 2            # 계정2 (양 상품)
    python3 buy/food_buy.py 2 3 4 5      # 2~5 (계정 간 7분)
    python3 buy/food_buy.py 7-12         # 범위
"""
from __future__ import annotations
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ADB = "/opt/homebrew/bin/adb"
PHONE_PY = "/opt/homebrew/bin/python3"   # phone_auto (brew python)
BROWSER_PY = "python3"                    # run.py (system python, playwright)
HMALL = "com.hmallapp"
ACCOUNT_DELAY = 7 * 60
STATE = ROOT / "buy/_food_done.json"
PLAN_FILE = ROOT / "buy/cart_plan.json"            # 매일 갱신되는 계정별 플랜 (SoT)
PLAN_TMP = ROOT / "buy/_cart_plan_food_tmp.json"   # 단독결제용 임시 단일상품 카트 (run.py 전용, PLAN_FILE 보존)

# 계정별 상품(단독 순서) — cart_plan.json 에서 로드. ★하드코딩 금지(플랜은 매일 바뀜).
# cart_plan.json items:[{product,accounts,qty}] → 계정→[상품id], (계정,상품)→수량 으로 역매핑.
_plan = json.loads(PLAN_FILE.read_text(encoding="utf-8"))
PLAN: dict[int, list[int]] = {}
QTY_BY: dict[tuple[int, int], int] = {}
for _it in _plan.get("items", []):
    _pid = int(_it["product"])
    _qty = int(_it.get("qty", 1))
    for _acc in _it["accounts"]:
        _acc = int(_acc)
        PLAN.setdefault(_acc, []).append(_pid)
        QTY_BY[(_acc, _pid)] = _qty

# 상품별 적립 prmo (today.json)
PRMO: dict[int, list[str]] = {}
for _p in json.loads((ROOT / "cart/today.json").read_text(encoding="utf-8")).get("products", []):
    _evs = _p.get("events") or []
    if _evs:
        PRMO[_p["id"]] = [e["prmo"] for e in _evs if e.get("prmo")]


def _sh(cmd, timeout=60):
    return subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, timeout=timeout)


def _done() -> set:
    if STATE.exists():
        return {tuple(x) for x in json.loads(STATE.read_text())}
    return set()


def _mark_done(acct, pid):
    s = _done(); s.add((acct, pid))
    STATE.write_text(json.dumps(sorted(s)))


def pc_cart_set(acct: int, pid: int) -> tuple[bool, str]:
    """PC: 카트 비우고 그 상품 1종만 담기 (CART_ONLY).
    단일상품 카트는 임시파일(PLAN_TMP)에 쓰고 run.py 에 CART_PLAN_FILE 로 넘긴다 —
    cart_plan.json(마스터 플랜)은 건드리지 않음(재개 시 PLAN 훼손 방지)."""
    qty = QTY_BY.get((acct, pid), 1)
    PLAN_TMP.write_text(json.dumps(
        {"date": "food-single", "_comment": "단독결제 임시(run.py 전용)", "items": [{"product": pid, "accounts": [acct], "qty": qty}]},
        ensure_ascii=False, indent=2), encoding="utf-8")
    r = subprocess.run([BROWSER_PY, "buy/run.py", str(acct)], cwd=str(ROOT),
                       env={**os.environ, "CART_ONLY": "true", "DRY_PAYMENT": "true",
                            "CART_PLAN_FILE": str(PLAN_TMP)},
                       capture_output=True, text=True, timeout=300)
    ok = ("담기: 1/1" in r.stdout) or ("cart-only 담기 1/1" in r.stdout)
    return ok, r.stdout[-300:]


def phone_reset():
    """폰 hmall force-stop + 재실행 → 깨끗한 메인(이전 광고/주문화면 클리어)."""
    _sh([ADB, "shell", "am", "force-stop", HMALL]); time.sleep(2)
    _sh([ADB, "shell", "monkey", "-p", HMALL, "-c", "android.intent.category.LAUNCHER", "1"]); time.sleep(4)


def phone_pay(acct: int) -> tuple[bool, str]:
    """폰 인앱 결제 (hmall_hyundai_buy 단일계정 — 주문완료검증+뷰티+당일카드).
    ※ 정식 진입점은 루트 buy.py. 이 모듈은 식품 cart-set+resume 레거시."""
    r = subprocess.run([PHONE_PY, "-m", "phone_auto.hmall_hyundai_buy", str(acct)], cwd=str(ROOT),
                       env={**os.environ, "PYTHON_BIN": PHONE_PY}, capture_output=True, text=True, timeout=900)
    tail = r.stdout[-700:]
    ok = ("=> DONE" in r.stdout) or ("SKIP_EMPTY" in r.stdout)
    return ok, tail


def phone_dismiss_ad():
    """구매후 광고 닫기 ('오늘 그만 보기' 우선, 없으면 '닫기')."""
    _sh([ADB, "shell", "uiautomator", "dump", "/sdcard/_ad.xml"])
    xml = _sh([ADB, "shell", "cat", "/sdcard/_ad.xml"]).stdout
    for label in ("오늘 그만 보기", "닫기"):
        for m in re.finditer(rf'text="{label}"[^/]*?bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', xml):
            x1, y1, x2, y2 = map(int, m.groups())
            if (x2 - x1) > 40 and (y2 - y1) > 30:        # 실제 버튼(미세노드 제외)
                _sh([ADB, "shell", "input", "tap", str((x1 + x2) // 2), str((y1 + y2) // 2)])
                time.sleep(1.2)
                return label
    return None


def pc_apply_reward(acct: int, pid: int) -> tuple[bool, str]:
    """PC: H.Point 적립신청 (prmo 있는 상품만)."""
    prmos = PRMO.get(pid, [])
    if not prmos:
        return True, "prmo 없음(적립신청 불필요)"
    code = (
        "import sys; sys.path.insert(0,'buy'); import run\n"
        "from playwright.sync_api import sync_playwright\n"
        f"acc=run.load_json(run.ACCOUNTS_FILE)['accounts'][{acct}-1]\n"
        "with sync_playwright() as p:\n"
        " br=p.chromium.connect_over_cdp(run.CDP_ENDPOINT); ctx=br.contexts[0] if br.contexts else br.new_context(); page=ctx.new_page()\n"
        " run._hmall_clean(ctx,page,deep=True)\n"
        " ok=run.login(page,acc['id'],acc['pw']); print('login',ok)\n"
        f" for prmo in {prmos}:\n"
        "  print('HP', prmo, run.apply_hpoint(page,prmo))\n"
        " page.close()\n"
    )
    r = subprocess.run([BROWSER_PY, "-c", code], cwd=str(ROOT), capture_output=True, text=True, timeout=180)
    ok = ("신청 완료" in r.stdout) or ("already_done': True" in r.stdout) or ("'success': True" in r.stdout)
    return ok, r.stdout[-300:]


def do_account(acct: int) -> bool:
    pids = PLAN.get(acct)
    if not pids:
        print(f"  [SKIP] 계정 {acct} 플랜 없음"); return True
    print(f"\n{'='*52}\n[계정 #{acct}] 상품 {pids} (단독결제)")
    done = _done()
    for pid in pids:
        if (acct, pid) in done:
            print(f"  -- product {pid}: 이미 완료 — skip"); continue
        print(f"  -- product {pid} --", flush=True)
        ok, log = pc_cart_set(acct, pid); print(f"  [PC카트] {ok}", flush=True)
        if not ok:
            print(f"  ❌ 카트 실패 — 중단\n{log}"); return False
        phone_reset()
        ok, log = phone_pay(acct); print(f"  [폰결제] {ok}", flush=True)
        if not ok:
            print(f"  ❌ 결제 실패 — 중단 (resume: 이 상품부터)\n{log}"); return False
        ad = phone_dismiss_ad(); print(f"  [광고] {ad or '없음'}", flush=True)
        ok, log = pc_apply_reward(acct, pid); print(f"  [적립신청] {ok} — {log[:120]}", flush=True)
        if not ok:
            print(f"  ⚠️ 적립신청 실패 — 중단 (결제는 됨, 적립만 재시도 필요)\n{log}"); return False
        _mark_done(acct, pid)
        # 구매대장 기록 (JSON + 시트). 실패해도 흐름 무영향. 식품=today.json 즉시할인가(실제 qty 비례 환산).
        try:
            sys.path.insert(0, str(ROOT))
            import purchase_ledger as PL
            _accts = json.loads((ROOT / "hmall_config.json").read_text(encoding="utf-8"))["accounts"]
            _aid = _accts[acct - 1]["id"] if 0 <= acct - 1 < len(_accts) else None
            PL.record_food("현대Hmall", _aid, pid, qty=QTY_BY.get((acct, pid), 1))
        except Exception as e:
            print(f"  [ledger] 기록 실패(무시): {e}", flush=True)
        print(f"  ✓ product {pid} 완료(결제+적립)", flush=True)
    print(f"[계정 #{acct}] ✓ 완료")
    return True


def _parse_accts(args) -> list[int]:
    out = []
    for a in args:
        if "-" in a:
            lo, hi = a.split("-"); out += list(range(int(lo), int(hi) + 1))
        else:
            out.append(int(a))
    return out


def main():
    accts = _parse_accts(sys.argv[1:])
    if not accts:
        print(__doc__); return 1
    print(f"[food_buy] 계정 {accts} / 7분 간격 / 완료기록 {STATE.name}")
    for i, acct in enumerate(accts):
        if i > 0:
            print(f"\n  ⏳ 계정 간 7분 대기...", flush=True); time.sleep(ACCOUNT_DELAY)
        if not do_account(acct):
            print(f"\n[STOP] 계정 {acct} 에서 중단. 고친 뒤 'python3 buy/food_buy.py {acct}' 로 재개(완료분 자동 skip).")
            return 1
    print("\n[food_buy] 전체 완료")
    return 0


if __name__ == "__main__":
    sys.exit(main())
