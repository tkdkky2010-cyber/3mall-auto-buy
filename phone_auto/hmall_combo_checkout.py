"""콤보 결제 루프 (phone-only, 현대카드).

계정별: 로그인(hmall_webview CDP) → 장바구니 확인 → 차있으면 현대카드 결제(flow_runner) →
비었으면 skip(이미 완료) → 로그아웃 → 다음.

전제: 폰 hmall 앱 + adb 연결. 카트는 PC(first_cart)로 사전 세팅됨. 현대카드가 결제수단 기본.
실돈 — DRY 없음. 설화수(콤보)는 계정 간 대기 없음(PAY_DELAY 기본 0). 7분 추적회피 정책은 Hmall 식품 buy/run.py 전용.

CLI:
    python3 -m phone_auto.hmall_combo_checkout            # 전체 plan
    python3 -m phone_auto.hmall_combo_checkout 3          # 특정 계정만
    PAY_DELAY_SEC=60 python3 -m phone_auto.hmall_combo_checkout 3 4
"""
from __future__ import annotations
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from phone_auto import hmall_webview as hw

PY = os.environ.get("PYTHON_BIN", "/usr/bin/python3")
PLAN = [1, 2, 3, 4, 5, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19]
PAY_DELAY = int(os.environ.get("PAY_DELAY_SEC", "0"))   # 설화수 = 계정 간 대기 없음 (7분은 Hmall 식품 전용)
SKIP_DELAY = int(os.environ.get("SKIP_DELAY_SEC", "0"))


def run_hyundai(serial: str) -> tuple[int, str]:
    """폰 hmall 메인으로 리셋 후 hyundai_card flow_payment 실행."""
    hw._launch(serial)          # hmall 메인 foreground (flow 전제)
    time.sleep(2)
    env = {**os.environ, "ANDROID_SERIAL": serial, "FLOW_USE_CAMERA": "0", "FLOW_PORTRAIT": "0",
           "PATH": os.path.dirname(hw.ADB) + os.pathsep + os.environ.get("PATH", "")}
    try:
        r = subprocess.run([PY, "-m", "phone_auto.flow_runner", "hyundai_card", "flow_payment"],
                           cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        return 124, "flow_runner TIMEOUT(300s)"
    tail = (r.stdout or "")[-1800:]
    if r.stderr:
        tail += "\n[stderr]\n" + r.stderr[-600:]
    return r.returncode, tail


def _dismiss_launch_ad(serial: str, max_iter: int = 4) -> int:
    """앱 콜드런치 시 뜨는 홈 광고 모달 닫기 — 안 닫으면 webview 로그인이 막힘(실측 2026-06-17).
    광고는 WebView라 uiautomator 텍스트 미노출 → screencap OCR 로 검출.
    ★반드시 '오늘 그만 보기'(당일 재등장 방지)로 닫는다 — '닫기'/'X'는 로그인 후 같은 팝업 재등장하므로 사용 금지.
    '오늘 그만 보기'/'그만 보기'/'보지 않기'/'오늘 하루' 류만 탭."""
    from phone_auto.screen_ocr import ocr_text
    png = "/tmp/_hmall_ad.png"
    norm = lambda s: s.replace(" ", "")
    closed = 0
    for _ in range(max_iter):
        r = subprocess.run([hw.ADB, "-s", serial, "exec-out", "screencap", "-p"],
                           capture_output=True, timeout=20)
        with open(png, "wb") as f:
            f.write(r.stdout)
        try:
            items = ocr_text(png)
        except Exception as e:
            print(f"   [popup] OCR 실패({e}) — 광고닫기 skip", flush=True); break
        hit = None
        for key in ("오늘그만보기", "그만보기", "보지않기", "오늘하루"):   # ★'닫기'는 의도적으로 제외(재등장 방지)
            hit = next((it for it in items if key in norm(it["text"])), None)
            if hit:
                break
        if not hit:
            break
        subprocess.run([hw.ADB, "-s", serial, "shell", "input", "tap", str(hit["cx"]), str(hit["cy"])],
                       capture_output=True, timeout=10)
        print(f"   [popup] 광고 닫기 '{hit['text']}' @({hit['cx']},{hit['cy']})", flush=True)
        closed += 1
        time.sleep(1.2)
    if closed == 0:
        print("   [popup] 시작 광고 없음 — 통과", flush=True)
    return closed


def _apply_account_rewards(idx: int) -> tuple[bool, str]:
    """결제 완료 후 PC(run.py CDP 9222)로 그 계정이 산 식품들의 distinct 적립이벤트(prmo)를 각각 신청.
    출처: buy/cart_plan.json(계정→제품) + cart/today.json(제품→events.prmo). 같은 prmo=1회, 다르면 각각.
    설화수 등 today.json 에 없는 제품은 prmo 0 → skip. food_buy.pc_apply_reward 와 동일한 PC 서브프로세스 패턴."""
    import json as _json
    plan_p = ROOT / "buy" / "cart_plan.json"
    today_p = ROOT / "cart" / "today.json"
    if not plan_p.exists() or not today_p.exists():
        return True, "plan/today.json 없음 — 적립신청 skip"
    plan = _json.loads(plan_p.read_text(encoding="utf-8"))
    pids = [it["product"] for it in plan.get("items", []) if idx in it.get("accounts", [])]
    if not pids:
        return True, f"계정 {idx} 플랜 제품 없음 — skip"
    today = _json.loads(today_p.read_text(encoding="utf-8"))
    ev_by_pid = {str(p.get("id")): [e.get("prmo") for e in (p.get("events") or []) if e.get("prmo")]
                 for p in today.get("products", [])}
    prmos: list[str] = []
    for pid in pids:
        for pr in ev_by_pid.get(str(pid), []):
            if pr and pr not in prmos:        # distinct: 같은 이벤트는 1회만
                prmos.append(pr)
    if not prmos:
        return True, f"계정 {idx} 적립이벤트(prmo) 없음 — skip"
    code = (
        "import sys; sys.path.insert(0,'buy'); import run\n"
        "from playwright.sync_api import sync_playwright\n"
        f"acc=run.load_json(run.ACCOUNTS_FILE)['accounts'][{idx}-1]\n"
        "with sync_playwright() as p:\n"
        " br=p.chromium.connect_over_cdp(run.CDP_ENDPOINT); ctx=br.contexts[0] if br.contexts else br.new_context(); page=ctx.new_page()\n"
        " run._hmall_clean(ctx,page,deep=True)\n"
        " ok=run.login(page,acc['id'],acc['pw']); print('login',ok)\n"
        f" for prmo in {prmos}:\n"
        "  print('HP', prmo, run.apply_hpoint(page,prmo))\n"
        " page.close()\n"
    )
    try:
        r = subprocess.run(["python3", "-c", code], cwd=str(ROOT),
                           capture_output=True, text=True, timeout=240)
    except subprocess.TimeoutExpired:
        return False, f"적립신청 TIMEOUT (prmos={prmos})"
    ok = ("신청 완료" in r.stdout) or ("already_done" in r.stdout) or ("success" in r.stdout)
    return ok, f"prmos={prmos} | " + (r.stdout or "")[-240:]


def main() -> int:
    serial = hw._serial()
    only = [int(a) for a in sys.argv[1:] if a.isdigit()]
    plan = [i for i in PLAN if not only or i in only]
    print(f"[serial] {serial}  plan={plan}  PAY_DELAY={PAY_DELAY}s", flush=True)
    summary: list[tuple[int, str, str]] = []
    for pos, idx in enumerate(plan):
        print(f"\n{'='*52}\n[{pos+1}/{len(plan)}] #{idx} 로그인 중...", flush=True)
        hw.reset_to_main(serial)          # 콜드런치 → 클린 메인 (광고 모달 등장 유도)
        _dismiss_launch_ad(serial)        # ★앱 켜자마자 광고 닫고 시작 (안 닫으면 webview 로그인 막힘)
        try:
            lr = hw.login_account(idx, serial)
        except Exception as e:
            print(f"#{idx}: LOGIN EXC {e}", flush=True)
            summary.append((idx, "?", f"LOGIN_EXC")); continue
        aid = lr.get("id", "?")
        if not lr.get("success"):
            print(f"#{idx} {aid}: LOGIN FAIL {lr.get('error')}", flush=True)
            summary.append((idx, aid, "LOGIN_FAIL")); continue
        try:
            cs = hw.cart_state(serial)
        except Exception as e:
            print(f"#{idx} {aid}: CART_STATE EXC {e}", flush=True)
            summary.append((idx, aid, "CART_EXC")); continue
        if cs.get("empty"):
            print(f"#{idx} {aid}: 장바구니 비어있음 → 이미 완료 skip", flush=True)
            summary.append((idx, aid, "DONE(empty)"))
            time.sleep(SKIP_DELAY); continue
        print(f"#{idx} {aid}: 장바구니 차있음 → 현대카드 결제 실행 ⚠️실돈", flush=True)
        rc, log = run_hyundai(serial)
        status = "PAID" if rc == 0 else f"PAY_FAIL(rc={rc})"
        print(f"#{idx} {aid}: {status}\n--- flow log tail ---\n{log}\n--- end ---", flush=True)
        if rc == 0:
            # 식품 우수몰: 결제 완료 후 그 계정이 산 제품들의 distinct 적립이벤트를 각각 신청
            # (설화수는 today.json prmo 없어 자동 skip). 같은 이벤트=1회 / 다른 이벤트=각각.
            rok, rlog = _apply_account_rewards(idx)
            print(f"#{idx} {aid}: 적립신청 {'OK' if rok else '⚠️FAIL'} — {rlog}", flush=True)
        summary.append((idx, aid, status))
        time.sleep(PAY_DELAY if rc == 0 else SKIP_DELAY)
    print(f"\n{'='*52}\nSUMMARY ({len(summary)}):", flush=True)
    for idx, aid, st in summary:
        print(f"  #{idx:2d} {aid:14s} {st}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
