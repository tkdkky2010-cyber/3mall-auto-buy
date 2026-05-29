"""Hmall 19계정 cart 담기 + checkout + monimo 7자리 코드 추출 (Phase 3-A)

기존 Hmall10/run_cart.py의 cart 담기 흐름 + 결제 단계 확장.
폰 자동화는 Phase 3-B (별도). 이 파일은 7자리 코드 추출까지만.

사용법:
    pip install -r requirements.txt
    patchright install chromium
    cp .env.example .env  # 그 후 PIN/카드명 채움
    python run.py            # 전체 (INACTIVE 자동 제외)
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

ACCOUNTS_FILE = Path(os.environ.get("HMALL_CONFIG_PATH") or (PROJECT_ROOT / "hmall_config.json"))
PRODUCTS_FILE = ROOT / "products.json"
PLAN_FILE = ROOT / "cart_plan.json"

INACTIVE_ACCOUNTS: list[int] = [6]

LOGIN_URL = "https://www.hmall.com/mo/cob/loginForm"
CART_URL = "https://www.hmall.com/mo/odb/basktList"
ITEM_URL_FMT = "https://www.hmall.com/md/pda/itemPtc?slitmCd={slitmCd}{extra}"

ACCOUNT_DELAY_SEC = 420  # 7분 — 본사 주소/IP 추적 회피 (같은 계정 product 사이 + 계정 사이 둘 다 적용)
CDP_PORT = os.environ.get("CDP_PORT", "9222")
CDP_ENDPOINT = f"http://127.0.0.1:{CDP_PORT}"

# Phase 3-A 안전장치: 결제하기 클릭 후 7자리 코드만 추출하고 폰 결제는 수동 (또는 Phase 3-B)
DRY_PAYMENT = os.environ.get("DRY_PAYMENT", "true").lower() == "true"

# CART_ONLY: 계정별로 plan 의 모든 상품을 장바구니에 누적 담기만 하고 checkout/결제는 안 함.
# (쿠폰 받기는 add_to_cart 안에서 auto_coupon 시 그대로 수행) — 카카오페이 등 사용자 수동 결제일 때.
# 계정당 clear_cart 1회 후 전 상품 add → 재시도 시에도 중복 없이 plan 그대로 재구성.
CART_ONLY = os.environ.get("CART_ONLY", "false").lower() == "true"
CART_ONLY_DELAY_SEC = int(os.environ.get("CART_ONLY_DELAY_SEC", "20"))  # cart-only 계정 간 대기(결제 없어 추적위험 낮음)

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
    "cardCd10": ("hana_card",      "flow_payment", True),   # 하나카드 — FLAG_SECURE
    "cardCd01": ("bc_paybook_isp", "flow_payment", False),  # BC 페이북 — ADB screencap (nf_key_serial, FLAG_SECURE 아님, 5/29 검증). 카메라 불필요
    # cardCd03 KB / cardCd40 NH 는 좌표 미완 — 추후
}

# 오늘의 결제수단 강제 지정 — 비우면 캐러셀 자동 판독
# 값 예: "삼성카드" / "현대카드" / "카카오페이" / "토스페이"
TODAY_BRAND_OVERRIDE = os.environ.get("TODAY_BRAND", "").strip()


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
        body = page.inner_text("body")
        if "로그아웃" in body:
            return True
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


def clear_cart(page: Page) -> None:
    try:
        page.goto(CART_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(1500)
        body = page.inner_text("body")
        if "장바구니가 비어" in body or "담긴 상품이 없" in body:
            print("    [cart] 이미 비어있음")
            return

        clicked = page.evaluate("""
            () => {
                const labels = Array.from(document.querySelectorAll('label.chklabel'));
                const target = labels.find(l => {
                    const span = l.querySelector('span');
                    return span && span.textContent.trim() === '일반상품';
                });
                if (!target) return false;
                const cb = target.querySelector('input[type="checkbox"]');
                if (cb && !cb.checked) target.click();
                return true;
            }
        """)
        if not clicked:
            print("    [cart] 일반상품 체크박스 없음")
            return
        page.wait_for_timeout(500)
        delete_btn = page.locator("button.btn-linelgray").filter(has_text="선택삭제").first
        if delete_btn.count() == 0:
            delete_btn = page.locator("button").filter(has_text="선택삭제").first
        if delete_btn.count() == 0:
            print("    [cart] 선택삭제 버튼 없음")
            return
        delete_btn.click()
        page.wait_for_timeout(800)
        for txt in ("예", "확인", "삭제"):
            confirm = page.locator("button").filter(has_text=txt).first
            if confirm.count() > 0 and confirm.is_visible():
                confirm.click()
                page.wait_for_timeout(400)
                break
        page.wait_for_timeout(500)
        print("    [cart] 비우기 완료")
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

    if info.get("auto_coupon", False):
        click_coupon_receive(page)

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


def do_checkout(page: Page) -> dict:
    """cart→구매하기→카드선택→결제하기→7자리 추출.
    Returns dict: {success, code, card_brand, error}
    """
    out = {"success": False, "code": None, "card_brand": None, "card_cd": None,
           "is_pay": False, "qr_pay": None, "error": None}
    try:
        # 1) cart 페이지로 이동 + 일반상품 체크
        page.goto(CART_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(1500)
        page.evaluate("""
            () => {
                const labels = Array.from(document.querySelectorAll('label.chklabel'));
                const target = labels.find(l => {
                    const span = l.querySelector('span');
                    return span && span.textContent.trim() === '일반상품';
                });
                if (target) {
                    const cb = target.querySelector('input[type="checkbox"]');
                    if (cb && !cb.checked) target.click();
                }
            }
        """)
        page.wait_for_timeout(500)

        # 2) 구매하기 클릭
        purchase_btn = page.locator("button.btn-purchase").filter(has_text="구매하기").first
        if purchase_btn.count() == 0:
            purchase_btn = page.locator("button").filter(has_text="구매하기").first
        if purchase_btn.count() == 0:
            out["error"] = "cart 구매하기 버튼 없음 (cart 비어있을 수 있음)"
            return out
        purchase_btn.click()
        page.wait_for_load_state("domcontentloaded", timeout=15000)
        page.wait_for_timeout(2500)

        # 3) 캐러셀 판독 + 슬라이드 선택 (TODAY_BRAND 있으면 그 카드 강제, 없으면 최고%)
        slides = detect_carousel_slides(page)
        if not slides:
            out["error"] = "캐러셀 슬라이드 미발견 (h2:카드할인 섹션 X)"
            return out
        all_summary = [f"{s['brand']}({s['percent']}%)" + ("" if s["isCard"] else "[PAY]") for s in slides]
        print(f"    [INFO] 캐러셀 슬라이드 {len(slides)}개: {all_summary}")

        pick = pick_carousel_slide(slides, override=TODAY_BRAND_OVERRIDE)
        modal_fallback = False
        if not pick:
            if TODAY_BRAND_OVERRIDE:
                # 결제수단변경 modal fallback — 캐러셀에 오늘 즉시할인 없는 카드 선택
                print(f"    [INFO] 캐러셀에 '{TODAY_BRAND_OVERRIDE}' 없음 — 결제수단변경 modal fallback 시도")
                pick = select_card_via_change_modal(page, TODAY_BRAND_OVERRIDE)
                if pick:
                    modal_fallback = True
                    print(f"    [OK] modal fallback: '{TODAY_BRAND_OVERRIDE}' 선택 (cardCd={pick.get('cardCd')})")
                else:
                    out["error"] = f"TODAY_BRAND='{TODAY_BRAND_OVERRIDE}' 캐러셀+모달 fallback 모두 실패"
                    return out
            else:
                out["error"] = "슬라이드 선택 실패"
                return out

        cardcd = pick.get("cardCd", "")
        out["card_cd"] = cardcd
        brand_text = pick.get("brand", "")
        # 카카오페이/토스페이는 alt='cardCdXX' 라도 brand 텍스트 기준으로 페이로 강제 분류
        # (alt.startsWith('cardCd') 만으로는 카카오페이를 카드로 오분류 → 결제까지 진행되는 버그)
        is_kakao = "카카오" in brand_text
        is_toss = "토스" in brand_text and "페이" in brand_text
        is_pay_brand = is_kakao or is_toss or (not pick.get("isCard", True))
        tag = "페이" if is_pay_brand else "카드"
        mode = "override" if TODAY_BRAND_OVERRIDE else "auto"
        print(f"    [OK] {tag} {mode} 선택: '{brand_text}' {pick.get('percent')}% ({cardcd})")

        if is_pay_brand:
            internal_brand = "PAY"
            if is_kakao:
                out["card_brand"] = "카카오페이"
            elif is_toss:
                out["card_brand"] = "토스페이"
            else:
                out["card_brand"] = brand_text
        else:
            internal_brand = CARD_CD_TO_BRAND.get(cardcd, "UNKNOWN")
            out["card_brand"] = CARD_CD_TO_NAME.get(cardcd, brand_text)
        out["is_pay"] = is_pay_brand

        # 카카오페이 선택 시 — cart까지만 (본폰 카카오페이로 user 수동 결제)
        if "카카오페이" in out["card_brand"]:
            print(f"    [SKIP] 카카오페이 선택됨 — 결제하지 않고 cart만 유지 (본폰에서 수동 결제)")
            out["success"] = True
            out["cart_only"] = True
            out["manual_payment_needed"] = "kakao_pay"
            return out

        # modal fallback 으로 이미 선택된 경우 캐러셀 click 건너뜀
        if modal_fallback:
            print(f"    [SKIP] modal 로 카드 선택됨 — 캐러셀 click skip")
        else:
            # 캐러셀 슬라이드 click — 결제 페이지 진입 시 최적 카드(첫 슬라이드)가 자동 selected 상태.
            # 자동 모드 (TODAY_BRAND 빈문자열) = pick 한 카드가 첫 슬라이드와 같으면 click X (toggle off 방지).
            # override 모드 또는 다른 카드 pick 시에만 click.
            already_selected = page.evaluate(f"""() => {{
                const el = document.querySelector('div._32o920j:has(img[alt="{cardcd}"])');
                return el ? (el.className || '').includes('selected') : false;
            }}""")
            if already_selected:
                print(f"    [SKIP] cardCd={cardcd} 이미 selected 상태 — click 안 함 (한 번 더 누르면 toggle off)")
            else:
                if not click_carousel_slide(page, cardcd):
                    out["error"] = "캐러셀 슬라이드 클릭 실패"
                    return out

        # 4) 결제하기 클릭 (button.btn-confirm + "N원 결제하기" 형태 매칭)
        page.wait_for_timeout(800)
        pay_btn = page.locator("button.btn-confirm").filter(has_text="결제하기").first
        if pay_btn.count() == 0:
            pay_btn = page.locator("button").filter(has_text="결제하기").first
        if pay_btn.count() == 0:
            out["error"] = "결제하기 버튼 없음"
            return out
        pay_btn.click()  # v3 에서 동작 검증된 locator click
        page.wait_for_timeout(3500)  # v3 timing

        # 5) 카드 path → 7자리 / 페이 path → QR 화면 도달 확인
        if out["is_pay"]:
            qr_pay = detect_qr_screen(page)
            out["qr_pay"] = qr_pay or "UNKNOWN"
            print(f"    [INFO] 페이 path: {out['qr_pay']} QR 화면 도달 — Phase 3-B 폰 자동화 대기")
            out["success"] = True
            return out

        if not click_payment_app_option(page, internal_brand):
            print(f"    [WARN] 결제수단 popup에서 app pay 버튼 미발견 — 7자리 코드 직접 추출 시도")
        page.wait_for_timeout(2000)
        code = extract_monimo_code(page)
        if not code:
            out["error"] = "monimo 7자리 코드 미추출 (popup 셀렉터 검토 필요)"
            return out
        out["code"] = code
        out["success"] = True
        return out
    except Exception as e:
        out["error"] = f"checkout 예외: {e}"
        return out


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


def detect_qr_screen(page: Page) -> str | None:
    """페이 path에서 결제하기 후 QR 화면 도달 확인. 'KAKAOPAY' / 'TOSSPAY' / None 반환."""
    print("    [INFO] QR 화면 대기 (10초)...")
    deadline = time.time() + 10
    while time.time() < deadline:
        for p in page.context.pages:
            for frame in p.frames:
                try:
                    txt = frame.evaluate("() => document.body ? document.body.innerText : ''") or ""
                    title = frame.evaluate("() => document.title || ''") or ""
                except Exception:
                    continue
                blob = (title + " " + txt).lower()
                if "카카오페이" in (title + txt) and "qr" in blob:
                    return "KAKAOPAY"
                if "토스페이" in (title + txt) and "qr" in blob:
                    return "TOSSPAY"
        page.wait_for_timeout(500)
    return None


def dismiss_card_overlay_popups(all_pages) -> None:
    """카드사별 안내/권한 overlay popup 자동 dismiss.
    NH: vbv.nonghyup.com의 '크롬·엣지 141 업데이트 안내' (a#btnClose).
    추가 카드사는 아래 dismiss_actions에 등록.
    """
    dismiss_actions = [
        # (URL substring, JS selector to click)
        ("nonghyup.com", "a#btnClose"),
    ]
    for p in all_pages:
        url = p.url
        for url_sub, sel in dismiss_actions:
            if url_sub not in url:
                continue
            try:
                # selector 존재하면 click
                clicked = p.evaluate(f"""
                    () => {{
                        const el = document.querySelector("{sel}");
                        if (el && el.offsetParent !== null) {{ el.click(); return true; }}
                        return false;
                    }}
                """)
                if clicked:
                    print(f"    [INFO] overlay popup 닫기: {url[:60]} ({sel})")
            except Exception:
                pass


def click_nh_pay_button(page: Page) -> bool:
    """NH 카드 popup의 파란 '결제하기' 버튼 클릭.
    NH popup frame 식별 → frame 안에서 '결제하기' click.
    """
    deadline = time.time() + 8
    while time.time() < deadline:
        for p in page.context.pages:
            dismiss_card_overlay_popups([p])
            for frame in p.frames:
                try:
                    text = frame.evaluate("() => document.body ? document.body.innerText : ''") or ""
                except Exception:
                    continue
                # NH popup 식별 — "NH pay" + "다른 결제" 둘 다 있어야 NH 결제 frame
                if "NH pay" not in text or "다른 결제" not in text:
                    continue
                # frame 안에서 결제하기 click (frame URL 기록)
                try:
                    clicked = frame.evaluate("""
                        () => {
                            const els = Array.from(document.querySelectorAll('*'))
                                .filter(el => el.children.length === 0 && el.textContent.trim() === '결제하기' && el.offsetParent !== null);
                            if (els.length === 0) return false;
                            for (const el of els) {
                                let target = el;
                                for (let d = 0; d < 6 && target; d++) {
                                    if (target.tagName === 'BUTTON' || target.tagName === 'A' || target.onclick) {
                                        target.click();
                                        return true;
                                    }
                                    target = target.parentElement;
                                }
                            }
                            els[0].click();
                            return true;
                        }
                    """)
                    if clicked:
                        print(f"    [OK] NH popup '결제하기' 클릭 (frame={(frame.url or '?')[:60]})")
                        return True
                except Exception as e:
                    print(f"    [WARN] NH frame 클릭 시도 실패: {e}")
                    continue
        page.wait_for_timeout(500)
    print(f"    [WARN] NH popup '결제하기' 버튼 미발견 (8s)")
    return False


def click_payment_app_option(page: Page, card_brand: str) -> bool:
    """결제하기 후 carrier-card popup(주로 iframe)에서 app pay 버튼 클릭.
    삼성 → monimo pay 결제. iframe 모든 frame 순회.
    """
    page.wait_for_timeout(3000)  # iframe 로드 충분히 대기

    # NH는 2단계 (popup 식별 + 결제하기) — 별도 함수
    if card_brand == "NH":
        return click_nh_pay_button(page)

    brand_app_keywords: dict[str, list[str]] = {
        "SAMSUNG": ["monimo pay", "모니모"],
        "HYUNDAI": ["앱카드 결제"],   # 현대카드 popup의 "앱카드 결제" → 7자리(4-3) 화면
        "KB": ["KB Pay", "KB페이", "앱으로 결제"],
        "BC": ["페이북", "ISP", "BC페이북"],
        "LOTTE": ["롯데카드 앱", "L.pay", "엘페이"],
        "HANA": ["하나카드 앱", "하나페이", "1Q페이"],
    }
    keywords = brand_app_keywords.get(card_brand, [])
    if not keywords:
        keywords = ["PC결제", "PC 결제", "monimo pay", "앱으로 결제"]
    excludes: list[str] = []

    js = """
        (kwList) => {
            for (const kw of kwList) {
                const els = Array.from(document.querySelectorAll('*'))
                    .filter(el => el.offsetParent !== null && el.textContent && el.textContent.includes(kw));
                if (els.length === 0) continue;
                els.sort((a, b) => a.textContent.length - b.textContent.length);
                for (const el of els) {
                    let target = el;
                    for (let depth = 0; depth < 6 && target; depth++) {
                        const tag = target.tagName;
                        const role = target.getAttribute && target.getAttribute('role');
                        if (tag === 'BUTTON' || tag === 'A' || role === 'button' || target.onclick) {
                            target.click();
                            return { ok: true, kw, depth, tag };
                        }
                        target = target.parentElement;
                    }
                }
                els[0].click();
                return { ok: true, kw, depth: -1, tag: els[0].tagName };
            }
            return { ok: false };
        }
    """
    # 모든 page (새 창 popup 포함) + 각 page의 모든 frame 순회
    # 삼성카드 결제 popup은 별도 브라우저 창으로 열림 (window.open)
    context = page.context
    deadline = time.time() + 8  # 새 창 8초까지 기다림 (v3 검증)
    while time.time() < deadline:
        all_pages = context.pages
        # 우선순위: hmall.com orderRequest > hmall.com 기타 > 외부 도메인 (monimo.com 등)
        # 이전 잔여 탭 (monimo.com 메인 등) 의 '모니모' 텍스트가 먼저 매칭되어 잘못 클릭되는 것 방지
        all_pages = sorted(all_pages, key=lambda p: (
            0 if 'hmall.com' in p.url and 'order' in p.url else
            1 if 'hmall.com' in p.url else
            2
        ))
        # NH 등 카드사별 overlay 안내 popup 자동 dismiss
        dismiss_card_overlay_popups(all_pages)
        for pi, p in enumerate(all_pages):
            for fi, frame in enumerate(p.frames):
                # 카드별 exclude URL — 해당 substring 포함된 프레임은 skip
                if excludes and any(e in (frame.url or "") for e in excludes):
                    continue
                try:
                    result = frame.evaluate(js, keywords)
                    if result and result.get("ok"):
                        page_label = f"page[{pi}]({p.url[:50]})"
                        frame_label = "main" if fi == 0 else f"iframe[{fi}]"
                        print(f"    [OK] 결제수단 click on {page_label} {frame_label}: '{result.get('kw')}'")
                        return True
                except Exception:
                    continue
        page.wait_for_timeout(800)
    print(f"    [WARN] {len(context.pages)}개 page 전부 검색했지만 keyword {keywords} 미발견")
    return False


def extract_monimo_code(page: Page) -> str | None:
    """결제 popup의 7자리 코드 추출. monimo (4-3) / NH (2-2-3) / 단순 7자리 모두 지원."""
    print("    [INFO] 결제 코드 popup 대기 (15초)...")
    context = page.context
    deadline = time.time() + 15
    # 컨텍스트 키워드 — 결제 코드 화면임을 식별
    ctx_keywords = (
        "남은 시간", "monimo", "PC결제", "PC 결제",
        "결제코드", "결제완료", "결제 완료",
        "QR코드", "QR 촬영", "숫자코드", "앱카드",
    )
    # 추출 패턴 (priority order)
    patterns = [
        re.compile(r"\b(\d{2})[-\s](\d{2})[-\s](\d{3})\b"),  # NH: "26-35-585"
        re.compile(r"\b(\d{4})[-\s]*(\d{3})\b"),               # monimo: "3358 599"
        re.compile(r"\b(\d)\s+(\d)\s+(\d)\s+(\d)\s+(\d)\s+(\d)\s+(\d)\b"),  # 현대카드: 각 자리 줄바꿈 분리
    ]
    while time.time() < deadline:
        for p in context.pages:
            for frame in p.frames:
                try:
                    txt = frame.evaluate("() => document.body ? document.body.innerText : ''") or ""
                except Exception:
                    continue
                if not txt:
                    continue
                txt_lower = txt.lower()
                if not any(k.lower() in txt_lower for k in ctx_keywords):
                    continue
                for pat in patterns:
                    m = pat.search(txt)
                    if m:
                        code = "".join(m.groups())
                        print(f"    [OK] 결제 코드: {code} (page={p.url[:50]})")
                        # 결제 코드 페이지를 사용자 시야로 활성화 (background 가 아닌 foreground)
                        try:
                            p.bring_to_front()
                        except Exception:
                            pass
                        return code
        page.wait_for_timeout(500)
    print("    [WARN] 15초 안에 결제 코드 미발견")
    return None


# ───────────── 결제 verify ─────────────


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
            target = ctx.new_page()
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


def _load_prmo_list(product_id: int) -> list[str]:
    """cart/today.json 의 products[].events 에서 prmoNo list 추출 (dual 적립 대응)."""
    try:
        today_path = PROJECT_ROOT / "cart" / "today.json"
        if not today_path.exists():
            return []
        d = json.loads(today_path.read_text(encoding="utf-8"))
        for p in d.get("products", []):
            if str(p.get("id")) == str(product_id):
                return [e.get("prmo") for e in (p.get("events") or []) if e.get("prmo")]
    except Exception as e:
        print(f"    [HPOINT] prmo lookup error: {e}")
    return []


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


# ───────────── 폰 자동 결제 brige ─────────────


def trigger_phone_payment(card_cd: str, code: str, timeout_sec: int = 180) -> dict:
    """7자리 코드 추출 성공 시 phone_auto/flow_runner.py 자동 호출.

    cardCd → CARD_CD_TO_PHONE_FLOW 매핑으로 (coords_name, flow_key) 결정.
    flow 안의 input_pin step 은 vars["code"] 로 7자리 자동 주입.
    """
    out = {"success": False, "error": None, "log_tail": ""}
    mapping = CARD_CD_TO_PHONE_FLOW.get(card_cd)
    if not mapping:
        out["error"] = f"폰 자동화 flow 미정의 (cardCd={card_cd}) — CARD_CD_TO_PHONE_FLOW 에 추가 필요"
        return out
    # 2-tuple (legacy) / 3-tuple 둘 다 지원 — 미지정 시 카메라 default
    if len(mapping) == 3:
        coords_name, flow_key, use_camera = mapping
    else:
        coords_name, flow_key = mapping
        use_camera = True
    import subprocess
    py = os.environ.get("PYTHON_BIN") or "/opt/homebrew/bin/python3"
    cmd = [py, "-m", "phone_auto.flow_runner", coords_name, flow_key, f"--code={code}"]
    print(f"  [PHONE] 자동 결제 호출: {coords_name}/{flow_key} code={code} camera={use_camera}")
    # camera 모드 = FLAG_SECURE 키패드 → portrait frame OCR 필수
    # (메모리 [[feedback_phone_landscape_mount_cam_portrait]] — 폰 가로 마운트 + rotation 90 = portrait 1080x1920)
    env = {**os.environ, "FLOW_USE_CAMERA": "1" if use_camera else "0",
           "FLOW_PORTRAIT": "1" if use_camera else "0"}
    try:
        result = subprocess.run(
            cmd, cwd=str(PROJECT_ROOT), env=env,
            capture_output=True, text=True, timeout=timeout_sec,
        )
        out["log_tail"] = (result.stdout[-600:] or "") + (("\n[stderr]\n" + result.stderr[-300:]) if result.stderr else "")
        if result.returncode == 0:
            out["success"] = True
        else:
            out["error"] = f"flow_runner exit {result.returncode}"
    except subprocess.TimeoutExpired:
        out["error"] = f"flow_runner timeout ({timeout_sec}s)"
    return out


# ───────────── 메인 흐름 ─────────────


def process_account(context: BrowserContext, idx: int, account: dict, items: list[dict],
                     cdp_mode: bool = False) -> tuple[int, int, bool, dict | None]:
    """현대몰 쿠폰 정책: 쿠폰 가능한 상품은 단독 결제 필수.
    → 같은 계정의 여러 product 도 각각 별도 cycle (clear_cart + add 1개 + checkout + 결제).
    cycle 사이 ITEM_DELAY_SEC 대기.
    """
    print(f"\n=== #{idx} {account['id']} — 담을 상품 {len(items)}개 (각 단독 결제) ===")
    page = context.new_page()

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
        page.close()
        return (0, len(items), False, None)

    if CART_ONLY:
        # cart-only: clear 1회 → plan 전 상품 누적 담기 (checkout/결제 없음). 결제는 사용자가 수동.
        clear_cart(page)
        success = 0
        for ci, entry in enumerate(items, 1):
            print(f"\n  ── #{idx} (cart-only) {ci}/{len(items)} — product {entry['product_id']} x{entry['qty']} ──")
            if add_to_cart(page, entry["product_id"], entry["info"], entry["qty"]):
                success += 1
            else:
                print(f"  [SKIP] #{idx} {ci}번째 add_to_cart 실패")
            if ci < len(items):
                page.wait_for_timeout(3000)
        print(f"  ✓ #{idx} {account['id']} cart-only 담기 {success}/{len(items)}")
        page.close()
        return (success, len(items), True, None)

    success = 0
    last_checkout_result: dict | None = None
    for ci, entry in enumerate(items, 1):
        print(f"\n  ── #{idx} cycle {ci}/{len(items)} — product {entry['product_id']} x{entry['qty']} 단독 결제 ──")
        clear_cart(page)
        if not add_to_cart(page, entry["product_id"], entry["info"], entry["qty"]):
            print(f"  [SKIP] cycle {ci} add_to_cart fail")
            continue
        success += 1

        print(f"  [CHECKOUT] cycle {ci} 시작...")
        checkout_result = do_checkout(page)
        last_checkout_result = checkout_result
        if checkout_result["success"]:
            if checkout_result.get("is_pay"):
                print(f"  ✓ [PAY QR] {checkout_result['card_brand']} → {checkout_result.get('qr_pay')} QR")
            else:
                print(f"  ✓ [PAYMENT CODE] {checkout_result['card_brand']} → {checkout_result['code']}")
                # 카드 path + 7자리 추출 성공 + DRY_PAYMENT=false → 폰 자동 결제 trigger
                if not DRY_PAYMENT and checkout_result.get("code") and checkout_result.get("card_cd"):
                    # 결제 전 mypage 결제완료 count snapshot — 결제 후 +1 검증
                    pre_count = _read_mypage_paid_count(page)
                    phone_res = trigger_phone_payment(
                        checkout_result["card_cd"], checkout_result["code"]
                    )
                    checkout_result["phone_result"] = phone_res
                    if phone_res["success"]:
                        # hmall 측 결제 완료 verify — mypage 결제완료 count 갱신 확인
                        page.wait_for_timeout(8000)
                        post_count = _read_mypage_paid_count(page)
                        if pre_count is not None and post_count is not None and post_count > pre_count:
                            print(f"  ✓ [PHONE+HMALL OK] #{idx} 결제 완료 확정 ({pre_count} → {post_count})")
                        else:
                            print(f"  ⚠ [VERIFY FAIL] #{idx} phone OK 이지만 hmall mypage 결제완료 미증가 ({pre_count}→{post_count}) — PIN 잘못 입력 가능")
                            phone_res["success"] = False
                            phone_res["error"] = "hmall mypage 결제완료 미증가"
                        # H.Point 적립 신청 — 결제 완료 후만 신청 가능
                        prmo_list = _load_prmo_list(entry["product_id"])
                        if prmo_list:
                            print(f"  [HPOINT] {len(prmo_list)}개 행사 신청 시도...")
                            for prmo in prmo_list:
                                apply_hpoint(page, prmo)
                        else:
                            print(f"  [HPOINT] product {entry['product_id']} prmoNo 없음 — skip")
                    else:
                        print(f"  ✗ [PHONE FAIL] #{idx} {phone_res['error']}")
                        if phone_res.get("log_tail"):
                            print(f"    └─ log: {phone_res['log_tail'][-200:]}")
            if DRY_PAYMENT:
                print(f"  ⚠️ DRY_PAYMENT=true — 폰 자동 결제 skip, 7자리 코드만 추출")
        else:
            print(f"  ✗ [CHECKOUT FAIL] cycle {ci}: {checkout_result['error']}")

        # 같은 계정의 cycle 사이 = 즉시 진행 (한 아이디 안에서는 wait 불필요)
        # 7분 wait 는 계정 간 (_process 의 ACCOUNT_DELAY_SEC) 에만 적용
        if ci < len(items):
            cycle_delay = 5
            print(f"  [WAIT] 같은 계정 다음 cycle 전 {cycle_delay}s 대기...")
            time.sleep(cycle_delay)

    page.close()
    return (success, len(items), True, last_checkout_result)


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
    print(f"[INFO] DRY_PAYMENT={DRY_PAYMENT}")
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
