"""롯데 구매사은 적립금 신청 — **CFT(PC 크롬)** 경로. (2026-08-31 신설)

⛔ **2026-09-09 현재 이 모듈은 아무도 호출하지 않는다. 정본은 폰이다.**
   사용자 지시 2026-09-09: "폰으로신청하게끔 해놔 CFT는 안되는 경우 종종 있는거 맞음".
   정본 = `phone_auto/lotte_homeshopping_buy.py claim_lotte_reward` (같은 날 라이브 검증).
   이 파일은 CFT 로 되는 행사를 수동 확인할 때만 CLI 로 직접 실행한다.
   ⚠️ 새 코드가 이 모듈을 import 하지 말 것 — 적립 경로가 두 벌이 되면 다시 어긋난다.

사용자 지시 (문장 그대로, 2026-08-30 / READ_FIRST.md):
  "롯데건은 결제 끝나면 cft에서 항상 최대 x원 ,최대x % 포인트 적립신청하는걸로 해"

왜 CFT 인가 (2026-08-31 실측):
  종전엔 폰에서 주문완료 → 구매상품 탭 → 상품상세 로 갔다. 그 경로가 **상품상세에 못 들어가면
  그대로 SKIP** 이다 — #11 이 정확히 그렇게 날아갔다
  (`구매상품 탭 후 상품상세 미진입(주문완료 잔류) — reward SKIP`).
  CFT 는 상품 URL 로 **직접** 들어가므로 그 실패지점 자체가 없다. OCR 대신 DOM 으로 읽는다.
  (2026-06-01 워크로그에도 "G(적립금신청)=웹 크롬으로 분리 결정" 이 이미 적혀 있었다.)

사용:
    python buy/lotte_reward.py <account_idx> [goods_no]
    # goods_no 생략 시 조합의 첫 상품(e=탄력3종) 사용. 구매사은은 store-wide 라 상품 1개면 된다.

⚠️⚠️ 2026-08-31 실측 결론 — **이 구매사은 적립은 PC 웹에서 신청이 안 된다.**
    '혜택 신청하기' 를 누르면 사이트가 alert 로 막는다:
        "'롯데홈쇼핑 앱'에서 응모 가능 합니다."
    행사안내 문구도 "롯데홈쇼핑 모바일 앱(APP)으로 구매 후 신청 시" 다.
    → 지시("cft에서 항상 적립신청")는 이 이벤트에 대해선 **사이트가 막아서 불가능**하다.
      적립은 폰 경로(phone_auto claim_lotte_reward)로 해야 한다.
    이 모듈은 그 사실을 **명확히 보고**하는 용도로 남긴다(앱 전용이 아닌 이벤트면 그대로 동작).

⚠️ 적립 신청만 한다 — 결제/장바구니는 건드리지 않는다.
⚠️ 이미 '신청완료' 면 idempotent 로 ok 처리하고 아무것도 누르지 않는다.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "buy"))
sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright   # noqa: E402
import sulwhasoo as S                             # noqa: E402

IGNORE_FILE = ROOT / "lotte_ignore_keywords.txt"
# 폰 경로(claim_lotte_reward)와 **같은 정규식**을 쓴다 — 단위·수치는 매일 바뀐다.
REWARD_PAT = re.compile(r"최대\s*[\d,]+\s*[%만]\s*적립")


def _ignore_keywords() -> list[str]:
    try:
        return [ln.strip() for ln in IGNORE_FILE.read_text(encoding="utf-8").splitlines() if ln.strip()]
    except Exception:
        return ["페이백", "L.CLUB", "선물", "선물하기", "무료가입", "창립", "이리오십쇼", "가정의달", "게이트페이지"]


def _default_goods_no() -> str:
    """조합 상품 중 하나의 롯데 goods_no. 구매사은은 store-wide 라 1개면 충분."""
    d = json.loads((ROOT / "hsmaster/config/sulwhasoo-ids.json").read_text(encoding="utf-8"))
    v = d["ids"]["e"]["lotte"]
    return v[0] if isinstance(v, list) else str(v)


def claim(page, goods_no: str) -> dict:
    """상품상세 → 구매사은 '최대 N%/N만 적립' → 광세일 행사페이지 → '혜택 신청하기'."""
    out = {"goods_no": goods_no}
    ignore = _ignore_keywords()
    # ★alert 를 삼키지 않고 기록한다 — 앱 전용 차단이 여기로 온다(2026-08-31).
    dialogs: list[str] = []

    def _on_dialog(d):
        dialogs.append(d.message)
        try:
            d.accept()
        except Exception:
            pass
    page.on("dialog", _on_dialog)
    url = f"https://www.lotteimall.com/goods/viewGoodsDetail.lotte?goods_no={goods_no}"
    page.goto(url, wait_until="domcontentloaded", timeout=20000)
    page.wait_for_timeout(2500)
    try:
        S.dismiss_popup(page)
    except Exception:
        pass

    # 1) 페이지 전체에서 '최대 N%/N만 적립' 링크 후보 — ignore 키워드 제외.
    cands = []
    for el in page.locator("a, button").all():
        try:
            t = (el.inner_text(timeout=600) or "").strip()
        except Exception:
            continue
        if not t or not REWARD_PAT.search(t.replace("\n", " ")):
            continue
        if any(k in t for k in ignore):
            continue
        cands.append((t, el))
    out["candidates"] = [t[:40] for t, _ in cands]
    if not cands:
        out["err"] = "구매사은 '최대 N%/N만 적립' 링크 미발견(광세일 행사상품 아닐 수 있음)"
        return out

    text, el = cands[0]
    out["card"] = text[:60]
    # 2) 새 탭으로 열릴 수 있다 — 둘 다 받는다.
    ev_page = page
    try:
        with page.context.expect_page(timeout=4000) as pop:
            el.click(timeout=5000)
        ev_page = pop.value
        ev_page.on("dialog", _on_dialog)
        ev_page.wait_for_load_state("domcontentloaded", timeout=15000)
    except Exception:
        page.wait_for_timeout(2500)
    ev_page.wait_for_timeout(1500)

    # 3) ★광세일 행사페이지 게이트 — 폰 경로와 같은 검증(오claim 방지).
    body = (ev_page.inner_text("body", timeout=8000) or "")
    if not ("행사안내" in body or "광세일" in body):
        out["err"] = f"광세일 적립 event 미도달(잘못된 카드: {text[:40]})"
        return out

    # 4) 이미 신청완료면 아무것도 누르지 않는다 (idempotent).
    if "신청완료" in body:
        out["already"] = True
        out["ok"] = True
        return out

    # 5) '혜택 신청하기' 클릭
    btn = None
    for el2 in ev_page.locator("a, button").all():
        try:
            t2 = (el2.inner_text(timeout=600) or "").strip()
        except Exception:
            continue
        if "신청" in t2 and ("혜택" in t2 or "하기" in t2) and "완료" not in t2:
            btn = el2
            out["button"] = t2[:40]
            break
    if btn is None:
        out["err"] = "'혜택 신청하기' 버튼 미발견"
        return out
    try:
        btn.click(timeout=6000)
    except Exception as e:
        out["err"] = f"신청 클릭 실패: {type(e).__name__}"
        return out
    ev_page.wait_for_timeout(2500)
    if dialogs:
        out["dialog"] = dialogs[-1]
        if "앱" in dialogs[-1]:
            out["err"] = f"앱 전용 이벤트 — PC 웹에서 신청 불가: {dialogs[-1]}"
            out["app_only"] = True
            return out
    after = (ev_page.inner_text("body", timeout=8000) or "")
    out["completed"] = ("신청이 완료" in after or "완료되었" in after or "신청완료" in after)
    # ★눌렀다는 사실만으로 ok 를 주지 않는다 (2026-08-31 — 이 파일 첫 판이 그 실수를 했다).
    #   완료 문구가 안 뜨면 **신청 안 된 것**으로 본다. 오늘 하루 실패의 공통 원인이
    #   '관측 못 했는데 ok 로 통과'였고, 여기서 같은 짓을 반복하면 적립이 조용히 새어나간다.
    out["ok"] = bool(out["completed"])
    if not out["completed"]:
        out["err"] = "신청 클릭했으나 완료 문구 미확인 — 신청 안 됐을 수 있다"
        out["after_snippet"] = " ".join(after[:300].split())
    return out


def check(page, goods_no: str) -> dict:
    """★확인 전용 — 아무것도 누르지 않는다 (사용자 지시 2026-09-09: "신청완료 확인은 CFT로,
    적립은 폰으로"). 상품상세의 '최대 N% 적립' 카드를 **전부** 열어보고, 각 행사페이지의
    '나의 적립현황' 문구와 버튼 상태(신청완료/신청하기)를 그대로 돌려준다.

    claim() 과 달리 첫 후보만 보지 않는다 — 상품상세엔 무관한 행사가 같이 걸려 있어서
    첫 카드가 그 주문의 행사가 아닐 수 있다(2026-09-09 실측: 20% 광세일 vs 10% 아모레)."""
    out = {"goods_no": goods_no, "events": []}
    ignore = _ignore_keywords()
    url = f"https://www.lotteimall.com/goods/viewGoodsDetail.lotte?goods_no={goods_no}"
    page.goto(url, wait_until="domcontentloaded", timeout=20000)
    page.wait_for_timeout(2500)
    try:
        S.dismiss_popup(page)
    except Exception:
        pass
    cands = []
    for el in page.locator("a, button").all():
        try:
            t = (el.inner_text(timeout=600) or "").strip()
        except Exception:
            continue
        if not t or not REWARD_PAT.search(t.replace("\n", " ")):
            continue
        if any(k in t for k in ignore):
            continue
        cands.append((" ".join(t.split())[:40], el))
    out["candidates"] = [t for t, _ in cands]
    if not cands:
        out["err"] = "'최대 N%/N만 적립' 카드 미발견"
        return out
    for text, el in cands:
        ev = page
        try:
            with page.context.expect_page(timeout=4000) as pop:
                el.click(timeout=5000)
            ev = pop.value
            ev.wait_for_load_state("domcontentloaded", timeout=15000)
        except Exception:
            page.wait_for_timeout(2500)
        ev.wait_for_timeout(1500)
        try:
            body = " ".join((ev.inner_text("body", timeout=8000) or "").split())
        except Exception:
            body = ""
        st = {"card": text}
        m = re.search(r"([\d,]+원 구매)", body)
        st["구매"] = m.group(1) if m else None
        m = re.search(r"(적립금\s*[\d,]+\s*원\s*적립가능)", body)
        st["적립"] = " ".join(m.group(1).split()) if m else None
        m = re.search(r"([\d,]+원 남았어요)", body)
        st["남음"] = m.group(1) if m else None
        st["신청완료"] = "신청완료" in body
        st["신청하기"] = ("혜택 신청하기" in body) and not st["신청완료"]
        st["최대달성"] = "최대 혜택을 달성" in body
        out["events"].append(st)
        if ev is not page:
            try:
                ev.close()
            except Exception:
                pass
        else:
            # go_back 은 롯데 행사페이지에서 domcontentloaded 를 못 잡고 타임아웃한다(2026-09-09 실측).
            # 상품 URL 로 다시 들어가는 편이 확실하다.
            page.goto(url, wait_until="domcontentloaded", timeout=20000)
            page.wait_for_timeout(2000)
    done = [e for e in out["events"] if e["신청완료"]]
    open_ = [e for e in out["events"] if e["신청하기"]]
    out["verdict"] = ("신청완료" if done else ("미신청(신청가능)" if open_ else "대상행사 없음"))
    out["ok"] = bool(done)
    return out


def main() -> int:
    if len(sys.argv) < 2:
        print("사용: python buy/lotte_reward.py [check] <account_idx> [account_idx...] [goods_no]")
        return 2
    if sys.argv[1] == "check":
        args = sys.argv[2:]
        idxs = [int(x) for x in args if x.isdigit() and len(x) <= 3]
        goods = next((x for x in args if x.isdigit() and len(x) > 3), None) or _default_goods_no()
        accounts = S.load_json(S.LOTTE_ACCOUNTS)["accounts"]
        port = S.resolve_cdp_port(int(S.CDP_PORT))
        rows = []
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
            ctx = browser.contexts[0] if browser.contexts else browser.new_context()
            for i in idxs:
                acc = accounts[i - 1]
                usable = [pg for pg in ctx.pages
                          if not pg.is_closed() and S.LOTTE_PW_CAMPAIGN_URL not in (pg.url or "")]
                page = usable[-1] if usable else ctx.new_page()
                page.goto(S.LOTTE_HOME, wait_until="domcontentloaded", timeout=20000)
                page.wait_for_timeout(1500)
                if not S.lotte_login(page, acc["id"], acc["pw"]):
                    rows.append({"idx": i, "id": acc["id"], "verdict": "LOGIN_FAIL"}); continue
                r = check(page, goods)
                r.update({"idx": i, "id": acc["id"]})
                rows.append(r)
                print(f"[#{i} {acc['id']}] {r.get('verdict')} — {r.get('events')}", flush=True)
        print("\n===== CFT 확인 요약 =====")
        for r in rows:
            print(f"  #{r['idx']} {r['id']}: {r.get('verdict')}")
        return 0
    idx = int(sys.argv[1])
    goods_no = sys.argv[2] if len(sys.argv) > 2 else _default_goods_no()

    accounts = S.load_json(S.LOTTE_ACCOUNTS)["accounts"]
    if idx < 1 or idx > len(accounts):
        print(f"[ERR] idx 범위 1~{len(accounts)}")
        return 1
    acc = accounts[idx - 1]
    print(f"[적립] 롯데 #{idx} {acc['id']} — goods_no={goods_no}", flush=True)

    port = S.resolve_cdp_port(int(S.CDP_PORT))
    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
        except Exception as e:
            print(f"[FATAL] CDP 연결 실패(CFT 9222 떠 있나?): {e}")
            return 1
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        usable = [pg for pg in ctx.pages
                  if not pg.is_closed() and S.LOTTE_PW_CAMPAIGN_URL not in (pg.url or "")]
        page = usable[-1] if usable else ctx.new_page()

        page.goto(S.LOTTE_HOME, wait_until="domcontentloaded", timeout=20000)
        page.wait_for_timeout(2000)
        if not S.lotte_login(page, acc["id"], acc["pw"]):
            print("[FATAL] 롯데 로그인 실패")
            return 1
        r = claim(page, goods_no)
        print(f"[적립] 결과: {r}", flush=True)
        return 0 if r.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
