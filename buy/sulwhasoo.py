"""설화수 자동구매 — 롯데 + 갤러리아 직접 진입.

Hmall 식품(buy/run.py)과 다른 모듈:
- mall 홈 직접 진입 → 로그인 → 상품 URL → 카트 → 결제

가이드: cart codegen 결과(롯데/갤러리아) + Sulwhasoo_Supply_Rate.md.

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

# galleria/lotte 모두 정상 user session (cookies 영구) 사용 →
# stealth (patchright) 불필요. 단순한 playwright 사용으로 Chrome 148 호환성 확보.
from playwright.sync_api import sync_playwright, Page, BrowserContext, TimeoutError as PlaywrightTimeoutError
PW_BACKEND = "playwright"

ROOT = Path(__file__).parent
PROJECT_ROOT = ROOT.parent
sys.path.insert(0, str(PROJECT_ROOT))
from chrome_launcher import resolve_cdp_port  # noqa: E402
load_dotenv(ROOT / ".env")

CREDENTIALS_FILE = PROJECT_ROOT / "credentials.json"
LOTTE_ACCOUNTS = PROJECT_ROOT / "lotte.json"
GALLERIA_ACCOUNTS = PROJECT_ROOT / "galleria.json"

CDP_PORT = os.environ.get("CDP_PORT", "9222")
CDP_ENDPOINT = f"http://127.0.0.1:{CDP_PORT}"
DRY_PAYMENT = os.environ.get("DRY_PAYMENT", "true").lower() == "true"
# lotte 는 구매사은혜택 적립 받으려면 앱 결제 필수 → 컴터에선 cart-only.
# galleria/hmall 은 컴터 결제 가능.
LOTTE_CART_ONLY = os.environ.get("LOTTE_CART_ONLY", "true").lower() == "true"

GALLERIA_HOME = "https://www.galleria.co.kr/main/initMain.action"
LOTTE_HOME = "https://www.lotteimall.com/"
LOTTE_LOGIN_URL = "https://www.lotteimall.com/member/login/forward.LCLoginMem_pop.lotte"

# 갤러리아 상품 정보 — ★goods_no 는 config(SoT) 에서 읽는다 (2026-08-19).
#   종전엔 여기 번호를 하드코딩해 뒀는데, 롯데가 8/17 에 겪은 것과 **같은 사고 구조**다:
#   config 만 갱신하면 담기는 여기 박힌 구 번호를 계속 썼다. (n=2502913437 이 그 상태였다.)
#   READ_FIRST "버그 하나를 고치면 같은 모양을 폴더 전체에서 찾는다" 에 따라 같이 정리.
#   이름(짧은 표기)은 로그·매칭용이라 유지하고, **번호만** SoT 에서 가져온다.
_GALLERIA_SHORT_NAMES = {
    "b": "윤조3종", "c": "자음2종", "d": "본윤2종", "e": "탄력3종",
    "f": "윤조에센스90", "g": "자음생2종", "h": "자음생크림리치세트", "n": "탄력크림EX75",
}

# 롯데 상품 정보 — ★goods_no 는 hsmaster/config/sulwhasoo-ids.json (SoT) 에서 읽는다.
#   종전엔 이 자리에 번호를 하드코딩하고 "월 1회 갱신" 주석만 달아뒀는데, 실제로 갱신이 안 돼
#   config 와 어긋났다: 2026-08-17 사용자가 롯데 g 를 (i몰단독)자음생2종(2834421446)으로 바꿨는데
#   config 만 고치면 담기는 여기 박힌 구 번호(2719761525=(공통)자음생2종)를 그대로 썼다.
#   (galleria SET_COMBINE_RULES 의 s07 단가 사고와 같은 유형 — 사본이 조용히 낡는다.)
#   이름(짧은 표기)은 로그·매칭용이라 여기 유지하고, **번호만** SoT 에서 가져온다.
_LOTTE_SHORT_NAMES = {
    "b": "윤조3종", "c": "자음2종", "d": "본윤2종", "e": "탄력3종",
    "f": "윤조에센스90", "g": "자음생2종", "h": "자음생크림리치세트", "n": "탄력크림EX75",
}
sys.path.insert(0, str(PROJECT_ROOT / "rate-check"))
from _common import id_candidates as _id_candidates  # noqa: E402

_IDS_FILE = PROJECT_ROOT / "hsmaster" / "config" / "sulwhasoo-ids.json"
_ALL_IDS = json.loads(_IDS_FILE.read_text(encoding="utf-8"))["ids"]
_LOTTE_IDS = _ALL_IDS   # 하위호환 별칭


def _mall_products(short_names: dict, mall: str) -> dict:
    """{code: {name, goods_no, goods_no_candidates}} — goods_no 는 **1순위 후보**.

    ★2026-08-19: config 값이 문자열이 아니라 **후보 리스트**일 수 있다(`n` 은 상품번호가
      수시로 바뀌고, `g` 는 한정품→공통품 순). goods_no 는 기존 호출부 호환을 위해 1순위를
      그대로 담고, 폴백이 필요한 호출부는 goods_no_candidates 를 쓴다.
    """
    out = {}
    for code, short in short_names.items():
        entry = _ALL_IDS.get(code) or {}
        cands = _id_candidates(entry, mall)
        if not cands:
            continue
        out[code] = {"name": short, "goods_no": cands[0], "goods_no_candidates": cands}
    return out


LOTTE_PRODUCTS = _mall_products(_LOTTE_SHORT_NAMES, "lotte")
GALLERIA_PRODUCTS = _mall_products(_GALLERIA_SHORT_NAMES, "galleria")
for _label, _tbl, _src in (("롯데", LOTTE_PRODUCTS, _LOTTE_SHORT_NAMES),
                           ("갤러리아", GALLERIA_PRODUCTS, _GALLERIA_SHORT_NAMES)):
    _missing = [c for c in _src if c not in _tbl]
    if _missing:   # 조용히 빠지면 담기에서 그 상품만 누락된다 → 크게 알린다
        print(f"[WARN] sulwhasoo-ids.json 에 {_label} goods_no 없음: {_missing}")

# 조합 = rate-check/_common.py 의 COMBOS 단일 소스 (TOP 20).
# rate-check 의 list[0..N-1] 을 dict{1..N} 으로 변환 (combo_no 1-based 사용).
from _common import COMBOS as _COMBOS_LIST  # noqa: E402
COMBOS: dict[int, list[tuple[str, int]]] = {i + 1: c for i, c in enumerate(_COMBOS_LIST)}


# ───────────── 공통 ─────────────


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def dismiss_popup(page: Page) -> None:
    """페이지 popup 닫기. ★'30일간 보이지 않기' 우선(재등장 억제, 롯데 비번변경/이벤트 팝업이
    담기 클릭을 가로막던 원인 — 2026-07-08) → 없으면 '닫기'. 둘 다 없으면 return."""
    for _ in range(4):  # 여러 popup 동시/연속
        clicked = False
        for pat in (r"30일간\s*보이지\s*않기", r"오늘\s*하루\s*보지\s*않기", r"^닫기$|닫기\s*$"):
            for kind in ("button", "link"):
                try:
                    el = page.get_by_role(kind, name=re.compile(pat)).first
                    if el.count() > 0:
                        el.click(timeout=1500)
                        clicked = True
                        page.wait_for_timeout(400)
                        break
                except Exception:
                    continue
            if clicked:
                break
        if not clicked:
            break


# ───────────── 갤러리아 ─────────────


def _galleria_hide_dim(page: Page) -> None:
    """pw_noti(비밀번호 변경안내) + dim 오버레이 숨김 — 로그인 버튼 클릭 가림 방지(2026-06-03 UI)."""
    try:
        page.evaluate("""() => {
            ['lyr_pw_noti','lyr_pw_noti_dim'].forEach(id => {
                const e = document.querySelector('#' + id); if (e) e.style.display = 'none';
            });
        }""")
    except Exception:
        pass


# 갤러리아 '개인정보 보호를 위한 비밀번호 변경안내' 팝업에서 **눌러야 하는** 버튼.
# ⚠️ '변경하기' 는 **절대 누르지 않는다** — 비밀번호가 실제로 바뀐다.
#    (롯데 비번변경 캠페인에서 '지금 변경하기' 를 금지한 것과 같은 이유. READ_FIRST 참조.)
_GAL_PW_POPUP_TITLE = "비밀번호 변경안내"
_GAL_PW_POPUP_SAFE = "30일 후 변경"
_GAL_PW_POPUP_FORBIDDEN = "변경하기"


def _galleria_pw_popup_present(page: Page) -> bool:
    try:
        return _GAL_PW_POPUP_TITLE in page.inner_text("body")
    except Exception:
        return False


def _galleria_dismiss_pw_popup(page: Page) -> bool:
    """'개인정보 보호를 위한 비밀번호 변경안내' → **'30일 후 변경'** 클릭. 닫혔으면 True.

    ★role=button 하나로만 찾지 말 것 (2026-09-02 보강). 갤러리아는 로그인을 오래 안 한 계정에서
      이 팝업을 자주 띄우는데, 버튼이 `<a>`/`<span>` 로 그려지는 경우가 있어 role 매칭이 조용히
      빗나간다. 롯데 비번변경 캠페인이 `<img alt>` 라 `innerText` 로는 절대 못 잡던 것과 같은 계열
      (READ_FIRST 「롯데 장바구니 담기 — 비밀번호 변경 캠페인」).
    ★누르고 끝내지 않고 **팝업이 실제로 사라졌는지 검증**한다 — 안 닫히면 뒤 동작이 딤에 막힌다.
    ⚠️ '변경하기' 는 비밀번호를 실제로 바꾸므로 절대 클릭 금지. 그래서 정확일치로만 찾는다.
    """
    if not _galleria_pw_popup_present(page):
        return True
    clicked = False
    # ① 역할 무관 정확일치 탐색 (button/a/span/div 전부)
    try:
        clicked = bool(page.evaluate(
            """(safe) => {
                const els = Array.from(document.querySelectorAll('button,a,span,div,input[type=button]'));
                const t = els.find(e => ((e.innerText || e.value || '').trim()) === safe
                                        && e.offsetParent !== null);
                if (t) { t.click(); return true; }
                return false;
            }""", _GAL_PW_POPUP_SAFE))
    except Exception:
        pass
    # ② 폴백 — playwright 역할/텍스트 매칭
    if not clicked:
        for getter in (lambda: page.get_by_role("button", name=_GAL_PW_POPUP_SAFE),
                       lambda: page.get_by_role("link", name=_GAL_PW_POPUP_SAFE),
                       lambda: page.get_by_text(_GAL_PW_POPUP_SAFE, exact=True)):
            try:
                loc = getter()
                if loc.count() and loc.first.is_visible():
                    loc.first.click(timeout=2000); clicked = True; break
            except Exception:
                continue
    page.wait_for_timeout(1200)
    if not _galleria_pw_popup_present(page):
        print(f"  [popup] 비밀번호 변경안내 → '{_GAL_PW_POPUP_SAFE}' 클릭 (닫힘 확인)")
        return True
    print(f"  [popup] ⚠️ 비밀번호 변경안내가 안 닫혔다 (clicked={clicked}) — "
          f"'{_GAL_PW_POPUP_FORBIDDEN}' 는 비밀번호가 바뀌므로 누르지 않는다. 수동 확인 필요")
    return False


def _galleria_dismiss_popups(page: Page) -> None:
    """포스트로그인 팝업 닫기: 쇼핑클래스 혜택안내(닫기/다시 보지 않기) / 비밀번호 변경안내(30일 후 변경)."""
    _galleria_dismiss_pw_popup(page)
    for nm in ("닫기", "다시 보지 않기", "30일 후 변경"):
        try:
            loc = page.get_by_role("button", name=nm)
            if loc.count() and loc.first.is_visible():
                loc.first.click(timeout=2000); page.wait_for_timeout(700)
        except Exception:
            pass


def galleria_login(page: Page, account_id: str, account_pw: str) -> bool:
    """갤러리아 로그인 — ★2026-06-03 UI개편 대응. **progressive 2단계**: 로그인레이어 → ID → '다음'(#next_btn_lyr)
    → 비번칸(#pwd) 등장 → PW → '로그인'(#lyrLoginBtn) → 포스트로그인 팝업(쇼핑클래스 혜택안내/비번변경안내) 닫기.
    캡차(#answer)는 실패 누적 시만 강제(평소 숨김) → 보이면 자동 불가로 중단."""
    # 0) 다른 계정 로그인 상태면 로그아웃
    if "로그아웃" in page.inner_text("body"):
        try:
            page.evaluate("overpass.link('LOGOUT',{})"); page.wait_for_timeout(2500)
        except Exception:
            pass
        page.goto(GALLERIA_HOME, wait_until="domcontentloaded", timeout=15000); page.wait_for_timeout(2000)
    # 1) 로그인 레이어 열기
    try:
        page.evaluate("overpass.link('LOGIN',{galloc:'COMMON_00_GNB'})")
    except Exception:
        try:
            page.get_by_role("link", name="로그인").first.click(timeout=4000)
        except Exception:
            pass
    page.wait_for_timeout(1800)
    _galleria_hide_dim(page)
    try:
        # 2) ID → '다음' (progressive — 비번칸은 ID 입력+다음 후 등장)
        page.locator("#login_id").fill(account_id); page.wait_for_timeout(400)
        try:
            page.locator("#next_btn_lyr").click(timeout=3000)
        except Exception:
            page.locator("#login_id").press("Enter")
        page.wait_for_timeout(1500); _galleria_hide_dim(page)
        # 캡차 강제되면 중단 (자동 불가)
        try:
            if page.locator("#answer").is_visible():
                print(f"  [LOGIN] {account_id}: ⚠️ 캡차(자동입력 방지 문자) 강제됨 — 자동 로그인 불가, 수동 필요")
                return False
        except Exception:
            pass
        # 3) PW → '로그인'
        page.locator("#pwd").fill(account_pw); page.wait_for_timeout(400)
        page.locator("#lyrLoginBtn").click(timeout=5000)
        page.wait_for_load_state("domcontentloaded", timeout=15000); page.wait_for_timeout(3000)
    except Exception as e:
        print(f"  [LOGIN ERR] {account_id}: {e}")
        return False
    # 4) 포스트로그인 팝업 닫기 + 검증
    _galleria_dismiss_popups(page); page.wait_for_timeout(1000)
    return "로그아웃" in page.inner_text("body")


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
            # 쿠폰 버튼 = 구조적 element 로 집는다. 쿠폰 *이름*(텍스트)으로 매칭 금지 —
            # 쿠폰명은 매일 바뀜("설화수 더블 N%"/"더블쿠폰 N%"…), 텍스트 하드코딩이 누락 사고 원인.
            # button.down / onclick=couponListLayer 가 쿠폰 영역 자체 (2026-06-04).
            coupon_btn = page.locator("button[onclick*='couponListLayer'], button.down").first
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


def naver_pay_input_password(pay_page, password_6: str) -> bool:
    """Naver Pay SecureKeyboard 자동 입력.
    sprite PNG (3x4 grid) + background-position 매핑으로 grid 위치 찾아 클릭."""
    import base64, io, re
    from PIL import Image
    import numpy as np

    refs_dir = ROOT / "secure_keyboard_refs"
    if not refs_dir.exists():
        print(f"    [WARN] reference 없음: {refs_dir}")
        return False

    # sprite PNG 추출 (모든 키 동일)
    style = pay_page.locator('.SecureKeyboard_number__0F2Ti').first.get_attribute('style')
    m = re.search(r'data:image/png;base64,([^)]+)', style or '')
    if not m:
        print("    [WARN] sprite PNG 못 찾음")
        return False
    sprite_img = Image.open(io.BytesIO(base64.b64decode(m.group(1)))).convert('RGBA')

    # PNG sprite: 120x200, 3 cols x 4 rows, cell 40x50
    W, H = 40, 50
    refs = {}
    for d in range(10):
        ref_img = Image.open(refs_dir / f"{d}.png").convert('RGBA')
        refs[d] = np.array(ref_img)[:, :, 3]  # alpha only

    # 각 cell → digit (closest reference)
    cell_to_digit = {}
    for r in range(4):
        for c in range(3):
            cell = sprite_img.crop((c * W, r * H, (c + 1) * W, (r + 1) * H))
            cell_arr = np.array(cell)[:, :, 3]
            best_d, best_dist = None, float('inf')
            for d, ref in refs.items():
                dist = float(((cell_arr.astype(int) - ref.astype(int)) ** 2).sum())
                if dist < best_dist:
                    best_dist, best_d = dist, d
            # threshold — 빈 셀 (백스페이스/전체삭제 위치)는 거리 큼
            if best_dist < 500_000:
                cell_to_digit[(r, c)] = best_d

    # digit → grid position (1-indexed for class)
    digit_to_grid = {d: (r, c) for (r, c), d in cell_to_digit.items()}

    # 비밀번호 자릿수마다 해당 grid의 button click
    for ch in password_6:
        d = int(ch)
        if d not in digit_to_grid:
            print(f"    [WARN] digit {d} grid에 없음")
            return False
        r, c = digit_to_grid[d]
        # class: 'SecureKeyboard_key-{r+1}-{c+1}__HASH' — substring match
        sel = f'.SecureKeyboard_key__jGpA_:has([class*="SecureKeyboard_key-{r+1}-{c+1}__"])'
        btn = pay_page.locator(sel).first
        btn.click(timeout=3000)
        pay_page.wait_for_timeout(150)
    return True


def galleria_checkout(page: Page, naver_id: str = "", naver_pw: str = "", naver_pay_pw: str = "",
                      order_phone: str = "") -> dict:
    """갤러리아 카트 → 주문하기 → 결제 popup → Naver Pay → DRY 모드 종료.
    order_phone: 첫 주문 계정은 주문고객 휴대폰번호가 비어 있어 결제가 막힌다(2026-08-23 #13 실측)
    — 주면 빈 칸일 때만 채운다."""
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

        # 1.5) 주문고객 휴대폰번호 — 첫 주문 계정은 비어 있어 결제 popup 자체가 안 뜬다(#13 실측).
        #      빈 칸일 때만 채운다(기존 번호 덮어쓰기 금지).
        if order_phone:
            try:
                digits = re.sub(r"\D", "", order_phone)
                p1, p2, p3 = digits[:3], digits[3:-4], digits[-4:]
                filled = page.evaluate("""(ph) => {
                    const label = [...document.querySelectorAll('*')].find(
                        e => e.children.length === 0 && /휴대폰\s*번호/.test(e.innerText || ''));
                    if (!label) return 'label 미발견';
                    let box = label.closest('tr, li, dl, div');
                    for (let i = 0; box && i < 4; i++) {
                        if (box.querySelector('select') && box.querySelectorAll('input[type=text],input[type=tel]').length >= 2) break;
                        box = box.parentElement;
                    }
                    if (!box) return '입력영역 미발견';
                    const sel = box.querySelector('select');
                    const inputs = [...box.querySelectorAll('input[type=text],input[type=tel]')];
                    if (inputs.some(i => i.value.trim())) return '이미 입력됨 — skip';
                    sel.value = ph.p1;
                    sel.dispatchEvent(new Event('change', {bubbles: true}));
                    inputs[0].value = ph.p2; inputs[1].value = ph.p3;
                    inputs.forEach(i => { i.dispatchEvent(new Event('input', {bubbles: true}));
                                          i.dispatchEvent(new Event('change', {bubbles: true})); });
                    return 'ok';
                }""", {"p1": p1, "p2": p2, "p3": p3})
                print(f"    [주문고객 휴대폰] {filled}")
            except Exception as e:
                print(f"    [WARN] 휴대폰번호 입력 실패: {e}")

        # 2) 포인트 전체사용 — id #point_all_2300 (G포인트/LIVE 포인트 X, 별도)
        try:
            page.locator("#point_all_2300").click(timeout=3000)
            page.wait_for_timeout(800)
            print("    [OK] 포인트 전체사용")
        except Exception as e:
            print(f"    [WARN] 포인트 전체사용 실패: {e}")

        # 3) 약관 전체 동의 — #ckTerms_all 체크 (보통 default true이지만 안전 차원)
        try:
            page.locator("#ckTerms_all").check(force=True, timeout=2000)
            page.wait_for_timeout(300)
        except Exception:
            pass

        # 3.5) ★결제수단 = 네이버페이 양성검증 (2026-08-23 #13 실측: 첫 주문 계정은 기본값이
        #      삼성카드라 그대로 누르면 카드사 모달이 뜨고 주문서 상태가 오염된다. 토글 UI 원칙 —
        #      상태 확인 없이 진행 금지. 미확인이면 결제 중단.)
        try:
            pay_sel = page.evaluate("""() => {
                const radios = [...document.querySelectorAll('input[name=pay_rdo]')];
                if (!radios.length) return 'radio 없음 — 구UI(기본 네이버페이) 가정';
                const naverLi = [...document.querySelectorAll('li')].find(
                    li => li.querySelector('input[name=pay_rdo]') && /네이버페이/.test(li.innerText || ''));
                if (!naverLi) return 'FAIL: 네이버페이 항목 미발견';
                const rd = naverLi.querySelector('input[name=pay_rdo]');
                if (!rd.checked) rd.click();
                return rd.checked ? 'ok' : 'FAIL: 클릭 후에도 미체크';
            }""")
            print(f"    [결제수단] 네이버페이: {pay_sel}")
            if str(pay_sel).startswith("FAIL"):
                out["error"] = f"네이버페이 선택 실패({pay_sel}) — 오결제 방지 위해 중단"
                return out
            page.wait_for_timeout(800)
        except Exception as e:
            print(f"    [WARN] 결제수단 검증 예외: {e}")

        # 3.9) ★결제 직전 금액 가드 (2026-09-02 신설).
        #   갤러리아 경로엔 금액 확인이 **아예 없었다** — 쿠폰/GWP 가 조용히 빠져도 그대로 결제된다.
        #   롯데에서 쿠폰 0장이 ok:True 로 통과해 계정당 15만원을 더 낼 뻔한 것과 같은 구멍이다
        #   (자동메모리 lotte-order-money-guards). 금액을 **못 읽어도** MAX_PAY 가 걸려 있으면 멈춘다
        #   — 모르는 금액은 결제하지 않는다.
        _max = os.environ.get("MAX_PAY")
        _amt = None
        try:
            _amt = page.evaluate(r"""() => {
                const body = document.body ? document.body.innerText : '';
                // '결제예정금액'/'최종결제금액'/'총 결제금액' 뒤에 오는 첫 금액
                const m = body.match(/(?:결제\s*예정\s*금액|최종\s*결제\s*금액|총\s*결제\s*금액)[^0-9]{0,20}([\d,]{4,})/);
                if (m) return parseInt(m[1].replace(/,/g, ''));
                return null;
            }""")
        except Exception as e:
            print(f"    [WARN] 결제금액 판독 예외: {e}")
        print(f"    [금액] 결제 예정: {_amt:,}원" if _amt else "    [금액] ⚠️ 결제 예정 금액 판독 실패")
        out["amount"] = _amt
        if _max:
            if _amt is None:
                out["error"] = f"AMOUNT_UNREADABLE(MAX_PAY={_max} — 금액을 못 읽으면 결제 안 함)"
                print(f"    [ABORT] {out['error']}"); return out
            if _amt > int(_max):
                out["error"] = f"AMOUNT_TOO_HIGH({_amt} > MAX_PAY {_max}) — 혜택 미적용 의심, 결제 안 함"
                print(f"    [ABORT] {out['error']}"); return out

        # 4) 결제하기 — id `regist_order_button` 직접 클릭
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
                print("    [DRY] 결제 popup 도달 — Naver 로그인 X")
            else:
                # Naver 로그인 (1 계정으로 12 갤러리아 계정 모두 결제)
                # nidlogin URL이면 로그인 폼 채움. 이미 pay.naver.com이면 cookies 살아있어 skip.
                if "nidlogin" in pay_page.url and naver_id and naver_pw:
                    try:
                        # ★2026-08-23 Naver 로그인 V3 UI: 제출버튼 #submit_btn → #loginBtn_row/#loginBtn_column.
                        #   fill 은 bvsd 봇감지에 걸릴 수 있어 키 타이핑(delay)으로 입력.
                        # ★입력칸을 **확인하고** 제출한다 (2026-09-02 실사고).
                        #   `fill("")` 만으로는 네이버 자동완성/폼복원이 되살려서, 아이디 칸이
                        #   실제로 `tkdkky20tkdkky20` (옛 값 두 번)이 된 채 제출됐다. 화면엔
                        #   "The ID or password is incorrect" 만 떠서 **비번 문제로 오인**했다.
                        #   로그인 실패는 계정에 흔적이 남는다(캡차·잠금) → 쓰레기 값으로 시도를
                        #   낭비하면 안 된다. 자동메모리 login-retry-locks-account.
                        def _fill_verified(sel: str, val: str, label: str) -> bool:
                            for attempt in (1, 2):
                                loc = pay_page.locator(sel)
                                loc.click()
                                pay_page.keyboard.press("Control+A")
                                pay_page.keyboard.press("Delete")
                                loc.fill("")
                                pay_page.wait_for_timeout(200)
                                pay_page.keyboard.type(val, delay=120)
                                pay_page.wait_for_timeout(300)
                                got = pay_page.evaluate(
                                    "(s) => { const e = document.querySelector(s); return e ? e.value : null; }", sel)
                                if got == val:
                                    return True
                                print(f"    [WARN] {label} 입력 불일치(시도{attempt}) — "
                                      f"기대 {len(val)}자 / 실제 {len(got or '')}자")
                            return False

                        if not _fill_verified("#id", naver_id, "네이버 아이디"):
                            out["error"] = ("NAVER_ID_INPUT_FAIL — 아이디 칸이 의도한 값이 되지 않았다. "
                                            "쓰레기 값으로 로그인 시도를 낭비하지 않기 위해 제출하지 않음")
                            print(f"    [ABORT] {out['error']}")
                            return out
                        if not _fill_verified("#pw", naver_pw, "네이버 비밀번호"):
                            out["error"] = "NAVER_PW_INPUT_FAIL — 비밀번호 칸 입력 실패, 제출하지 않음"
                            print(f"    [ABORT] {out['error']}")
                            return out
                        print(f"    [OK] 네이버 로그인 입력 검증 — id={naver_id}")
                        pay_page.wait_for_timeout(500)
                        for _bid in ("loginBtn_row", "loginBtn_column", "submit_btn"):
                            _b = pay_page.locator(f"#{_bid}")
                            if _b.count() and _b.first.is_visible():
                                _b.first.click()
                                break
                        pay_page.wait_for_load_state("domcontentloaded", timeout=15000)
                        pay_page.wait_for_timeout(2500)
                        # deviceConfirm — Register (#new.save)
                        if "deviceConfirm" in pay_page.url:
                            pay_page.locator("#new\\.save").click(timeout=5000)
                            pay_page.wait_for_load_state("domcontentloaded", timeout=15000)
                            pay_page.wait_for_timeout(2000)
                            print("    [OK] Naver 기기 등록 (Register)")
                        print(f"    [OK] Naver 로그인 → {pay_page.url[:90]}")
                    except Exception as e:
                        print(f"    [WARN] Naver 로그인 단계 실패: {e}")
                else:
                    print(f"    [INFO] Naver 로그인 skip (URL={pay_page.url[:60]})")

                # ★카드 선택 — 롯데 2224 (Naver Pay 등록카드 메모 '갤러리아 이걸로 결제', 사용자 지정).
                #   2026-06-03 UI: 카드 = swiper 캐러셀(1장씩). 활성카드=.swiper-slide-active 의 CardPlate_name.
                #   화살표(이전/다음 결제수단)는 Playwright 클릭이 actionability로 막힘 → blind span 조상 button **JS 클릭**.
                #   ⚠️ .first 읽기는 항상 DOM 첫카드(삼성)라 오인 — 반드시 active-slide 로 판정.
                TARGET_CARD = "롯데 2224"

                def _active_card():
                    # ★2026-08-23 UI: CardPlate_name 없어진 카드(머니통장 등) 대비 — active slide innerText 폴백.
                    return pay_page.evaluate("""() => {
                        const a = document.querySelector('.swiper-slide-active');
                        if (!a) return '';
                        const n = a.querySelector('[class*=CardPlate_name]');
                        if (n && (n.innerText || '').trim()) return n.innerText.trim();
                        return (a.innerText || '').trim().replace(/\\s+/g, ' ');
                    }""")

                def _nav_card(label):
                    return pay_page.evaluate("""(lbl) => {
                        const sp = [...document.querySelectorAll('span,em,i,button')].find(e => (e.innerText||'').trim()===lbl);
                        let el = sp; while (el && el.tagName!=='BUTTON' && el.tagName!=='A') el = el.parentElement;
                        if (el) { el.click(); return true; } return false;
                    }""", label)

                try:
                    pay_page.wait_for_selector('.swiper-slide-active', timeout=10000)
                except Exception:
                    pass
                pay_page.wait_for_timeout(1200)
                matched = False
                for direction in ("다음 결제수단", "이전 결제수단"):   # next 우선(롯데2224가 보통 우측), 안 되면 prev
                    for _ in range(24):
                        if TARGET_CARD in _active_card():
                            matched = True
                            break
                        if not _nav_card(direction):
                            break
                        pay_page.wait_for_timeout(450)
                    if matched:
                        break
                if matched:
                    print(f"    [OK] 카드 선택: {_active_card()}")
                else:
                    out["error"] = f"카드 '{TARGET_CARD}' 못 찾음 (현재 활성: {_active_card()}) — 오결제 방지 위해 결제 중단"
                    print(f"    [ABORT] {out['error']}")
                    return out

                # 동의하고 결제하기
                try:
                    pay_page.get_by_role("button", name="동의하고 결제하기").click(timeout=5000)
                    pay_page.wait_for_timeout(3000)
                    print(f"    [OK] 동의하고 결제하기 → {pay_page.url[:80]}")
                except Exception as e:
                    print(f"    [WARN] 결제하기 클릭 실패: {e}")

                # 6자리 SecureKeyboard 자동 입력
                if naver_pay_pw and "authentication/pw" in pay_page.url:
                    pay_page.wait_for_selector('.SecureKeyboard_key__jGpA_', timeout=10000)
                    pay_page.wait_for_timeout(1000)
                    if naver_pay_input_password(pay_page, naver_pay_pw):
                        print(f"    [OK] 6자리 비밀번호 자동 입력")
                else:
                    print("    [INFO] naver_id/pw 없음 — 수동 로그인 대기")
        except Exception as e:
            print(f"    [WARN] 결제하기 클릭 실패: {e}")
            if dialog_messages:
                print(f"    [DEBUG] dialog 메시지: {dialog_messages}")

        # ★주문 성사 검증 (2026-09-02 신설). 종전엔 결제창을 눌렀다는 것만으로 success=True 였다
        #   — "클릭 성공 ≠ 주문 완료". 어느 계정이 실제로 주문됐는지 알 수 없어 사후에 사람이
        #   주문내역을 뒤져야 했다(9/1 현대몰 #13 과 같은 상황).
        #   갤러리아는 결제가 끝나면 원래 창이 `initOrderFinish` 로 이동하고 '주문번호: NNN' 이 뜬다.
        try:
            for _ in range(20):                      # 최대 ~40초 폴링
                if "initOrderFinish" in (page.url or ""):
                    break
                page.wait_for_timeout(2000)
            body = page.inner_text("body")
            m = re.search(r"주문번호\s*[:：]\s*([0-9]{10,})", body)
            amt = re.search(r"최종\s*결제금액\s*([\d,]{4,})\s*원", body)
            if m:
                out["order_no"] = m.group(1)
                out["paid_amount"] = int(amt.group(1).replace(",", "")) if amt else None
                out["success"] = True
                print(f"    [OK] 주문완료 — 주문번호 {out['order_no']}"
                      + (f" / 결제 {out['paid_amount']:,}원" if out.get("paid_amount") else ""))
            else:
                out["success"] = False
                out["error"] = (f"ORDER_UNCONFIRMED — 결제 절차는 진행했으나 주문완료 화면/주문번호를 "
                                f"확인하지 못했다 (url={page.url[:60]}). 주문내역 확인 필요")
                print(f"    [WARN] {out['error']}")
        except Exception as e:
            out["success"] = False
            out["error"] = f"ORDER_VERIFY_EXC: {e}"
            print(f"    [WARN] {out['error']}")
        return out
    except Exception as e:
        out["error"] = f"checkout 예외: {e}"
        return out


# ───────────── 롯데 (skeleton — 시연 정보 기반, smoke test 후 보강) ─────────────


def _captcha_str(res) -> str:
    """단일 엔진 OCR 결과 → 숫자 문자열(읽기순서). GCV 는 전체를 한 토큰(동일 x)으로 주므로
    x 가 모두 같으면 리스트 순서(=읽기순서) 유지, 아니면 x 오름차순 정렬."""
    items = [(ch, x) for ch, x, *_ in res if ch.strip().isdigit()]
    if not items:
        return ""
    if len({x for _, x in items}) == 1:
        return "".join(ch for ch, _ in items)
    return "".join(ch for ch, _ in sorted(items, key=lambda t: t[1]))


def _refresh_lotte_captcha(login_page: Page) -> None:
    """'새로고침' 으로 새 캡차 요청(제출 아님 → 시도횟수/anti-bot 무관)."""
    try:
        login_page.get_by_role("button", name="새로고침").click(timeout=2000)
    except Exception:
        try:
            login_page.locator('img[alt="보안문자"]').click()
        except Exception:
            pass
    login_page.wait_for_timeout(700)


def _solve_lotte_captcha(login_page: Page, max_refresh: int = 3) -> str:
    """롯데 보안문자(캡차) 견고 판독 — 다중 엔진 투표.
    취소선 때문에 단일 엔진이 가끔 자리수 누락/오판 → ≥2 엔진이 같은 6자리에 합의할 때만 채택.
    불합의면 '새로고침' 으로 더 쉬운 캡차로 교체 후 재시도(제출 낭비=anti-bot 자극 방지).
    합의 못 얻으면 첫 엔진의 6자리 폴백. easyocr/torch 는 lazy.

    ⚠️ 2026-08-02: **GCV 를 엔진 목록에서 뺐다** — BILLING_DISABLED(403) 라 매 호출 예외만 나고
       느려진다. 남은 건 macOS Vision + Tesseract 인데 **tesseract 는 미설치**라 실질 단일엔진이다
       → 합의가 안 나 '새로고침' 재시도에 의존한다. 캡차 정확도를 올리려면 tesseract 설치 또는
       GCV billing 복구가 필요하다. (키패드와 달리 캡차는 문자+숫자라 클로드 숫자엔진 재사용 불가)"""
    from collections import Counter
    sys.path.insert(0, str(PROJECT_ROOT / "phone_auto"))
    import ocr_keypad as _K  # noqa: E402
    engines = [_K._ocr_macos_vision, _K._ocr_tesseract]   # gcv 제외(8/02 BILLING_DISABLED)
    cap = str(ROOT / "_tmp_lotte_captcha.png")
    fallback = ""
    for _ in range(max_refresh + 1):
        login_page.locator('img[alt="보안문자"]').screenshot(path=cap)
        votes = Counter()
        first6 = ""
        for i, fn in enumerate(engines):
            try:
                s = _captcha_str(fn(cap))
            except Exception:
                s = ""
            if len(s) == 6:
                votes[s] += 1
                if i == 0:
                    first6 = s
        if first6:
            fallback = first6
        if votes:
            top, n = votes.most_common(1)[0]
            if n >= 2:           # ≥2 엔진 합의 = 고신뢰 → 즉시 채택
                return top
        _refresh_lotte_captcha(login_page)
    return fallback  # 합의 실패 시 첫 엔진 최선값(이후 로그인 실패하면 재시도 루프가 처리)


# 비밀번호 변경 캠페인 팝업 '창'의 URL 표식 (2026-08-01 실측: forward.popup_pwd_campaign_av.lotte)
LOTTE_PW_CAMPAIGN_URL = "popup_pwd_campaign"

# 캠페인 창 안에서 눌러야 하는 것 = '30일간 보이지 않기'.
# ★라벨이 텍스트가 아니라 <img alt="30일간 보이지 않기"> 이고 클릭 대상은 그 부모 <a onclick="fn_changeNext()">.
#   (2026-08-01 실측. innerText 만 보던 옛 코드가 못 찾은 직접 원인.)
_JS_CLICK_DEFER = r"""() => {
    const imgs = Array.from(document.querySelectorAll('img'));
    const img = imgs.find(i => /30일간?\s*보이지\s*않기|나중에|다음에\s*변경/.test(i.alt || ''));
    if (img) { (img.closest('a,button') || img).click(); return 'img-alt'; }
    // 폴백: 텍스트형 버튼 (레이어형 변형 대비). '지금 변경' 은 절대 제외 — 누르면 비번 실제 변경.
    const els = Array.from(document.querySelectorAll('a,button,input[type=button],span'));
    const safe = els.find(el => {
        const t = (el.innerText || el.value || '').trim();
        return /30일\s*후|30일간\s*보이지|나중에|다음에\s*변경/.test(t) && !/지금\s*변경/.test(t);
    });
    if (safe) { safe.click(); return 'text'; }
    return null;
}"""

_JS_CAMPAIGN_PRESENT = r"""() => {
    const imgs = Array.from(document.querySelectorAll('img'));
    if (imgs.some(i => /비밀번호\s*변경\s*캠페인|30일간?\s*보이지\s*않기/.test(i.alt || ''))) return true;
    const els = Array.from(document.querySelectorAll('a,button,input[type=button]'));
    return els.some(el => /지금\s*변경하기|30일\s*후/.test((el.innerText || el.value || '').trim()));
}"""


def _lotte_campaign_pages(page: Page) -> list[Page]:
    """캠페인이 떠 있는 page 목록 — ★같은 context 의 **모든 창**을 본다.
    캠페인은 별도 window 로 열린다(2026-08-01 실측) → 넘겨받은 page 만 보면 영원히 못 찾는다."""
    out = []
    try:
        pages = list(page.context.pages)
    except Exception:
        pages = [page]
    for pg in pages:
        try:
            if pg.is_closed():
                continue
            if LOTTE_PW_CAMPAIGN_URL in (pg.url or "") or pg.evaluate(_JS_CAMPAIGN_PRESENT):
                out.append(pg)
        except Exception:
            continue
    return out


def _lotte_pw_campaign_present(page: Page) -> bool:
    """비밀번호 변경 캠페인 감지 (별도 창 포함). 떠 있으면 실제 로그인 미확정 = 카트 컨텍스트 무효."""
    return bool(_lotte_campaign_pages(page))


def _lotte_dismiss_pw_campaign(page: Page) -> None:
    """비밀번호 변경 캠페인을 **'30일간 보이지 않기'** 로 닫는다. ⚠️'지금 변경하기' 절대 금지(비번 실변경).

    사고 이력:
    - 2026-07-21: 텍스트 패턴이 버튼을 못 잡아 캠페인 위에서 카트담기가 미로그인 컨텍스트로 헛돎.
    - 2026-08-01: 위 수정도 무효였음. 실측 결과 원인 2개 —
        ① 캠페인이 **별도 window**(forward.popup_pwd_campaign_av.lotte)라 page 하나만 보면 장님.
        ② 버튼 라벨이 텍스트가 아니라 **<img alt="30일간 보이지 않기">**.
      → 모든 창 스캔 + img[alt] 매칭 + 닫힘 검증으로 교체. (#2 카트 0건 / #3 page-closed 의 직접 원인)
    """
    for _ in range(3):
        targets = _lotte_campaign_pages(page)
        if not targets:
            return
        for pg in targets:
            try:
                how = pg.evaluate(_JS_CLICK_DEFER)
                if how:
                    print(f"  [popup] 비번변경 캠페인 '30일간 보이지 않기' 클릭 ({how})")
                    pg.wait_for_timeout(1200)
            except Exception:
                pass
            # 창이 안 닫혔으면 명시적으로 닫는다 — 남아 있으면 pages[-1] 이 이 창을 잡아 담기가 헛돈다.
            try:
                if not pg.is_closed() and LOTTE_PW_CAMPAIGN_URL in (pg.url or ""):
                    pg.close()
                    print("  [popup] 캠페인 창 close()")
            except Exception:
                pass
        page.wait_for_timeout(600)


def lotte_login(page: Page, account_id: str, account_pw: str) -> bool:
    """롯데 로그인 — ★팝업/새창 없이 기존 탭을 로그인 URL 로 직접 이동(2026-06-08).
    popup 이 macOS 창 focus 강탈 주범 → 직접 nav 로 Chrome 백그라운드 유지(focus 안 뺏음).
    보안문자(캡차)는 _solve_lotte_captcha 자동해결(엔진투표), 틀리면 새로고침 후 최대 3회.
    멀티계정: 기존 로그인 상태면 logout 후 fresh login.
    """
    pw_name = "비밀번호(영문+숫자+특수 8~15자)"
    try:
        # 0) 기존 로그인 상태면 logout (같은 탭, 홈에서)
        try:
            page.goto(LOTTE_HOME, wait_until="domcontentloaded", timeout=20000)
            page.wait_for_timeout(1500)
            body = page.inner_text("body")
            if "로그아웃" in body and "로그인" not in body.split("로그아웃")[0][-20:]:
                try:
                    page.get_by_role("link", name="로그아웃").first.click(timeout=3000)
                    page.wait_for_timeout(2500)
                    print(f"  [INFO] 기존 로그인 해제 → {account_id} fresh login")
                except Exception as e:
                    print(f"  [WARN] logout click 실패: {e} — 그대로 login 시도")
        except Exception:
            pass

        # 1) 로그인 페이지 직접 진입 + 제출 (캡차 자동판독, 최대 3회)
        for attempt in range(3):
            page.goto(LOTTE_LOGIN_URL, wait_until="domcontentloaded", timeout=20000)
            page.wait_for_timeout(1500)
            page.get_by_role("textbox", name="아이디 또는 이메일").fill(account_id)
            page.wait_for_timeout(800)
            page.get_by_role("textbox", name=pw_name).fill(account_pw)
            page.wait_for_timeout(500)
            answer = page.locator("#answer")
            has_captcha = answer.count() > 0 and answer.is_visible()
            if has_captcha:
                code = _solve_lotte_captcha(page)
                answer.fill(code); page.wait_for_timeout(300)
                print(f"  [CAPTCHA] OCR={code} (시도 {attempt + 1})")
                target = answer
            else:
                target = page.get_by_role("textbox", name=pw_name)
            try:
                target.press("Enter")
                page.wait_for_load_state("domcontentloaded", timeout=12000)
            except PlaywrightTimeoutError:
                pass
            page.wait_for_timeout(2500)
            # ★실패판정 = URL(pop_loginfailure) 기준. (실패 페이지에도 '로그아웃' 텍스트가 있어
            #   body '로그아웃' 매칭은 오탐 → URL 로 판정.) 메시지는 캡차/비번 구분 없이 동일.
            if "loginfailure" in page.url:
                if not has_captcha:
                    print("  [LOGIN] 자격증명 거부 — 중단")
                    return False
                print(f"  [LOGIN] 실패(캡차오판 또는 비번오류) — 재시도 ({attempt + 1}/3)")
                continue
            # ★비밀번호 변경 캠페인(2026-07-21 사고): 로그인 자체는 성공이나 인터스티셜/팝업이 뜬 상태.
            #   '30일 후'(연기)로 닫아야 실제 로그인 확정. ⚠️ '지금 변경하기' 절대 금지(비번 실제 변경됨).
            #   미처리 시 '로그아웃' 텍스트만 보고 오탐 → 카트담기가 미로그인 컨텍스트에서 헛됨.
            _lotte_dismiss_pw_campaign(page)
            # 성공 추정 → ★홈으로 이동(이후 clear_cart/add_combo 가 홈 기준) + 최종 검증
            page.goto(LOTTE_HOME, wait_until="domcontentloaded", timeout=20000)
            page.wait_for_timeout(2000)
            _lotte_dismiss_pw_campaign(page)   # 홈에서 재등장 가능 → 다시 닫기
            if "로그아웃" in page.inner_text("body") and "loginfailure" not in page.url \
               and not _lotte_pw_campaign_present(page):
                return True
        return False
    except Exception as e:
        print(f"  [LOGIN ERR] {account_id}: {e}")
        return False


def lotte_clear_cart(page: Page) -> bool:
    """롯데 장바구니 비우기. 모든 item checkbox 직접 클릭 + 선택삭제.

    Returns True (비어있음 or 비우기 성공) / False (실패).
    feedback_cart_clear_must_succeed.md — 실패 시 caller 가 add 진행 중단해야 함.

    전략 (2026-05-19 수정):
      1. 카트 페이지 진입 + 안정화 대기
      2. 빈 카트 텍스트/카운트 확인 (이미 비어있으면 즉시 True)
      3. JS 로 모든 cart item checkbox 강제 check + change event 발생
      4. 선택삭제 클릭 + dialog accept
      5. 페이지 재로드 후 cart item 카운트 = 0 검증
    """
    try:
        page.get_by_role("link", name="장바구니 장바구니").click(timeout=5000)
        page.wait_for_load_state("domcontentloaded", timeout=10000)
        page.wait_for_timeout(2500)

        # 빈 카트 확인 — 본문 텍스트 + DOM count 둘 다
        def cart_item_count() -> int:
            return page.evaluate("""() => {
                const m = (document.body.innerText || '').match(/일반\\s*\\(\\d+\\/(\\d+)\\)/);
                return m ? parseInt(m[1]) : 0;
            }""")

        n_items = cart_item_count()
        if n_items == 0:
            print("    [cart] 이미 비어있음")
            return True
        print(f"    [cart] {n_items}개 상품 존재 → 전체 선택 + 삭제 시도")

        # 모든 cart item checkbox JS 로 강제 check + change event
        checked = page.evaluate("""() => {
            const boxes = Array.from(document.querySelectorAll('input[type="checkbox"]'));
            let cnt = 0;
            for (const box of boxes) {
                if (!box.disabled && box.offsetParent !== null) {
                    box.checked = true;
                    box.dispatchEvent(new Event('change', {bubbles: true}));
                    box.dispatchEvent(new Event('click', {bubbles: true}));
                    cnt++;
                }
            }
            return cnt;
        }""")
        print(f"    [cart] checkbox {checked}개 체크")
        page.wait_for_timeout(800)

        # 선택삭제 — dialog accept
        page.once("dialog", lambda dialog: dialog.accept())
        try:
            page.get_by_role("link", name="선택삭제").click(timeout=5000)
        except Exception:
            # button 형태 fallback
            page.get_by_role("button", name="선택삭제").click(timeout=5000)
        page.wait_for_timeout(2500)

        # 검증 — cart item 카운트 0 확인
        remaining = cart_item_count()
        if remaining == 0:
            print("    [cart] 비우기 완료 (검증)")
            return True
        # ★ 벌크 '선택삭제'가 안 먹히는 카트(일부 계정, 2026-06-03 #18/#19 실측: 다이얼로그는 뜨나 미삭제)
        #   → 개별 '삭제' 링크를 1개씩 클릭 폴백. 각 삭제마다 native dialog accept.
        print(f"    [cart] 벌크삭제 미동작({remaining}개 잔존) → 개별삭제 폴백")
        page.on("dialog", lambda d: d.accept())
        for _ in range(remaining + 5):
            dels = page.get_by_role("link", name="삭제", exact=True)   # '선택삭제' 제외 (exact)
            try:
                if dels.count() == 0:
                    break
                dels.first.click(timeout=3000)
            except Exception:
                break
            page.wait_for_timeout(1800)
            if cart_item_count() == 0:
                break
        final = cart_item_count()
        if final == 0:
            print("    [cart] 개별삭제로 비우기 완료 (검증)")
            return True
        print(f"    [cart] 개별삭제 후에도 {final}개 남음 → 실패")
        return False
    except Exception as e:
        print(f"    [cart] 비우기 예외: {e}")
        return False


def lotte_add_product_by_url(page: Page, goods_no: str, qty: int) -> bool:
    """롯데 상품 URL 직접 진입 → 쿠폰 다운로드 → 옵션 → 수량 +N → 장바구니."""
    url = f"https://www.lotteimall.com/goods/viewGoodsDetail.lotte?goods_no={goods_no}"
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=15000)
        page.wait_for_timeout(2000)
        dismiss_popup(page)

        # 쿠폰받기 → 쿠폰 전체 다운로드 → 닫기 (단계별 명시 로그)
        coupon_status = "skip"  # skip / popup_only / downloaded / err_*
        try:
            page.get_by_role("button", name="쿠폰받기").click(timeout=3000)
            page.wait_for_timeout(800)
            coupon_status = "popup_opened"
            try:
                page.get_by_role("button", name="쿠폰 전체 다운로드").click(timeout=2000)
                page.wait_for_timeout(800)
                coupon_status = "downloaded"
            except Exception as ce:
                coupon_status = f"err_download:{type(ce).__name__}"
            try:
                page.get_by_role("button", name="닫기", exact=True).click(timeout=2000)
                page.wait_for_timeout(500)
            except Exception:
                pass
        except Exception as oe:
            coupon_status = f"err_open:{type(oe).__name__}"
        print(f"      [coupon] {goods_no}: {coupon_status}")

        # ★쿠폰 레이어(#layer_down_coupon)는 '닫기' 클릭 후에도 남는 경우가 있어
        #   '타입 선택' 클릭을 가로챈다(2026-08-21 실측: 옵션 클릭 timeout → 옵션 미선택 →
        #   saveCart 에서 "타입 옵션을 선택해주세요" alert 가 자동 dismiss 돼 조용히 미담김).
        #   → 옵션 선택 **전에** 강제 숨김. (saveCart 직전 숨김만으론 옵션 단계가 무방비)
        try:
            page.evaluate(
                "() => { const l = document.querySelector('#layer_down_coupon');"
                " if (l) l.style.display = 'none'; }")
        except Exception:
            pass

        # 옵션 (타입 선택 → 세트)
        # ★실패를 삼키지 않는다(2026-08-21): 옵션 미선택이면 saveCart 가 "타입 옵션을
        #   선택해주세요" alert 로 조용히 미담김 → 원인 로그가 없어 debugging 불가였다.
        # ★'타입 선택'은 뷰포트 하단 경계(y≈810/vh≈811)에 걸쳐 있어 Playwright 자동 스크롤
        #   재시도 루프가 ~3초 소요 → timeout 3000ms 가 경계선에서 터졌다(2026-08-21 실측,
        #   창 크기 따라 되다 안 되다 한 원인). 명시 scrollIntoView 후 여유 timeout 으로 클릭.
        try:
            page.evaluate(
                "() => { const a = Array.from(document.querySelectorAll('a'))"
                ".find(x => (x.getAttribute('data-optselectnm')||'') === '타입 선택');"
                " if (a) a.scrollIntoView({block: 'center'}); }")
            page.wait_for_timeout(400)
            page.get_by_role("link", name="타입 선택").click(timeout=6000)
            page.wait_for_timeout(500)
            page.get_by_role("link", name="세트", exact=True).click(timeout=6000)
            page.wait_for_timeout(500)
        except Exception as opt_e:
            print(f"      [option] 선택 실패({type(opt_e).__name__}): {str(opt_e).splitlines()[0][:120]}")

        # 수량 = qty. ★'+' 를 눌러놓고 **확인하지 않으면 조용히 1개가 담긴다.**
        #   2026-08-31 실사고: 조합25 의 e(탄력3종)가 **7계정 전부 1개**로 담겼는데 로그는
        #   `× 2` 로 찍혔다. 종전 코드는 `except: break` 로 클릭 실패를 통째로 삼켰고(로그도 없음),
        #   최종 '담기 검증'은 줄 수만 세서 아무도 못 잡았다 → 폰 결제 금액이 436,500원(정상 630,000).
        #   ★실제 수량 필드는 `#cal_ord_qty` 가 아니라 **`#order_count`** 다(2026-08-31 실측:
        #     '+' 클릭 시 cal_ord_qty 는 1 그대로, order_count 만 1→2→3 으로 올라간다).
        #     order_count 는 **옵션(세트) 선택 후에야 DOM 에 생긴다** → 0이면 옵션 단계가 깨진 것.
        #   ⚠️ order_count 는 **같은 id 가 3개** 있다(중복 id — 옵션마다 행이 복제된다).
        #      `querySelector('#order_count')` 는 그중 **숨겨진 템플릿 행**(opt_..._0)을 집는데
        #      그건 영원히 1이다 → 클릭이 먹었는데도 '수량 1' 로 오판한다(2026-08-31 실측).
        #      실제로 움직이는 건 **보이는** 행(opt_..._1) 이므로 offsetParent 로 걸러 읽는다.
        def _order_count() -> int:
            try:
                v = page.evaluate(
                    "() => { const i = Array.from(document.querySelectorAll("
                    "'#order_count, [name=order_count]')).filter(x => !!x.offsetParent);"
                    " return i.length ? (parseInt(i[0].value) || 0) : 0; }")
                return int(v or 0)
            except Exception:
                return 0

        # ★`get_by_role("button", name="+").first` 를 쓰면 안 된다 — 화면엔 '+' 가 여러 개고
        #   그중 **수량과 무관한 것**이 먼저 잡힌다. 클릭은 성공(에러 없음)하는데 order_count 는
        #   그대로라, 종전 코드가 "눌렀으니 됐다"고 믿고 1개인 채 담았다 (2026-08-31 실측).
        #   → 보이는 '+' 를 차례로 눌러보고 **값이 실제로 오른 버튼**만 계속 쓴다.
        def _visible_plus() -> int:
            return page.evaluate(
                "() => Array.from(document.querySelectorAll('button'))"
                ".filter(x => (x.innerText||'').trim() === '+' && !!x.offsetParent).length")

        def _click_plus(i: int) -> None:
            page.evaluate(
                "(i) => { const b = Array.from(document.querySelectorAll('button'))"
                ".filter(x => (x.innerText||'').trim() === '+' && !!x.offsetParent);"
                " if (b[i]) b[i].click(); }", i)

        hit = None                                  # 값을 올리는 '+' 의 인덱스 (한 번 찾으면 재사용)
        for _ in range(max(1, (qty - 1) * 4)):
            cur = _order_count()
            if cur >= qty:
                break
            idxs = [hit] if hit is not None else range(_visible_plus())
            for i in idxs:
                _click_plus(i)
                page.wait_for_timeout(350)
                if _order_count() > cur:
                    hit = i
                    break
        got = _order_count()
        if got != qty:
            # ★조용히 담지 않는다 — 틀린 수량으로 담는 것보다 시끄럽게 실패하는 게 낫다.
            print(f"      [qty] ✗ 수량 {got} ≠ 기대 {qty} "
                  f"({'옵션 미선택(order_count 없음)' if got == 0 else '+ 클릭 미반영'}) — 담기 중단")
            return False
        print(f"      [qty] ✓ {got}개")

        # 쿠폰 레이어(#layer_down_coupon)가 안 닫히면(이미 다운로드 등) 장바구니 클릭을 가로챔 → 강제 숨김
        try:
            page.evaluate(
                "() => { const l = document.querySelector('#layer_down_coupon');"
                " if (l) l.style.display = 'none'; }")
        except Exception:
            pass

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
    # ★alert 가시화(2026-08-21): Playwright 기본은 dialog 자동 dismiss — 담기 실패 alert
    #   ("타입 옵션을 선택해주세요" 등)가 로그 없이 사라져 미담김 원인을 못 봤다.
    if not getattr(page, "_lotte_dialog_logged", False):
        page.on("dialog", lambda d: (print(f"      [dialog:{d.type}] {d.message.strip()[:120]}"), d.dismiss()))
        page._lotte_dialog_logged = True
    for sku, qty in combo:
        prod = LOTTE_PRODUCTS.get(sku)
        if not prod:
            print(f"    [ERR] sku '{sku}' 상품 없음")
            return False
        print(f"    [INFO] {sku} ({prod['name']}) × {qty}")
        if not lotte_add_product_by_url(page, prod["goods_no"], qty):
            return False
    # ★담기 검증 (2026-07-28): #saveCart-btn 은 확인 레이어 없이 조용히 처리 → 클릭 성공 ≠ 담김.
    #   검증 없이 True 반환하면 빈 카트를 '담기완료'로 보고(#4~#7 실측, 폰결제서 CART_FAIL).
    try:
        page.get_by_role("link", name="장바구니 장바구니").click(timeout=8000)
        page.wait_for_load_state("domcontentloaded", timeout=15000)
        page.wait_for_timeout(2500)
        n = page.evaluate("""() => {
            const m = (document.body.innerText || '').match(/일반\\s*\\(\\d+\\/(\\d+)\\)/);
            return m ? parseInt(m[1]) : 0;
        }""")
        if n < len(combo):
            print(f"    [ERR] 담기 검증 실패 — 카트 {n}건 (기대 {len(combo)}건)")
            return False
        # ★줄 수만 세면 수량 오류를 못 잡는다 (2026-08-31: e 가 1개인데 '카트 2건 ✓' 로 통과).
        #   카트 각 줄의 'N개' 를 읽어 조합 수량 합과 대조한다. 못 읽으면 **크게 남기고** 넘어간다
        #   (PC 카트 DOM 변경 시 오탐으로 담기를 막지 않기 위해 — 대신 조용히 지나가지 않는다).
        want = sorted(q for _, q in combo)
        # ★수량은 innerText 에 없다 — 롯데 PC 카트의 수량은 `<input name="ord_qty">` **값**이라
        #   화면 텍스트로만 찾으면 **항상** 못 읽고 '수량 미검증' 으로 조용히 통과한다
        #   (2026-09-01 실측: #15~#20 여섯 계정이 전부 이 WARN 을 달고 지나갔다. 그날 카트는
        #    정상이었지만, 수량이 틀린 날에도 똑같이 통과했을 것이다 — 8/31 에 막으려던 바로 그
        #    구멍이 판독 경로만 바뀐 채 남아 있었다).
        #   → 행(input[name=goods_no] 보유) 단위로 ord_qty 를 읽는다. innerText 는 폴백.
        rows = page.evaluate("""() => {
            const all = Array.from(document.querySelectorAll('tr, li, div'))
                .filter(el => el.querySelector('input[name=goods_no]'));
            const leaf = all.filter(el => !all.some(o => o !== el && el.contains(o)));
            return leaf.map(el => ({
                goods_no: (el.querySelector('input[name=goods_no]') || {}).value,
                qty: parseInt((el.querySelector('input[name=ord_qty]') || {}).value || '0'),
            })).filter(r => r.goods_no && r.qty > 0);
        }""") or []
        qtys = [r["qty"] for r in rows]
        if rows:
            print("    [cart] " + ", ".join(f"{r['goods_no']}x{r['qty']}" for r in rows))
        if not qtys:   # 폴백 — 옛 DOM(텍스트에 'N개')
            qtys = page.evaluate("""() => (document.body.innerText || '')
                .split('\\n').map(s => (s.match(/^\\s*(?:세트\\s*[|｜]\\s*)?(\\d+)개\\s*$/) || [])[1])
                .filter(Boolean).map(Number)""")
        if not qtys:
            print(f"    [WARN] 담기 검증 — 카트 {n}건 확인, 그러나 **수량을 읽지 못했다** "
                  f"(기대 {want}). 폰 결제 전 금액으로 반드시 확인할 것")
        elif sorted(qtys) != want:
            print(f"    [ERR] 담기 검증 실패 — 수량 {sorted(qtys)} ≠ 기대 {want}")
            return False
        else:
            print(f"    [OK] 담기 검증 — 카트 {n}건, 수량 {sorted(qtys)}")
    except Exception as e:
        print(f"    [ERR] 담기 검증 예외: {e}")
        return False
    return True


def lotte_checkout(page: Page, account_id: str = "") -> dict:
    """롯데 카트 → 주문 → 주소 선택 → 쿠폰/L포인트 → KB Pay (DRY)."""
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
                addr_map = json.loads(addr_map_path.read_text(encoding="utf-8", errors="replace"))
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

        # 3) direct 쿠폰 — modal 열고 각 select 첫 옵션 (placeholder 다음, disabled skip)
        ifr = page.frame_locator('iframe[name="modal_ifrmWrap"]')
        page.locator("#modal_btn_coupon").scroll_into_view_if_needed()
        page.locator("#modal_btn_coupon").click(force=True)
        page.wait_for_timeout(2000)
        for i in range(8):
            sel = ifr.locator(f"#direct_coupon_{i}")
            if sel.count() == 0:
                break
            try:
                sel.select_option(index=1, timeout=3000)
                page.wait_for_timeout(200)
            except Exception:
                pass
        ifr.get_by_role("link", name="확인").click()
        # modal 사라짐 대기
        page.wait_for_function(
            "() => { const f = document.querySelector('iframe[name=\"modal_ifrmWrap\"]'); return !f || f.offsetParent === null; }",
            timeout=10000,
        )
        page.wait_for_timeout(500)
        print("    [OK] direct 쿠폰 적용")

        # 4) plus 쿠폰 — 조회/적용 nth(1), 각 select 첫 옵션 (disabled 옵션은 skip)
        page.get_by_role("link", name="조회/적용").nth(1).click()
        page.wait_for_timeout(2000)
        for i in range(8):
            sel = ifr.locator(f"#plus_coupon_{i}")
            if sel.count() == 0:
                break
            try:
                sel.select_option(index=1, timeout=3000)
                page.wait_for_timeout(200)
            except Exception:
                pass
        ifr.get_by_role("link", name="적용").click()
        page.wait_for_function(
            "() => { const f = document.querySelector('iframe[name=\"modal_ifrmWrap\"]'); return !f || f.offsetParent === null; }",
            timeout=10000,
        )
        page.wait_for_timeout(500)
        print("    [OK] plus 쿠폰 적용")

        # 5) "동의함" 이미지
        page.get_by_role("img", name="동의함").click()
        page.wait_for_timeout(500)

        # 6) L포인트 모두사용 (활성화돼있을 때만 — #modal_btn_lpoint_all_use 가 visible 인지 확인)
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

        # 7) 카드사 select — 매일 청구할인 카드 자동 추출
        # 카드코드: 016=KB국민, 018=NH농협, 047=롯데, 029=신한, 026=BC, 048=현대, 031=삼성, 021=우리, 020=하나
        CARD_NAME_TO_CODE = {
            "국민": "016", "KB": "016",
            "농협": "018", "NH": "018",
            "롯데": "047",
            "신한": "029",
            "BC": "026", "비씨": "026",
            "현대": "048",
            "삼성": "031",
            "우리": "021",
            "하나": "020",
        }
        today_card_code = None
        try:
            # 청구할인 안내 텍스트에서 카드사명 추출 (예: "국민카드(신용카드/L.PAY) 5%")
            anno_text = page.locator("#card_corp_dc_html").inner_text()
            for name, code in CARD_NAME_TO_CODE.items():
                if name in anno_text:
                    today_card_code = code
                    print(f"    [INFO] 오늘 청구할인 카드 자동감지: {name} (code={code}) — '{anno_text[:60]}'")
                    break
            if not today_card_code:
                today_card_code = "016"  # fallback to KB
                print(f"    [WARN] 카드사 자동감지 실패 — KB로 fallback. 안내: '{anno_text[:60]}'")
        except Exception as e:
            today_card_code = "016"
            print(f"    [WARN] 청구할인 안내 파싱 실패 — KB로 fallback: {e}")

        try:
            page.locator("#iscm_cd").select_option(today_card_code)
            page.wait_for_timeout(500)
            print(f"    [OK] 카드사 선택: {today_card_code}")
        except Exception as e:
            print(f"    [WARN] 카드사 선택 실패: {e}")

        if DRY_PAYMENT:
            print("    [DRY] 결제하기 버튼 클릭 X")
            out["success"] = True
            out["lpoint_used"] = lpoint_used
            return out

        # 8) 결제하기 1차 + 사업자등록번호 (L포인트 사용 시) + 결제하기 2차
        # 첫 결제하기 click → "현금영수증 신청하시겠습니까?" confirm dialog → accept(예) 해야 사업자번호 페이지로 진입
        def _handle_dialog(d):
            print(f"    [dialog] type={d.type} msg={d.message[:80]}")
            if "현금영수증" in d.message:
                d.accept()
            else:
                d.dismiss()
        page.on("dialog", _handle_dialog)
        page.get_by_role("link", name="결제하기").click()
        page.wait_for_timeout(2500)
        # 사업자번호 라디오 보이면 L 포인트 흐름 — check + 입력 + 2차 결제하기
        try:
            radio = page.get_by_role("radio", name="사업자등록번호")
            if radio.count() > 0 and radio.first.is_visible():
                radio.first.check()
                page.get_by_role("textbox", name="현금영수증 발행 번호입력").fill("5071815504")
                page.wait_for_timeout(500)
                print("    [OK] 사업자등록번호 입력")
                page.get_by_role("link", name="결제하기").click()
            else:
                print("    [INFO] 사업자번호 라디오 visible 아님 — 단일 결제하기로 진행")
        except Exception as e:
            print(f"    [WARN] 사업자번호 단계 실패: {e}")

        # 9) KCP modal → KB Pay 버튼 클릭 (이중 iframe: MPI_cert > kbframe)
        try:
            page.wait_for_selector('iframe[name^="MPI_cert"]', timeout=15000)
            page.wait_for_timeout(2000)
            kb_btn = (
                page.frame_locator('iframe[name^="MPI_cert"]')
                .frame_locator('iframe[name="kbframe"]')
                .get_by_role("button", name="KB Pay KB Pay")
            )
            kb_btn.click(timeout=10000)
            print("    [OK] KB Pay 버튼 클릭 → 7자리 코드 화면")
        except Exception as e:
            print(f"    [WARN] KB Pay 버튼 클릭 실패: {e}")

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
    # ★비활성 계정 차단 (2026-09-02). 계정 파일이 SoT — 코드에 번호를 또 적으면 두 곳이 어긋난다.
    #   항목을 지우지 않는 이유: 롯데는 계정을 **인덱스**로 참조해서(#19·#20) 지우면 번호가 밀린다.
    if acc.get("inactive"):
        print(f"[SKIP] #{idx} {acc['id']} 는 비활성 계정 — {acc.get('inactive_reason', '사유 미기재')}")
        return 0

    # Naver 자격증명 로드 (갤러리아 네이버페이용)
    cred = load_json(CREDENTIALS_FILE)
    naver_id = cred.get("naver_id", "")
    naver_pw = cred.get("naver_pw", "")
    naver_pay_pw = cred.get("naver_pay_pw", "")

    print(f"[INFO] mall={mall}, account #{idx} {acc['id']}, DRY={DRY_PAYMENT}")
    print(f"[INFO] PW backend: {PW_BACKEND}")

    _port = resolve_cdp_port(int(CDP_PORT))   # 9222 막히면 9223→9224 (같은 CFT)
    _endpoint = f"http://127.0.0.1:{_port}"
    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(_endpoint)
        except Exception as e:
            print(f"[FATAL] CDP 연결 실패: {e}")
            return 1
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        # 기존 탭 재사용 — 새 탭 생성(new_page)이 macOS 창 focus 강탈 (2026-07-16 갤러리아 잔재 제거)
        # ★단 비번변경 캠페인 창은 제외 (2026-08-01): 그 창을 잡으면 미로그인 컨텍스트라
        #   담기가 전부 헛돌고(카트 0건), 창이 닫히면 'page has been closed' 로 죽는다.
        _usable = [pg for pg in context.pages
                   if not pg.is_closed() and LOTTE_PW_CAMPAIGN_URL not in (pg.url or "")]
        mall_page = _usable[-1] if _usable else context.new_page()

        home = LOTTE_HOME if mall == "lotte" else GALLERIA_HOME
        try:
            mall_page.goto(home, wait_until="domcontentloaded", timeout=15000)
            mall_page.wait_for_timeout(2000)
        except Exception as e:
            print(f"[FATAL] {mall} 홈 진입 실패: {e}")
            mall_page.close()
            return 1

        if mall == "lotte":
            ok = lotte_login(mall_page, acc["id"], acc["pw"])
        else:
            ok = galleria_login(mall_page, acc["id"], acc["pw"])
        if not ok:
            print("[FATAL] mall 로그인 실패")
            mall_page.close()
            return 1
        print(f"[OK] {mall} 로그인")

        # 카트 비우기 + 상품 추가 (조합)
        if mall == "galleria":
            galleria_clear_cart(mall_page)
            print(f"[INFO] 조합 {combo_no} 추가: {COMBOS.get(combo_no)}")
            if not galleria_add_combo(mall_page, combo_no):
                print("[FATAL] 조합 추가 실패")
                return 1
            result = galleria_checkout(mall_page, naver_id=naver_id, naver_pw=naver_pw, naver_pay_pw=naver_pay_pw)
        else:
            if not lotte_clear_cart(mall_page):
                print("[FATAL] 카트 비우기 실패 — add 진행 중단 (기존 상품 보호)")
                return 1
            print(f"[INFO] 조합 {combo_no} 추가: {COMBOS.get(combo_no)}")
            if not lotte_add_combo(mall_page, combo_no):
                print("[FATAL] 조합 추가 실패")
                return 1
            if LOTTE_CART_ONLY:
                # 적립 받으려면 앱 결제 필수 → 컴터에선 cart 담은 상태로 종료.
                # 사용자는 폰 앱에서 동일 계정 로그인 → 장바구니 → 주문/결제.
                print(f"\n✓ [lotte] #{idx} 장바구니 담기 완료 (LOTTE_CART_ONLY=true)")
                print("  → 폰 앱에서 동일 계정 로그인 후 결제 진행 필요 (적립 받으려면 앱 결제 필수)")
                return 0
            result = lotte_checkout(mall_page, account_id=acc["id"])

        if result["success"]:
            print(f"\n✓ [{mall}] #{idx} 결제 진행 완료 (DRY={DRY_PAYMENT})")
            return 0
        else:
            print(f"\n✗ [{mall}] #{idx} 실패: {result['error']}")
            return 1


if __name__ == "__main__":
    sys.exit(main())
