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


TODAY_JSON = ROOT / "cart" / "today.json"


def prmos_for_product(pid) -> list[str]:
    """cart/today.json 의 그 상품 `events[]` 에서 prmo 를 **전부** 뽑는다.

    ★한 상품에 **적립 이벤트가 2개 이상**일 수 있다 (2026-08-05 실측: 데이즈온 17·34 는
      `P202608043368` 건강식품 특별전 + `P202607292371` 데이즈온 10% = **2군데**).
      하나만 신청하면 절반을 놓친다 — 사용자 지시 "데이즈온은 적립 2군데 해야 돼".
    ★today_carts.json 의 손으로 적은 `prmo` 필드에 의존하지 말 것 — stale 되면 조용히 0건이 된다
      (2026-08-05 실측: 7/30 자 파일이라 적립·구매대장이 동시에 조용히 누락)."""
    try:
        d = json.loads(TODAY_JSON.read_text(encoding="utf-8"))
    except Exception:
        return []
    p = next((x for x in d.get("products", []) if str(x.get("id")) == str(pid)), None)
    return [e["prmo"] for e in (p or {}).get("events", []) or [] if e.get("prmo")]


def cart_prmos(cart: dict) -> list[str]:
    """카트의 모든 상품에 대한 prmo 합집합(순서 유지). today.json events 가 정본,
    today_carts.json 에 손으로 적힌 `prmo` 는 보조로만 합친다."""
    out: list[str] = []
    for it in cart.get("items", []):
        for pr in prmos_for_product(it.get("product")) + ([it["prmo"]] if it.get("prmo") else []):
            if pr not in out:
                out.append(pr)
    return out


def _parse_hp(out: str) -> dict:
    """서브프로세스 출력 → {prmo: 'new'|'already'|'fail'}. 라인 형식 `HP|prmo|success|already|error`."""
    res = {}
    for ln in out.splitlines():
        if not ln.startswith("HP|"):
            continue
        _, prmo, succ, already, err = (ln.split("|", 4) + ["", "", "", ""])[:5]
        res[prmo] = "already" if already == "True" else ("new" if succ == "True" else f"fail:{err}")
    return res


def apply_reward(cart: dict) -> dict:
    """결제 성공 후 H.Point 적립신청 — 그 상품에 10% prmo 있을 때만.
    쿠폰만 있거나 적립이벤트 없으면(prmo 없음) skip. 결제 성공/실패 판정과 무관(best-effort).

    ★반환 = {"prmos": [...], "results": {prmo: 상태}, "ok": bool} — 호출측이 요약에 드러내라.
    ★2026-08-06 수정: 종전 판정은 `'success': True' in last_out` 처럼 **출력 전체 문자열 검색**이라
      prmo 2개 중 1개만 성공해도 ✓ 로 끝났다(나머지는 조용히 누락). 이제 **prmo 전건이 확인돼야 ok**.
      출력도 마지막 140자만 남겨서 어느 prmo 가 됐는지 알 수 없었다 → **건별 한 줄**로 바꿨다."""
    prmos = cart_prmos(cart)
    if not prmos:
        print("  [적립] 10% prmo 없음 (쿠폰만/적립없음) — 적립단계 skip", flush=True)
        return {"prmos": [], "results": {}, "ok": True, "skip": "prmo 없음"}
    acct = cart["account"]
    # ★9222 Chrome 창이 전부 닫혀 페이지 타깃 0개면 connect_over_cdp 가
    #   'Browser context management is not supported'로 실패(2026-07-13 실측) → 탭 1개 보장 후 연결.
    # 포트 체인: 9222 막히면 9223→9224 (같은 로그인된 CFT) — 2026-07-16
    from chrome_launcher import resolve_cdp_port
    cdp_port = str(resolve_cdp_port(int(os.environ.get("CDP_PORT", "9222"))))
    try:
        import urllib.request
        if not json.loads(urllib.request.urlopen(f"http://127.0.0.1:{cdp_port}/json/list", timeout=5).read()):
            urllib.request.urlopen(urllib.request.Request(
                f"http://127.0.0.1:{cdp_port}/json/new?https://www.hmall.com", method="PUT"), timeout=5)
            print("  [적립] 9222 페이지 0개 → 탭 생성", flush=True)
    except Exception as e:
        print(f"  [적립] ⚠️ CDP preflight 실패(계속 시도): {e}", flush=True)
    code = (
        "import sys; sys.path.insert(0,'buy'); import run\n"
        "sync_playwright=run.sync_playwright\n"   # patchright 우선 — 오늘 담기와 동일 백엔드(plain은 9222 연결 실패 이력)
        f"acc=run.load_json(run.ACCOUNTS_FILE)['accounts'][{acct}-1]\n"
        "with sync_playwright() as p:\n"
        # ★기존 탭 재사용 — 새 탭=macOS Chrome 창 포커스 강탈(2026-07-10). close 안 함.
        f" br=p.chromium.connect_over_cdp('http://127.0.0.1:{cdp_port}'); ctx=br.contexts[0] if br.contexts else br.new_context(); page=ctx.pages[-1] if ctx.pages else ctx.new_page()\n"
        " run._hmall_clean(ctx,page,deep=True)\n"
        " ok=run.login(page,acc['id'],acc['pw']); print('login',ok)\n"
        f" for prmo in {prmos}:\n"
        # ★건별 파싱용 고정 포맷 — 종전 dict repr 는 부모가 문자열 검색으로만 판정해 절반 누락을 놓쳤다.
        "  _r=run.apply_hpoint(page,prmo)\n"
        "  print('HP|%s|%s|%s|%s' % (prmo,_r.get('success'),_r.get('already_done'),_r.get('error')))\n"
    )
    # ★로그인 실패(login False)일 땐 apply_hpoint의 '이미 신청 완료'가 신뢰 불가 —
    #   2026-07-13 실측: login False + already_done=True가 전부 오판이었고, 재로그인 성공 시
    #   실제로는 미신청(fresh apply)이었음. 그래서 login True 확인될 때까지 재시도(최대 3회).
    #   good = login True AND (신규신청 or 이미신청). login False는 미검증으로 취급 → 재시도.
    import time as _t
    last_out = last_err = ""
    got: dict = {}
    for attempt in range(1, 4):
        try:
            r = subprocess.run([BROWSER_PY, "-c", code], cwd=str(ROOT), capture_output=True, text=True, timeout=180)
            last_out, last_err = r.stdout, r.stderr
        except Exception as e:
            print(f"  [적립] ⚠️ 적립신청 호출 실패(결제는 정상): {e}", flush=True)
            return {"prmos": prmos, "results": got, "ok": False, "err": str(e)}
        login_ok = "login True" in last_out
        got = _parse_hp(last_out)
        # ★전건 확인 — prmo 하나라도 빠지거나 실패하면 미완이다(부분성공을 ✓ 로 치던 게 누락의 원인).
        done = login_ok and all(got.get(p, "").startswith(("new", "already")) for p in prmos)
        for p in prmos:      # 건별로 반드시 한 줄씩 남긴다 — 뭐가 됐는지 로그로 확정
            st = {"new": "신규신청", "already": "이미신청"}.get(got.get(p), got.get(p) or "무응답")
            print(f"  [적립] prmo={p} → {st}", flush=True)
        if done:
            print(f"  [적립] RESULT ok=True {len(prmos)}/{len(prmos)} 계정#{acct}", flush=True)
            return {"prmos": prmos, "results": got, "ok": True}
        print(f"  [적립] 시도{attempt} 미확정(login_ok={login_ok}) — 재시도", flush=True)
        _t.sleep(4)
    err_tail = " | stderr: " + last_err.strip().splitlines()[-1] if last_err.strip() else ""
    ok_n = sum(1 for p in prmos if got.get(p, "").startswith(("new", "already")))
    print(f"  [적립] RESULT ok=False {ok_n}/{len(prmos)} 계정#{acct} — ⚠️확인필요(3회 실패) "
          f"재실행: python3 buy.py reward {acct}{err_tail}", flush=True)
    return {"prmos": prmos, "results": got, "ok": False}


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
        # ★성공해도 로그를 남긴다 (2026-08-19). 종전엔 실패할 때만 찍어서 **어느 경로로 성공했는지
        #   확인할 방법이 없었다** — 폴백(OCR→dump)·일반결제 탭·주문번호가 전부 버려졌다.
        #   READ_FIRST 「성공 메시지는 검증이 아니다」. 계정 9개를 실돈으로 돌리기 전에 필요한 증거다.
        print(f"--- log(성공) ---\n{log}\n-----------------", flush=True)
        if mall == "hmall":               # 현대 식품: 10% prmo 있으면 적립신청 (없으면 skip)
            rw = apply_reward(cart)
            cart["reward_ok"] = bool(rw.get("ok"))
            _save(data)                   # 적립 성패도 즉시 기록 — 중단돼도 뭐가 남았는지 남는다
            if not rw.get("ok"):
                print(f"  ⚠️ [적립] #{cart['account']} 미완 — "
                      f"python3 buy.py reward {cart['account']}", flush=True)
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

    # ★적립신청만 단독 실행 — 폰 결제(phone_auto.*)는 이 스크립트를 안 거치므로 결제 직후 여기로 온다.
    #   `python3 buy.py reward <계정> [상품키워드...]`
    #   키워드는 분리주문(only=데이즈온 등)일 때 **이번에 결제한 상품만** 적립 대상으로 거르기 위한 것.
    if args and args[0] == "reward":
        if len(args) < 2 or not args[1].isdigit():
            print("[ERR] 사용: python3 buy.py reward <계정번호> [상품키워드...]")
            return 1
        acct, kws = int(args[1]), args[2:]
        cart = next((c for c in data["carts"]
                     if MALL.get(c["mall"]) == "hmall" and c["account"] == acct), None)
        if cart is None:
            print(f"[적립] ⚠️ today_carts.json 에 현대 #{acct} 카트 없음 — 적립 대상 불명(수동 확인 필요)")
            return 1
        items = [it for it in cart.get("items", [])
                 if not kws or any(k in (it.get("name") or "") for k in kws)]
        if not items:
            print(f"[적립] ⚠️ #{acct} 키워드 {kws} 에 맞는 상품 없음 — skip")
            return 1
        # ★rc 를 적립 성패에 연동 — 종전엔 실패해도 항상 0 이라 호출측(apply_reward_now)이
        #   '성공'으로 착각했다. 미완이면 rc=1 로 시끄럽게 끝낸다.
        return 0 if apply_reward({"account": acct, "items": items}).get("ok") else 1

    # ★결제 전 preflight — today.json/today_carts.json 이 오늘자가 아니면 중단.
    #   (stale 이면 구매대장·적립이 **둘 다 조용히** 누락된다 — 2026-08-05 실측. 정본은
    #    phone_auto.hmall_hyundai_buy.preflight_today_files, 3사 공용.)
    #   status/reward 는 결제가 아니므로 통과시킨다.
    if not (args and args[0] in ("status", "reward")):
        sys.path.insert(0, str(ROOT))
        from phone_auto.hmall_hyundai_buy import preflight_today_files
        if not preflight_today_files():
            return 1

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
