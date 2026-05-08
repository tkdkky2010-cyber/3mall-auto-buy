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

# Google Sheets — Step 1과 동일 시트의 "{M.DD}" 탭에 추가 입력
SHEET_KEY = "1fxB0UvLRy2iQfonCWn5U5mWnXbzSdn6l4e2XuQluhwo"
GSPREAD_KEY = next(iter(PROJECT_ROOT.glob("gen-lang-*.json")), None)

# 가이드 cart/Hmall 10% Check Guide.md 의 상품 목록 (Phase 2)
PRODUCTS = [
    {"id": 1,  "name": "이너플로라",                          "slitmCd": "2154750833", "url_extra": ""},
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
        "min_purchase": None,   # 최소 구매금액 (예: "30,000원 이상", "1원 이상")
        "rate": None,           # 적립률 (예: "10%")
        "max_reward": None,     # 최대 적립금 (예: "200,000P")
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

    # Step 2: H.Point 적립 상세 링크 click → 표에서 최소구매/적립률 + 본문에서 최대적립
    detail_link = page.locator('a[href*="evntHPointDtl"]').first
    if detail_link.count() > 0:
        try:
            detail_link.click()
            page.wait_for_load_state("domcontentloaded", timeout=10000)
            page.wait_for_timeout(2000)
            extracted = page.evaluate("""
                () => {
                    const out = {};
                    // 표 첫 데이터 행: 최소구매금액 | 적립률
                    const tables = document.querySelectorAll('table');
                    for (const tbl of tables) {
                        const rows = tbl.querySelectorAll('tr');
                        for (const r of rows) {
                            const cells = r.querySelectorAll('td');
                            if (cells.length >= 2) {
                                const c0 = cells[0].textContent.trim();
                                const c1 = cells[1].textContent.trim();
                                if (/원\\s*이상|개\\s*이상/.test(c0) && /%/.test(c1)) {
                                    out.min_purchase = c0;
                                    out.rate = c1;
                                    break;
                                }
                            }
                        }
                        if (out.min_purchase) break;
                    }
                    // 본문에서 "최대 N,NNN P 까지만" 추출
                    const body = document.body ? document.body.innerText : '';
                    const m = body.match(/최대\\s*([\\d,]+)\\s*P/);
                    if (m) out.max_reward = m[1] + 'P';
                    return out;
                }
            """)
            if extracted:
                out["min_purchase"] = extracted.get("min_purchase")
                out["rate"] = extracted.get("rate")
                out["max_reward"] = extracted.get("max_reward")
            page.go_back(wait_until="domcontentloaded", timeout=10000)
            page.wait_for_timeout(1200)
        except Exception:
            pass

    # Step 3: 쿠폰 보유 여부 — strong.rvej6q8 (쿠폰 적용가 라벨) 존재만 확인
    # 가격은 폰트 난독화로 부정확하니 추출 X. 다운로드는 buy 단계의 click_coupon_receive에서.
    try:
        out["has_coupon"] = page.locator("strong.rvej6q8").count() > 0
    except Exception:
        pass

    return out


def write_to_sheet(results: list[dict], date_str: str) -> bool:
    """Step 1과 동일 시트의 "{M.DD}" 탭에 Hmall 10% 결과 추가 입력.
    기존 데이터 마지막 행에서 2행 띄운 후 헤더+상품 행들 입력.
    """
    if not GSPREAD_KEY or not GSPREAD_KEY.exists():
        print("[WARN] gspread 서비스 계정 키 없음 (gen-lang-*.json) — 시트 입력 skip")
        return False
    try:
        import gspread
    except ImportError:
        print("[WARN] gspread 미설치 (pip install gspread google-auth) — 시트 입력 skip")
        return False

    # 탭명 = "M.DD" (zero-padded DD): 2026-05-08 → "5.08"
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    tab_candidates = [f"{dt.month}.{dt.day:02d}", f"{dt.month}.{dt.day}"]

    try:
        gc = gspread.service_account(filename=str(GSPREAD_KEY))
        sh = gc.open_by_key(SHEET_KEY)
    except Exception as e:
        print(f"[WARN] 시트 연결 실패: {e}")
        return False

    ws = None
    for tab in tab_candidates:
        try:
            ws = sh.worksheet(tab)
            print(f"[INFO] 탭 '{tab}' 사용")
            break
        except Exception:
            continue
    if ws is None:
        # 없으면 첫 후보로 생성
        new_tab = tab_candidates[0]
        try:
            ws = sh.add_worksheet(title=new_tab, rows=200, cols=10, index=0)
            print(f"[INFO] 탭 '{new_tab}' 신규 생성")
        except Exception as e:
            print(f"[WARN] 탭 생성 실패: {e}")
            return False

    try:
        all_vals = ws.get_all_values()
    except Exception as e:
        print(f"[WARN] 탭 읽기 실패: {e}")
        return False

    # 기존 "현대Hmall 10% 적립 체크" 섹션이 있으면 그 행부터 다음 빈 행까지만 지움
    # — 다른 섹션 (rate-check 결과 등)은 안 건드림
    section_marker = "현대Hmall 10% 적립 체크"
    hmall_start_idx = None  # 0-based
    for i, row in enumerate(all_vals):
        if row and row[0].strip().startswith(section_marker):
            hmall_start_idx = i
            break
    if hmall_start_idx is not None:
        # 섹션 끝 = 다음 완전 빈 행 (모든 셀 빈 칸)
        hmall_end_idx = len(all_vals)
        for j in range(hmall_start_idx + 1, len(all_vals)):
            if not any(c.strip() for c in all_vals[j]):
                hmall_end_idx = j
                break
        clear_range = f"A{hmall_start_idx + 1}:Z{hmall_end_idx}"
        try:
            ws.batch_clear([clear_range])
            print(f"[INFO] 기존 Hmall 섹션 삭제: {clear_range}")
        except Exception as e:
            print(f"[WARN] 기존 섹션 삭제 실패: {e}")
        # 다시 읽기 — 삭제 후 마지막 데이터 행 재계산
        try:
            all_vals = ws.get_all_values()
        except Exception:
            pass

    # 마지막 데이터 행 찾기
    last_row = 0
    for i, row in enumerate(all_vals):
        if any(c.strip() for c in row):
            last_row = i + 1
    start_row = last_row + 3 if last_row > 0 else 1   # 2행 띄움 (rate-check 데이터와 시각적 분리)

    # 데이터 준비
    section_title = [f"현대Hmall 10% 적립 체크 ({tab_candidates[0]}) — {len(results)}개 상품"]
    headers = ["#", "제품명", "10%적립", "적립 문구", "최소구매", "적립률", "최대적립", "쿠폰", "URL"]
    rows = []
    for r in results:
        if r.get("error"):
            ten = "ERR"
            phrase = r.get("error", "")
            min_p = ""
            rate = ""
            max_r = ""
            coupon = ""
        else:
            ten = "✓" if r["ten_percent"] else "✗"
            phrase = r.get("phrase") or ""
            min_p = r.get("min_purchase") or ""
            rate = r.get("rate") or ""
            max_r = r.get("max_reward") or ""
            coupon = "🎟️ 보유" if r.get("has_coupon") else ""
        rows.append([str(r["id"]), r["name"], ten, phrase, min_p, rate, max_r, coupon, r.get("url", "")])

    # 한 번에 입력 (gspread batch update — Chrome UI 조작 X)
    payload = [section_title] + [headers] + rows
    end_row = start_row + len(payload) - 1
    range_str = f"A{start_row}:I{end_row}"
    try:
        ws.update(values=payload, range_name=range_str, value_input_option="USER_ENTERED")
        print(f"[OK] 시트 입력 완료: {ws.title}!{range_str} ({len(rows)}개 상품)")
        return True
    except Exception as e:
        print(f"[WARN] 시트 입력 실패: {e}")
        return False


def _short_min(v: str | None) -> str:
    """'30,000원 이상' → '30,000원↑', '1원 이상' → '1원↑'."""
    if not v:
        return "—"
    return v.replace("이상", "").strip() + "↑" if "이상" in v else v


def print_report(results: list[dict]) -> None:
    print("\n========= 10% 적립 체크 결과 =========")
    print(f"{'#':>3} | {'제품명':38s} | {'10%':4s} | {'최소구매':12s} | {'적립률':6s} | {'최대적립':10s} | 쿠폰")
    print("-" * 110)
    for r in results:
        if r.get("error"):
            mark_10 = "ERR"
            min_p = r["error"][:10]
            rate = "—"
            max_r = "—"
        else:
            mark_10 = "✓" if r["ten_percent"] else "✗"
            min_p = _short_min(r.get("min_purchase"))
            rate = r.get("rate") or "—"
            max_r = r.get("max_reward") or "—"
        coupon = "🎟️" if r.get("has_coupon") else "—"
        name = (r["name"][:36] + "…") if len(r["name"]) > 37 else r["name"]
        print(f"{r['id']:>3} | {name:38s} | {mark_10:4s} | {min_p:12s} | {rate:6s} | {max_r:10s} | {coupon}")


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
    date_str = datetime.now().strftime("%Y-%m-%d")
    payload = {
        "date": date_str,
        "account_used": acc["id"],
        "products": results,
        "ten_percent_ids": [r["id"] for r in results if r["ten_percent"] and not r.get("error")],
    }
    TODAY_OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[OK] 결과 저장: {TODAY_OUT}")

    # Google Sheets 입력 (Step 1과 동일 시트의 "{M.DD}" 탭에 추가)
    write_to_sheet(results, date_str)

    page.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
