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
        "tiers": [],            # [{"min_won": 50000, "reward_pt": 5000}, ...] 구간별 적립
        "max_reward": None,     # 마지막 구간 적립금 (예: "200,000P")
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

    # Step 3 (쿠폰 검출) — ten_percent 여부와 무관하게 항상 실행
    # "쿠폰 받기" 버튼 존재 여부 (buy/run.py의 click_coupon_receive와 동일 로직)
    try:
        out["has_coupon"] = page.locator("button").filter(has_text="쿠폰 받기").count() > 0
    except Exception:
        pass

    # Step 1: "10% 적립" 검출
    if "10% 적립" not in body_text:
        return out  # ten_percent=False (쿠폰 정보는 위에서 이미 채움)

    out["ten_percent"] = True

    # 적립 문구 추출 — "10% 적립" 앞 약 30자
    m = re.search(r"([^\n]{0,50}?)10%\s*적립", body_text)
    if m:
        out["phrase"] = (m.group(1) + "10% 적립").strip()

    # Step 2: H.Point 적립 상세 링크 click → 구간별 적립 표 추출
    detail_link = page.locator('a[href*="evntHPointDtl"]').first
    if detail_link.count() > 0:
        try:
            detail_link.click()
            page.wait_for_load_state("domcontentloaded", timeout=10000)
            page.wait_for_timeout(2000)
            extracted = page.evaluate("""
                () => {
                    const out = {tiers: []};
                    // 구간 적립 표: 행마다 [<N원/개 이상>, <N,NNNP>]
                    const tables = document.querySelectorAll('table');
                    for (const tbl of tables) {
                        const rows = tbl.querySelectorAll('tr');
                        const tierRows = [];
                        for (const r of rows) {
                            const cells = Array.from(r.querySelectorAll('td,th'))
                                              .map(c => c.innerText.trim());
                            if (cells.length < 2) continue;
                            if (/(원|개)\\s*이상/.test(cells[0]) && /\\dP/.test(cells[1].replace(/\\s/g,''))) {
                                tierRows.push(cells);
                            }
                        }
                        if (tierRows.length > 0) {
                            out.tiers = tierRows;
                            break;
                        }
                    }
                    const body = document.body ? document.body.innerText : '';
                    const m = body.match(/최대\\s*([\\d,]+)\\s*P/);
                    if (m) out.max_reward = m[1] + 'P';
                    return out;
                }
            """)
            if extracted:
                for row in extracted.get("tiers", []):
                    min_m = re.search(r"([\d,]+)\s*(원|개)", row[0])
                    rw_m = re.search(r"([\d,]+)\s*P", row[1])
                    if not (min_m and rw_m):
                        continue
                    out["tiers"].append({
                        "min_won": int(min_m.group(1).replace(",", "")),
                        "min_unit": min_m.group(2),
                        "reward_pt": int(rw_m.group(1).replace(",", "")),
                    })
                if out["tiers"]:
                    out["max_reward"] = f"{out['tiers'][-1]['reward_pt']:,}P"
                elif extracted.get("max_reward"):
                    out["max_reward"] = extracted["max_reward"]
            page.go_back(wait_until="domcontentloaded", timeout=10000)
            page.wait_for_timeout(1200)
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

    # 탭명 = "M.DD 식품" (식품 전용 탭, rate-check의 "M.DD" 와 분리)
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    tab_candidates = [f"{dt.month}.{dt.day:02d} 식품", f"{dt.month}.{dt.day} 식품"]

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

    # 전용 탭이므로 통째로 클리어 후 새로 쓰기
    try:
        ws.clear()
    except Exception as e:
        print(f"[WARN] 탭 클리어 실패: {e}")

    # 구간 수 최대값 → 컬럼 수 결정
    max_tiers = max((len(r.get("tiers") or []) for r in results), default=0)

    section_title = [f"현대Hmall 10% 적립 체크 ({tab_candidates[0]}) — {len(results)}개 상품"]
    headers = ["#", "제품명", "10%적립", "적립 문구", "최대적립", "쿠폰"]
    headers += [f"구간{i+1}" for i in range(max_tiers)]
    headers += ["URL"]

    rows = []
    for r in results:
        if r.get("error"):
            ten = "ERR"
            phrase = r.get("error", "")
            max_r = ""
            coupon = ""
            tier_cells = [""] * max_tiers
        else:
            ten = "✓" if r["ten_percent"] else "✗"
            phrase = r.get("phrase") or ""
            max_r = r.get("max_reward") or ""
            coupon = "🎟️ 보유" if r.get("has_coupon") else ""
            tiers = r.get("tiers") or []
            unit = tiers[0]["min_unit"] if tiers else "원"
            tier_cells = [f"{t['min_won']:,}{unit}/{t['reward_pt']:,}P" for t in tiers]
            tier_cells += [""] * (max_tiers - len(tier_cells))
        rows.append([str(r["id"]), r["name"], ten, phrase, max_r, coupon] + tier_cells + [r.get("url", "")])

    payload = [section_title] + [headers] + rows
    n_cols = len(headers)
    # 1-based col index → A1 column letter (AA, AB, ... 지원)
    def _col(n: int) -> str:
        s = ""
        while n > 0:
            n, r = divmod(n - 1, 26)
            s = chr(ord("A") + r) + s
        return s
    range_str = f"A1:{_col(n_cols)}{len(payload)}"
    try:
        ws.update(values=payload, range_name=range_str, value_input_option="USER_ENTERED")
        print(f"[OK] 시트 입력 완료: {ws.title}!{range_str} ({len(rows)}개 상품, 최대 {max_tiers}개 구간)")
        return True
    except Exception as e:
        print(f"[WARN] 시트 입력 실패: {e}")
        return False


def _tier_summary(tiers: list[dict]) -> str:
    """[{50000,5000},{100000,10000},...] → '50K/5K … 500K/50K (6단)'."""
    if not tiers:
        return "—"
    def k(n): return f"{n//1000}K" if n >= 1000 else str(n)
    first = f"{k(tiers[0]['min_won'])}/{k(tiers[0]['reward_pt'])}"
    last = f"{k(tiers[-1]['min_won'])}/{k(tiers[-1]['reward_pt'])}"
    if len(tiers) == 1:
        return f"{first} (1단)"
    return f"{first} … {last} ({len(tiers)}단)"


def print_report(results: list[dict]) -> None:
    print("\n========= 10% 적립 체크 결과 =========")
    print(f"{'#':>3} | {'제품명':38s} | {'10%':4s} | {'구간 적립 (min/reward)':28s} | {'최대적립':10s} | 쿠폰")
    print("-" * 120)
    for r in results:
        if r.get("error"):
            mark_10 = "ERR"
            tier_str = r["error"][:26]
            max_r = "—"
        else:
            mark_10 = "✓" if r["ten_percent"] else "✗"
            tier_str = _tier_summary(r.get("tiers") or [])
            max_r = r.get("max_reward") or "—"
        coupon = "🎟️" if r.get("has_coupon") else "—"
        name = (r["name"][:36] + "…") if len(r["name"]) > 37 else r["name"]
        print(f"{r['id']:>3} | {name:38s} | {mark_10:4s} | {tier_str:28s} | {max_r:10s} | {coupon}")


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
        n_tiers = len(result.get("tiers") or [])
        print(f"     → 10%={status}  구간={n_tiers}단  phrase={(result.get('phrase') or '')[:40]}")

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
