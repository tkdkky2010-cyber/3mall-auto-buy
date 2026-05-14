"""Hmall 우수스토어 10% 적립 상품 자동 필터 (Step 2).

29개 상품 페이지 순회 → "10% 적립" 검출 + 단순/구간별 분류 → cart/today.json.
가이드: cart/Hmall 10% Check Guide.md

사용법:
    pip install -r ../buy/requirements.txt
    bash launch-check10-chrome.sh                    # CDP 9223 Chrome 미리 띄우기 (idempotent)
    python3 cart/check10.py                          # 기본 (account #1)
    python3 cart/check10.py 3                        # 특정 계정 idx

CDP attach 모드 — Chrome은 launch-check10-chrome.sh 가 책임.
"""
from __future__ import annotations
import json
import math
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

CDP_PORT = os.environ.get("CDP_PORT", "9223")
CDP_ENDPOINT = f"http://127.0.0.1:{CDP_PORT}"
LOGIN_URL = "https://www.hmall.com/mo/cob/loginForm"
ITEM_URL_FMT = "https://www.hmall.com/md/pda/itemPtc?slitmCd={slitmCd}{extra}"

# Google Sheets — Step 1과 동일 시트의 "{M.DD}" 탭에 추가 입력
SHEET_KEY = "1fxB0UvLRy2iQfonCWn5U5mWnXbzSdn6l4e2XuQluhwo"
GSPREAD_KEY = next(iter(PROJECT_ROOT.glob("gen-lang-*.json")), None)

# 카드 페이백 — 설화수 가이드(rate-check/Sulwhasoo_Supply_Rate.md §6) 의 5개 카드.
# 즉시할인 슬라이드와 무관하게 적용되며, 결제 카드 자체 정책.
# 페이백계수 = 1 - 페이백%/100. 최종가 = (우수가 또는 즉시할인가) × 페이백계수 - 적립.
EVERYDAY_PAYBACK_CARDS = [
    ("롯데카드", 0.02),
    ("비씨카드", 0.015),
    ("삼성카드", 0.01),
    ("하나카드", 0.01),
    ("농협카드", 0.01),
]


def _compute_reward(price: int | None, tiers: list[dict], simple_ranges: list[dict] | None = None) -> int:
    """price 가 어느 구간에 도달하는지로 reward 결정.
    - tier-based: applicable tier 중 max reward
    - simple_range (예: 5만원 ~ 1천만원 10% 적립): min ≤ price ≤ max 면 price × pct 추가
    """
    if not price:
        return 0
    rw = 0
    if tiers:
        applicable = [t["reward_pt"] for t in tiers if price >= t["min_won"]]
        rw += max(applicable) if applicable else 0
    if simple_ranges:
        for sr in simple_ranges:
            if sr.get("min_won", 0) <= price <= sr.get("max_won", 0):
                rw += round(price * sr.get("pct", 0) / 100)
    return rw


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
    {"id": 20, "name": "바디랩 유기농 레몬즙 1박",              "slitmCd": "2244671296", "url_extra": "&sectId=3059445"},
    {"id": 22, "name": "올바른건강식품 와이 9박",                "slitmCd": "2244934734", "url_extra": "&sectId=3059445"},
    {"id": 23, "name": "셀게이트 컬리케일 6박",                 "slitmCd": "2244447010", "url_extra": "&sectId=3059445"},
    {"id": 25, "name": "유기농 석류젤리 9박(90개)",             "slitmCd": "2243971283", "url_extra": "&sectId=3059445"},
    {"id": 26, "name": "오라틱스 구강유산균 10박",              "slitmCd": "2244032427", "url_extra": "&sectId=3059445"},
    {"id": 28, "name": "스키니랩 시서스 다이어트 11박",          "slitmCd": "2202464603", "url_extra": "&sectId=3059445"},
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
        "phrase": None,         # 행사명들 / 슬래시 join (예: "올인데이 10% 적립 / 릴레이푸드 최대 10% 적립")
        "events": [],           # [{prmo, name, event_end, tiers}, ...] — 행사별 raw 데이터
        "tiers": [],            # 모든 행사 합산 — 같은 min_won 에서 reward 합산
        "max_reward": None,
        "event_end": None,      # 행사별 종료 시각 / 슬래시 join (예: "5.12 23:59 / 5.24 23:59")
        "has_coupon": False,
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

    # "구매 혜택" 아코디언 펼치기 + 그 panel scope 안에서만 적립/링크 검사
    # (본문 전체에서 검색하면 카드할인 영역의 'N% 적립'·'H.Point 적립' 등과 혼동됨)
    panel_data = page.evaluate("""() => {
        const btns = Array.from(document.querySelectorAll('button.accordion-trigger'));
        let panel = null;
        for (const b of btns) {
            if (!(b.textContent || '').includes('구매 혜택')) continue;
            if (b.getAttribute('aria-expanded') !== 'true') b.click();
            const ctrl = b.getAttribute('aria-controls');
            if (ctrl) { panel = document.getElementById(ctrl); }
            if (!panel) {
                const h3 = b.closest('h3');
                if (h3 && h3.nextElementSibling) panel = h3.nextElementSibling;
            }
            break;
        }
        if (!panel) return {has_reward: false, links: []};
        const text = panel.textContent || '';
        const has_reward = /적립/.test(text);
        const anchors = Array.from(panel.querySelectorAll('a[href*="evntHPointDtl"]'));
        const seen = new Set();
        const links = [];
        for (const a of anchors) {
            const m = a.href.match(/prmoNo=([^&]+)/);
            const prmo = m ? m[1] : a.href;
            if (seen.has(prmo)) continue;
            seen.add(prmo);
            links.push({prmo, href: a.href});
        }
        return {has_reward, links, panel_text: text.slice(0, 500)};
    }""") or {}
    page.wait_for_timeout(800)

    # Step 1: '구매 혜택' panel 안에 '적립' 키워드가 있어야 진입 (카드할인 영역과 혼동 방지)
    if not panel_data.get("has_reward"):
        return out  # 적립 행사 자체 없음
    out["ten_percent"] = True  # 임시, events 수집 후 재판정

    links = panel_data.get("links") or []

    # 각 행사 detail page 방문해서 events list 만들기
    for ld in links:
        try:
            page.goto(ld["href"], wait_until="domcontentloaded", timeout=10000)
            page.wait_for_timeout(1800)
            extracted = page.evaluate("""() => {
                const out = {tiers: [], event_period: null, event_name: null, simple_range: null};
                const tables = document.querySelectorAll('table');
                for (const tbl of tables) {
                    const rows = tbl.querySelectorAll('tr');
                    for (const r of rows) {
                        const cells = Array.from(r.querySelectorAll('td,th'))
                                          .map(c => c.innerText.trim());
                        if (cells.length < 2) continue;
                        if (!out.event_period && cells[0] === '행사기간') out.event_period = cells[1];
                        if (!out.event_name && cells[0] === '행사상품') {
                            const lines = cells[1].split('\\n').map(s => s.trim()).filter(Boolean);
                            out.event_name = lines[lines.length - 1] || null;
                        }
                        if (/(원|개)\\s*이상/.test(cells[0]) && /\\dP/.test(cells[1].replace(/\\s/g,''))) {
                            out.tiers.push(cells);
                        }
                    }
                    if (out.tiers.length > 0) break;
                }
                // 단순 N% 행사 추출 (tier 브래킷 없는 경우) — 본문 + 테이블 모두 스캔
                if (out.tiers.length === 0) {
                    const body = document.body.innerText || '';
                    // 패턴 1: "X원 이상 ~ Y원 이하 N% 적립" 또는 "X원 이상 Y원 이하 N%"
                    const p1 = body.match(/([\\d,]+)\\s*원\\s*이상[^A-Za-z]{0,40}?([\\d,]+)\\s*원\\s*이하[^A-Za-z]{0,40}?(\\d+)\\s*%/);
                    // 패턴 2: "X원 이상 N% 적립 (최대 Y원)" or "최대 Y원"
                    const p2 = body.match(/([\\d,]+)\\s*원\\s*이상[^A-Za-z]{0,40}?(\\d+)\\s*%[^A-Za-z]{0,40}?최대[^A-Za-z]{0,10}?([\\d,]+)\\s*원/);
                    if (p1) {
                        out.simple_range = {
                            min_won: parseInt(p1[1].replace(/,/g, '')),
                            max_won: parseInt(p1[2].replace(/,/g, '')),
                            pct: parseInt(p1[3])
                        };
                    } else if (p2) {
                        out.simple_range = {
                            min_won: parseInt(p2[1].replace(/,/g, '')),
                            max_won: parseInt(p2[3].replace(/,/g, '')),
                            pct: parseInt(p2[2])
                        };
                    }
                }
                return out;
            }""")
            event = {"prmo": ld["prmo"], "name": None, "event_end": None, "tiers": [], "simple_range": None}
            if extracted:
                event["name"] = extracted.get("event_name")
                for row in extracted.get("tiers", []):
                    min_m = re.search(r"([\d,]+)\s*(원|개)", row[0])
                    rw_m = re.search(r"([\d,]+)\s*P", row[1])
                    if not (min_m and rw_m):
                        continue
                    event["tiers"].append({
                        "min_won": int(min_m.group(1).replace(",", "")),
                        "min_unit": min_m.group(2),
                        "reward_pt": int(rw_m.group(1).replace(",", "")),
                    })
                # 단순 N% 범위 (tier 없는 경우 fallback)
                sr = extracted.get("simple_range")
                if sr and not event["tiers"]:
                    event["simple_range"] = sr
                ev = extracted.get("event_period")
                if ev:
                    end_part = ev.split("~")[-1].strip()
                    em = re.search(r"(\d{4})\.(\d{1,2})\.(\d{1,2})\([^)]+\)\s*(\d{1,2}:\d{2})", end_part)
                    if em:
                        event["event_end"] = f"{int(em.group(2))}.{int(em.group(3)):02d} {em.group(4)}"
            out["events"].append(event)
        except Exception:
            continue

    # events 를 합산 → tiers, event_end, phrase 채움
    combined: dict[int, int] = {}
    unit = "원"
    for e in out["events"]:
        for t in e["tiers"]:
            combined[t["min_won"]] = combined.get(t["min_won"], 0) + t["reward_pt"]
            unit = t["min_unit"]
    out["tiers"] = [{"min_won": k, "min_unit": unit, "reward_pt": v}
                    for k, v in sorted(combined.items())]
    # 단순 N% 범위 행사 — 별도 list 보관 (구간1에 별도 셀로 표기)
    out["simple_ranges"] = [e["simple_range"] for e in out["events"] if e.get("simple_range")]
    if out["tiers"]:
        out["max_reward"] = f"{out['tiers'][-1]['reward_pt']:,}P"
    elif out["simple_ranges"]:
        sr = out["simple_ranges"][0]
        out["max_reward"] = f"{round(sr['max_won'] * sr['pct'] / 100):,}P (단순 {sr['pct']}%)"
    out["event_end"] = " / ".join(e["event_end"] for e in out["events"] if e.get("event_end")) or None
    # phrase: 행사명 join (없으면 본문에서 첫 N% 적립 매칭 사용)
    names = [e["name"] for e in out["events"] if e.get("name")]
    if names:
        out["phrase"] = " / ".join(names)
    else:
        pm = re.search(r"([^\n]{0,50}?)\d+%\s*적립", body_text)
        if pm:
            out["phrase"] = pm.group(0).strip()

    # ten_percent 재판정: events 또는 phrase 에 '10%' 가 포함된 경우만 True
    has_10pct = (
        any(e.get("name") and re.search(r"10\s*%", e["name"]) for e in out["events"])
        or (out["phrase"] and "10%" in out["phrase"])
    )
    out["ten_percent"] = bool(has_10pct)

    # 상품 페이지로 복귀 (이후 흐름에서 page 사용 위해)
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=10000)
        page.wait_for_timeout(800)
    except Exception:
        pass

    return out


def _click_coupon(page: Page) -> None:
    """상품 페이지에서 쿠폰 받기 (buy/run.py 의 click_coupon_receive 축약)."""
    try:
        btn = page.locator("button").filter(has_text="쿠폰 받기").first
        if not (btn.count() > 0 and btn.is_visible()):
            return
        btn.click()
        page.wait_for_timeout(800)
        for b in page.locator("button").filter(has_text="다운").all():
            try:
                if b.is_visible() and "다운 완료" not in b.inner_text():
                    b.click()
                    page.wait_for_timeout(400)
                    for txt in ("확인", "예"):
                        ok = page.locator("button").filter(has_text=txt).first
                        if ok.count() > 0 and ok.is_visible():
                            ok.click()
                            page.wait_for_timeout(300)
                            break
            except Exception:
                pass
        page.evaluate("""
            () => {
                const btns = Array.from(document.querySelectorAll('button'));
                const t = btns.find(b => b.offsetParent !== null
                    && b.querySelector('span.hiding')?.textContent.trim() === '닫기');
                if (t) t.click();
            }
        """)
        page.wait_for_timeout(400)
    except Exception:
        pass


def _execute_buy_now(page: Page, qty: int) -> bool:
    """구매하기 → 옵션[선택 1] → qty + → 바로구매 → /order 페이지 도달 확인."""
    try:
        page.locator("button.btn-purchase").first.click()
        page.wait_for_timeout(1500)
    except Exception:
        return False
    try:
        opt = page.locator("span.choice-num.title").filter(has_text="[선택 1]").first
        if opt.count() > 0:
            opt.click()
            page.wait_for_timeout(700)
    except Exception:
        pass
    if qty > 1:
        try:
            plus = page.locator("button.btn-plus").first
            for _ in range(qty - 1):
                plus.click()
                page.wait_for_timeout(180)
        except Exception:
            pass
    clicked = page.evaluate("""
        () => {
            const buyNow = Array.from(document.querySelectorAll('button'))
                .find(b => b.offsetParent !== null && b.textContent.trim() === '바로구매');
            if (buyNow) { buyNow.click(); return true; }
            return false;
        }
    """)
    if not clicked:
        return False
    try:
        page.wait_for_load_state("domcontentloaded", timeout=20000)
    except Exception:
        pass
    page.wait_for_timeout(3500)
    return "/order" in (page.url or "")


def _extract_order_page(page: Page) -> dict:
    """결제 페이지에서 카드할인 슬라이드 + 정가/수량 추출."""
    # 카드할인 섹션이 React-render 지연되는 케이스 대응 (최대 5초 대기)
    try:
        page.wait_for_selector('h2:has-text("카드할인")', timeout=5000)
        page.wait_for_timeout(800)
    except Exception:
        pass
    return page.evaluate("""
        () => {
            const out = {slides: [], list_price: null, member_price: null, qty: null, list_total: null};
            const h2 = Array.from(document.querySelectorAll('h2'))
                .find(h => h.textContent.trim() === '카드할인');
            if (h2) {
                let section = h2.closest('div');
                for (let lvl = 0; lvl < 5 && section; lvl++) {
                    const slides = Array.from(section.querySelectorAll('.swiper-slide'))
                        .filter(s => s.offsetParent !== null);
                    if (slides.length > 0) {
                        out.slides = slides.map(s => {
                            const txt = s.innerText.replace(/\\s+/g, ' ').trim();
                            const alt = s.querySelector('img[alt]')?.alt || '';
                            const pct = (txt.match(/(\\d+)\\s*%\\s*즉시할인/) || [])[1];
                            const price = (txt.match(/([\\d,]{4,})\\s*원/) || [])[1];
                            return {
                                alt, text: txt.slice(0, 120),
                                percent: pct ? parseInt(pct) : null,
                                price: price ? parseInt(price.replace(/,/g, '')) : null,
                            };
                        });
                        break;
                    }
                    section = section.parentElement;
                }
            }
            const body = document.body ? document.body.innerText : '';
            // qty 만 추출 (정가합은 라벨로)
            const qm = body.match(/(\\d+)개\\s*\\n?\\s*([\\d,]+)\\s*원/);
            if (qm) {
                out.qty = parseInt(qm[1]);
                out.list_total = parseInt(qm[2].replace(/,/g, ''));
            }
            // 라벨 옆 값 추출 (텍스트 노드 + 형제 / 부모로 매칭)
            const grab = (label) => {
                const all = Array.from(document.querySelectorAll('*'));
                for (const el of all) {
                    if (el.children.length === 0 && el.textContent.trim() === label) {
                        const p = el.parentElement;
                        const txt = p ? p.innerText : '';
                        const m = txt.match(/([\\d,]{4,})\\s*원/);
                        if (m) return parseInt(m[1].replace(/,/g, ''));
                    }
                }
                return null;
            };
            out.list_price = grab('상품금액');    // 정가 (소비자가격)
            out.member_price = grab('총 결제금액');  // 우수고객 혜택가 (카드할인 임계 기준)
            // 결제수단 영역 본문 — 페이백 카드 노출 여부 판정용
            const ms = Array.from(document.querySelectorAll('*')).find(el =>
                el.children.length === 0 && el.textContent.trim() === '결제수단');
            if (ms) {
                let sec = ms.parentElement;
                for (let i = 0; i < 5 && sec; i++) {
                    if (sec.innerText && sec.innerText.length > 50) {
                        out.payment_methods_text = sec.innerText.slice(0, 2000);
                        break;
                    }
                    sec = sec.parentElement;
                }
            }
            return out;
        }
    """) or {}


def check_payment_flow(page: Page, prod: dict, tiers: list[dict], simple_ranges: list[dict] | None = None) -> dict:
    """상품 페이지 → 쿠폰받기 → 바로구매 → 결제 페이지에서 가격 정보 추출.
    우수가(member_price) < 5만원이면 카드할인 임계 미달 → qty 늘려 1회 재시도.
    적립 계산:
      - tiers (구간별, 예: 50K/5K, 100K/10K, ...)
      - simple_ranges (단순 N%, 예: 5만원 ~ 1천만원 10% 적립)
    """
    url = ITEM_URL_FMT.format(slitmCd=prod["slitmCd"], extra=prod.get("url_extra", ""))
    out = {
        "qty": 1,
        "list_price": None,        # 상품금액 라벨 (소비자가격, 정가)
        "member_price": None,      # 총 결제금액 라벨 (우수고객 혜택가)
        "card_slides": [],
        "kakao_price": None,       # 카카오페이 즉시할인가
        "kakao_reward_pt": 0,
        "kakao_final_cost": None,
        # 슬라이드에 등장한 페이백 5개 카드만 — 즉시할인 + 페이백 둘 다 받는 케이스
        "paybacks": {},            # {카드명: {immediate_price, payback_pct, after_payback, reward_pt, final_cost}}
        "error": None,
    }
    qty = 1
    kakao_slide = None
    for attempt in range(2):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=20000)
            page.wait_for_timeout(1500)
        except Exception as e:
            out["error"] = f"goto: {e}"
            return out

        # ★ 상품 상세페이지의 '혜택가' 추출 (= 진짜 우수가, 즉시할인 적용 전)
        # 사용자 정의: H열 우수가 = 혜택가 (1개 기준)
        try:
            benefit_unit = page.evaluate("""() => {
                const all = document.body.innerText || '';
                const m = all.match(/혜택가\\s*\\n?\\s*([\\d,]+)\\s*원/);
                return m ? parseInt(m[1].replace(/,/g, '')) : null;
            }""")
        except Exception:
            benefit_unit = None
        out["benefit_unit_price"] = benefit_unit  # 1개 기준 혜택가

        _click_coupon(page)
        if not _execute_buy_now(page, qty):
            out["error"] = "바로구매 실패"
            return out

        info = _extract_order_page(page)
        out["card_slides"] = info.get("slides") or []
        out["list_price"] = info.get("list_price") or info.get("list_total")
        # member_price를 혜택가 × qty 로 override (주문 페이지 '총 결제금액'은
        # 추가 할인이 적용된 값이라 H열 의미와 다름)
        actual_qty = info.get("qty") or qty
        if benefit_unit:
            out["member_price"] = benefit_unit * actual_qty
        else:
            out["member_price"] = info.get("member_price")
        out["qty"] = actual_qty

        kakao_slide = next((s for s in out["card_slides"] if "카카오" in (s.get("text") or "")), None)

        # retry 조건: 우수가 < 5만원 → qty 늘려 임계 돌파
        member = out["member_price"] or 0
        if attempt == 0 and 0 < member < 50000:
            unit_member = member / max(out["qty"], 1)
            qty = math.ceil(50000 / unit_member) if unit_member > 0 else qty + 1
            continue
        break

    # ── I열/J열 (즉시할인가 / 실비) — 수식 기반 ──
    # I = 우수가 × (1 - 금일 즉시할인%)
    # 즉시할인% 결정: card_slides 중 카카오페이 우선 → 없으면 max % 슬라이드
    # J = I × (1 - 카드페이백%) - 적립금(I 가격 기준)
    member = out.get("member_price") or 0
    pick_slide = kakao_slide
    if not pick_slide:
        with_pct = [s for s in out["card_slides"] if s.get("percent")]
        if with_pct:
            pick_slide = max(with_pct, key=lambda s: s.get("percent") or 0)
    if pick_slide and pick_slide.get("percent") and member > 0:
        imm_pct = pick_slide["percent"] / 100
        slide_text = pick_slide.get("text") or ""
        # 카드 페이백 매칭 (text에 페이백 대상 카드명 포함되면 해당 페이백 적용)
        pb_pct = 0
        for name, pct in EVERYDAY_PAYBACK_CARDS:
            short = name.replace("카드", "")
            if name in slide_text or short in slide_text:
                pb_pct = pct
                break
        # I = 우수가 × (1 - 즉시할인%)
        imm_price = round(member * (1 - imm_pct))
        # J = round(I × (1 - 페이백%)) - 적립(at J가 아닌 I 가격 기준 = 결제금액 기준)
        after_payback = round(imm_price * (1 - pb_pct))
        rw = _compute_reward(after_payback, tiers, simple_ranges)
        out["kakao_price"] = imm_price        # I열 — "즉시할인가" 라벨
        out["kakao_reward_pt"] = rw
        out["kakao_final_cost"] = after_payback - rw   # J열 — "실비" 라벨

    # 페이백 5개 카드 — 카드할인 슬라이드에 등장한 경우에만 적용
    # (예: 어느 날 슬라이드에 '롯데 7%즉시할인' 가 뜨면 → 즉시할인가 × 0.98 - 적립)
    # 오늘처럼 슬라이드가 카카오페이+KB국민/현대카드 뿐이면 페이백 매칭 없음.
    for slide in out["card_slides"]:
        text = slide.get("text") or ""
        imm = slide.get("price")
        if not imm:
            continue
        for name, pct in EVERYDAY_PAYBACK_CARDS:
            short = name.replace("카드", "")
            if name in text or short in text:
                after = round(imm * (1 - pct))
                rw = _compute_reward(after, tiers, simple_ranges)
                out["paybacks"][name] = {
                    "immediate_price": imm,
                    "payback_pct": pct * 100,
                    "after_payback": after,
                    "reward_pt": rw,
                    "final_cost": after - rw,
                }
                break
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

    # 구간 수 최대값 → 컬럼 수 결정 (tier + simple_range 합)
    max_tiers = max(((len(r.get("tiers") or []) + len(r.get("simple_ranges") or [])) for r in results), default=0)

    section_title = [f"현대Hmall 10% 적립 체크 ({tab_candidates[0]}) — {len(results)}개 상품"]
    headers = ["#", "제품명", "10%적립", "적립 문구", "쿠폰",
               "수량", "정가",
               "즉시할인가", "실비(= 즉시할인가 × 카드페이백계수 − 적립금)"]
    headers += ["행사종료"]
    headers += [f"구간{i+1}" for i in range(max_tiers)]
    headers += ["URL"]

    def _fmt(v):
        return f"{v:,}" if isinstance(v, (int, float)) and v else ""

    rows = []
    for r in results:
        if r.get("error"):
            ten = "ERR"
            phrase = r.get("error", "")
            coupon = ""
            qty_s = lp_s = imm_s = real_s = ""
            event_end_s = ""
            tier_cells = [""] * max_tiers
        else:
            ten = "✓" if r["ten_percent"] else "✗"
            phrase = r.get("phrase") or ""
            coupon = "🎟️ 보유" if r.get("has_coupon") else ""
            p = r.get("payment") or {}
            qty_s = str(p.get("qty")) if p.get("qty") else ""
            lp_s = _fmt(p.get("list_price"))
            imm_s = _fmt(p.get("kakao_price"))         # 즉시할인가
            real_s = _fmt(p.get("kakao_final_cost"))   # 실비
            event_end_s = r.get("event_end") or ""
            tiers_p = r.get("tiers") or []
            unit = tiers_p[0]["min_unit"] if tiers_p else "원"
            tier_cells = [f"{t['min_won']:,}{unit}/{t['reward_pt']:,}P" for t in tiers_p]
            # 단순 N% 범위 — 별도 셀로 prefix (구간1 자리)
            sr_list = r.get("simple_ranges") or []
            sr_cells = [f"{sr['min_won']:,}원 ~ {sr['max_won']:,}원 {sr['pct']}%적립" for sr in sr_list]
            tier_cells = sr_cells + tier_cells
            tier_cells += [""] * (max_tiers - len(tier_cells))
        rows.append([str(r["id"]), r["name"], ten, phrase, coupon,
                     qty_s, lp_s, imm_s, real_s, event_end_s] + tier_cells + [r.get("url", "")])

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
    print("\n========= 10% 적립 + 결제 흐름 결과 =========")
    hdr = (f"{'#':>3} | {'제품명':30s} | {'qty':>3} | {'정가':>9} | {'우수가':>9} | "
           f"{'즉시할인가':>10} | {'실비':>9} | 페이백카드")
    print(hdr)
    print("-" * 120)
    for r in results:
        name = (r["name"][:28] + "…") if len(r["name"]) > 29 else r["name"]
        if r.get("error"):
            print(f"{r['id']:>3} | {name:30s} | ERR {r['error'][:30]}")
            continue
        p = r.get("payment") or {}
        qty = str(p.get("qty") or "—")
        lp = f"{p['list_price']:,}" if p.get("list_price") else "—"
        mp = f"{p['member_price']:,}" if p.get("member_price") else "—"
        kk = f"{p['kakao_price']:,}" if p.get("kakao_price") else "—"
        kkf = f"{p['kakao_final_cost']:,}" if p.get("kakao_final_cost") is not None else "—"
        pb = p.get("paybacks") or {}
        pb_str = ", ".join(f"{k}={v['final_cost']:,}" for k, v in pb.items()) if pb else "—"
        print(f"{r['id']:>3} | {name:30s} | {qty:>3} | {lp:>9} | {mp:>9} | "
              f"{kk:>9} | {kkf:>9} | {pb_str}")


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
            print(f"[FATAL] CDP {CDP_PORT} 연결 실패: {e}")
            print(f"  먼저 'bash launch-check10-chrome.sh' 실행 필요")
            return 1
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        return _run(context, acc)


def _run(context, acc) -> int:
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
        status = "✓" if result["ten_percent"] else ("ERR" if result.get("error") else "✗")
        n_tiers = len(result.get("tiers") or [])
        print(f"     → 10%={status}  구간={n_tiers}단  phrase={(result.get('phrase') or '')[:40]}")
        # 10% 적립 상품만 결제 흐름 진행 (정가/카카오할인가/실비 추출)
        if result.get("ten_percent") and not result.get("error"):
            payment = check_payment_flow(page, prod, result.get("tiers") or [], result.get("simple_ranges") or [])
            result["payment"] = payment
            pb = payment.get("paybacks") or {}
            pb_str = " / ".join(f"{k}={v['final_cost']:,}" for k, v in pb.items()) if pb else "-"
            ppr = (f"qty={payment['qty']}  정가={payment.get('list_price') or '?'}  "
                   f"우수가={payment.get('member_price') or '?'}  "
                   f"즉시할인가={payment.get('kakao_price') or '?'}  "
                   f"실비={payment.get('kakao_final_cost') or '?'}  "
                   f"페이백[{pb_str}]")
            print(f"     → 결제: {ppr}{' [' + payment['error'] + ']' if payment.get('error') else ''}")
        results.append(result)

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
