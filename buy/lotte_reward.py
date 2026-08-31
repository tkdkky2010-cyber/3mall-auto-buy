"""롯데 구매사은 적립금 신청 — **CFT(PC 크롬)** 경로. (2026-08-31 신설)

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


def main() -> int:
    if len(sys.argv) < 2:
        print("사용: python buy/lotte_reward.py <account_idx> [goods_no]")
        return 2
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
