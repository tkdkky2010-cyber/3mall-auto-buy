"""현대몰 19계정 H.Point 잔액 조회 (읽기 전용 — 담기/결제 없음).

사용법:
    python hmall_points.py          # 전체
    python hmall_points.py 5-19     # 범위
    python hmall_points.py 7        # 단일

로그인/로그아웃/세션정리는 `buy/run.py` 정본을 그대로 쓴다(중복 구현 금지).
잔액은 마이페이지 `ul.activity-list` 의 '포인트' 항목에서만 읽는다 —
body 전체를 긁으면 하단 배너·최근본상품이 섞인다(READ_FIRST 규칙).
"""
from __future__ import annotations
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "buy"))
import run as hrun  # noqa: E402  buy/run.py — login/_hmall_clean/poll_until 정본

MYPAGE_URL = "https://www.hmall.com/mo/mpf/selectMyPageMain"
EVENT_URL_FMT = "https://www.hmall.com/md/eva/evntHPointDtl?prmoNo={prmo}"

# 적립 이벤트 상세의 '예상 H.Point' 아코디언을 펼쳐 대상 구매 합계를 읽는다.
# 합계 문구는 펼친 패널 안에만 있다 — 접힌 상태로 body 를 긁으면 안 나온다.
_JS_EVENT_OPEN = """() => {
    const btn = document.querySelector('button.accordion-trigger');
    if (!btn) return false;
    btn.click();            // ★1회만 — 토글이라 폴링에서 반복 호출하면 도로 접힌다
    return true;
}"""

_JS_EVENT_READ = """() => {
    const btn = document.querySelector('button.accordion-trigger');
    if (!btn) return {expect: null, total: null, count: null};
    const em = btn.querySelector('em');
    const panel = btn.closest('div,section,li') || document.body;
    const m = (panel.innerText || '').match(/기준\\s*총\\s*([\\d,]+)\\s*원\\s*&\\s*(\\d+)\\s*개/);
    return {expect: em ? em.innerText.trim() : null,
            total: m ? m[1] : null, count: m ? m[2] : null};
}"""


def read_event_total(page, prmo: str) -> dict:
    """이벤트 대상 구매 합계. 합계 문구는 **펼친 패널 안에만** 있다."""
    page.goto(EVENT_URL_FMT.format(prmo=prmo), wait_until="domcontentloaded", timeout=25000)
    if not hrun.poll_until(lambda: page.evaluate(_JS_EVENT_OPEN), timeout_ms=15000):
        return {"expect": None, "total": None, "count": None}   # 참여내역 없음 = 아코디언 자체가 없다
    hrun.poll_until(lambda: page.evaluate(_JS_EVENT_READ)["total"] is not None, timeout_ms=10000)
    return page.evaluate(_JS_EVENT_READ)

_JS_POINT = """() => {
    const ul = document.querySelector('ul.activity-list');
    if (!ul) return null;
    const li = Array.from(ul.querySelectorAll('li')).find(
        l => ((l.querySelector('strong') || {}).innerText || '').trim() === '포인트');
    if (!li) return null;
    const v = (li.querySelector('span') || {}).innerText || '';
    const nameEl = Array.from(document.querySelectorAll('strong,span,p,em'))
        .find(e => /^[^\\n]{1,12}님$/.test((e.innerText || '').trim()));
    return {point: v.trim(), name: nameEl ? nameEl.innerText.trim() : ''};
}"""


def read_point(page) -> dict | None:
    """마이페이지에서 포인트 잔액 + 회원명. 비동기 렌더라 나올 때까지 폴링."""
    page.goto(MYPAGE_URL, wait_until="domcontentloaded", timeout=20000)
    hrun.poll_until(lambda: page.evaluate(_JS_POINT) is not None, timeout_ms=10000)
    return page.evaluate(_JS_POINT)


def check_one(context, idx: int, account: dict, prmo: str = "") -> dict:
    page = context.pages[-1] if context.pages else context.new_page()
    # ★deep=True 로 한 번에 간다 — 얕은 정리는 로그인폼이 홈으로 리다이렉트돼
    #   30초 타임아웃을 태운 뒤에야 재시도로 넘어간다(#14 실측).
    hrun._hmall_clean(context, page, deep=True)
    ok = hrun.login_capped(page, account["id"], account["pw"])
    if not ok:
        print(f"  [RETRY] #{idx} {account['id']} — 쿠키/스토리지 폐기 후 재시도")
        hrun._hmall_clean(context, page, deep=True)
        page.wait_for_timeout(2000)
        ok = hrun.login_capped(page, account["id"], account["pw"])
    if not ok:
        return {"idx": idx, "id": account["id"], "ok": False, "point": None, "name": ""}
    got = read_point(page)
    if not got:
        return {"idx": idx, "id": account["id"], "ok": False, "point": None,
                "name": "", "err": "포인트 항목 미발견"}
    out = {"idx": idx, "id": account["id"], "ok": True,
           "point": int(got["point"].replace(",", "").rstrip("P") or 0),
           "name": got["name"]}
    if prmo:
        ev = read_event_total(page, prmo)
        out["evt_total"] = int(ev["total"].replace(",", "")) if ev["total"] else None
        out["evt_count"] = int(ev["count"]) if ev["count"] else None
        out["evt_expect"] = ev["expect"]
    return out


def parse_targets(argv: list[str], n: int) -> list[int]:
    if len(argv) < 2:
        return list(range(1, n + 1))
    a = argv[1]
    if "-" in a:
        lo, hi = a.split("-", 1)
        return list(range(int(lo or 1), int(hi or n) + 1))
    return [int(a)]


def main() -> int:
    accounts = json.loads((ROOT / "hmall_config.json").read_text(encoding="utf-8"))["accounts"]
    targets = parse_targets(sys.argv, len(accounts))
    prmo = sys.argv[2] if len(sys.argv) > 2 else ""
    port = hrun.resolve_cdp_port(int(hrun.CDP_PORT))
    endpoint = f"http://127.0.0.1:{port}"
    print(f"[INFO] CDP endpoint={endpoint} / 대상 {targets}")

    results = []
    with hrun.sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(endpoint, slow_mo=300)
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        for idx in targets:
            acc = accounts[idx - 1]
            print(f"\n─── #{idx} {acc['id']} ───")
            try:
                r = check_one(context, idx, acc, prmo)
            except Exception as e:
                r = {"idx": idx, "id": acc["id"], "ok": False, "point": None,
                     "name": "", "err": str(e)[:120]}
            results.append(r)
            ev = f" | 구매 {r.get('evt_total')}원 {r.get('evt_count')}건 예상 {r.get('evt_expect')}" if prmo else ""
            print(f"  → {r.get('name','')} {r['point'] if r['ok'] else 'FAIL ' + r.get('err','')}{ev}")

    print("\n========= H.Point 잔액 =========")
    for r in results:
        val = f"{r['point']:>9,}P" if r["ok"] else f"  {r.get('err', '로그인 실패')}"
        ev = ""
        if prmo and r["ok"]:
            t = r.get("evt_total")
            ev = f"  구매 {t:>10,}원 ({r.get('evt_count')}건, 예상 {r.get('evt_expect')})" \
                 if t is not None else "  구매 판독실패"
        print(f"  #{r['idx']:2d} {r['id']:24s} {r['name']:8s} {val}{ev}")
    ok = [r for r in results if r["ok"]]
    print(f"  ───────────────────────────")
    print(f"  성공 {len(ok)}/{len(results)}  합계 {sum(r['point'] for r in ok):,}P")
    (ROOT / "logs" / "hmall_points_latest.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
