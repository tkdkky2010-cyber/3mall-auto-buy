#!/usr/bin/env python3
# ──────────────────────────────────────────────────────────────────
#  유일한 "구매(결제) 시작" 진입점.  (그날 담아둔 장바구니를 결제만 한다)
# ──────────────────────────────────────────────────────────────────
#   현대몰(식품·설화수 모두)  = 무조건 폰 앱 결제   → phone_auto.hmall_hyundai_buy
#                              (주문완료검증 wait_order_complete + 뷰티포인트 + 당일카드 자동감지)
#   롯데홈쇼핑               = 무조건 폰 앱 결제   → phone_auto.lotte_homeshopping_buy
#   갤러리아몰               = PC 결제            → buy/sulwhasoo.py galleria
# ──────────────────────────────────────────────────────────────────
#  장바구니는 미리 담겨 있고(cart/today_carts.json 에 기록), 이 스크립트는
#  그 기록에서 "아직 결제 안 한(paid:false)" 카트를 몰별로 찾아 결제한다.
#
#  사용:
#    python3 buy.py            # = "결제진행해" : 기록의 미결제 카트 자동 결제
#    python3 buy.py 현대 8     # 특정 몰·계정 명시 결제
#    python3 buy.py 롯데 5
#    python3 buy.py 갤러리아 1
#    python3 buy.py status     # 오늘 카트/결제 현황만 출력
# ──────────────────────────────────────────────────────────────────
from __future__ import annotations
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MANIFEST = ROOT / "cart" / "today_carts.json"
# phone_auto 인터프리터: pyobjc/Vision 필요. brew python3 심볼릭이 사라져(2026-07-10) 존재하는 것 폴백.
PHONE_PY = os.environ.get("PHONE_PY") or next(
    (p for p in ("/opt/homebrew/bin/python3", "/usr/bin/python3") if Path(p).exists()), sys.executable)
BROWSER_PY = os.environ.get("BROWSER_PY", "python3")                # sulwhasoo PC (playwright)
DELAY = int(os.environ.get("BUY_DELAY_SEC", "0"))                   # 결제 성공 사이 대기(추적회피 필요 시)

# 몰 이름 정규화
MALL = {
    "현대": "hmall", "현대몰": "hmall", "hmall": "hmall",
    "롯데": "lotte", "롯데홈쇼핑": "lotte", "lotte": "lotte",
    "갤러리아": "galleria", "갤러리아몰": "galleria", "galleria": "galleria",
}


def _load() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def _save(data: dict) -> None:
    MANIFEST.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# ───────────── 몰별 결제 (담긴 카트 결제만) ─────────────

def _free_port() -> int:
    """폰 webview adb-forward 용 빈 TCP 포트 — 데스크톱 Chrome(9222/9223) 충돌 회피."""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _run(args, timeout, extra_env=None) -> tuple[int, str, bool]:
    """subprocess 실행 + timeout 안전처리. 반환 (returncode, stdout, timed_out)."""
    env = {**os.environ, "PYTHON_BIN": PHONE_PY}
    if extra_env:
        env.update(extra_env)
    try:
        r = subprocess.run(args, cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout, False
    except subprocess.TimeoutExpired as e:
        out = e.stdout if isinstance(e.stdout, str) else (e.stdout.decode() if e.stdout else "")
        return 124, (out or "") + "\n[TIMEOUT]", True


def pay_hmall(cart: dict) -> tuple[bool, str]:
    """현대몰 폰 앱 인앱 결제 (식품·설화수 공통) — hmall_hyundai_buy.
    주문완료 검증(wait_order_complete)+뷰티포인트+당일카드 자동감지. status 'DONE'/'SKIP_EMPTY'만 성공."""
    acct = cart["account"]
    rc, out, timed = _run(
        [PHONE_PY, "-m", "phone_auto.hmall_hyundai_buy", str(acct)],
        timeout=900, extra_env={"HMALL_CDP_PORT": str(_free_port())},
    )
    if timed:
        return False, out[-800:] + "\n⚠️ 타임아웃 — 폰에서 주문 완료됐을 수 있음, 수동확인 필요(중복결제 주의)"
    ok = (rc == 0) and (("=> DONE" in out) or ("SKIP_EMPTY" in out))
    return ok, out[-800:]


def pay_lotte(cart: dict) -> tuple[bool, str]:
    """롯데홈쇼핑 폰 앱 인앱 결제."""
    acct = cart["account"]
    args = [PHONE_PY, "-m", "phone_auto.lotte_homeshopping_buy", str(acct)]
    if cart.get("card"):
        args.append(cart["card"])
    if cart.get("combo"):
        args.append(f"combo={cart['combo']}")
    rc, out, timed = _run(args, timeout=900)
    if timed:
        return False, out[-800:] + "\n⚠️ 타임아웃 — 수동확인 필요"
    ok = (rc == 0) and ("DONE(주문" in out)
    return ok, out[-800:]


def pay_galleria(cart: dict) -> tuple[bool, str]:
    """갤러리아몰 PC 결제 (네이버페이 → 롯데2224)."""
    acct = cart["account"]
    combo = str(cart.get("combo", 1))
    rc, out, timed = _run(
        [BROWSER_PY, str(ROOT / "buy" / "sulwhasoo.py"), "galleria", str(acct), combo],
        timeout=600,
    )
    if timed:
        return False, out[-800:] + "\n⚠️ 타임아웃 — 수동확인 필요"
    ok = (rc == 0) and ("결제 진행 완료" in out)
    return ok, out[-800:]


def apply_reward(cart: dict) -> None:
    """결제 성공 후 H.Point 적립신청 — 그 상품에 10% prmo 있을 때만.
    쿠폰만 있거나 적립이벤트 없으면(prmo 없음) skip. 결제 성공/실패 판정과 무관(best-effort)."""
    prmos = [it["prmo"] for it in cart.get("items", []) if it.get("prmo")]
    if not prmos:
        print("  [적립] 10% prmo 없음 (쿠폰만/적립없음) — 적립단계 skip", flush=True)
        return
    acct = cart["account"]
    code = (
        "import sys; sys.path.insert(0,'buy'); import run\n"
        "from playwright.sync_api import sync_playwright\n"
        f"acc=run.load_json(run.ACCOUNTS_FILE)['accounts'][{acct}-1]\n"
        "with sync_playwright() as p:\n"
        # ★기존 탭 재사용 — 새 탭=macOS Chrome 창 포커스 강탈(2026-07-10). close 안 함.
        " br=p.chromium.connect_over_cdp(run.CDP_ENDPOINT); ctx=br.contexts[0] if br.contexts else br.new_context(); page=ctx.pages[-1] if ctx.pages else ctx.new_page()\n"
        " run._hmall_clean(ctx,page,deep=True)\n"
        " ok=run.login(page,acc['id'],acc['pw']); print('login',ok)\n"
        f" for prmo in {prmos}:\n"
        "  print('HP', prmo, run.apply_hpoint(page,prmo))\n"
    )
    try:
        r = subprocess.run([BROWSER_PY, "-c", code], cwd=str(ROOT), capture_output=True, text=True, timeout=180)
        good = ("already_done': True" in r.stdout) or ("'success': True" in r.stdout)
        print(f"  [적립] {'✓' if good else '⚠️확인필요'} prmo={prmos} {r.stdout.strip()[-140:]}", flush=True)
    except Exception as e:
        print(f"  [적립] ⚠️ 적립신청 호출 실패(결제는 정상): {e}", flush=True)


PAYER = {"hmall": pay_hmall, "lotte": pay_lotte, "galleria": pay_galleria}
LABEL = {"hmall": "현대(폰)", "lotte": "롯데(폰)", "galleria": "갤러리아(PC)"}


def _pay_cart(cart: dict, data: dict) -> bool:
    mall = MALL.get(cart["mall"])
    if mall not in PAYER:
        print(f"  [SKIP] 알 수 없는 몰: {cart['mall']}")
        return False
    print(f"\n=== {LABEL[mall]} #{cart['account']} 결제 시작 ⚠️실돈 ===", flush=True)
    ok, log = PAYER[mall](cart)
    if ok:
        cart["paid"] = True
        _save(data)                       # 성공 즉시 기록 (중복결제 방지)
        print(f"  ✓ {LABEL[mall]} #{cart['account']} 결제 완료 (기록 paid:true)", flush=True)
        if mall == "hmall":               # 현대 식품: 10% prmo 있으면 적립신청 (없으면 skip)
            apply_reward(cart)
    else:
        print(f"  ✗ {LABEL[mall]} #{cart['account']} 결제 실패\n--- log ---\n{log}\n-----------", flush=True)
    return ok


def cmd_status(data: dict) -> int:
    carts = data.get("carts", [])
    print(f"[today_carts] {data.get('date')}  총 {len(carts)}건")
    for c in carts:
        mark = "✓결제" if c.get("paid") else "·미결제"
        names = ",".join(i.get("name", "?") for i in c.get("items", []))
        print(f"  {mark}  {c['mall']:6} #{c['account']:<3} {names}")
    unpaid = [c for c in carts if not c.get("paid")]
    print(f"  미결제 {len(unpaid)}건")
    return 0


def main() -> int:
    if not MANIFEST.exists():
        print(f"[FATAL] 장바구니 기록 없음: {MANIFEST}")
        return 1
    data = _load()
    args = sys.argv[1:]

    # status 만 출력
    if args and args[0] == "status":
        return cmd_status(data)

    # 몰+계정 명시 결제
    if args and args[0] in MALL:
        if len(args) < 2 or not args[1].isdigit():
            print(f"[ERR] 사용: python3 buy.py {args[0]} <계정번호>")
            return 1
        mall, acct = MALL[args[0]], int(args[1])
        cart = next((c for c in data["carts"] if MALL.get(c["mall"]) == mall and c["account"] == acct), None)
        if cart is None:                  # 기록에 없으면 임시 카트로 결제 (기록은 남기지 않음)
            cart = {"mall": mall, "account": acct, "items": []}
            print(f"[INFO] 기록에 없는 카트 — 명시 결제만 진행 (기록 갱신 X)")
        return 0 if _pay_cart(cart, data) else 1

    if args:
        print(f"[ERR] 몰은 현대/롯데/갤러리아 중 하나여야 함: {args[0]}")
        return 1

    # 인자 없음 = "결제진행해" : 미결제 카트 전부 자동 결제
    import time
    unpaid = [c for c in data["carts"] if not c.get("paid")]
    if not unpaid:
        print("[buy] 미결제 카트 없음 — 결제할 것 없음")
        return 0
    by_mall: dict[str, int] = {}
    for c in unpaid:
        by_mall[MALL.get(c["mall"], c["mall"])] = by_mall.get(MALL.get(c["mall"], c["mall"]), 0) + 1
    print(f"[buy] 결제진행 — 미결제 {len(unpaid)}건 {by_mall}")
    done = 0
    for i, cart in enumerate(unpaid):
        if i > 0 and DELAY:
            print(f"  ⏳ 다음 결제 전 {DELAY}s 대기...", flush=True)
            time.sleep(DELAY)
        if _pay_cart(cart, data):
            done += 1
        else:
            print(f"\n[STOP] {cart['mall']} #{cart['account']} 결제 실패 — 중단. "
                  f"고친 뒤 다시 'python3 buy.py' 실행(완료분은 자동 skip).")
            return 1
    print(f"\n[buy] 완료 {done}/{len(unpaid)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
