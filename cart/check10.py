"""Hmall 우수스토어 10% 적립 상품 자동 필터 (Step 2).

29개 상품 페이지 순회 → "10% 적립" 검출 + 단순/구간별 분류 → cart/today.json.
가이드: cart/Hmall 10% Check Guide.md

사용법:
    pip install -r ../buy/requirements.txt
    bash ../hsmaster/scripts/launch-chrome-cdp.sh   # CDP Chrome 띄우기
    python3 cart/check10.py                          # 기본 (account #1)
    python3 cart/check10.py 3                        # 특정 계정 idx
"""
from __future__ import annotations
import json
import os
import re
import sys
import time
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

PW_BACKEND = None
try:
    from patchright.sync_api import sync_playwright, Page, BrowserContext, TimeoutError as PlaywrightTimeoutError
    PW_BACKEND = "patchright"
except ImportError:
    from playwright.sync_api import sync_playwright, Page, BrowserContext, TimeoutError as PlaywrightTimeoutError  # type: ignore
    PW_BACKEND = "playwright"

ROOT = Path(__file__).parent
PROJECT_ROOT = ROOT.parent
load_dotenv(PROJECT_ROOT / "buy" / ".env")

ACCOUNTS_FILE = Path(os.environ.get("HMALL_CONFIG_PATH") or (PROJECT_ROOT / "hmall_config.json"))
TODAY_OUT = ROOT / "today.json"

CDP_PORT = os.environ.get("CDP_PORT", "9222")
CDP_ENDPOINT = f"http://127.0.0.1:{CDP_PORT}"
LOGIN_URL = "https://www.hmall.com/mo/cob/loginForm"
ITEM_URL_FMT = "https://www.hmall.com/md/pda/itemPtc?slitmCd={slitmCd}{extra}"

# 가이드 cart/Hmall 10% Check Guide.md 의 상품 목록 (Phase 2)
PRODUCTS = [
    {"id": 1,  "name": "이너플로라",                          "slitmCd": "2152461561", "url_extra": "&ordpreview=true"},
    {"id": 2,  "name": "하루견과 초록색 100봉",                "slitmCd": "2151046312", "url_extra": "&sectId=3059445"},
    {"id": 3,  "name": "하루견과 갈색 100봉",                 "slitmCd": "2225431602", "url_extra": "&sectId=3059445"},
    {"id": 4,  "name": "곡물도감 곡물서리태",                  "slitmCd": "2227834416", "url_extra": "&sectId=3059445"},
    {"id": 5,  "name": "말차 (4와 동일 URL)",                  "slitmCd": "2227834416", "url_extra": "&sectId=3059445", "alias_of": 4},
    {"id": 6,  "name": "레놉티",                             "slitmCd": "2244138695", "url_extra": "&sectId=3059445"},
    {"id": 7,  "name": "락토핏",                             "slitmCd": "2151878435", "url_extra": ""},
    {"id": 8,  "name": "이디야 디카페인",                      "slitmCd": "2244409628", "url_extra": "&sectId=3059445"},
    {"id": 9,  "name": "이디야 카페인",                       "slitmCd": "2246603712", "url_extra": "&sectId=3059445"},
    {"id": 10, "name": "이경제 더힘찬녹용 30포",                "slitmCd": "2240802022", "url_extra": "&ordpreview=true"},
    {"id": 11, "name": "라메종드미엘 프랑스 라벤더 천연꿀 8병",   "slitmCd": "2246845189", "url_extra": "&sectId=3059445"},
    {"id": 12, "name": "갱년기 다이어트 리얼퀸 3병",            "slitmCd": "2202276847", "url_extra": "&sectId=3059445"},
    {"id": 13, "name": "GRN 핑크 초록이 (12와 동일)",           "slitmCd": "2202276847", "url_extra": "",                "alias_of": 12},
    {"id": 14, "name": "GRN 곰돌이 (12와 동일)",                "slitmCd": "2202276847", "url_extra": "&sectId=3059445", "alias_of": 12},
    {"id": 15, "name": "셀게이트 글루타치온 30p",               "slitmCd": "2244515588", "url_extra": ""},
    {"id": 16, "name": "루솔",                               "slitmCd": "2225275921", "url_extra": ""},
    {"id": 17, "name": "데이즈온 원데이 알파 18개",             "slitmCd": "2247036059", "url_extra": "&sectId=3059445"},
    {"id": 19, "name": "뉴트리원 164",                        "slitmCd": "2120671185", "url_extra": "&sectId=3059445"},
    {"id": 20, "name": "바디랩 유기농 레몬즙 1박",              "slitmCd": "2244671296", "url_extra": "&sectId=3059445"},
    {"id": 21, "name": "뉴트리원 브레인알파PS 8박",             "slitmCd": "2148410018", "url_extra": "&sectId=3059445"},
    {"id": 22, "name": "올바른건강식품 와이 9박",                "slitmCd": "2244934734", "url_extra": "&sectId=3059445"},
    {"id": 23, "name": "셀게이트 컬리케일 6박",                 "slitmCd": "2244447010", "url_extra": "&sectId=3059445"},
    {"id": 24, "name": "알파CD 옐로우컷 20박",                  "slitmCd": "2245143490", "url_extra": "&sectId=3059445"},
    {"id": 25, "name": "유기농 석류젤리 9박(90개)",             "slitmCd": "2243971283", "url_extra": "&sectId=3059445"},
    {"id": 26, "name": "오라틱스 구강유산균 10박",              "slitmCd": "2244032427", "url_extra": "&sectId=3059445"},
    {"id": 27, "name": "에이투젠 혈당유산균 3개 1박스",          "slitmCd": "2150414954", "url_extra": "&sectId=3059445"},
    {"id": 28, "name": "스키니랩 시서스 다이어트 11박",          "slitmCd": "2202464603", "url_extra": "&sectId=3059445"},
    {"id": 29, "name": "뉴트리원 루테인 AX GR 100정",          "slitmCd": "2237504874", "url_extra": "&sectId=3059445"},
]


def login(page: Page, account_id: str, account_pw: str) -> bool:
    """buy/run.py의 login 흐름 그대로 — 1.2s/0.6s 자동입력 회피."""
    page.goto(LOGIN_URL, wait_until="domcontentloaded")
    page.wait_for_timeout(800)
    try:
        body = page.inner_text("body")
        if "로그아웃" in body:
            return True
    except Exception:
        pass
    try:
        id_box = page.get_by_role("textbox", name="Hmall / H.Point 아이디")
        id_box.fill("")
        id_box.fill(account_id)
        page.wait_for_timeout(1200)
        pw_box = page.get_by_role("textbox", name="비밀번호")
        pw_box.fill("")
        pw_box.fill(account_pw)
        page.wait_for_timeout(600)
        login_btn = page.locator("button").filter(has_text="로그인").first
        login_btn.click()
        page.wait_for_load_state("domcontentloaded", timeout=15000)
        page.wait_for_timeout(1500)
        body = page.inner_text("body")
        return "로그아웃" in body
    except Exception as e:
        print(f"  [LOGIN ERR] {account_id}: {e}")
        return False


def check_one_product(page: Page, prod: dict) -> dict:
    """단일 상품 페이지 → 10% 적립 검출 + 단순/구간별 판별 + 쿠폰가."""
    url = ITEM_URL_FMT.format(slitmCd=prod["slitmCd"], extra=prod.get("url_extra", ""))
    out = {
        "id": prod["id"],
        "name": prod["name"],
        "url": url,
        "ten_percent": False,
        "phrase": None,
        "type": None,           # "simple" / "tier" / "unknown"
        "has_coupon": False,    # strong.rvej6q8 (쿠폰 적용가 라벨) 존재 여부
        "error": None,
    }
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=15000)
        page.wait_for_timeout(1500)
    except Exception as e:
        out["error"] = f"page load: {e}"
        return out

    try:
        body_text = page.inner_text("body")
    except Exception as e:
        out["error"] = f"body read: {e}"
        return out

    if "판매가 중단" in body_text or "판매 중단" in body_text:
        out["error"] = "판매중단"
        return out

    # Step 1: "10% 적립" 검출
    if "10% 적립" not in body_text:
        return out  # ten_percent=False

    out["ten_percent"] = True

    # 적립 문구 추출 — "10% 적립" 앞 약 30자
    m = re.search(r"([^\n]{0,50}?)10%\s*적립", body_text)
    if m:
        out["phrase"] = (m.group(1) + "10% 적립").strip()

    # Step 2: H.Point 적립 상세 링크 click → 단순/구간별
    # 가이드 기준: 상세 페이지 테이블 행 1개 = simple, 2+ = tier
    detail_link = page.locator('a[href*="evntHPointDtl"]').first
    if detail_link.count() > 0:
        try:
            href = detail_link.get_attribute("href") or ""
            # 같은 탭 navigate
            detail_link.click()
            page.wait_for_load_state("domcontentloaded", timeout=10000)
            page.wait_for_timeout(2000)
            # "행사 상품 구매시" 하단 테이블 행 수
            try:
                rows = page.locator("table tbody tr").count()
            except Exception:
                rows = 0
            if rows == 1:
                out["type"] = "simple"
            elif rows >= 2:
                out["type"] = "tier"
            else:
                # 행 0 — 다른 구조일 수 있음. 문구로 fallback
                out["type"] = "tier" if "최대" in (out["phrase"] or "") else "simple"
            # 뒤로가기
            page.go_back(wait_until="domcontentloaded", timeout=10000)
            page.wait_for_timeout(1200)
        except Exception as e:
            out["type"] = "unknown"
    else:
        # H.Point 링크 없으면 phrase 기준 fallback
        out["type"] = "tier" if "최대" in (out["phrase"] or "") else "simple"

    # Step 3: 쿠폰 보유 여부 — strong.rvej6q8 (쿠폰 적용가 라벨) 존재만 확인
    # 가격은 폰트 난독화로 부정확하니 추출 X. 다운로드는 buy 단계의 click_coupon_receive에서.
    try:
        out["has_coupon"] = page.locator("strong.rvej6q8").count() > 0
    except Exception:
        pass

    return out


def print_report(results: list[dict]) -> None:
    print("\n========= 10% 적립 체크 결과 =========")
    print(f"{'#':>3} | {'제품명':40s} | {'10%':5s} | {'구분':10s} | 쿠폰")
    print("-" * 90)
    for r in results:
        if r.get("error"):
            mark_10 = "ERR"
            type_str = r["error"][:10]
        else:
            mark_10 = "✓" if r["ten_percent"] else "✗"
            type_map = {"simple": "✅단순10%", "tier": "⚠️구간별", "unknown": "?", None: "—"}
            type_str = type_map.get(r["type"], "—")
        coupon = "보유" if r.get("has_coupon") else "—"
        name = (r["name"][:38] + "…") if len(r["name"]) > 39 else r["name"]
        print(f"{r['id']:>3} | {name:40s} | {mark_10:5s} | {type_str:10s} | {coupon}")

    good = [r for r in results if r["ten_percent"] and r["type"] == "simple"]
    print(f"\n>>> 단순 10% 적립 (구매 후보): {len(good)}개")
    for r in good:
        print(f"     #{r['id']:>3} {r['name']}")


def main() -> int:
    if not ACCOUNTS_FILE.exists():
        print(f"[FATAL] {ACCOUNTS_FILE} 미존재")
        return 1
    accounts = json.loads(ACCOUNTS_FILE.read_text(encoding="utf-8"))["accounts"]

    acc_idx = 1
    if len(sys.argv) > 1:
        try:
            acc_idx = int(sys.argv[1])
        except ValueError:
            print(f"[ERR] argv[1]은 정수: {sys.argv[1]}")
            return 1
    if acc_idx < 1 or acc_idx > len(accounts):
        print(f"[ERR] 계정 idx 범위: 1~{len(accounts)}")
        return 1
    acc = accounts[acc_idx - 1]
    print(f"[INFO] 사용 계정 #{acc_idx} {acc['id']}")
    print(f"[INFO] PW backend: {PW_BACKEND}")

    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(CDP_ENDPOINT, slow_mo=300)
        except Exception as e:
            print(f"[FATAL] CDP 연결 실패: {e}")
            print(f"  Chrome을 --remote-debugging-port={CDP_PORT} 옵션으로 띄웠는지 확인")
            return 1
        return _run(browser, acc)


def _run(browser, acc) -> int:
    context = browser.contexts[0] if browser.contexts else browser.new_context()
    page = context.new_page()
    if not login(page, acc["id"], acc["pw"]):
        print(f"[FATAL] 로그인 실패")
        page.close()
        return 1
    print(f"[OK] 로그인")

    # 중복 URL은 한 번만 체크 (alias_of 처리)
    cache: dict[str, dict] = {}
    results: list[dict] = []
    for prod in PRODUCTS:
        if prod.get("alias_of"):
            base = next((r for r in results if r["id"] == prod["alias_of"]), None)
            if base:
                cloned = dict(base)
                cloned["id"] = prod["id"]
                cloned["name"] = prod["name"]
                cloned["alias_of"] = prod["alias_of"]
                results.append(cloned)
                print(f"  #{prod['id']:>3} (alias of #{prod['alias_of']}) 건너뜀, 동일 결과 적용")
                continue

        print(f"  #{prod['id']:>3} {prod['name'][:30]:30s} 검사 중...", flush=True)
        result = check_one_product(page, prod)
        results.append(result)
        status = "✓" if result["ten_percent"] else ("ERR" if result.get("error") else "✗")
        type_str = result.get("type") or "—"
        print(f"     → 10%={status}  type={type_str}  phrase={(result.get('phrase') or '')[:40]}")

    print_report(results)

    # JSON 출력
    payload = {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "account_used": acc["id"],
        "products": results,
        "good_ids": [r["id"] for r in results if r["ten_percent"] and r["type"] == "simple"],
    }
    TODAY_OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[OK] 결과 저장: {TODAY_OUT}")
    page.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
