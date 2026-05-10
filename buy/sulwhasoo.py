"""설화수 자동구매 — OKCashbag 경유 (롯데 + 갤러리아).

Hmall 식품(buy/run.py)과 다른 모듈:
- OKCashbag 메인 → 쇼핑몰(롯데/갤러리아) 클릭 → "복사" → "쇼핑몰로 이동" → popup
- popup에서 그 몰 로그인 + 카트 + 결제
- 결제 마지막 OK캐쉬백 회원번호 4×4 입력 → 추가 적립

가이드: cart codegen 결과(롯데/갤러리아) + Sulwhasoo_Supply_Rate.md.
주의: Hmall 설화수는 OKCashbag 경유 X (카드 할인 못 받음). 별도 흐름 또는 buy/run.py 활용.

사용법:
    bash hsmaster/scripts/launch-hmall-chrome.sh   # CFT 띄우기
    python3 buy/sulwhasoo.py lotte 1               # 몰=lotte/galleria, account idx=1
    python3 buy/sulwhasoo.py galleria 1            # 갤러리아
"""
from __future__ import annotations
import json
import os
import re
import sys
import time
from pathlib import Path
from dotenv import load_dotenv

# sulwhasoo는 OKCashbag/galleria/lotte 모두 정상 user session (cookies 영구) 사용 →
# stealth (patchright) 불필요. 단순한 playwright 사용으로 Chrome 148 호환성 확보.
from playwright.sync_api import sync_playwright, Page, BrowserContext, TimeoutError as PlaywrightTimeoutError
PW_BACKEND = "playwright"

ROOT = Path(__file__).parent
PROJECT_ROOT = ROOT.parent
load_dotenv(ROOT / ".env")

OKCASHBAG_FILE = PROJECT_ROOT / "okcashbag.json"
LOTTE_ACCOUNTS = PROJECT_ROOT / "lotte.json"
GALLERIA_ACCOUNTS = PROJECT_ROOT / "galleria.json"

CDP_PORT = os.environ.get("CDP_PORT", "9222")
CDP_ENDPOINT = f"http://127.0.0.1:{CDP_PORT}"
DRY_PAYMENT = os.environ.get("DRY_PAYMENT", "true").lower() == "true"

OKCASHBAG_URL = "https://www.okcashbag.com/"
GALLERIA_HOME = "https://www.galleria.co.kr/main/initMain.action"
LOTTE_HOME = "https://www.lotteimall.com/"

# 갤러리아 상품 정보 (hsmaster/config/sulwhasoo-ids.json 기준)
GALLERIA_PRODUCTS = {
    "b": {"name": "윤조3종",            "goods_no": "2502913432"},
    "c": {"name": "자음2종",            "goods_no": "2502913250"},
    "d": {"name": "본윤2종",            "goods_no": "2206740470"},
    "e": {"name": "탄력3종",            "goods_no": "2502913294"},
    "f": {"name": "윤조에센스90",        "goods_no": "2204658942"},
    "g": {"name": "자음생2종",          "goods_no": "2408977039"},
    "h": {"name": "자음생크림리치세트", "goods_no": "2408977059"},
}

# 롯데 상품 정보 (hsmaster/config/sulwhasoo-ids.json 기준, 월 1회 갱신)
LOTTE_PRODUCTS = {
    "b": {"name": "윤조3종",            "goods_no": "2923416935"},
    "c": {"name": "자음2종",            "goods_no": "2923389602"},
    "d": {"name": "본윤2종",            "goods_no": "2008758498"},
    "e": {"name": "탄력3종",            "goods_no": "2923406968"},
    "f": {"name": "윤조에센스90",        "goods_no": "2091578259"},
    "g": {"name": "자음생2종",          "goods_no": "2719761525"},
    "h": {"name": "자음생크림리치세트", "goods_no": "2719761746"},
}

# 11개 고정 조합 (가이드 섹션 7)
COMBOS = {
    1:  [("g", 2), ("h", 1)],
    2:  [("d", 2), ("g", 2)],
    3:  [("d", 4), ("e", 1)],
    4:  [("e", 2), ("h", 1)],
    5:  [("b", 2), ("d", 2)],
    6:  [("e", 2), ("f", 2)],
    7:  [("c", 3), ("h", 1)],
    8:  [("c", 3), ("d", 2)],
    9:  [("c", 1), ("f", 4)],
    10: [("c", 2), ("f", 3)],
    11: [("f", 5)],
}


# ───────────── 공통 ─────────────


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def split_ok_number(num16: str) -> list[str]:
    """OK캐쉬백 16자리 → 4자리 4개."""
    digits = re.sub(r"\D", "", num16)
    if len(digits) != 16:
        raise ValueError(f"OK 번호는 16자리여야 함: {digits!r}")
    return [digits[0:4], digits[4:8], digits[8:12], digits[12:16]]


def pre_logout_mall(page: Page, mall: str) -> None:
    """⚠️ OKCashbag 진입 전 필수 단계.
    OKCashbag → mall popup 진입 시 이미 로그인되어 있으면 OKCashbag tracking 활성 안 됨.
    그래서 사전에 mall에 직접 가서 로그아웃 후 다시 OKCashbag 통해 진입해야 함.
    """
    home = LOTTE_HOME if mall == "lotte" else GALLERIA_HOME
    try:
        page.goto(home, wait_until="domcontentloaded", timeout=15000)
        page.wait_for_timeout(2500)
        body = page.inner_text("body")
        if "로그아웃" in body:
            try:
                page.get_by_role("link", name="로그아웃").first.click(timeout=5000)
                page.wait_for_timeout(2500)
                print(f"  [OK] {mall} 사전 로그아웃 (OKCashbag tracking 활성 위해)")
            except Exception as e:
                print(f"  [WARN] {mall} 로그아웃 실패: {e}")
        else:
            print(f"  [INFO] {mall} 이미 로그아웃 상태")
    except Exception as e:
        print(f"  [WARN] 사전 로그아웃 단계 실패: {e}")


def dismiss_popup(page: Page) -> None:
    """페이지에 popup 뜨면 '닫기' 텍스트 찾아 클릭. 없으면 그냥 return."""
    for _ in range(3):  # 여러 popup 동시 뜨는 경우
        clicked = False
        for kind in ("button", "link"):
            try:
                close_el = page.get_by_role(kind, name=re.compile(r"^닫기$|닫기\s*$")).first
                if close_el.count() > 0:
                    close_el.click(timeout=1500)
                    clicked = True
                    page.wait_for_timeout(400)
                    break
            except Exception:
                continue
        if not clicked:
            break


# ───────────── OKCashbag 진입 ─────────────


def enter_via_okcashbag(page: Page, mall: str) -> Page:
    """OKCashbag 메인 → 해당 몰 진입. popup page 반환.

    mall: 'lotte' (홈쇼핑→롯데홈쇼핑) | 'galleria' (종합몰→갤러리아)
    """
    page.goto(OKCASHBAG_URL, wait_until="domcontentloaded", timeout=15000)
    page.wait_for_timeout(2000)

    # 로그인 상태 체크 — Profile 6 cookies 기반
    body = page.inner_text("body")
    if "로그아웃" not in body:
        print("[ERROR] OKCashbag 비로그인 — Chrome 창에서 카카오 로그인 1회 후 재시도")
        raise RuntimeError("OKCashbag not logged in")

    if mall == "lotte":
        page.get_by_role("button", name="홈쇼핑").click()
        page.wait_for_timeout(800)
        page.get_by_role("img", name="롯데홈쇼핑").click()
    elif mall == "galleria":
        page.get_by_role("button", name="종합몰").click()
        page.wait_for_timeout(800)
        page.get_by_role("img", name="갤러리아").click()
    else:
        raise ValueError(f"unknown mall: {mall}")

    page.wait_for_timeout(1500)
    # "복사" 버튼: OK 번호 클립보드 복사 (자동화에서는 직접 입력 시 안 씀, 단계 호환 위해 클릭만)
    try:
        page.get_by_role("button", name="복사").click(timeout=3000)
        page.wait_for_timeout(500)
    except Exception:
        pass

    # "쇼핑몰로 이동하기" → popup 으로 mall 열림
    with page.expect_popup() as popup_info:
        page.get_by_role("button", name="쇼핑몰로 이동하기").click()
    mall_page = popup_info.value
    # 최소 대기 — 일부 mall popup은 inactive 시 auto-close
    try:
        mall_page.wait_for_load_state("domcontentloaded", timeout=10000)
    except Exception:
        pass
    mall_page.wait_for_timeout(1500)
    print(f"[OK] OKCashbag → {mall} popup: {mall_page.url[:80]}")
    return mall_page


# ───────────── 갤러리아 ─────────────


def galleria_login(page: Page, account_id: str, account_pw: str) -> bool:
    """갤러리아 로그인 (이미 로그인된 다른 계정이면 로그아웃 후 재로그인)."""
    body = page.inner_text("body")
    if "로그아웃" in body:
        try:
            page.get_by_role("link", name="로그아웃").click()
            page.wait_for_timeout(1500)
        except Exception:
            pass

    try:
        page.get_by_role("link", name="로그인").click(timeout=5000)
    except Exception:
        # 이미 로그인 페이지일 수 있음
        pass
    page.wait_for_timeout(1500)
    try:
        page.get_by_role("textbox", name="아이디 또는 이메일").fill(account_id)
        page.get_by_role("button", name="다음").click()
        page.wait_for_timeout(1200)
        page.get_by_role("textbox", name="비밀번호").fill(account_pw)
        page.get_by_role("textbox", name="비밀번호").press("Enter")
        page.wait_for_load_state("domcontentloaded", timeout=15000)
        page.wait_for_timeout(2000)
        return "로그아웃" in page.inner_text("body")
    except Exception as e:
        print(f"  [LOGIN ERR] {account_id}: {e}")
        return False


def galleria_clear_cart(page: Page) -> None:
    """갤러리아 장바구니 비우기. 빈 카트 자동 skip. popup 자동 dismiss."""
    try:
        # 카트 link 또는 직접 URL로 이동
        try:
            page.get_by_role("link", name=re.compile(r"장바구니 이동")).click(timeout=5000)
        except Exception:
            page.goto("https://www.galleria.co.kr/order/cart.do", wait_until="domcontentloaded", timeout=10000)
        page.wait_for_load_state("domcontentloaded", timeout=10000)
        page.wait_for_timeout(2000)

        # popup 뜨면 닫기
        dismiss_popup(page)

        body = page.inner_text("body")
        if "담긴 상품이 없" in body or "비어 있" in body or "장바구니에 담긴 상품수0" in body.replace(" ", ""):
            print("    [cart] 이미 비어있음")
            return
        try:
            page.get_by_text("전체선택", exact=True).click(timeout=5000)
            page.wait_for_timeout(500)
        except Exception:
            print("    [cart] '전체선택' 미발견 — skip")
            return
        page.once("dialog", lambda dialog: dialog.accept())
        page.get_by_role("button", name="삭제", exact=True).click()
        page.wait_for_timeout(1500)
        print("    [cart] 비우기 완료")
    except Exception as e:
        print(f"    [cart] 비우기 실패: {e}")


def galleria_add_product_by_url(page: Page, goods_no: str, qty: int) -> bool:
    """갤러리아 상품 URL 직접 진입 → 쿠폰 다운로드 → 수량 +N → 장바구니."""
    url = f"https://www.galleria.co.kr/goods/initDetailGoods.action?goods_no={goods_no}"
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=15000)
        page.wait_for_timeout(2000)
        dismiss_popup(page)

        # 쿠폰 영역 펼치기 + 모든 쿠폰 다운로드
        try:
            coupon_btn = page.get_by_role("button").filter(has_text=re.compile(r"\[화장\].*설화수.*\d+%")).first
            coupon_btn.click(timeout=3000)
            page.wait_for_timeout(800)
            page.get_by_role("button", name="모든 쿠폰 다운로드").click(timeout=3000)
            page.wait_for_timeout(800)
            page.get_by_role("button", name="확인", exact=True).click(timeout=2000)
            page.wait_for_timeout(500)
            page.get_by_role("button", name="사용가능 쿠폰안내 레이어 닫기").click(timeout=2000)
            page.wait_for_timeout(500)
        except Exception:
            pass

        # 수량 (qty - 1) 번 + 클릭
        for _ in range(qty - 1):
            try:
                page.locator("#gds_sltd").get_by_role("button", name="상품수량증가").click(timeout=2000)
                page.wait_for_timeout(300)
            except Exception:
                break

        # 장바구니 담기
        page.get_by_role("button", name="장바구니 상품담기 레이어 열기").click(timeout=5000)
        page.wait_for_timeout(800)
        page.get_by_role("button", name="동의").click(timeout=2000)
        page.wait_for_timeout(800)
        try:
            page.get_by_role("button", name="계속 쇼핑").click(timeout=2000)
        except Exception:
            pass
        page.wait_for_timeout(1000)
        return True
    except Exception as e:
        print(f"    [ERR] {goods_no} qty={qty} 추가 실패: {e}")
        return False


def galleria_add_combo(page: Page, combo_no: int) -> bool:
    """갤러리아 조합 N (1~11) 자동 카트 추가."""
    combo = COMBOS.get(combo_no)
    if not combo:
        print(f"    [ERR] 조합 {combo_no} 정의 없음")
        return False
    for sku, qty in combo:
        prod = GALLERIA_PRODUCTS.get(sku)
        if not prod:
            print(f"    [ERR] sku '{sku}' 상품 없음")
            return False
        print(f"    [INFO] {sku} ({prod['name']}) × {qty}")
        if not galleria_add_product_by_url(page, prod["goods_no"], qty):
            return False
    return True


def galleria_checkout(page: Page, ok_number: str) -> dict:
    """갤러리아 카트 → 주문하기 → OK 캐쉬백 조회/사용 → 번호 입력 → DRY 모드 종료."""
    out = {"success": False, "error": None}
    try:
        # 카트 link 클릭 (URL 직접보다 안정)
        try:
            page.get_by_role("link", name=re.compile(r"장바구니 이동")).click(timeout=5000)
        except Exception:
            page.goto("https://www.galleria.co.kr/order/cart.do", wait_until="domcontentloaded", timeout=10000)
        page.wait_for_load_state("domcontentloaded", timeout=10000)
        page.wait_for_timeout(2500)
        dismiss_popup(page)

        # 전체선택 + 주문하기
        page.get_by_text("전체선택", exact=True).click()
        page.wait_for_timeout(500)
        page.get_by_role("button", name="주문하기", exact=True).click()
        page.wait_for_load_state("domcontentloaded", timeout=15000)
        page.wait_for_timeout(3000)
        dismiss_popup(page)

        # 1) 배송메시지: select '직접입력' (self) → "선물포장" 입력
        try:
            page.locator("#ord_memo_cont_select1").select_option("self")
            page.wait_for_timeout(500)
            page.locator("#ord_memo_cont1").fill("선물포장")
            page.wait_for_timeout(300)
            print("    [OK] 배송메시지: 선물포장")
        except Exception as e:
            print(f"    [WARN] 배송메시지 입력 실패: {e}")

        # 2) 포인트 전체사용 — id #point_all_2300 (G포인트/LIVE 포인트 X, 별도)
        try:
            page.locator("#point_all_2300").click(timeout=3000)
            page.wait_for_timeout(800)
            print("    [OK] 포인트 전체사용")
        except Exception as e:
            print(f"    [WARN] 포인트 전체사용 실패: {e}")

        # 3) OK캐시백 사용 동의 체크박스 ON — label "개인정보 및 주문정보 제공에 동의합니다"
        # input이 label로 가려져 있으므로 label 클릭 (또는 force=True)
        # ⚠️ "[필수] LIVE 포인트 사용을 위한 개인정보 제3자 동의" (#ckTerms_hlive) 는 X
        try:
            page.locator("label[for='afcr_okcashback_checkbox']").click(timeout=3000)
            page.wait_for_timeout(800)
            print("    [OK] OK캐시백 사용 동의 체크박스 ON")
        except Exception as e:
            # fallback: force check
            try:
                page.locator("#afcr_okcashback_checkbox").check(force=True, timeout=2000)
                print("    [OK] OK캐시백 체크박스 (force)")
            except Exception as e2:
                print(f"    [WARN] OK캐시백 체크박스 실패: {e2}")

        # OK 번호 입력 — 4자리×4
        ok_parts = split_ok_number(ok_number)
        page.get_by_role("textbox", name="OK캐쉬백 포인트 카드번호").wait_for(state="visible", timeout=8000)
        page.get_by_role("textbox", name="OK캐쉬백 포인트 카드번호").fill(ok_parts[0])
        page.get_by_role("textbox", name="두번째자리 입력").fill(ok_parts[1])
        page.get_by_role("textbox", name="세번째자리 입력").fill(ok_parts[2])
        page.get_by_role("textbox", name="네번째자리 입력").fill(ok_parts[3])
        page.wait_for_timeout(500)
        print(f"    [OK] OK 번호 입력: {ok_parts[0]}-...-{ok_parts[3]}")

        # 4) 약관 전체 동의 — #ckTerms_all 체크 (보통 default true이지만 안전 차원)
        try:
            page.locator("#ckTerms_all").check(force=True, timeout=2000)
            page.wait_for_timeout(300)
        except Exception:
            pass

        # 5) 결제하기 — id `regist_order_button` 직접 클릭
        # alert이 떠서 popup 막힐 수 있으므로 dialog handler 등록
        dialog_messages = []
        page.on("dialog", lambda d: (dialog_messages.append(d.message), d.dismiss()))
        try:
            with page.expect_popup(timeout=15000) as pay_popup_info:
                page.locator("#regist_order_button").click()
            pay_page = pay_popup_info.value
            pay_page.wait_for_load_state("domcontentloaded", timeout=10000)
            print(f"    [OK] 결제 popup 도달: {pay_page.url[:60]}")
            if DRY_PAYMENT:
                print("    [DRY] 결제 popup 도달 — SK Payment 로그인 X (Phase 3-B 폰 자동화 대기)")
        except Exception as e:
            print(f"    [WARN] 결제하기 클릭 실패: {e}")
            if dialog_messages:
                print(f"    [DEBUG] dialog 메시지: {dialog_messages}")

        out["success"] = True
        return out
    except Exception as e:
        out["error"] = f"checkout 예외: {e}"
        return out


# ───────────── 롯데 (skeleton — 시연 정보 기반, smoke test 후 보강) ─────────────


def lotte_login(page: Page, account_id: str, account_pw: str) -> bool:
    """롯데 로그인. codegen 흐름 그대로:
    page1(mall popup) → 로그인 클릭 → page2(login popup) → fill → Enter →
    page3(guide popup) → page2.close() → page3 처리 후 close.
    page1(mall popup)은 절대 close 안 함.
    """
    context = page.context
    try:
        # 클릭 전 page focus (anti-bot detection 우회 도움)
        try:
            page.bring_to_front()
        except Exception:
            pass

        # context.expect_page (more stable than page.expect_popup)
        with context.expect_page() as new_page_info:
            page.get_by_role("link", name="로그인 로그인").click()
        login_page = new_page_info.value

        login_page.wait_for_load_state("domcontentloaded", timeout=15000)
        login_page.get_by_role("textbox", name="아이디 또는 이메일").click()
        login_page.get_by_role("textbox", name="아이디 또는 이메일").fill(account_id)
        login_page.get_by_role("textbox", name="아이디 또는 이메일").press("Tab")
        login_page.get_by_role("textbox", name="비밀번호(영문+숫자+특수 8~15자)").fill(account_pw)

        # Enter → guide popup (없으면 timeout)
        guide_page = None
        try:
            with context.expect_page(timeout=8000) as guide_info:
                login_page.get_by_role("textbox", name="비밀번호(영문+숫자+특수 8~15자)").press("Enter")
            guide_page = guide_info.value
        except PlaywrightTimeoutError:
            pass

        # login popup close
        try:
            login_page.close()
        except Exception:
            pass

        # guide popup 처리 + close
        if guide_page is not None:
            try:
                guide_page.get_by_role("link", name="일간 보이지 않기").click(timeout=3000)
            except Exception:
                pass
            try:
                guide_page.close()
            except Exception:
                pass

        # mall popup이 살아있으면 거기서 로그아웃 텍스트 검증, 아니면 fresh page 찾기
        try:
            if page.is_closed():
                lps = [p for p in context.pages if 'lotteimall' in p.url and not p.is_closed()]
                if not lps:
                    return False
                page = lps[-1]
            page.wait_for_timeout(2500)
            body = page.inner_text("body")
            return "로그아웃" in body
        except Exception:
            return False
    except Exception as e:
        print(f"  [LOGIN ERR] {account_id}: {e}")
        return False


def lotte_clear_cart(page: Page) -> None:
    """롯데 장바구니 비우기. '일반 (N/N)' 전체선택 + '선택삭제' + dialog accept."""
    try:
        page.get_by_role("link", name="장바구니 장바구니").click(timeout=5000)
        page.wait_for_load_state("domcontentloaded", timeout=10000)
        page.wait_for_timeout(2000)
        body = page.inner_text("body")
        if "장바구니에 담긴 상품이 없" in body or "0/0" in body:
            print("    [cart] 이미 비어있음")
            return
        # "일반 (N/N)" 텍스트 — 클릭해서 전체선택 토글
        try:
            general_label = page.get_by_text(re.compile(r"일반\s*\(\d+/\d+\)")).first
            general_label.click()
            page.wait_for_timeout(500)
        except Exception:
            pass
        # 선택삭제 — dialog accept
        page.once("dialog", lambda dialog: dialog.accept())
        page.get_by_role("link", name="선택삭제").click()
        page.wait_for_timeout(1500)
        print("    [cart] 비우기 완료")
    except Exception as e:
        print(f"    [cart] 비우기 실패: {e}")


def lotte_add_product_by_url(page: Page, goods_no: str, qty: int) -> bool:
    """롯데 상품 URL 직접 진입 → 쿠폰 다운로드 → 옵션 → 수량 +N → 장바구니."""
    url = f"https://www.lotteimall.com/goods/viewGoodsDetail.lotte?goods_no={goods_no}"
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=15000)
        page.wait_for_timeout(2000)
        dismiss_popup(page)

        # 쿠폰받기 → 쿠폰 전체 다운로드 → 닫기
        try:
            page.get_by_role("button", name="쿠폰받기").click(timeout=3000)
            page.wait_for_timeout(800)
            page.get_by_role("button", name="쿠폰 전체 다운로드").click(timeout=2000)
            page.wait_for_timeout(800)
            page.get_by_role("button", name="닫기", exact=True).click(timeout=2000)
            page.wait_for_timeout(500)
        except Exception:
            pass

        # 옵션 (타입 선택 → 세트)
        try:
            page.get_by_role("link", name="타입 선택").click(timeout=3000)
            page.wait_for_timeout(500)
            page.get_by_role("link", name="세트", exact=True).click(timeout=3000)
            page.wait_for_timeout(500)
        except Exception:
            pass

        # 수량 (qty - 1) 번 + 클릭
        for _ in range(qty - 1):
            try:
                page.get_by_role("button", name="+").first.click(timeout=2000)
                page.wait_for_timeout(300)
            except Exception:
                break

        # 장바구니 담기
        page.locator("#saveCart-btn").click(timeout=5000)
        page.wait_for_timeout(1500)
        return True
    except Exception as e:
        print(f"    [ERR] 롯데 {goods_no} qty={qty} 추가 실패: {e}")
        return False


def lotte_add_combo(page: Page, combo_no: int) -> bool:
    """롯데 조합 N (1~11) 자동 카트 추가."""
    combo = COMBOS.get(combo_no)
    if not combo:
        print(f"    [ERR] 조합 {combo_no} 정의 없음")
        return False
    for sku, qty in combo:
        prod = LOTTE_PRODUCTS.get(sku)
        if not prod:
            print(f"    [ERR] sku '{sku}' 상품 없음")
            return False
        print(f"    [INFO] {sku} ({prod['name']}) × {qty}")
        if not lotte_add_product_by_url(page, prod["goods_no"], qty):
            return False
    return True


def lotte_checkout(page: Page, ok_number: str, account_id: str = "") -> dict:
    """롯데 카트 → 주문 → 주소 선택 → OK 번호 입력 (DRY)."""
    out = {"success": False, "error": None}
    try:
        page.get_by_role("link", name="장바구니 장바구니").click()
        page.wait_for_timeout(2000)
        # "일반 (X/Y)" 토글 — 클릭하면 전체선택/해제 토글. 전체선택 상태 보장 위해
        # 텍스트 매칭 후 click. 만약 이미 전체선택 (X==Y)이면 click하면 해제 → 다시 click.
        try:
            general_label = page.get_by_text(re.compile(r"일반\s*\(\d+/\d+\)")).first
            label_text = general_label.text_content() or ""
            general_label.click(timeout=5000)
            page.wait_for_timeout(800)
            # 클릭 후 (X/Y) 다시 확인 — X != Y이면 한번 더 토글
            m = re.match(r"일반\s*\((\d+)/(\d+)\)", label_text)
            if m and m.group(1) == m.group(2):
                # 이미 전체 선택이었음 → click으로 해제됨 → 다시 토글
                general_label.click(timeout=3000)
                page.wait_for_timeout(800)
        except Exception as e:
            print(f"    [WARN] 일반 (N/N) 토글 실패: {e}")
        page.get_by_role("link", name=re.compile(r"주문하기")).click()
        page.wait_for_load_state("domcontentloaded", timeout=15000)
        page.wait_for_timeout(2500)

        # 0) 주소 선택 — lotte_address_map.json에서 account별 dlvp_sn 매핑
        if account_id:
            try:
                addr_map_path = Path(__file__).resolve().parent.parent / "lotte_address_map.json"
                addr_map = json.loads(addr_map_path.read_text())
                dlvp_sn = addr_map.get(account_id, {}).get("matched")
                if dlvp_sn:
                    page.locator("#base_rmit_nm").select_option(value=str(dlvp_sn))
                    page.wait_for_timeout(1500)
                    print(f"    [OK] 주소 선택: dlvp_sn={dlvp_sn}")
                else:
                    print(f"    [WARN] {account_id} 주소 매핑 없음 — 기본배송지 사용")
            except Exception as e:
                print(f"    [WARN] 주소 매핑 적용 실패: {e}")

        # 1) 포장함 (codegen: get_by_text("포장함").first/nth click)
        page.get_by_text("포장함").first.click()
        page.wait_for_timeout(500)
        page.get_by_text("포장함").nth(1).click()
        page.wait_for_timeout(500)
        print("    [OK] 포장함 클릭")

        # 2) 동의합니다 (#assent checkbox)
        page.locator("#assent").scroll_into_view_if_needed()
        page.locator("#assent").check(force=True)
        page.wait_for_timeout(500)
        print("    [OK] 동의합니다 체크")

        # 3) direct 쿠폰 — modal 열고 각 select 첫 옵션 (placeholder 다음)
        ifr = page.frame_locator('iframe[name="modal_ifrmWrap"]')
        page.locator("#modal_btn_coupon").scroll_into_view_if_needed()
        page.locator("#modal_btn_coupon").click(force=True)
        page.wait_for_timeout(2000)
        for i in range(8):
            sel = ifr.locator(f"#direct_coupon_{i}")
            if sel.count() == 0:
                break
            sel.select_option(index=1)
            page.wait_for_timeout(200)
        ifr.get_by_role("link", name="확인").click()
        page.wait_for_timeout(1000)
        print("    [OK] direct 쿠폰 적용")

        # 4) plus 쿠폰 — 조회/적용 nth(1), 각 select 첫 옵션
        page.get_by_role("link", name="조회/적용").nth(1).click()
        page.wait_for_timeout(2000)
        for i in range(8):
            sel = ifr.locator(f"#plus_coupon_{i}")
            if sel.count() == 0:
                break
            sel.select_option(index=1)
            page.wait_for_timeout(200)
        ifr.get_by_role("link", name="적용").click()
        page.wait_for_timeout(1000)
        print("    [OK] plus 쿠폰 적용")

        # 5) "동의함" 이미지
        page.get_by_role("img", name="동의함").click()
        page.wait_for_timeout(500)

        # 6) OK 입력
        ok_parts = split_ok_number(ok_number)
        page.locator("#ok_yes").check()
        page.get_by_role("textbox", name="OK캐쉬백 회원번호 첫번째 네자리 입력").fill(ok_parts[0])
        page.get_by_role("textbox", name="OK캐쉬백 회원번호 두번째 네자리 입력").fill(ok_parts[1])
        page.get_by_role("textbox", name="OK캐쉬백 회원번호 세번째 네자리 입력").fill(ok_parts[2])
        page.get_by_role("textbox", name="OK캐쉬백 회원번호 마지막 네자리 입력").fill(ok_parts[3])
        page.wait_for_timeout(500)
        print(f"    [OK] OK 번호 입력 완료: {ok_parts[0]}-...-{ok_parts[3]}")

        # 7) OK캐시백 모두사용 (활성화돼있을 때만)
        try:
            page.get_by_role("link", name="모두사용").first.click(timeout=2000)
            page.wait_for_timeout(500)
            print("    [OK] OK캐시백 모두사용")
        except Exception:
            pass

        # 8) L포인트 모두사용 (활성화돼있을 때만 — #modal_btn_lpoint_all_use 가 visible 인지 확인)
        lpoint_used = False
        try:
            btn = page.locator("#modal_btn_lpoint_all_use")
            if btn.count() > 0 and btn.is_visible():
                btn.click()
                page.wait_for_timeout(500)
                lpoint_used = True
                print("    [OK] L포인트 모두사용")
        except Exception:
            pass

        # 9) 카드사 select — 오늘 청구할인 카드. 매일 업데이트 필요
        # TODO: 매일 청구할인 안내 (#card_corp_dc_html) 파싱해서 자동 매핑
        TODAY_CARD_CODE = "016"  # 국민카드 (2026-05-10)
        try:
            page.locator("#iscm_cd").select_option(TODAY_CARD_CODE)
            page.wait_for_timeout(500)
            print(f"    [OK] 카드사 선택: {TODAY_CARD_CODE}")
        except Exception as e:
            print(f"    [WARN] 카드사 선택 실패: {e}")

        if DRY_PAYMENT:
            print("    [DRY] 결제하기 버튼 클릭 X")
            out["success"] = True
            out["lpoint_used"] = lpoint_used
            return out

        # 10) 결제하기 1차 + 사업자등록번호 (L포인트 사용 시) + 결제하기 2차
        page.once("dialog", lambda d: d.dismiss())
        page.get_by_role("link", name="결제하기").click()
        page.wait_for_timeout(2000)
        if lpoint_used:
            page.get_by_role("radio", name="사업자등록번호").check()
            page.get_by_role("textbox", name="현금영수증 발행 번호입력").fill("5071815504")
            page.wait_for_timeout(500)
            print("    [OK] 사업자등록번호 입력 (L포인트 사용)")
            page.get_by_role("link", name="결제하기").click()
        out["success"] = True
        out["lpoint_used"] = lpoint_used
        return out
    except Exception as e:
        out["error"] = f"checkout 예외: {e}"
        return out


# ───────────── 메인 ─────────────


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 1
    mall = sys.argv[1].lower()
    try:
        idx = int(sys.argv[2])
    except ValueError:
        print(f"[ERR] account idx는 정수: {sys.argv[2]}")
        return 1
    combo_no = int(sys.argv[3]) if len(sys.argv) > 3 else 1

    # 계정 로드
    if mall == "lotte":
        accounts = load_json(LOTTE_ACCOUNTS)["accounts"]
    elif mall == "galleria":
        accounts = load_json(GALLERIA_ACCOUNTS)["accounts"]
    else:
        print(f"[ERR] mall은 'lotte' 또는 'galleria': {mall}")
        return 1
    if idx < 1 or idx > len(accounts):
        print(f"[ERR] idx 범위 1~{len(accounts)}")
        return 1
    acc = accounts[idx - 1]

    # OK 번호 로드
    ok_cfg = load_json(OKCASHBAG_FILE)
    ok_number = ok_cfg["ok_number"]

    print(f"[INFO] mall={mall}, account #{idx} {acc['id']}, DRY={DRY_PAYMENT}")
    print(f"[INFO] PW backend: {PW_BACKEND}")

    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(CDP_ENDPOINT)
        except Exception as e:
            print(f"[FATAL] CDP 연결 실패: {e}")
            return 1
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        ok_page = context.new_page()

        # ★ 사전 로그아웃 (OKCashbag tracking 활성 위해 필수)
        pre_logout_mall(ok_page, mall)

        try:
            mall_page = enter_via_okcashbag(ok_page, mall)
        except Exception as e:
            print(f"[FATAL] OKCashbag 진입 실패: {e}")
            ok_page.close()
            return 1

        if mall == "lotte":
            ok = lotte_login(mall_page, acc["id"], acc["pw"])
        else:
            ok = galleria_login(mall_page, acc["id"], acc["pw"])
        if not ok:
            print("[FATAL] mall 로그인 실패")
            mall_page.close()
            ok_page.close()
            return 1
        print(f"[OK] {mall} 로그인")

        # 카트 비우기 + 상품 추가 (조합)
        if mall == "galleria":
            galleria_clear_cart(mall_page)
            print(f"[INFO] 조합 {combo_no} 추가: {COMBOS.get(combo_no)}")
            if not galleria_add_combo(mall_page, combo_no):
                print("[FATAL] 조합 추가 실패")
                return 1
            result = galleria_checkout(mall_page, ok_number)
        else:
            lotte_clear_cart(mall_page)
            print(f"[INFO] 조합 {combo_no} 추가: {COMBOS.get(combo_no)}")
            if not lotte_add_combo(mall_page, combo_no):
                print("[FATAL] 조합 추가 실패")
                return 1
            result = lotte_checkout(mall_page, ok_number, account_id=acc["id"])

        if result["success"]:
            print(f"\n✓ [{mall}] #{idx} 진입 + OK번호 입력 완료 (DRY={DRY_PAYMENT})")
            return 0
        else:
            print(f"\n✗ [{mall}] #{idx} 실패: {result['error']}")
            return 1


if __name__ == "__main__":
    sys.exit(main())
