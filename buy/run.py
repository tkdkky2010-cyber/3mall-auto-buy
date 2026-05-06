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
from patchright.sync_api import sync_playwright, Page, BrowserContext, TimeoutError as PlaywrightTimeoutError

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

# 카드 brand 매핑 (텍스트 키워드 → 내부 코드)
CARD_BRAND_MAP = {
    "현대": "HYUNDAI",
    "삼성": "SAMSUNG",
    "롯데": "LOTTE",
    "KB": "KB",
    "국민": "KB",
    "하나": "HANA",
    "농협": "NH",
    "NH": "NH",
    "BC": "BC",
    "비씨": "BC",
}

# 다중 카드 중 default 선택 — .env에서 로드
DEFAULT_CARD_NAME: dict[str, str] = {
    "HYUNDAI": os.environ.get("HYUNDAI_CARD_NAME", "").strip(),
    "KB": os.environ.get("KB_CARD_NAME", "").strip(),
}


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
    out = {"success": False, "code": None, "card_brand": None, "error": None}
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

        # 3) 카드할인 섹션 → 추천 카드 선택
        card_brand = select_best_card(page)
        if not card_brand:
            out["error"] = "카드할인 섹션 또는 추천 카드 없음 (셀렉터 검토 필요)"
            return out
        out["card_brand"] = card_brand

        # 4) 결제하기 클릭
        page.wait_for_timeout(800)
        pay_btn = page.locator("button").filter(has_text=re.compile(r"^\s*결제하기\s*$")).first
        if pay_btn.count() == 0:
            # 좀 더 너그럽게
            pay_btn = page.locator("button").filter(has_text="결제하기").first
        if pay_btn.count() == 0:
            out["error"] = "결제하기 버튼 없음"
            return out
        pay_btn.click()
        page.wait_for_timeout(3500)

        # 5) monimo 7자리 코드 추출
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


def select_best_card(page: Page) -> str | None:
    """카드할인 섹션 parse → 추천 카드 클릭. brand 코드 반환."""
    section_loc = page.locator("h2:has-text('카드할인')").first
    if section_loc.count() == 0:
        print("    [WARN] '카드할인' h2 미발견")
        return None

    try:
        section_text = section_loc.locator("xpath=ancestor::div[1]").inner_text(timeout=3000)
    except Exception:
        section_text = ""
    print(f"    [DEBUG] 카드할인 섹션 첫 200자: {section_text[:200].replace(chr(10), ' | ')}")

    recommended_brand: str | None = None
    matched_keyword: str | None = None
    for keyword, brand in CARD_BRAND_MAP.items():
        if keyword in section_text:
            recommended_brand = brand
            matched_keyword = keyword
            print(f"    [INFO] 카드 brand 감지: '{keyword}' → {brand}")
            break

    if not recommended_brand or not matched_keyword:
        print("    [WARN] 카드 brand 텍스트 매칭 실패 — 셀렉터 검토 필요")
        return None

    keywords_list = list(CARD_BRAND_MAP.keys())
    js = """
        (kwList) => {
            const h2 = Array.from(document.querySelectorAll('h2'))
                .find(h => h.textContent.trim() === '카드할인');
            if (!h2) return false;
            const section = h2.closest('div');
            if (!section) return false;
            const els = Array.from(section.querySelectorAll('button, a, li, label'));
            for (const kw of kwList) {
                const target = els.find(el => el.textContent.includes(kw) && el.offsetParent !== null);
                if (target) { target.click(); return true; }
            }
            return false;
        }
    """
    clicked = page.evaluate(js, keywords_list)
    if not clicked:
        print(f"    [WARN] 카드 클릭 실패")
    page.wait_for_timeout(1200)

    if recommended_brand in DEFAULT_CARD_NAME and DEFAULT_CARD_NAME[recommended_brand]:
        select_specific_card_variant(page, recommended_brand, DEFAULT_CARD_NAME[recommended_brand])

    return recommended_brand


def select_specific_card_variant(page: Page, brand: str, card_name: str) -> None:
    """현대 X BOOST / KB 아시아나 체크 RF 3300 같은 다중 카드 default."""
    print(f"    [INFO] {brand} 다중 카드 — '{card_name}' 선택 시도")
    page.wait_for_timeout(1500)
    target = page.locator("button, a, label, li").filter(has_text=card_name).first
    if target.count() > 0 and target.is_visible():
        target.click()
        page.wait_for_timeout(800)
        print(f"    [OK] '{card_name}' 클릭")
    else:
        print(f"    [WARN] '{card_name}' 미발견 — default 카드 그대로")


def extract_monimo_code(page: Page) -> str | None:
    """결제하기 후 monimo popup의 7자리 (4-3 형식) 추출."""
    print("    [INFO] monimo popup 대기 (10초)...")
    deadline = time.time() + 10
    while time.time() < deadline:
        for frame in [page] + list(page.frames):
            try:
                txt = frame.evaluate("() => document.body ? document.body.innerText : ''") or ""
            except Exception:
                continue
            # 4-3 패턴 + popup 컨텍스트 검증 (남은 시간 / monimo / PC결제 키워드 중 하나)
            if not txt:
                continue
            ctx_ok = ("남은 시간" in txt) or ("monimo" in txt.lower()) or ("PC결제" in txt) or ("PC 결제" in txt)
            if not ctx_ok:
                continue
            m = re.search(r"\b(\d{4})\s*(\d{3})\b", txt)
            if m:
                code = m.group(1) + m.group(2)
                print(f"    [OK] monimo 코드: {code}")
                return code
        page.wait_for_timeout(500)
    print("    [WARN] 10초 안에 monimo 코드 미발견")
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
    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(CDP_ENDPOINT, slow_mo=500)
        except Exception as e:
            print(f"[FATAL] CDP 연결 실패: {e}")
            print(f"        Chrome을 --remote-debugging-port={CDP_PORT} 옵션으로 띄웠는지 확인")
            print(f"        (예: hsmaster/scripts/launch-chrome-cdp.sh)")
            return 1

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
