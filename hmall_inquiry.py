"""현대몰 1:1 게시판 상담(시스템오류) 자동 접수 — 아모레 뷰티포인트 적립 요청.

계정별로 로그인 → /mo/ccd/cancelCall → 상담유형 '시스템오류'(0712) 선택
→ 상담사유 '시스템오류/불편사항'(071201) 자동선택 확인 → 문의내용 입력
→ 답변방법 SMS + 휴대폰번호 → 개인정보 동의 → 확인.

DOM 실측 (2026-08-19, CFT 9222):
  상담유형/상담사유 = <select> (id/name 없음, 옵션 첫 텍스트로 식별)
  문의내용   textarea#cnslCntn (maxlength 500)
  답변방법   input[name=answReqnGbcd] value=1 SMS / 2 전화  (기본 1 체크됨)
  휴대폰     input[name=hpNum]  ← 계정 등록번호가 프리필됨. 반드시 덮어쓴다.
  동의       input#deliverCheck
  제출       button '확인' (class 'btn btn btn-default ml')

사용:
    python3 hmall_inquiry.py --dry 1        # 1번 폼만 채우고 확인 클릭 안 함
    python3 hmall_inquiry.py 1              # 1번 접수
    python3 hmall_inquiry.py 2-19           # 범위 접수
"""
from __future__ import annotations
import json, sys, time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "buy"))

import run as buy_run  # type: ignore
from run import CDP_ENDPOINT, login, logout_if_needed  # type: ignore

INQUIRY_URL = "https://www.hmall.com/mo/ccd/cancelCall"
REVIEW_URL = "https://www.hmall.com/mo/ccd/myMtoMReview"
HP_NUM = "01026541598"
HEAD = "추후 아모레 주문건들에 대해 다음 이름, 뷰티포인트 적립번호로 뷰티포인트 적립부탁드립니다"

# (idx, hmall_config 상의 id, 이름, 뷰티포인트 적립번호) — 사용자 지정 원본. id 는 검증용.
ROWS = [
    (1,  "tkdkky2002",            "이예나", "5279 1515 6354 0469"),
    (2,  "1plu1mall1",            "전영미", "5279 1517 1588 3916"),
    (3,  "kgi5907@daum.net",      "이예진", "5279 1517 1621 8050"),
    (4,  "1plus1mall0",           "조영진", "5279 1517 1828 7519"),
    (5,  "1plus1mall1",           "김진화", "5279 1517 2883 3470"),
    (6,  "1plus1mall4",           "이남수", "5279 1517 2883 3874"),
    (7,  "linguist01",            "김예린", "5279 1517 0961 8860"),
    (8,  "ybkim8225",             "김미경", "5279 1517 7368 1918"),
    (9,  "lee0503031",            "조영은", "5279 1517 0263 6970"),
    (10, "jye40323",              "조영은", "5279 1517 0263 6970"),
    (11, "kimgaeun04",            "김미경", "5279 1517 7368 1918"),
    (12, "Jinhwa4553",            "김예린", "5279 1517 0961 8860"),
    (13, "gcd0327",               "이남수", "5279 1517 2883 3874"),
    (14, "johwajeong",            "김진화", "5279 1517 2883 3470"),
    (15, "jyj041220",             "조영진", "5279 1517 1828 7519"),
    (16, "miu1838",               "이예진", "5279 1517 1621 8050"),
    (17, "skykow",                "전영미", "5279 1517 1588 3916"),
    (18, "ybkim9960",             "이예나", "5279 1515 6354 0469"),
    (19, "yeonsuk0428@naver.com", "이예나", "5279 1515 6354 0469"),
]


def body_text(name: str, point_no: str) -> str:
    return f"{HEAD}\n{name} {point_no}"


def _select_by_first_option(page, first_text: str):
    """id/name 없는 select 를 첫 옵션 텍스트로 식별."""
    sels = page.locator("select")
    for i in range(sels.count()):
        if sels.nth(i).locator("option").first.inner_text().strip() == first_text:
            return sels.nth(i)
    return None


def fill_form(page, name: str, point_no: str) -> dict:
    """폼을 채우고 **채워진 값을 되읽어** 반환. 확인 클릭은 하지 않는다."""
    page.goto(INQUIRY_URL, wait_until="domcontentloaded")
    page.wait_for_timeout(2500)

    sel_type = _select_by_first_option(page, "상담 유형")
    if sel_type is None:
        return {"ok": False, "error": "상담유형 select 없음"}
    sel_type.select_option("0712")
    page.wait_for_timeout(2000)

    # 상담사유는 유형 선택 시 자동 채워짐(옵션 1개). 값이 비면 직접 선택.
    reason = page.evaluate("""() => {
        const s = Array.from(document.querySelectorAll('select'))
            .find(e => Array.from(e.options).some(o => o.value === '071201'));
        if (!s) return null;
        return s.value;
    }""")
    if reason != "071201":
        return {"ok": False, "error": f"상담사유 자동선택 실패 (value={reason})"}

    page.wait_for_selector("textarea#cnslCntn", timeout=10000)
    page.fill("textarea#cnslCntn", body_text(name, point_no))
    page.wait_for_timeout(400)

    page.locator("input[name=answReqnGbcd][value='1']").check()   # SMS
    page.wait_for_timeout(300)
    # ★hpNum 은 React controlled input — .fill() 은 DOM value 만 바꾸고 상태가 안 붙어
    #   '휴대폰 번호를 입력해주세요.' alert 로 접수가 막힌다(2026-08-19 실측). 실제 키 입력으로 친다.
    hp = page.locator("input[name=hpNum]")
    hp.click()
    page.keyboard.press("Meta+A")
    page.keyboard.press("Backspace")
    page.wait_for_timeout(300)
    hp.press_sequentially(HP_NUM, delay=60)
    hp.blur()
    page.wait_for_timeout(400)
    # ★#deliverCheck 는 커스텀 스타일드 체크박스라 Playwright 기준 '보이지 않음' → .check() 실패.
    #   네이티브 .click() 은 숨은 체크박스에도 change 이벤트를 발생시킨다(React onChange 수신).
    page.evaluate("""() => { const c = document.querySelector('input#deliverCheck');
                             if (c && !c.checked) c.click(); }""")
    page.wait_for_timeout(400)

    got = page.evaluate("""() => ({
        cntn: document.querySelector('textarea#cnslCntn')?.value || '',
        sms:  !!document.querySelector("input[name=answReqnGbcd][value='1']")?.checked,
        tel:  !!document.querySelector("input[name=answReqnGbcd][value='2']")?.checked,
        hp:   document.querySelector('input[name=hpNum]')?.value || '',
        agree: !!document.querySelector('input#deliverCheck')?.checked,
    })""")
    got["ok"] = (got["cntn"] == body_text(name, point_no) and got["sms"] and not got["tel"]
                 and got["hp"] == HP_NUM and got["agree"])
    if not got["ok"]:
        got["error"] = "폼 값 검증 실패"
    return got


def submit(page) -> dict:
    """'확인' 클릭 → 성공 모달(#confirm-portal '…신청이 완료되었습니다.') 확인 → 모달 닫기.

    ★알럿(dialog)은 **검증 실패**다 — '휴대폰 번호를 입력해주세요.' 같은 게 뜨면 접수 안 된다.
      성공은 alert 이 아니라 #confirm-portal 모달로 온다 (2026-08-19 실측).
    """
    msgs: list[str] = []

    def _dlg(d):
        msgs.append(d.message)
        try:
            d.accept()
        except Exception:
            pass                     # run.login 이 이미 붙여둔 핸들러가 먼저 먹었을 때

    page.on("dialog", _dlg)
    try:
        clicked = page.evaluate("""() => {
            const b = Array.from(document.querySelectorAll('button')).find(e =>
                (e.innerText||'').trim() === '확인' && e.offsetParent && !e.closest('#confirm-portal'));
            if (b) { b.click(); return true; }
            return false;
        }""")
        if not clicked:
            return {"ok": False, "error": "확인 버튼 없음"}

        modal = ""
        for _ in range(20):                       # 성공 모달 폴링 (최대 10초)
            page.wait_for_timeout(500)
            modal = page.evaluate(
                "() => { const m = document.querySelector('#confirm-portal');"
                "        return m ? (m.innerText||'').replace(/\\s+/g,' ').trim() : ''; }")
            if modal or msgs:
                break
        if msgs:
            return {"ok": False, "error": f"alert: {msgs}"}
        if "완료" not in modal:
            return {"ok": False, "error": f"성공 모달 없음 (modal={modal!r})"}

        page.locator("#confirm-portal button", has_text="확인").first.click()
        page.wait_for_timeout(3000)
        return {"ok": True, "modal": modal, "url_after": page.url}
    finally:
        page.remove_listener("dialog", _dlg)


def latest_inquiry(page) -> str:
    """1:1 상담내역 첫 행 텍스트 — 접수 검증용."""
    page.goto(REVIEW_URL, wait_until="domcontentloaded")
    page.wait_for_timeout(3000)
    return page.evaluate("""() => {
        const a = Array.from(document.querySelectorAll('a'))
            .find(e => /\\d{4}\\.\\d{2}\\.\\d{2}/.test(e.innerText||''));
        return a ? (a.innerText||'').replace(/\\s+/g,' ').trim() : '';
    }""")


def parse_targets(argv: list[str]) -> list[int]:
    out: list[int] = []
    for a in argv:
        if "-" in a:
            s, e = a.split("-")
            out += list(range(int(s), int(e) + 1))
        else:
            out.append(int(a))
    return out


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    dry = "--dry" in sys.argv
    targets = parse_targets(args) if args else [r[0] for r in ROWS]

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
            _, exp_id, name, point_no = rows[idx]
            acc = accounts[idx - 1]
            # ★인덱스 drift 방어 — config 순서가 바뀌면 딴 계정에 딴 이름을 접수하게 된다.
            if acc["id"] != exp_id:
                print(f"[{idx}] ABORT: config id={acc['id']} != 지정 id={exp_id}")
                return 2
            print(f"\n=== [{idx}] {exp_id} / {name} {point_no} ===")
            logout_if_needed(page)
            if not login(page, acc["id"], acc["pw"]):
                print(f"[{idx}] 로그인 실패 — skip")
                results.append({"idx": idx, "id": exp_id, "ok": False, "step": "login"})
                continue
            f = fill_form(page, name, point_no)
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
