"""Hmall 19계정 PC 장바구니 채우기 전용 (결제 X).

★ 결제는 이 파일이 아니라 루트 buy.py → 폰 앱 인앱 결제다.
   (현대몰 PC checkout 은 끝나지 않아 폐기 — do_checkout/7자리코드/폰트리거 전부 제거됨)
이 파일은 cart 담기 흐름 + login/clear_cart/add_to_cart/apply_hpoint 공유 라이브러리.

사용법:
    python run.py            # 전체 계정 카트 담기 (INACTIVE 자동 제외)
    python run.py 4          # 단일 계정
    python run.py 5-19       # 범위
"""
from __future__ import annotations
import json
import os
import re
import sys
import time
from pathlib import Path
from dotenv import load_dotenv

# patchright (stealth) 먼저 시도, 실패 시 plain playwright fallback
# 최신 Chrome (147+) 에서 patchright connect_over_cdp 가 'Browser context management not supported' 에러 가능
PW_BACKEND = None
try:
    from patchright.sync_api import sync_playwright, Page, BrowserContext, TimeoutError as PlaywrightTimeoutError
    PW_BACKEND = "patchright"
except ImportError:
    pass
if PW_BACKEND is None:
    from playwright.sync_api import sync_playwright, Page, BrowserContext, TimeoutError as PlaywrightTimeoutError  # type: ignore
    PW_BACKEND = "playwright"

ROOT = Path(__file__).parent
PROJECT_ROOT = ROOT.parent
load_dotenv(ROOT / ".env")
sys.path.insert(0, str(PROJECT_ROOT))
from chrome_launcher import ensure_chrome, resolve_cdp_port  # noqa: E402  (CDP attach 스크립트는 필수)

ACCOUNTS_FILE = Path(os.environ.get("HMALL_CONFIG_PATH") or (PROJECT_ROOT / "hmall_config.json"))
PRODUCTS_FILE = ROOT / "products.json"
PLAN_FILE = Path(os.environ.get("CART_PLAN_FILE") or (ROOT / "cart_plan.json"))

INACTIVE_ACCOUNTS: list[int] = []  # 2026-06-23: #6 구매금지 해제 (사용자 지시, 다시 사용 가능)

LOGIN_URL = "https://www.hmall.com/mo/cob/loginForm"
CART_URL = "https://www.hmall.com/mo/odb/basktList"
ITEM_URL_FMT = "https://www.hmall.com/md/pda/itemPtc?slitmCd={slitmCd}{extra}"

# ★7분 대기는 **결제(buy)** 용이다. 이 파일은 담기 전용이라 계정 간 대기가 필요 없다
#   (사용자 지시 2026-08-05: "buy 에는 7분인데 담는거엔 필요없어"). 14계정 × 7분 = 91분 헛대기였다.
ACCOUNT_DELAY_SEC = 420  # 7분 — 결제 경로 전용. 본사 주소/IP 추적 회피.
CDP_PORT = os.environ.get("CDP_PORT", "9222")
CDP_ENDPOINT = f"http://127.0.0.1:{CDP_PORT}"

# Phase 3-A 안전장치: 결제하기 클릭 후 7자리 코드만 추출하고 폰 결제는 수동 (또는 Phase 3-B)

# CART_ONLY: 계정별로 plan 의 모든 상품을 장바구니에 누적 담기만 하고 checkout/결제는 안 함.
# (쿠폰 받기는 add_to_cart 안에서 '있으면 무조건' 수행 — 우수식품 규칙, auto_coupon 게이팅 제거됨)
# 계정당 clear_cart 1회 후 전 상품 add → 재시도 시에도 중복 없이 plan 그대로 재구성.
# ★이 파일은 담기 전용(결제 코드는 폐기됨) → CART_ONLY 기본 true, 계정 간 대기 0.
#   담기는 돈이 안 나가서 추적 위험이 없다(사용자 지시 2026-08-05).
CART_ONLY = os.environ.get("CART_ONLY", "true").lower() == "true"
CART_ONLY_DELAY_SEC = int(os.environ.get("CART_ONLY_DELAY_SEC", "0"))  # 담기 = 대기 불필요

# 캐러셀 <img alt="cardCdXX"> → 내부 brand 코드
CARD_CD_TO_BRAND = {
    "cardCd01": "BC",
    "cardCd02": "SAMSUNG",
    "cardCd03": "KB",
    "cardCd04": "HYUNDAI",
    "cardCd07": "SHINHAN",
    "cardCd08": "LOTTE",
    "cardCd10": "HANA",
    "cardCd40": "NH",
}

# cardCd → 결제수단변경 모달의 <li value="..."> 텍스트 (override 흐름용)
CARD_CD_TO_NAME = {
    "cardCd01": "비씨카드(페이북)",
    "cardCd02": "삼성카드",
    "cardCd03": "KB국민카드",
    "cardCd04": "현대카드",
    "cardCd07": "신한카드",
    "cardCd08": "롯데카드",
    "cardCd10": "하나카드",
    "cardCd40": "NH농협카드",
}

# cardCd → phone_auto/coords/apps/{name}.json 의 flow 매핑.
# 7자리 코드 추출 성공 시 자동 호출. 향후 카드 추가 시 이 dict 만 갱신.
CARD_CD_TO_PHONE_FLOW = {
    # (coords_name, flow_key, use_camera)
    # use_camera=True = FLAG_SECURE 화면 (screencap=검정) → Continuity 카메라 frame OCR
    "cardCd02": ("samsung_monimo", "flow_payment", True),   # 삼성 monimo — FLAG_SECURE
    "cardCd04": ("hyundai_card",   "flow_payment", False),  # 현대카드 — ADB screencap
    "cardCd08": ("lotte_card",     "flow_payment", False),  # 롯데카드 — ADB
    "cardCd10": ("hana_card",      "flow_payment", False),  # 하나카드 — ADB screencap (5/29 nFilter screencap 읽힘 확인, FLAG_SECURE 아님. 카메라/미러/portrait 불필요)
    "cardCd01": ("bc_paybook_isp", "flow_payment", False),  # BC 페이북 — ADB screencap (nf_key_serial, FLAG_SECURE 아님, 5/29 검증). 카메라 불필요
    "cardCd03": ("kb_kbpay",       "flow_payment", False),  # KB국민카드 — KB Pay 앱, FLAG_SECURE 비번(dump 모드, screencap/카메라 불필요). ⚠️DRAFT 라이브 미검증
    # cardCd40 NH 는 좌표 미완 — 추후
}

# 오늘의 결제수단 강제 지정 — 비우면 캐러셀 자동 판독
# 값 예: "삼성카드" / "현대카드" / "카카오페이" / "토스페이"
TODAY_BRAND_OVERRIDE = os.environ.get("TODAY_BRAND", "").strip()


def poll_until(check, timeout_ms: int = 8000, poll_ms: int = 200) -> bool:
    """check() 가 참을 돌려줄 때까지 폴링. 성공 True / 시간초과 False.

    ★**기존 고정 대기를 줄이지 않는다 — 그 뒤에 덧붙여 쓴다.** 목적은 '더 빨리'가 아니라
      '느리게 그려질 때 놓치지 않기'다. 이 몰들은 뼈대(HTML)를 먼저 주고 헤더 네비·카트 목록·
      옵션 레이어를 **나중에 JS로 채운다**. 그래서 `readyState==='complete'` 나 고정 대기 뒤
      **1회 읽기**로 판정하면, 아직 안 그려진 것을 '없다'로 오판한다.

    실제 사고:
      - 로그인 성공인데 헤더가 늦어 '로그아웃' 미발견 → 실패 판정 → #4·#6 스킵(2026-08-11).
        재시도는 이미 로그인 상태라 로그인 폼을 못 찾아 또 실패하는 2차 피해까지 났다.
      - 카트에 상품이 있는데 목록이 늦어 0건으로 읽힘 → '이미 비어있음' → 잔여물 위에 담김.
    """
    waited = 0
    while waited < timeout_ms:
        try:
            if check():
                return True
        except Exception:
            pass
        time.sleep(poll_ms / 1000)
        waited += poll_ms
    return False


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def build_account_plan(plan: dict, products: dict) -> dict[int, list[dict]]:
    out: dict[int, list[dict]] = {}
    for item in plan["items"]:
        pid = str(item["product"])
        if pid not in products:
            print(f"[WARN] cart_plan.json 에 없는 상품 # {pid} — 스킵")
            continue
        info = products[pid]
        for acc in item["accounts"]:
            out.setdefault(acc, []).append({
                "product_id": pid,
                "qty": item["qty"],
                "info": info,
            })
    return out


# ───────────── 기존 Hmall10/run_cart.py 흐름 (검증된 부분) ─────────────


def _hmall_clean(context: BrowserContext, page: Page, deep: bool = False) -> None:
    """Hmall 세션 폐기 — 시크릿 모드 동등 클린업."""
    try:
        logout_if_needed(page)
    except Exception:
        pass
    for domain in ("hmall.com", ".hmall.com", "www.hmall.com"):
        try:
            context.clear_cookies(domain=domain)
        except Exception:
            pass
    if deep:
        try:
            page.goto("https://www.hmall.com/mo/cob/loginForm", wait_until="domcontentloaded")
            page.wait_for_timeout(500)
            page.evaluate("() => { try { localStorage.clear(); sessionStorage.clear(); } catch(e) {} }")
        except Exception as e:
            print(f"  [storage clear warn] {e}")


def logout_if_needed(page: Page) -> None:
    try:
        page.goto("https://www.hmall.com/md/dpl/index", wait_until="domcontentloaded")
        page.wait_for_timeout(800)
        # ★헤더 네비(로그인/로그아웃)가 그려질 때까지 기다린 뒤 판정한다. 성급히 읽고 '이미 로그아웃'
        #   으로 오판하면 **로그아웃을 건너뛰어 이전 계정 세션으로 담기·조회가 돈다**
        #   (2026-08-11: 주문조회에서 #1·#2 가 같은 계정을 두 번 읽었다).
        #   둘 다 못 찾으면 아래로 진행 = 로그아웃 시도 쪽(안전한 방향).
        poll_until(lambda: any(k in page.inner_text("body") for k in ("로그아웃", "로그인")),
                   timeout_ms=8000)
        if "로그아웃" not in page.inner_text("body"):
            return
        page.evaluate("""
            () => {
                const els = Array.from(document.querySelectorAll('a, button'));
                const t = els.find(el => el.innerText.trim() === '로그아웃');
                if (t) t.click();
            }
        """)
        page.wait_for_timeout(1500)
    except Exception as e:
        print(f"  [logout warn] {e}")


def login(page: Page, account_id: str, account_pw: str) -> bool:
    captured_dialogs: list[str] = []

    def _handle_dialog(d):
        captured_dialogs.append(f"{d.type}:{d.message}")
        try:
            d.accept()
        except Exception:
            pass

    page.on("dialog", _handle_dialog)
    page.goto(LOGIN_URL, wait_until="domcontentloaded")
    page.wait_for_timeout(800)
    try:
        id_box = page.get_by_role("textbox", name="Hmall / H.Point 아이디")
        id_box.fill("")
        id_box.fill(account_id)
        page.wait_for_timeout(1200)  # 가이드 E.1: 자동입력 감지 회피
        pw_box = page.get_by_role("textbox", name="비밀번호")
        pw_box.fill("")
        pw_box.fill(account_pw)
        page.wait_for_timeout(600)
        login_btn = page.locator("button").filter(has_text="로그인").first
        login_btn.click()
        page.wait_for_load_state("domcontentloaded", timeout=15000)
        page.wait_for_timeout(1500)
        # ★위 1500ms 뒤에도 헤더 네비가 아직 안 그려질 수 있다 → 판정 근거가 뜰 때까지만 더 기다린다.
        #   (없앤 대기 없음. 이 폴링은 '결론이 날 때까지'만 돌고, 결론이 나면 즉시 빠진다.)
        poll_until(lambda: any(k in page.inner_text("body") for k in
                               ("로그아웃", "비밀번호 변경", "다른 로그인 수단", "로그인에 실패")),
                   timeout_ms=8000)
        body = page.inner_text("body")
        if "로그아웃" in body:
            return True
        # 비밀번호 변경 캠페인(pwdChangePup): 로그인 자체는 성공, 인터스티셜만 뜬 상태.
        # '90일 후 변경하기'(연기)로 닫고 재확인. ⚠️ '지금 변경하기' 절대 금지(비번 실제 변경됨).
        if "pwdChangePup" in page.url or "비밀번호 변경" in body:
            clicked = page.evaluate("""() => {
                const els = Array.from(document.querySelectorAll('a, button'));
                const t = els.find(el => /90일\\s*후\\s*변경/.test((el.innerText||'').trim()));
                if (t) { t.click(); return true; }
                return false;
            }""")
            if clicked:
                page.wait_for_timeout(1500)
                try:
                    page.goto("https://www.hmall.com/md/dpl/index", wait_until="domcontentloaded", timeout=10000)
                    page.wait_for_timeout(800)
                    if "로그아웃" in page.inner_text("body"):
                        print(f"  [PWCAMP] {account_id} — 비밀번호 변경 캠페인 '90일 후 변경'으로 닫음 → 로그인 성공")
                        return True
                except Exception:
                    pass
        if "다른 로그인 수단" in body or "로그인에 실패" in body:
            print(f"  [BLOCKED] {account_id} — 로그인 차단됨")
        elif "비밀번호" in body and "일치" in body:
            print(f"  [PW ERR] {account_id} — 비밀번호 불일치")
        else:
            print(f"  [LOGIN UNKNOWN] {account_id} — 로그아웃 텍스트 없음")
        _dump_login_debug(page, account_id, captured_dialogs)
        return False
    except Exception as e:
        print(f"  [LOGIN ERR] {account_id}: {e}")
        _dump_login_debug(page, account_id, captured_dialogs)
        return False


def _dump_login_debug(page: Page, account_id: str, dialogs: list[str]) -> None:
    try:
        url = page.url
    except Exception:
        url = "?"
    try:
        body = page.inner_text("body")[:300].replace("\n", " | ")
    except Exception:
        body = "?"
    print(f"      [DBG] url={url}")
    print(f"      [DBG] body[:300]={body}")
    print(f"      [DBG] alerts={dialogs or '(none)'}")


# 개별상품 체크박스 = input[type=checkbox][name=backet] (2026-08-05 §10 DOM 실측).
# 헤더/그룹 전체선택은 name 이 없어 자연히 제외된다 → 이걸로 세면 **배송 그룹이 몇 개든** 정확하다.
_JS_CART_ROWS = """() => Array.from(document.querySelectorAll('input[type=checkbox][name=backet]'))
    .map(cb => { const r = cb.closest('div.pdwrap');
                 return r ? (r.innerText||'').split('\\n')[0].trim() : ''; })"""
_JS_SELECT_ALL = """() => {
    const cbs = Array.from(document.querySelectorAll('input[type=checkbox][name=backet]'));
    let n = 0;
    for (const cb of cbs) { if (!cb.checked) { cb.click(); n++; } }   // ★.checked 대입 금지(핸들러 미동작)
    return [cbs.length, n];
}"""


def cart_rows(page: Page) -> list[str]:
    """카트에 실제로 담긴 상품명. 개별 체크박스 기준이라 그룹 구조에 안 흔들린다."""
    try:
        return [s for s in page.evaluate(_JS_CART_ROWS)]
    except Exception:
        return []


_JS_CART_ROWS = r"""() => Array.from(document.querySelectorAll('input[type=checkbox][name=backet]'))
    .map(cb => {
        const row = cb.closest('div.pdwrap');
        if (!row) return '';
        const lines = (row.innerText || '').split('
').map(s => s.trim()).filter(Boolean);
        return lines[0] || '';
    }).filter(Boolean)"""


def cart_items(page: Page) -> list:
    """카트에 담긴 상품명 목록. ★개별상품 체크박스(name=backet) 기준 — 배송 그룹이 몇 개든 정확하고
    하단 '최근 본 상품' 캐러셀이 섞이지 않는다(rate-check/hmall.py cart_items 와 같은 규칙)."""
    try:
        return page.evaluate(_JS_CART_ROWS) or []
    except Exception as e:
        print(f"    [cart] 목록 읽기 실패: {e}")
        return []


def clear_cart(page: Page) -> None:
    """장바구니 비우기 — **비었는지 확인될 때까지** 최대 3회. (사용자 지시: 담기 전 기존 카트 삭제)

    ★2026-08-06 재작성. 종전 3가지 결함으로 **안 지워졌는데 '비우기 완료'** 가 찍혔다:
      ① '일반상품' 그룹 체크박스 **하나만** 눌렀다 → 배송 그룹이 나뉘면 다른 그룹 상품이 그대로 남는다.
         (#10 서정희pick 올리브오일 ×2 잔존 — 8/5 이월 판매중단품)
      ② '일반상품' 라벨이 없으면 **아무것도 안 하고 return** (#12 스키니랩 ×1 잔존).
      ③ 삭제 후 **검증이 없었다** → 남아도 완료로 보고.
    → 개별 체크박스(name=backet)를 **전부** 클릭하고, 삭제 후 **0건 확인**될 때까지 재시도한다.
    """
    try:
        for attempt in (1, 2, 3):
            page.goto(CART_URL, wait_until="domcontentloaded")
            page.wait_for_timeout(1500)
            # ★카트 목록은 body 가 찬 **뒤에** JS 로 그려진다. 1500ms 뒤 1회 읽기로 0건이 나오면
            #   '이미 비어있음' 으로 오판하고 **잔여물 위에 담겨 측정·결제가 오염**된다.
            #   → 상품행이 보이거나 '비었다' 문구가 뜰 때까지(둘 중 먼저) 기다린 뒤 읽는다.
            poll_until(lambda: bool(cart_rows(page)) or any(
                k in page.inner_text("body") for k in ("장바구니가 비어", "담긴 상품이 없")),
                timeout_ms=6000)
            rows = cart_rows(page)
            if not rows:
                body = page.inner_text("body")
                if attempt == 1 and ("장바구니가 비어" in body or "담긴 상품이 없" in body):
                    print("    [cart] 이미 비어있음")
                else:
                    print(f"    [cart] 비우기 완료 (검증 0건)")
                return
            total, newly = page.evaluate(_JS_SELECT_ALL)
            page.wait_for_timeout(600)
            delete_btn = page.locator("button.btn-linelgray").filter(has_text="선택삭제").first
            if delete_btn.count() == 0:
                delete_btn = page.locator("button").filter(has_text="선택삭제").first
            if delete_btn.count() == 0:
                # ★품절/구매불가 상품은 체크박스가 disabled 라 전체선택에 안 걸리고,
                #   '선택삭제' 대신 '품절/불가 삭제' 버튼만 뜬다 (2026-08-26 실측: 품절된 스키니랩 1건이
                #   Hmall 27조합 전부를 clear_cart 실패로 중단시켜 공급률이 통째로 안 나왔다).
                delete_btn = page.locator("button").filter(has_text="품절/불가 삭제").first
            if delete_btn.count() == 0:
                print(f"    [cart] ⚠️ 선택삭제 버튼 없음 — 잔여 {len(rows)}건 {rows}")
                return
            delete_btn.click()
            page.wait_for_timeout(900)
            for txt in ("예", "확인", "삭제"):
                confirm = page.locator("button").filter(has_text=txt).first
                if confirm.count() > 0 and confirm.is_visible():
                    confirm.click()
                    page.wait_for_timeout(500)
                    break
            print(f"    [cart] 삭제 시도{attempt} — 선택 {newly}/{total}건")
        left = cart_rows(page)
        if left:
            # 조용히 넘어가면 잔여물 위에 담겨 결제/측정이 오염된다(8/2 조합 오염 사고와 같은 계열).
            print(f"    [cart] ⚠️⚠️ 3회 시도 후에도 잔여 {len(left)}건 — {left}\n"
                  f"           수동 삭제 필요. 이대로 담으면 결제에 섞인다.", flush=True)
    except Exception as e:
        print(f"    [cart] 비우기 실패: {e}")


def click_coupon_receive(page: Page) -> None:
    try:
        btn = page.locator("button").filter(has_text="쿠폰 받기").first
        if btn.count() > 0 and btn.is_visible():
            btn.click()
            page.wait_for_timeout(700)
            for b in page.locator("button").filter(has_text="다운").all():
                try:
                    if b.is_visible() and "다운 완료" not in b.inner_text():
                        b.click()
                        page.wait_for_timeout(500)
                        for txt in ("확인", "예"):
                            ok = page.locator("button").filter(has_text=txt).first
                            if ok.count() > 0 and ok.is_visible():
                                ok.click()
                                page.wait_for_timeout(400)
                                break
                except Exception:
                    pass
            closed = page.evaluate("""
                () => {
                    const btns = Array.from(document.querySelectorAll('button'));
                    const target = btns.find(b => {
                        if (b.offsetParent === null) return false;
                        const span = b.querySelector('span.hiding');
                        return span && span.textContent.trim() === '닫기';
                    });
                    if (target) { target.click(); return true; }
                    return false;
                }
            """)
            if not closed:
                close = page.locator("button").filter(has_text="닫기").first
                if close.count() > 0 and close.is_visible():
                    close.click()
            page.wait_for_timeout(400)
    except Exception as e:
        print(f"    [coupon skip] {e}")


def add_to_cart(page: Page, product_id: str, info: dict, qty: int) -> bool:
    url = ITEM_URL_FMT.format(slitmCd=info["slitmCd"], extra=info.get("url_extra", ""))
    page.goto(url, wait_until="domcontentloaded")
    page.wait_for_timeout(1500)
    body = page.inner_text("body")
    if "판매가 중단" in body or "판매 중단" in body:
        print(f"    [SKIP] #{product_id} {info['name']} — 판매중단")
        return False

    # 우수식품 규칙: 쿠폰 있으면 무조건 받는다 (사용자 지시). auto_coupon 플래그로 게이팅하지 않음 —
    # click_coupon_receive 는 '쿠폰 받기' 버튼이 없으면 내부에서 no-op 이므로 항상 호출해도 안전.
    click_coupon_receive(page)

    # 쿠폰 레이어 처리 중 페이지가 이탈하는 경우가 있음(2026-07-28 #25: '확인/닫기' 매칭 버튼이
    # 네비게이션 유발 → Execution context destroyed → btn-purchase 30s timeout).
    # 상품 페이지를 벗어났으면 1회 복귀 후 진행.
    if info["slitmCd"] not in page.url:
        print(f"    [recover] 쿠폰 처리 중 페이지 이탈({page.url[:60]}) → 상품페이지 재진입")
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_timeout(1500)

    try:
        purchase = page.locator("button.btn-purchase").first
        purchase.click()
    except Exception as e:
        print(f"    [ERR] 구매하기 버튼 못 찾음: {e}")
        return False
    page.wait_for_timeout(1500)

    # 구매하기 후 옵션 레이어 판별: 옵션 행이 없으면 단일 상품(선택 불필요),
    # 여러 개면 option_index([선택 N], 기본 1=최상단) 선택. 라벨 못 찾으면 최상단 fallback.
    option_idx = info.get("option_index") or 1
    try:
        choices = page.locator("span.choice-num.title")
        if choices.count() == 0:
            print(f"    [opt] 단일 옵션 — 선택 불필요")
        else:
            opt = choices.filter(has_text=f"[선택 {option_idx}]").first
            if opt.count() == 0:
                print(f"    [opt] [선택 {option_idx}] 라벨 못 찾음 → 최상단 옵션 선택")
                opt = choices.first
            opt.click()
            page.wait_for_timeout(700)
    except Exception as e:
        print(f"    [WARN] 옵션 클릭 실패: {e}")

    if qty > 1:
        try:
            plus_btn = page.locator("button.btn-plus").first
            for _ in range(qty - 1):
                plus_btn.click()
                page.wait_for_timeout(200)
        except Exception as e:
            print(f"    [WARN] 수량 + 클릭 실패: {e}")

    try:
        ok = page.evaluate("""
            () => {
                const candidates = Array.from(document.querySelectorAll('button.btn-cart'))
                    .filter(b => b.offsetParent !== null && !b.classList.contains('btn-linered'));
                if (candidates.length === 0) return { ok: false };
                const buyNow = Array.from(document.querySelectorAll('button'))
                    .find(b => b.offsetParent !== null && b.textContent.trim() === '바로구매');
                if (buyNow) {
                    const sibling = candidates.find(c => buyNow.parentElement && buyNow.parentElement.contains(c));
                    if (sibling) { sibling.click(); return { ok: true, src: 'sibling' }; }
                }
                candidates[0].click();
                return { ok: true, src: 'first' };
            }
        """)
        if not ok or not ok.get("ok"):
            print(f"    [ERR] 장바구니 버튼 못 찾음")
            return False
    except Exception as e:
        print(f"    [ERR] 장바구니 클릭 실패: {e}")
        return False

    page.wait_for_timeout(1200)
    try:
        for txt in ("확인", "닫기"):
            btn = page.locator("button").filter(has_text=txt).first
            if btn.count() > 0 and btn.is_visible():
                btn.click()
                page.wait_for_timeout(300)
                break
    except Exception:
        pass
    print(f"    [OK] #{product_id} {info['name']} x{qty}")
    return True


# ───────────── Phase 3-A 신규: checkout 흐름 ─────────────


def detect_carousel_slides(page: Page) -> list[dict]:
    """카드할인 캐러셀 모든 슬라이드의 메타데이터 list 반환 (클릭 X).

    Hmall 결제 페이지 DOM 변경 대응 (5/25):
    - 옛 구조: .swiper-slide (deprecated, Swiper.js 제거됨)
    - 새 구조: img[alt^="cardCd"] 의 _32o920j ancestor 컨테이너
      카드 1개만 DOM 에 렌더링되는 게 정상 (가장 왼쪽 = 최고 할인율).
      여러 카드인 날은 swipe 안 해도 N개 다 DOM 에 있을 수도 있음 (둘 다 처리).
    """
    js = """
        () => {
            const cls = (el) => (el && typeof el.className === 'string') ? el.className : '';
            const h2 = Array.from(document.querySelectorAll('h2'))
                .find(h => h.textContent.trim() === '카드할인');
            if (!h2) return [];

            // ── 1차: 새 _32o920j 구조 (현재 Hmall DOM) ──
            const section = h2.closest('section') || h2.closest('div');
            if (section) {
                const imgs = Array.from(section.querySelectorAll('img[alt^="cardCd"]'));
                const slides = [];
                for (const img of imgs) {
                    // _32o920j ancestor (개별 카드 컨테이너)
                    let container = img;
                    for (let d = 0; d < 10 && container; d++) {
                        if (cls(container).includes('_32o920j')) break;
                        container = container.parentElement;
                    }
                    if (!container) continue;
                    const strong = container.querySelector('strong');
                    if (!strong) continue;
                    const m = (strong.textContent || '').match(/(\\d+)\\s*%/);
                    if (!m) continue;
                    // brand 텍스트 — img.alt 만으로 매핑 가능 (CARD_CD_TO_NAME). p 텍스트 fallback.
                    const ps = Array.from(container.querySelectorAll('p'))
                        .map(p => p.textContent.trim()).filter(t => t);
                    const brand = ps.find(t => t.includes('카드') || t.includes('페이')) || ps[0] || '';
                    slides.push({
                        cardCd: img.alt,
                        brand: brand,
                        percent: parseInt(m[1]),
                        isCard: img.alt.startsWith('cardCd'),
                        left: Math.round(container.getBoundingClientRect().left),
                    });
                }
                if (slides.length > 0) return slides;
            }

            // ── 2차: 옛 .swiper-slide 구조 (DOM 롤백 대비 fallback) ──
            let scope = h2.closest('div');
            for (let lvl = 0; lvl < 5 && scope; lvl++) {
                const slides = Array.from(scope.querySelectorAll('.swiper-slide'))
                    .filter(s => {
                        if (s.offsetParent === null) return false;
                        const img = s.querySelector('img[alt]');
                        const strong = s.querySelector('strong');
                        return img && strong && /\\d+\\s*%/.test(strong.textContent || '');
                    });
                if (slides.length > 0) {
                    return slides.map(s => {
                        const img = s.querySelector('img[alt]');
                        const strong = s.querySelector('strong');
                        const m = (strong.textContent || '').match(/(\\d+)\\s*%/);
                        const ps = Array.from(s.querySelectorAll('p'));
                        return {
                            cardCd: img.alt,
                            brand: (ps[0]?.textContent || '').trim(),
                            percent: m ? parseInt(m[1]) : 0,
                            isCard: img.alt.startsWith('cardCd'),
                            left: Math.round(s.getBoundingClientRect().left),
                        };
                    });
                }
                scope = scope.parentElement;
            }
            return [];
        }
    """
    return page.evaluate(js) or []


def select_card_via_change_modal(page: Page, card_name: str) -> dict | None:
    """캐러셀에 즉시할인으로 안 떠있는 카드 선택 — 결제수단변경 modal 경로.
    결제수단변경 → 신용카드 선택 dropdown → li[value="<card_name>"] click.
    card_name 은 CARD_CD_TO_NAME 의 value 와 동일 (예: "비씨카드(페이북)", "현대카드").
    Returns pick-like dict {cardCd, brand, percent=0, isCard=True, left=0} 또는 None.
    """
    try:
        btn = page.get_by_text("결제수단변경", exact=False).first
        if btn.count() == 0:
            print(f"    [WARN] '결제수단변경' button 없음")
            return None
        btn.click()
        page.wait_for_timeout(2200)
        dd = page.get_by_text("신용카드 선택", exact=False).first
        if dd.count() == 0:
            print(f"    [WARN] '신용카드 선택' dropdown 없음")
            return None
        dd.click()
        page.wait_for_timeout(1500)
        li = page.locator(f'li[value="{card_name}"]').first
        if li.count() == 0:
            print(f"    [WARN] li[value=\"{card_name}\"] 없음 — dropdown 안 열렸거나 카드 미발견")
            return None
        li.click()
        page.wait_for_timeout(1500)
        name_to_cd = {v: k for k, v in CARD_CD_TO_NAME.items()}
        cd = name_to_cd.get(card_name, "")
        return {"cardCd": cd, "brand": card_name, "percent": 0, "isCard": True, "left": 0, "via": "modal"}
    except Exception as e:
        print(f"    [WARN] select_card_via_change_modal 예외: {e}")
        return None


def pick_carousel_slide(slides: list[dict], override: str = "") -> dict | None:
    """슬라이드 list에서 하나 선택. override 있으면 매칭, 없으면 최고 % + leftmost."""
    if not slides:
        return None
    if override:
        # cardCd → 카드명 역매핑으로 비교
        for s in slides:
            full_name = CARD_CD_TO_NAME.get(s.get("cardCd", ""), "")
            if full_name == override or override in s.get("brand", "") or s.get("brand", "") in override:
                return s
        return None  # override 매칭 슬라이드 없음
    cards = [s for s in slides if s.get("isCard")]
    pool = cards if cards else slides
    pool.sort(key=lambda s: (-s.get("percent", 0), s.get("left", 0)))
    return pool[0]


def click_carousel_slide(page: Page, cardCd: str) -> bool:
    """카드할인 캐러셀의 슬라이드를 mouse.click(box center) 으로 선택.
    locator.click() 은 React onClick handler 가 chain 에 없는 element 에 실패 — bounding box mouse click 이 robust.

    DOM 변경 대응 (5/25): 새 컨테이너 = _32o920j (Swiper.js 제거됨).
    옛 .swiper-slide fallback 도 유지 (롤백 대비).
    """
    candidates = [
        ("_32o920j", page.locator(f'div._32o920j:has(img[alt="{cardCd}"])').first),
        (".swiper-slide outer", page.locator(f'.swiper-slide:has(img[alt="{cardCd}"])').first),
        ("img ancestor", page.locator(f'img[alt="{cardCd}"]').first),
    ]
    for label, slide in candidates:
        if slide.count() == 0:
            continue
        try:
            slide.scroll_into_view_if_needed(timeout=3000)
            slide.click()  # locator click — v3 에서 동작 검증된 방식
            page.wait_for_timeout(1500)
            print(f"    [OK] 캐러셀 슬라이드 click (cardCd={cardCd}, via {label})")
            return True
        except Exception as e:
            print(f"    [WARN] {label} 클릭 실패: {e}")
            continue
    print(f"    [WARN] 슬라이드 cardCd={cardCd} 미발견 (모든 셀렉터 실패)")
    return False


def _read_mypage_paid_count(page: Page) -> int | None:
    """hmall mypage 의 '결제완료' 주문 수 읽기 — 결제 전후 비교용.
    page 가 닫혔으면 context.pages 에서 살아있는 hmall page 또는 새 page 생성.
    """
    try:
        ctx = page.context
        # 살아있는 hmall page 우선
        target = None
        for p in ctx.pages:
            try:
                if not p.is_closed() and "hmall.com" in p.url:
                    target = p
                    break
            except Exception:
                continue
        if target is None:
            target = ctx.pages[-1] if ctx.pages else ctx.new_page()   # 기존 탭 재사용(포커스 강탈 방지)
        target.goto("https://www.hmall.com/mo/mpf/selectMyPageMain",
                  wait_until="domcontentloaded", timeout=15000)
        target.wait_for_timeout(2000)
        txt = target.evaluate("document.body.innerText")
        m = re.search(r"(\d+)\s*\n*\s*결제완료", txt)
        return int(m.group(1)) if m else None
    except Exception as e:
        print(f"    [VERIFY] mypage paid count err: {e}")
        return None


# ───────────── H.Point 적립 신청 ─────────────


def apply_hpoint(page: Page, prmoNo: str) -> dict:
    """결제 완료 후 H.Point 적립 신청 (evntHPointDtl 페이지 진입 + 신청하기 클릭).

    Returns {"success": bool, "already_done": bool, "error": str|None}.
    """
    out = {"success": False, "already_done": False, "error": None}
    try:
        url = f"https://www.hmall.com/md/eva/evntHPointDtl?prmoNo={prmoNo}"
        page.goto(url, wait_until="domcontentloaded", timeout=15000)
        page.wait_for_timeout(2000)
        # 이미 신청 완료 (.complete class)
        if page.locator(".get-reward-btn.complete").count() > 0:
            out["success"] = True
            out["already_done"] = True
            print(f"    [HPOINT] prmo={prmoNo} 이미 신청 완료 — skip")
            return out
        # 신청하기 버튼
        btn = page.locator(".get-reward-btn:not(.complete)").first
        if btn.count() == 0:
            out["error"] = "신청하기 버튼 없음"
            return out
        btn.click()
        page.wait_for_timeout(2500)
        # 확인 popup 자동 닫기 (있으면)
        for txt in ("확인", "예", "신청"):
            ok = page.locator("button").filter(has_text=txt).first
            if ok.count() > 0 and ok.is_visible():
                try:
                    ok.click()
                    page.wait_for_timeout(800)
                    break
                except Exception:
                    pass
        # 완료 검증
        page.wait_for_timeout(1000)
        if page.locator(".get-reward-btn.complete").count() > 0:
            out["success"] = True
            print(f"    [HPOINT] prmo={prmoNo} ✓ 신청 완료")
        else:
            out["success"] = True  # 일단 클릭 됐으니 success (검증은 다음 진입 시)
            print(f"    [HPOINT] prmo={prmoNo} 신청 클릭됨 (검증 미확인)")
    except Exception as e:
        out["error"] = f"{e}"
    return out


def process_account(context: BrowserContext, idx: int, account: dict, items: list[dict],
                     cdp_mode: bool = False) -> tuple[int, int, bool, dict | None]:
    """현대몰 PC 장바구니 채우기 (결제 X — 결제는 buy.py → 폰 앱).
    같은 계정의 여러 product 를 clear 1회 후 누적으로 담는다.
    """
    print(f"\n=== #{idx} {account['id']} — 담을 상품 {len(items)}개 (PC 카트 담기) ===")
    # ★새 탭(new_page)=macOS Chrome 창 포커스 강탈 → 기존 탭 재사용 (2026-07-10, sulwhasoo 롯데 패턴).
    #   계정마다 login()이 logout+재로그인, _hmall_clean이 상태 정리 → 탭 유지해도 안전(close 안 함).
    page = context.pages[-1] if context.pages else context.new_page()

    if cdp_mode:
        _hmall_clean(context, page)

    logged_in = login(page, account["id"], account["pw"])
    if not logged_in and cdp_mode:
        print(f"  [RETRY] #{idx} {account['id']} — 쿠키/스토리지 폐기 후 재시도")
        _hmall_clean(context, page, deep=True)
        page.wait_for_timeout(2000)
        logged_in = login(page, account["id"], account["pw"])

    if not logged_in:
        print(f"  [SKIP] #{idx} {account['id']} — 로그인 실패")
        return (0, len(items), False, None)   # 탭 재사용 — close 안 함(포커스 강탈 방지)

    # run.py = 현대몰 PC 장바구니 채우기 전용. 결제는 buy.py → 폰 앱 인앱(현대=hmall_hyundai_buy).
    # clear 1회 → plan 전 상품 누적 담기 (checkout/결제 없음).
    clear_cart(page)
    success = 0
    for ci, entry in enumerate(items, 1):
        print(f"\n  ── #{idx} (cart) {ci}/{len(items)} — product {entry['product_id']} x{entry['qty']} ──")
        if add_to_cart(page, entry["product_id"], entry["info"], entry["qty"]):
            success += 1
        else:
            print(f"  [SKIP] #{idx} {ci}번째 add_to_cart 실패")
        if ci < len(items):
            page.wait_for_timeout(3000)
    # ★담긴 줄 수 검증 (2026-09-02 신설). `add_to_cart` 의 True 는 **버튼을 눌렀다**는 뜻이지
    #   담겼다는 뜻이 아니다. 실측: #1 에서 3번(갈색견과) 옵션 레이어가 안 떠
    #   `[opt] 단일 옵션`(실제론 옵션 4개) + `수량 + 클릭 실패` 뒤에도 `[OK]` 가 찍혔고,
    #   카트엔 1건만 담겼는데 요약은 `담기 2/2` 였다. 조용히 한 품목을 빠뜨린다.
    #   (같은 형태를 rate-check/hmall.py·buy/sulwhasoo.py 에서도 고쳤다 — 여기만 남아 있었다.)
    try:
        page.goto(CART_URL, wait_until="domcontentloaded")
        # ★카트 페이지는 비동기 렌더 — 고정 대기로 읽으면 **덜 그려진 상태**를 보고 0건으로 오판한다
        #   (2026-09-02 실측: 1.5초 고정 대기 시 실제 2건인데 0건으로 읽혀 재시도가 돌았다).
        #   기대 건수가 나올 때까지 폴링하고, 안 나오면 마지막 값으로 판정한다.
        rows = []
        for _ in range(10):                      # 최대 ~10초
            page.wait_for_timeout(1000)
            rows = cart_items(page)
            if len(rows) >= len(items):
                break
        if len(rows) != len(items):
            # ⚠️ **경고 전용이다 — success 를 낮추지 않는다.** (2026-09-02)
            #   실행 중 이 컨텍스트에서 카트를 읽으면 실제로 담겨 있어도 0건으로 나온다
            #   (외부에서 재로그인해 읽으면 정상 2건). 원인 미규명. success 를 낮추면
            #   run.py 의 재시도가 돌면서 **멀쩡한 카트를 비우고 다시 담는다** — 오판의 대가가
            #   크므로 판정에는 쓰지 않고 사람이 볼 신호로만 남긴다.
            #   → 담기 결과는 `verify_hmall_cart.py` 처럼 **재로그인 후 외부 검수**로 확인할 것.
            print(f"  [WARN] #{idx} 카트 판독 {len(rows)}건 (기대 {len(items)}건) — "
                  f"실행 중 판독은 신뢰도가 낮다. 외부 검수로 확인할 것")
        else:
            print(f"  [OK] #{idx} 카트 검증 — {len(rows)}건")
    except Exception as e:
        print(f"  [WARN] #{idx} 카트 검증 예외: {e}")
    print(f"  ✓ #{idx} {account['id']} 담기 {success}/{len(items)}")
    return (success, len(items), True, None)   # 탭 재사용 — close 안 함(포커스 강탈 방지)


def main() -> int:
    if not ACCOUNTS_FILE.exists():
        print(f"[FATAL] {ACCOUNTS_FILE} 미존재")
        return 1

    accounts = load_json(ACCOUNTS_FILE)["accounts"]
    products = load_json(PRODUCTS_FILE)
    plan = load_json(PLAN_FILE)
    account_plan = build_account_plan(plan, products)

    arg_indices: list[int] | None = None
    if len(sys.argv) > 1:
        a = sys.argv[1]
        try:
            if "-" in a:
                lo_s, hi_s = a.split("-", 1)
                lo = int(lo_s) if lo_s else 1
                hi = int(hi_s) if hi_s else len(accounts)
                arg_indices = list(range(lo, hi + 1))
            else:
                arg_indices = [int(a)]
        except ValueError:
            print(f"[ERR] argv[1]은 정수 또는 'N-M' 형식: {a}")
            return 1

    all_indices = list(range(1, len(accounts) + 1))
    if arg_indices is not None:
        target_indices = [i for i in arg_indices if i not in INACTIVE_ACCOUNTS and i in all_indices]
        skipped = [i for i in arg_indices if i in INACTIVE_ACCOUNTS]
        if skipped:
            print(f"[INFO] INACTIVE 스킵: {skipped}")
    else:
        target_indices = [idx for idx in all_indices if idx not in INACTIVE_ACCOUNTS]

    print(f"[INFO] 처리할 계정: {target_indices}")

    # 포트 체인: 9222 막히면 9223→9224 (같은 CFT). 죽음/탭0 자동복구 포함.
    global CDP_PORT, CDP_ENDPOINT
    CDP_PORT = str(resolve_cdp_port(int(CDP_PORT)))
    CDP_ENDPOINT = f"http://127.0.0.1:{CDP_PORT}"
    print(f"[INFO] CDP endpoint={CDP_ENDPOINT}")

    summary = []
    print(f"[INFO] PW backend: {PW_BACKEND}")
    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(CDP_ENDPOINT, slow_mo=500)
        except Exception as e:
            err_msg = str(e)
            print(f"[WARN] {PW_BACKEND} CDP 연결 실패: {err_msg[:200]}")
            # Chrome 147+에서 patchright가 'Browser context management not supported' 던지면 playwright로 fallback
            if PW_BACKEND == "patchright" and "Browser context management" in err_msg:
                print("[INFO] plain playwright로 재시도...")
                try:
                    from playwright.sync_api import sync_playwright as sync_pw_plain
                except ImportError:
                    print("[FATAL] playwright 미설치 — pip install playwright")
                    return 1
                # 새 sync 컨텍스트 필요 — outer with 닫고 다시 시작
                pass  # fall through; handle below
            else:
                print(f"        Chrome을 --remote-debugging-port={CDP_PORT} 옵션으로 띄웠는지 확인")
                return 1
        else:
            return _run_with_browser(browser, accounts, target_indices, account_plan, summary)
    # patchright 실패 시 plain playwright로 재실행
    from playwright.sync_api import sync_playwright as sync_pw_plain
    print(f"[INFO] PW backend (fallback): playwright")
    with sync_pw_plain() as p:
        try:
            browser = p.chromium.connect_over_cdp(CDP_ENDPOINT, slow_mo=500)
        except Exception as e:
            print(f"[FATAL] playwright CDP 연결도 실패: {e}")
            return 1
        return _run_with_browser(browser, accounts, target_indices, account_plan, summary)


def _run_with_browser(browser, accounts, target_indices, account_plan, summary) -> int:

    context = browser.contexts[0] if browser.contexts else browser.new_context()

    def _process(idx_list, pass_label):
        local_results: dict[int, tuple] = {}
        for idx in idx_list:
            account = accounts[idx - 1]
            items = account_plan.get(idx, [])
            print(f"\n  ─── {pass_label} #{idx} {account['id']} (items={len(items)}) ───")
            try:
                ok, total, cleared, ckt = process_account(context, idx, account, items, cdp_mode=True)
                local_results[idx] = (idx, account["id"], ok, total, cleared, ckt)
            except Exception as e:
                print(f"  [FATAL] #{idx} {account['id']}: {e}")
                local_results[idx] = (idx, account["id"], 0, len(items), False, None)
            time.sleep(CART_ONLY_DELAY_SEC if CART_ONLY else ACCOUNT_DELAY_SEC)
        return local_results

    # 1차 pass
    first = _process(target_indices, pass_label="1차")
    summary.extend(first[idx] for idx in target_indices)

    # 실패 계정 추출 (담은 게 plan 의 모든 row보다 적으면 fail)
    failed_indices = [
        idx for idx in target_indices
        if first[idx][2] < first[idx][3]  # ok < total
    ]
    if failed_indices:
        print(f"\n========= 재시도 (1차 fail: {failed_indices}) =========")
        retry = _process(failed_indices, pass_label="재시도")
        # summary 갱신 — 재시도 결과를 우선
        for i, row in enumerate(summary):
            if row[0] in retry:
                summary[i] = retry[row[0]]

    print("\n========= SUMMARY =========")
    for idx, aid, ok, total, cleared, ckt in summary:
        if total == 0:
            mark = "□" if cleared else "✗"
        else:
            mark = "✓" if ok == total else ("△" if ok > 0 else "✗")
        cart_mark = "🧹" if cleared else "·"
        ck = ""
        if ckt is not None:
            if ckt["success"]:
                if ckt.get("is_pay"):
                    ck = f" 📱 {ckt['card_brand']}/{ckt.get('qr_pay', 'QR')}"
                else:
                    ck = f" 💳 {ckt['card_brand']}/{ckt['code']}"
            else:
                ck = f" ✗ckt:{ckt['error'][:40] if ckt['error'] else '?'}"
        print(f"  {mark} {cart_mark} #{idx:2d} {aid:30s}  {ok}/{total}{ck}")
    print(f"  ─────────────────────────")
    total_ok = sum(s[2] for s in summary)
    total_all = sum(s[3] for s in summary)
    cleared_count = sum(1 for s in summary if s[4])
    ckt_ok = sum(1 for s in summary if s[5] and s[5].get("success"))
    print(f"  비우기: {cleared_count}/{len(summary)}")
    print(f"  담기: {total_ok}/{total_all}")
    print(f"  결제 코드 추출 성공: {ckt_ok}/{len(summary)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
