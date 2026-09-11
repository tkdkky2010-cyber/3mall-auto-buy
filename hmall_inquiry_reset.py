"""현대몰 1:1 게시판 상담(시스템오류) — 뷰티포인트 적립 **초기화** 요청 접수.

hmall_inquiry.py 와 같은 흐름(로그인 → /mo/ccd/cancelCall → 시스템오류 → SMS+휴대폰
→ 동의 → 확인)이고 **문의내용만 다르다**. 계정별 이름/적립번호 없이 전 계정 동일 문구.

사용:
    python3 hmall_inquiry_reset.py --dry 2     # 2번 폼만 채우고 확인 클릭 안 함
    python3 hmall_inquiry_reset.py 2           # 2번 접수
    python3 hmall_inquiry_reset.py             # 기본 = 2-19 (사용자 지시: 2번부터 끝번호)
"""
from __future__ import annotations
import json, sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "buy"))

from hmall_inquiry import ROWS, fill_form, submit, latest_inquiry, parse_targets  # type: ignore
from run import CDP_ENDPOINT, login, logout_if_needed  # type: ignore

# 사용자 지정 문구 — 한 글자도 바꾸지 말 것.
BODY = ("뷰티포인트 적립 초기화 요청드립니다.\n"
        "따로 전화 주시지 마시기 바랍니다. 문자로 상담사님께서 지시한대로 "
        "뷰티포인트 적립란 초기화 하는거 신청하는 겁니다.")

DEFAULT_TARGETS = list(range(2, 20))   # 2번 ~ 19번 = 18계정


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    dry = "--dry" in sys.argv
    targets = parse_targets(args) if args else DEFAULT_TARGETS

    accounts = json.loads((ROOT / "hmall_config.json").read_text(encoding="utf-8"))["accounts"]
    rows = {r[0]: r for r in ROWS}
    today = date.today().strftime("%Y.%m.%d")

    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        br = p.chromium.connect_over_cdp(CDP_ENDPOINT)
        ctx = br.contexts[0]
        page = ctx.pages[-1] if ctx.pages else ctx.new_page()

        results = []
        for idx in targets:
            exp_id = rows[idx][1]
            acc = accounts[idx - 1]
            # ★인덱스 drift 방어 — config 순서가 바뀌면 딴 계정에 접수하게 된다.
            if acc["id"] != exp_id:
                print(f"[{idx}] ABORT: config id={acc['id']} != 지정 id={exp_id}")
                return 2
            print(f"\n=== [{idx}] {exp_id} ===")
            logout_if_needed(page)
            if not login(page, acc["id"], acc["pw"]):
                print(f"[{idx}] 로그인 실패 — skip")
                results.append({"idx": idx, "id": exp_id, "ok": False, "step": "login"})
                continue
            f = fill_form(page, BODY)
            print(f"[{idx}] fill: {json.dumps(f, ensure_ascii=False)}")
            if not f.get("ok"):
                results.append({"idx": idx, "id": exp_id, "ok": False, "step": "fill", **f})
                continue
            if dry:
                results.append({"idx": idx, "id": exp_id, "ok": True, "step": "dry"})
                continue
            s = submit(page)
            print(f"[{idx}] submit: {json.dumps(s, ensure_ascii=False)}")
            latest = latest_inquiry(page)
            print(f"[{idx}] 최신 상담내역: {latest}")
            # ★'시스템오류' 만 보면 예전 문의에도 걸린다 → **오늘 날짜**까지 맞아야 접수 성공.
            ok = bool(s.get("ok")) and today in latest and "시스템오류" in latest
            results.append({"idx": idx, "id": exp_id, "ok": ok,
                            "step": "submit", "latest": latest, "err": s.get("error")})

        print("\n===== 요약 =====")
        for r in results:
            print(f"  {r['idx']:>2} {r['id']:<24} {'OK' if r.get('ok') else 'FAIL'}  {r.get('latest','')}")
        return 0 if all(r.get("ok") for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
