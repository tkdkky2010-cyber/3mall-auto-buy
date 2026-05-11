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

ACCOUNT_DELAY_SEC = 5
CDP_PORT = os.environ.get("CDP_PORT", "9222")
CDP_ENDPOINT = f"http://127.0.0.1:{CDP_PORT}"

# Phase 3-A 안전장치: 결제하기 클릭 후 7자리 코드만 추출하고 폰 결제는 수동 (또는 Phase 3-B)
DRY_PAYMENT = os.environ.get("DRY_PAYMENT", "true").lower() == "true"

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

    option_idx = info.get("option_index") or 1
    try:
        opt = page.locator("span.choice-num.title").filter(has_text=f"[선택 {option_idx}]").first
        if opt.count() > 0:
            opt.click()
            page.wait_for_timeout(700)
        else:
            print(f"    [WARN] [선택 {option_idx}] 옵션 라벨 못 찾음")
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
    out = {"success": False, "code": None, "card_brand": None,
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
        if not pick:
            if TODAY_BRAND_OVERRIDE:
                out["error"] = f"TODAY_BRAND='{TODAY_BRAND_OVERRIDE}' 캐러셀 슬라이드 매칭 실패 (결제수단변경 모달 fallback 미구현)"
            else:
                out["error"] = "슬라이드 선택 실패"
            return out

        cardcd = pick.get("cardCd", "")
        is_pay = not pick.get("isCard", True)
        tag = "카드" if pick.get("isCard") else "페이"
        mode = "override" if TODAY_BRAND_OVERRIDE else "auto"
        print(f"    [OK] {tag} {mode} 선택: '{pick.get('brand')}' {pick.get('percent')}% ({cardcd})")

        if pick.get("isCard"):
            internal_brand = CARD_CD_TO_BRAND.get(cardcd, "UNKNOWN")
            out["card_brand"] = CARD_CD_TO_NAME.get(cardcd, pick.get("brand", ""))
        else:
            internal_brand = "PAY"
            short = pick.get("brand", "")
            out["card_brand"] = "카카오페이" if "카카오" in short else ("토스페이" if "토스" in short else short)
        out["is_pay"] = is_pay

        # 캐러셀 슬라이드 클릭 (Playwright real-click으로 React 핸들러 트리거)
        if not click_carousel_slide(page, cardcd):
            out["error"] = "캐러셀 슬라이드 클릭 실패"
            return out

        # 4) 결제하기 클릭
        page.wait_for_timeout(800)
        pay_btn = page.locator("button").filter(has_text=re.compile(r"^\s*결제하기\s*$")).first
        if pay_btn.count() == 0:
            pay_btn = page.locator("button").filter(has_text="결제하기").first
        if pay_btn.count() == 0:
            out["error"] = "결제하기 버튼 없음"
            return out
        pay_btn.click()
        page.wait_for_timeout(3500)

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
    """카드할인 캐러셀 모든 슬라이드의 메타데이터 list 반환 (클릭 X)."""
    js = """
        () => {
            const h2 = Array.from(document.querySelectorAll('h2'))
                .find(h => h.textContent.trim() === '카드할인');
            if (!h2) return [];
            let section = h2.closest('div');
            for (let lvl = 0; lvl < 5 && section; lvl++) {
                const slides = Array.from(section.querySelectorAll('.swiper-slide'))
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
                section = section.parentElement;
            }
            return [];
        }
    """
    return page.evaluate(js) or []


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
    """카드할인 캐러셀의 슬라이드를 Playwright real-click으로 선택.
    캐러셀 클릭이 정상 흐름 (99%). 결제수단변경 모달은 최후의 수단.
    """
    # img[alt=cardCd]을 가진 슬라이드 안의 hstack.root div가 실제 클릭 핸들러
    slide = page.locator(f'.swiper-slide:has(img[alt="{cardCd}"]) div[data-slot="hstack.root"]').first
    if slide.count() == 0:
        # fallback: swiper-slide 외곽
        slide = page.locator(f'.swiper-slide:has(img[alt="{cardCd}"])').first
    if slide.count() == 0:
        print(f"    [WARN] 슬라이드 cardCd={cardCd} 미발견")
        return False
    try:
        slide.scroll_into_view_if_needed(timeout=5000)
        slide.click()
        page.wait_for_timeout(1500)
        print(f"    [OK] 캐러셀 슬라이드 클릭 (cardCd={cardCd})")
        return True
    except Exception as e:
        print(f"    [WARN] 슬라이드 클릭 실패: {e}")
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
    deadline = time.time() + 8  # 새 창 8초까지 기다림
    while time.time() < deadline:
        all_pages = context.pages
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
                        return code
        page.wait_for_timeout(500)
    print("    [WARN] 15초 안에 결제 코드 미발견")
    return None


# ───────────── 메인 흐름 ─────────────


def process_account(context: BrowserContext, idx: int, account: dict, items: list[dict],
                     cdp_mode: bool = False) -> tuple[int, int, bool, dict | None]:
    print(f"\n=== #{idx} {account['id']} — 담을 상품 {len(items)}개 ===")
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

    clear_cart(page)

    success = 0
    for entry in items:
        if add_to_cart(page, entry["product_id"], entry["info"], entry["qty"]):
            success += 1

    checkout_result: dict | None = None
    if success > 0:
        print(f"  [CHECKOUT] #{idx} 시작...")
        checkout_result = do_checkout(page)
        if checkout_result["success"]:
            if checkout_result.get("is_pay"):
                print(f"  ✓ [PAY QR] #{idx} {checkout_result['card_brand']} → {checkout_result.get('qr_pay')} QR (Phase 3-B 폰 처리)")
            else:
                print(f"  ✓ [PAYMENT CODE] #{idx} {checkout_result['card_brand']} → {checkout_result['code']}")
            if DRY_PAYMENT:
                print(f"  ⚠️ DRY_PAYMENT=true — 폰에서 수동 결제 또는 Phase 3-B 자동화 대기")
        else:
            print(f"  ✗ [CHECKOUT FAIL] #{idx}: {checkout_result['error']}")

    page.close()
    return (success, len(items), True, checkout_result)


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
    for idx in target_indices:
        account = accounts[idx - 1]
        items = account_plan.get(idx, [])
        try:
            ok, total, cleared, ckt = process_account(context, idx, account, items, cdp_mode=True)
            summary.append((idx, account["id"], ok, total, cleared, ckt))
        except Exception as e:
            print(f"  [FATAL] #{idx} {account['id']}: {e}")
            summary.append((idx, account["id"], 0, len(items), False, None))
        time.sleep(ACCOUNT_DELAY_SEC)

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
