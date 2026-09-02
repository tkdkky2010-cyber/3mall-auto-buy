"""Hmall 우수스토어 10% 적립 상품 자동 필터 (Step 2).

29개 상품 페이지 순회 → "10% 적립" 검출 + 단순/구간별 분류 → cart/today.json.
가이드: cart/Hmall 10% Check Guide.md

사용법:
    pip install -r ../buy/requirements.txt
    python3 cart/check10.py                          # 기본 (account #1)
    python3 cart/check10.py 3                        # 특정 계정 idx

CDP attach 모드 — 로그인된 CFT 9222 단일 사용 (launch = ~/bin/launch-hmall-chrome.sh, 자동).
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
sys.path.insert(0, str(PROJECT_ROOT))
from chrome_launcher import resolve_cdp_port, PORT_CHAIN  # noqa: E402
load_dotenv(PROJECT_ROOT / "buy" / ".env")

ACCOUNTS_FILE = Path(os.environ.get("HMALL_CONFIG_PATH") or (PROJECT_ROOT / "hmall_config.json"))
TODAY_OUT = ROOT / "today.json"

CDP_PORT = "9222"  # ★단일 CFT 원칙(2026-07-16): 로그인된 CFT 9222 만 사용. 9223 실제 Chrome 폐기 (창 강탈 원인).
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
    ("KB국민카드", 0.01),  # 6/2 추가 — 신규발급 카드 1% 페이백 (간편결제 래핑 시 'KB국민' stem 매칭)
]


def derive_alias_result(base: dict, prod: dict) -> dict:
    """alias 상품(같은 URL, 다른 옵션)을 base의 benefit_ratio 로 derive.

    핵심:
    1. base의 benefit_ratio = base.benefit_unit_price / base.unit_list_price (대표옵션 우수가/정가)
    2. alias.unit_list_price (옵션의 1개당 정가) × benefit_ratio = alias 1개당 혜택가
    3. 5만원 임계 미달이면 qty 자동 증가 (카드 즉시할인 5만원 이상에만 적용)
    4. H = 혜택가 × qty × (1 - 즉시할인%) — base 카드 슬라이드 재사용
    5. I = (H × 페이백계수) - 적립금(H 기준) — _compute_reward로 산출

    prod 에 unit_list_price 가 없으면 (=옵션가 미상) → 옛 동작(clone) 폴백 + 경고.
    """
    base_pay = base.get("payment") or {}
    unit_list = prod.get("unit_list_price")
    base_list_total = base_pay.get("list_price")
    base_qty = base_pay.get("qty") or 1
    base_benefit = base_pay.get("benefit_unit_price")

    # ★ unit_list_price 미지정이면 base.options 에서 option_keyword 로 자동 lookup
    if not unit_list and prod.get("option_keyword"):
        opts = base_pay.get("options") or []
        kw = prod["option_keyword"]
        matched = next((o for o in opts if kw in (o.get("name") or "")), None)
        if matched:
            unit_list = matched["list_price"]
            print(f"     ⓘ #{prod['id']} 옵션 자동매칭: '{matched['name'][:35]}' [선택 {matched.get('idx')}] = {unit_list:,}원")

    if not unit_list or not base_list_total or not base_benefit:
        # 폴백: 옛 동작 (clone) — 경고 출력
        print(f"     ⚠️ alias #{prod['id']} unit_list_price 못찾음 (옵션 키워드 '{prod.get('option_keyword')}' 매칭 실패) → clone 폴백")
        cloned = dict(base)
        cloned["id"] = prod["id"]
        cloned["name"] = prod["name"]
        cloned["alias_of"] = prod.get("alias_of")
        return cloned

    # benefit_ratio = 대표옵션 우수가 / 대표옵션 정가
    base_unit_list_price = base_list_total / base_qty
    benefit_ratio = base_benefit / base_unit_list_price
    alias_benefit_unit = round(unit_list * benefit_ratio)

    # 5만원 임계 자동 qty 증가
    qty = 1
    while alias_benefit_unit * qty < 50000:
        qty += 1
        if qty > 99:
            break
    member = alias_benefit_unit * qty

    # 카드 슬라이드는 base 결과 재사용 (같은 URL이라 같은 카드 즉시할인 적용).
    # ★2026-08-19: 종전엔 max(percent) 로 골라 **7% 청구할인을 즉시할인으로 오인**할 수 있었다.
    #   이제 _settle() 이 즉시할인 슬라이드만 고르고 청구할인은 제외한다.
    card_slides = base_pay.get("card_slides") or []

    payment = {
        "qty": qty,
        "list_price": int(unit_list * qty),
        "benefit_unit_price": alias_benefit_unit,
        "member_price": member,
        "card_slides": card_slides,
        "kakao_price": None,
        "kakao_reward_pt": 0,
        "kakao_final_cost": None,
        "paybacks": {},
        "error": None,
    }

    if member > 0:
        # trust_price=False — base 슬라이드의 절대금액은 alias 수량에 안 맞는다 (% 만 빌린다)
        payment["kakao_price"], payment["kakao_reward_pt"], payment["kakao_final_cost"] = _settle(
            card_slides, member, base.get("tiers") or [], base.get("simple_ranges") or [],
            trust_price=False)

    out = dict(base)
    out["id"] = prod["id"]
    out["name"] = prod["name"]
    out["alias_of"] = prod.get("alias_of")
    out["url"] = ITEM_URL_FMT.format(slitmCd=prod["slitmCd"], extra=prod.get("url_extra", ""))
    out["payment"] = payment
    return out


def _compute_reward(price: int | None, tiers: list[dict], simple_ranges: list[dict] | None = None) -> int:
    """price 가 어느 구간에 도달하는지로 reward 결정.
    - tier-based: applicable tier 중 max reward
    - simple_range (단순 N% 적립, min_won ≤ price ≤ max_won 범위): price × pct 추가
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


# alias 상품 메타 (Guide.md 표에 없는 정보 — 같은 URL 다른 옵션 처리용)
# - alias_of: base 상품 id (benefit_ratio 재사용)
# - option_keyword: base 페이지에서 클릭할 옵션 키워드 (실제 DOM 텍스트에 포함되어야 매칭)
# - unit_list_price: 옵션의 1개당 정가 (None 이면 base.options 에서 자동 lookup)
# ★ id 는 문자열이다 ('1-1' 같은 소수점 id 허용, 2026-08-05 사용자 지정 목록)
ALIAS_META: dict[str, dict] = {
    "36": {"option_index": 3},   # 센텔리안24 더 마데카 크림 50ml 20개 [선택3] (사용자 지정 2026-07-28)
    "37": {"option_index": 6},   # 센텔리안24 마데카크림 타임리버스 50ml 10개 [선택6]
    "38": {"option_index": 2},   # 센텔리안24 마데카크림 타이트 리프팅 50ml 20개 [선택2]
    "5":  {"alias_of": "4",  "option_keyword": "말차",   "unit_list_price": 99_900},  # codegen 5/15 확정 [선택 2]
    "13": {"alias_of": "12", "option_keyword": "벨리곰", "unit_list_price": 26900},
    "14": {"alias_of": "12", "option_keyword": "벨리곰", "unit_list_price": None},   # auto-lookup → 26,900
}

GUIDE_PATH = Path(__file__).parent / "Hmall 10% Check Guide.md"
# ★ id 는 '1' 뿐 아니라 '1-1' 형태도 받는다. \d+ 로 두면 '1-1' 행이 조용히 누락된다.
_GUIDE_ROW_RE = re.compile(
    r"^\|\s*(\d+(?:-\d+)?)\s*\|\s*([^|]+?)\s*\|\s*(https?://www\.hmall\.com/md/pda/itemPtc\?[^\s|]+)"
)


# ============================================================
# 주문서 정산 — H(즉시할인가) / I(실비)   ★2026-08-19 사용자 지시로 규칙 2건 정정
# ============================================================
def _settle(card_slides: list, member: int, tiers: list, simple_ranges: list,
            *, trust_price: bool = True):
    """주문서 슬라이드 → (H 즉시할인가, 적립, I 실비). 계산 불가면 (None, 0, None).

    trust_price=False 는 **alias 상품 전용** — alias 는 base 의 card_slides 를 재사용하는데
    slide['price'] 는 base 의 수량·금액 기준 절대값이라 alias 에 그대로 쓰면 틀린다.
    이때는 % 만 빌려 쓰고 금액은 alias 자신의 member 로 계산한다.

    ★① H = **주문서가 알려준 실제 결제금액(slide['price'])** 을 우선 쓴다.
       이 금액에는 **결제 단계에서만 뜨는 '결제금액할인'** 이 이미 반영돼 있다 — 장바구니
       단계에도 안 뜨고 결제창에서만 뜬다(사용자 2026-08-19). 코드는 이미 바로구매로
       주문서까지 들어가 이 값을 읽어오면서도 버리고 member×(1-pct) 로 재계산했다.
       실측(2026-08-19): 대상 6건 전부 우수가 대비 **정확히 -10,000원** 차이.
       ⚠️ price 는 **즉시할인 슬라이드만** 신뢰한다 — 토스페이/네이버페이 슬라이드의 숫자는
          주문총액이 아니라 '최대 N원 혜택' 상한액이다(그대로 쓰면 13,000원짜리 주문이 된다).
       price 가 없을 때만 member×(1-pct) 로 추정한다.

    ★② **청구할인은 무조건 제외한다**(사용자 지시 2026-08-19).
       '제휴카드 현대홈쇼핑 현대카드 Ed2 7% 청구할인' 같은 구매혜택 배너는 안내일 뿐
       실제로 못 받는다. 종전엔 즉시할인과 청구할인을 겹쳐 적용해 실비가 과소계상됐다.
       (별건: 같은 이름의 '7% 적립' 은 인정 대상이나 **현재 수집되지 않는다** — 미해결.)
    """
    imm_candidates = [s for s in card_slides
                      if s.get("percent") and (s.get("discount_type") == "즉시할인"
                                               or "카카오" in (s.get("text") or ""))]
    imm_slide = max(imm_candidates, key=lambda s: s.get("percent") or 0) if imm_candidates else None

    imm_price = None
    if imm_slide:
        price = imm_slide.get("price") if trust_price else None
        if price and price > 0:
            imm_price = int(price)                 # 주문서 실측값 (결제금액할인 포함)
        elif member > 0:
            imm_price = round(member * (1 - (imm_slide.get("percent") or 0) / 100))
    if imm_price is None:
        imm_price = member or None
    if not imm_price:
        return None, 0, None

    # 일상 페이백 — 실제로 결제하는 카드(즉시할인 슬라이드) 기준
    pb_pct = 0.0
    if imm_slide:
        text = (imm_slide.get("text") or "") + " " + (imm_slide.get("alt") or "")
        for name, pct in EVERYDAY_PAYBACK_CARDS:
            if name in text or name.replace("카드", "") in text:
                pb_pct = max(pb_pct, pct)

    rw = _compute_reward(imm_price, tiers, simple_ranges)
    return imm_price, rw, round(imm_price * (1 - pb_pct)) - rw


def _load_products_from_guide() -> list[dict]:
    """Guide.md (Phase 2 상품 표) 를 single source of truth 로 파싱.

    | # | 제품명 | URL | 행을 모두 추출해서 PRODUCTS 리스트 생성.
    URL 에서 slitmCd / 나머지 query 분리 → url_extra 필드 채움.
    alias 상품은 ALIAS_META 의 메타데이터 자동 병합.
    ★ 순서 = 표에 적힌 행 순서 그대로 (id 숫자순 재정렬 금지, 2026-08-05 사용자 지정).
    """
    text = GUIDE_PATH.read_text(encoding="utf-8")
    by_id: dict[str, dict] = {}
    for line in text.splitlines():
        m = _GUIDE_ROW_RE.match(line)
        if not m:
            continue
        pid = m.group(1)
        name = m.group(2).strip()
        # ★'[중단 YYYY-MM-DD]' 표식이 붙은 행은 step2 대상에서 뺀다 (사용자 지시로 구매를 접은 상품).
        #   행 자체를 지우지 않는 이유 = URL·상품번호를 잃으면 되살릴 때 다시 찾아야 한다.
        #   (2026-09-01 사용자 지시: "36 37 38 동국제약, 센텔리안 제품은 이제 구매안하기로 했으니
        #    그건 스텝2 할때 안알아봐도돼")
        if name.startswith("[중단"):
            continue
        url = m.group(3).strip()
        qs = url.split("?", 1)[1]
        params = [p.split("=", 1) for p in qs.split("&") if "=" in p]
        slitm = next((v for k, v in params if k == "slitmCd"), None)
        if not slitm:
            continue
        url_extra = "".join(f"&{k}={v}" for k, v in params if k != "slitmCd")
        prod = {"id": pid, "name": name, "slitmCd": slitm, "url_extra": url_extra}
        meta = ALIAS_META.get(pid)
        if meta:
            prod.update(meta)
        by_id[pid] = prod  # 같은 id 가 두 표에 있어도 마지막 row 가 승
    return list(by_id.values())  # 표 등장 순서 유지


PRODUCTS = _load_products_from_guide()


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
        # ★위 1500ms 뒤에도 헤더 네비가 안 그려질 수 있다 → '로그아웃' 이 뜰 때까지만 더 기다린다.
        #   고정 대기 후 1회 읽기는 로그인 성공을 실패로 오판한다(buy/run.py poll_until 주석 참조).
        _w = 0
        while _w < 8000:
            try:
                if "로그아웃" in page.inner_text("body"):
                    return True
            except Exception:
                pass
            page.wait_for_timeout(200)
            _w += 200
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
    # 의미: 이 상품 페이지에 쿠폰 행사가 있는가 (미수령/이미 받음 둘 다 True)
    # - "쿠폰 받기" = 아직 안 받은 상태
    # - "받은 쿠폰" = 이미 받음 (계정이 다 받으면 라벨이 이렇게 바뀜)
    try:
        body_text = page.evaluate("document.body.innerText")
        out["has_coupon"] = ("쿠폰 받기" in body_text) or ("받은 쿠폰" in body_text)
        out["coupon_claimed"] = "받은 쿠폰" in body_text  # 이미 받았는지 표시
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
                    // 패턴 A (가이드 명시): table cell — cells[0]='X원 이상', cells[1] 안에 N% 또는 Y원 이하
                    for (const tbl of tables) {
                        const rows = tbl.querySelectorAll('tr');
                        for (const r of rows) {
                            const cells = Array.from(r.querySelectorAll('td,th')).map(c => c.innerText.trim());
                            if (cells.length < 2) continue;
                            const minM = cells[0].match(/([\\d,]+)\\s*원\\s*이상/);
                            if (!minM) continue;
                            const c1 = cells[1];
                            const pctM = c1.match(/(\\d+)\\s*%/);
                            const maxInline = c1.match(/([\\d,]+)\\s*원\\s*이하/);
                            if (!pctM && !maxInline) continue;
                            // max_won: cells[1] 있으면 그거, 없으면 본문 "X원 초과" 또는 default 1천만원
                            let maxWon = maxInline ? parseInt(maxInline[1].replace(/,/g, '')) : null;
                            if (!maxWon) {
                                const overM = body.match(/([\\d,]{4,})\\s*원\\s*초과/);
                                if (overM) maxWon = parseInt(overM[1].replace(/,/g, ''));
                            }
                            if (!maxWon) maxWon = 10000000;
                            out.simple_range = {
                                min_won: parseInt(minM[1].replace(/,/g, '')),
                                max_won: maxWon,
                                pct: pctM ? parseInt(pctM[1]) : 10,
                            };
                            break;
                        }
                        if (out.simple_range) break;
                    }
                    // 패턴 B (본문 inline): "X원 이상 ~ Y원 이하 N%"
                    if (!out.simple_range) {
                        const p1 = body.match(/([\\d,]+)\\s*원\\s*이상[^A-Za-z]{0,40}?([\\d,]+)\\s*원\\s*이하[^A-Za-z]{0,40}?(\\d+)\\s*%/);
                        if (p1) {
                            out.simple_range = {
                                min_won: parseInt(p1[1].replace(/,/g, '')),
                                max_won: parseInt(p1[2].replace(/,/g, '')),
                                pct: parseInt(p1[3]),
                            };
                        }
                    }
                    // 패턴 C: "X원 이상 N% 적립 (최대 Y원)"
                    if (!out.simple_range) {
                        const p2 = body.match(/([\\d,]+)\\s*원\\s*이상[^A-Za-z]{0,40}?(\\d+)\\s*%[^A-Za-z]{0,40}?최대[^A-Za-z]{0,10}?([\\d,]+)\\s*원/);
                        if (p2) {
                            out.simple_range = {
                                min_won: parseInt(p2[1].replace(/,/g, '')),
                                max_won: parseInt(p2[3].replace(/,/g, '')),
                                pct: parseInt(p2[2]),
                            };
                        }
                    }
                }
                return out;
            }""")
            if os.environ.get("DEBUG_ORDER"):
                # detail 페이지 본문에서 '원 이상' 주변 200자 + table cell 미리보기
                debug_info = page.evaluate("""() => {
                    const body = document.body.innerText || '';
                    const idx = body.search(/([\\d,]+)\\s*원\\s*이상/);
                    const around = idx >= 0 ? body.slice(Math.max(0, idx-100), idx+200) : '(없음)';
                    const tableCells = [];
                    document.querySelectorAll('table tr').forEach(r => {
                        const cs = Array.from(r.querySelectorAll('td,th')).map(c => c.innerText.trim()).filter(Boolean);
                        if (cs.length >= 2) tableCells.push(cs);
                    });
                    return {around, tableCells: tableCells.slice(0, 8), url: location.href};
                }""")
                print(f"[DEBUG_DETAIL] prmo={ld['prmo']}")
                print(f"[DEBUG_DETAIL]   url: {debug_info.get('url', '')[:100]}")
                print(f"[DEBUG_DETAIL]   '원 이상' 주변: {repr(debug_info.get('around', '')[:300])}")
                print(f"[DEBUG_DETAIL]   table cells (up to 8 rows):")
                for cs in debug_info.get("tableCells", []):
                    print(f"[DEBUG_DETAIL]     {cs}")
                print(f"[DEBUG_DETAIL]   extracted.simple_range: {extracted.get('simple_range')}")
                print(f"[DEBUG_DETAIL]   extracted.tiers_count: {len(extracted.get('tiers') or [])}")
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


def _scrape_options_only(page: Page) -> list[dict]:
    """구매하기 클릭 → 옵션 리스트만 스크랩 [{idx, name, list_price}] (구매 진행 안 함).
    다옵션 상품의 지정옵션 정가 확보용 (2026-07-28)."""
    try:
        page.locator("button.btn-purchase").first.click()
        page.wait_for_timeout(1500)
    except Exception:
        return []
    try:
        return page.evaluate("""() => {
            const out = [];
            const spans = Array.from(document.querySelectorAll('span.choice-num.title'))
                .filter(s => s.offsetParent !== null);
            for (const s of spans) {
                const text = (s.textContent || '').trim().replace(/\\s+/g, ' ');
                const m = text.match(/\\[선택\\s*(\\d+)\\]/);
                if (!m) continue;
                const idx = parseInt(m[1]);
                const name = text.replace(/\\[선택\\s*\\d+\\]/, '').replace(/^\\[우수고객전용\\]/, '').trim();
                let price = null, cur = s.parentElement;
                for (let i = 0; i < 5 && cur; i++) {
                    const pm = (cur.innerText || '').match(/([\\d,]{4,})\\s*원/);
                    if (pm) { price = parseInt(pm[1].replace(/,/g, '')); break; }
                    cur = cur.parentElement;
                }
                out.push({ idx, name, list_price: price });
            }
            const seen = new Set();
            return out.filter(o => { if (seen.has(o.idx)) return false; seen.add(o.idx); return true; });
        }""") or []
    except Exception:
        return []


def _execute_buy_now(page: Page, qty: int, option_keyword: str | None = None,
                     option_index: int | None = None) -> tuple[bool, list[dict]]:
    """구매하기 → (옵션 모두 스크랩) → 옵션 선택 → qty + → 바로구매 → /order 페이지 도달 확인.

    Returns (success, options).
    options: [{idx, name, list_price}] — 옵션 패널의 모든 선택지 (alias unit_list_price 자동 보충용)
    option_keyword: 지정 시 해당 키워드 포함 옵션 클릭. None이면 [선택 1] (대표).
    """
    try:
        page.locator("button.btn-purchase").first.click()
        page.wait_for_timeout(1500)
    except Exception:
        return False, []

    # ★ 옵션 패널 등장 → span.choice-num.title 기반 스크랩 (가이드 line 422)
    # 가격은 같은 span 에 없을 수 있어 조상 4단계까지 올라가며 'N원' 패턴 검색
    try:
        options = page.evaluate("""() => {
            const out = [];
            const spans = Array.from(document.querySelectorAll('span.choice-num.title'))
                .filter(s => s.offsetParent !== null);
            for (const s of spans) {
                const text = (s.textContent || '').trim().replace(/\\s+/g, ' ');
                const m = text.match(/\\[선택\\s*(\\d+)\\]/);
                if (!m) continue;
                const idx = parseInt(m[1]);
                const name = text.replace(/\\[선택\\s*\\d+\\]/, '').replace(/^\\[우수고객전용\\]/, '').trim();
                let price = null;
                let cur = s.parentElement;
                for (let i = 0; i < 5 && cur; i++) {
                    const t = cur.innerText || '';
                    const pm = t.match(/([\\d,]{4,})\\s*원/);
                    if (pm) { price = parseInt(pm[1].replace(/,/g, '')); break; }
                    cur = cur.parentElement;
                }
                out.push({ idx, name, list_price: price });
            }
            // 폴백: 옛 <a> 패턴 (DOM 롤백 대비)
            if (out.length === 0) {
                for (const a of document.querySelectorAll('a')) {
                    const text = (a.textContent || '').trim().replace(/\\s+/g, ' ');
                    const m = text.match(/\\[선택\\s*(\\d+)\\][^\\d]*?([\\d,]+)\\s*원/);
                    if (m) {
                        const name = text.replace(/\\[선택\\s*\\d+\\]/, '').replace(/[\\d,]+\\s*원.*$/, '').trim();
                        out.push({ idx: parseInt(m[1]), name, list_price: parseInt(m[2].replace(/,/g, '')) });
                    }
                }
            }
            const seen = new Set();
            return out.filter(o => { if (seen.has(o.idx)) return false; seen.add(o.idx); return true; });
        }""") or []
    except Exception:
        options = []

    # 옵션 클릭 — span.choice-num.title 우선 + 폴백 <a>. invisible 매칭 방지 위해 timeout 5초.
    try:
        # 선택 우선순위: option_keyword > option_index(N번) > 기본 [선택1]
        sel_pat = re.compile(rf"\[선택\s*{option_index}\]") if option_index else re.compile(r"\[선택\s*1\]")
        if option_keyword:
            opt = page.locator("span.choice-num.title").filter(has_text=option_keyword).first
            if opt.count() == 0:
                opt = page.locator("a").filter(has_text=option_keyword).first
        else:
            opt = page.locator("span.choice-num.title").filter(has_text=sel_pat).first
            if opt.count() == 0:
                opt = page.locator("a").filter(has_text=sel_pat).first
        if opt.count() > 0:
            opt.click(timeout=5000)
            page.wait_for_timeout(1500)
    except Exception:
        pass

    if qty > 1:
        try:
            # codegen 확인: 팝업 + 버튼은 텍스트 "증가"
            plus = page.get_by_role("button", name="증가").first
            for _ in range(qty - 1):
                plus.click()
                page.wait_for_timeout(180)
        except Exception:
            # fallback: 옛 클래스 button.btn-plus
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
        return False, options
    try:
        page.wait_for_load_state("domcontentloaded", timeout=20000)
    except Exception:
        pass
    page.wait_for_timeout(3500)
    return ("/order" in (page.url or "")), options


def _extract_order_page(page: Page) -> dict:
    """결제 페이지에서 카드할인 슬라이드 + 정가/수량 추출."""
    # 카드할인 섹션이 React-render 지연되는 케이스 대응 (최대 5초 대기)
    h2_found = False
    try:
        page.wait_for_selector('h2:has-text("카드할인")', timeout=5000)
        page.wait_for_timeout(800)
        h2_found = True
    except Exception:
        pass
    if os.environ.get("DEBUG_ORDER"):
        diag = page.evaluate("""() => {
            const out = {};
            const h2 = Array.from(document.querySelectorAll('h2')).find(h => h.textContent.trim() === '카드할인');
            if (h2) {
                // h2.parentElement 부터 5단계 위로 올라가며 innerText 길이 가장 긴 것 = 카드 정보 컨테이너
                let best = null, bestLen = 0;
                let cur = h2.parentElement;
                for (let i = 0; i < 6 && cur; i++) {
                    const len = (cur.innerText || '').length;
                    if (len > bestLen && len > 30) { best = cur; bestLen = len; }
                    cur = cur.parentElement;
                }
                if (best) {
                    out.containerTag = best.tagName + '.' + (best.className || '').toString().slice(0, 80);
                    out.containerText = (best.innerText || '').slice(0, 1500).replace(/\\n/g, ' | ');
                    // 자식 요소들 큰 그림
                    out.directChildren = Array.from(best.children).slice(0, 10).map(c => ({
                        tag: c.tagName, cls: (c.className || '').toString().slice(0, 80),
                        text: (c.innerText || '').slice(0, 100).replace(/\\n/g, ' | ')
                    }));
                }
                // h2의 next sibling chain
                let sib = h2.nextElementSibling;
                out.h2_next_sibling = sib ? {
                    tag: sib.tagName, cls: (sib.className || '').toString().slice(0, 80),
                    text: (sib.innerText || '').slice(0, 800).replace(/\\n/g, ' | ')
                } : null;
            }
            // 페이지에서 '즉시할인' 텍스트 둘러싼 element 5개
            const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
            const immBlocks = [];
            let n;
            while ((n = walker.nextNode()) && immBlocks.length < 5) {
                if (n.nodeValue.includes('즉시할인')) {
                    const p = n.parentElement;
                    immBlocks.push({
                        tag: p.tagName, cls: (p.className || '').toString().slice(0, 100),
                        text: p.innerText.slice(0, 200).replace(/\\n/g, ' | ')
                    });
                }
            }
            out.imm_blocks = immBlocks;
            return out;
        }""")
        print(f"[DEBUG] h2_wait_found={h2_found}")
        if diag.get("containerTag"):
            print(f"[DEBUG] containerTag: {diag['containerTag']}")
            print(f"[DEBUG] containerText: {diag['containerText'][:500]}")
            print(f"[DEBUG] directChildren:")
            for c in diag.get("directChildren", []):
                print(f"[DEBUG]   - <{c['tag']} class='{c['cls']}'> {c['text'][:120]}")
        if diag.get("h2_next_sibling"):
            sib = diag["h2_next_sibling"]
            print(f"[DEBUG] h2_next_sibling: <{sib['tag']} cls='{sib['cls']}'> {sib['text'][:300]}")
        print(f"[DEBUG] imm_blocks ({len(diag.get('imm_blocks', []))}):")
        for b in diag.get("imm_blocks", []):
            print(f"[DEBUG]   <{b['tag']} cls='{b['cls']}'> {b['text']}")
    return page.evaluate("""
        () => {
            const out = {slides: [], list_price: null, member_price: null, qty: null, list_total: null};
            const h2 = Array.from(document.querySelectorAll('h2'))
                .find(h => h.textContent.trim() === '카드할인');
            if (h2) {
                // ── 1차: 옛 swiper-slide 구조 시도 (DOM 롤백 대비) ──
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
                // ── 2차: 새 orderSection.payment 텍스트 라인 파싱 (현재 Hmall DOM) ──
                if (out.slides.length === 0) {
                    // h2의 부모 chain 중 가장 큰 innerText 노드 = 카드 정보 컨테이너
                    let best = null, bestLen = 0;
                    let cur = h2.parentElement;
                    for (let i = 0; i < 6 && cur; i++) {
                        const len = (cur.innerText || '').length;
                        if (len > bestLen && len > 30) { best = cur; bestLen = len; }
                        cur = cur.parentElement;
                    }
                    // 카드할인 sub-section만 좁히기 (h2 직속 wrapper)
                    let cardSec = h2.parentElement;
                    while (cardSec && cardSec.parentElement) {
                        const txt = cardSec.innerText || '';
                        // 카드할인 + 즉시할인 같이 들어있고 너무 커지지 않은 단계
                        if (txt.includes('즉시할인') && txt.length < 800) break;
                        cardSec = cardSec.parentElement;
                        if (!cardSec || (cardSec.innerText || '').length > 1500) break;
                    }
                    const target = (cardSec && (cardSec.innerText || '').includes('즉시할인')) ? cardSec : best;
                    if (target) {
                        // 빈 라인 보존 (필터링 X) — discount_type 이 별도 라인에 오는 케이스 처리
                        const lines = target.innerText.split(/\\n/).map(l => l.trim());
                        for (let i = 0; i < lines.length; i++) {
                            // 패턴 A: 한 라인에 "N% (즉시할인|청구할인)" 다 들어있는 경우
                            let m = lines[i].match(/(\\d+)\\s*%\\s*(즉시할인|청구할인)/);
                            let pct = null, dtype = null;
                            if (m) {
                                pct = parseInt(m[1]); dtype = m[2];
                            } else {
                                // 패턴 B: 라인이 "N%" 만, discount_type 은 다음 3라인 안
                                const mp = lines[i].match(/^\\s*(\\d+)\\s*%\\s*$/);
                                if (mp) {
                                    pct = parseInt(mp[1]);
                                    for (let j = i+1; j < Math.min(i+4, lines.length); j++) {
                                        const md = lines[j].match(/(즉시할인|청구할인)/);
                                        if (md) { dtype = md[1]; break; }
                                    }
                                }
                            }
                            if (pct === null || !dtype) continue;
                            // 카드명 = 이전 4라인 중 '카드' 포함 + '카드할인' 헤더 제외
                            let card = '';
                            for (let j = i-1; j >= Math.max(0, i-4); j--) {
                                const prev = lines[j].trim();
                                if (!prev || prev === '카드할인') continue;
                                if (prev.includes('카드') && prev.length < 50) {
                                    card = prev; break;
                                }
                            }
                            // 가격 = 다음 5라인 안에서 첫 "X원"
                            let price = null;
                            for (let j = i+1; j < Math.min(i+6, lines.length); j++) {
                                const pm = lines[j].match(/([\\d,]{4,})\\s*원/);
                                if (pm) { price = parseInt(pm[1].replace(/,/g, '')); break; }
                            }
                            out.slides.push({
                                alt: card,
                                text: (card + ' ' + pct + '% ' + dtype).trim().slice(0, 120),
                                percent: pct,
                                price: price,
                                discount_type: dtype,
                            });
                        }
                    }
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


def _find_optimal_qty(unit_price: int | None, tiers: list[dict], cap_won: int = 500_000, min_pct: float = 10.0,
                      simple_ranges: list[dict] | None = None) -> dict | None:
    """≥min_pct nominal ratio tier 중 best 선택.

    nominal_pct = tier.reward_pt / tier.min_won × 100.
    candidates 중 (highest nominal_pct, tiebreak smallest qty) 선정.
    actual_total (= qty × unit_price) > cap_won 인 tier 는 제외.

    simple_ranges(단순 N% 적립 — tiers 가 비는 상품)도 pseudo-tier(min_won 임계에서 pct%)로 편입해
    동일 로직으로 qty 최적화 → 주문서(바로구매)까지 진행 가능.

    Returns dict {qty, tier_min, tier_reward, nominal_pct, actual_total, actual_reward, effective_pct}
    또는 None (조건 만족 tier 없음).
    """
    pool = list(tiers or [])
    for sr in (simple_ranges or []):
        mw, pct = sr.get("min_won"), sr.get("pct")
        if mw and pct:
            pool.append({"min_won": mw, "reward_pt": int(round(mw * pct / 100)), "min_unit": "원"})
    if not pool or not unit_price or unit_price <= 0:
        return None
    candidates = []
    for tier in pool:
        tier_min = tier.get("min_won")
        reward = tier.get("reward_pt")
        if not tier_min or not reward:
            continue
        nominal_pct = (reward / tier_min) * 100
        if nominal_pct < min_pct:
            continue
        qty = math.ceil(tier_min / unit_price)
        actual_total = qty * unit_price
        if actual_total > cap_won:
            continue
        # 실제 hit 되는 가장 높은 tier = actual_reward
        actual_reward = max(
            (t["reward_pt"] for t in pool if t.get("min_won") and t["min_won"] <= actual_total),
            default=reward,
        )
        effective_pct = (actual_reward / actual_total) * 100
        candidates.append({
            "qty": qty,
            "tier_min": tier_min,
            "tier_reward": reward,
            "nominal_pct": nominal_pct,
            "actual_total": actual_total,
            "actual_reward": actual_reward,
            "effective_pct": effective_pct,
        })
    if not candidates:
        return None
    # best nominal_pct, tiebreak smallest qty
    return max(candidates, key=lambda c: (c["nominal_pct"], -c["qty"]))


def check_payment_flow(page: Page, prod: dict, tiers: list[dict], simple_ranges: list[dict] | None = None) -> dict:
    """상품 페이지 → 혜택가 추출 → optimal qty 결정 → 바로구매 → 결제 페이지에서 카드할인 추출.

    qty 결정 정책 (5/18~):
      - tiers 중 nominal ratio ≥ 10% AND actual_total ≤ 500K 인 best tier 선택
      - 만족 tier 없으면 effective_ten_percent=False, 바로구매 skip

    적립 계산:
      - tiers (구간별, min_won 임계마다 reward_pt 부여 — 적용 가능한 tier 중 max)
      - simple_ranges (단순 N% 적립, min_won ≤ price ≤ max_won 범위)

    카드할인 (5/18~ DOM 신구조):
      - 즉시할인 → H 감소 (member × (1 - imm_pct))
      - 청구할인 → 결제 후 카드사 환급 (페이백 취급, I 에서 차감)
    """
    url = ITEM_URL_FMT.format(slitmCd=prod["slitmCd"], extra=prod.get("url_extra", ""))
    out = {
        "qty": 1,
        "list_price": None,        # 상품금액 라벨 (소비자가격, 정가)
        "member_price": None,      # 총 결제금액 라벨 (우수고객 혜택가)
        "card_slides": [],
        "kakao_price": None,       # H — 즉시할인 적용된 결제 시점 금액
        "kakao_reward_pt": 0,
        "kakao_final_cost": None,  # I — 청구할인/페이백/적립 다 적용한 실비
        "paybacks": {},
        "optimal_tier": None,      # _find_optimal_qty 결과 (None = ≥10% 불가)
        "effective_ten_percent": None,
        "error": None,
    }

    # 1) PDP 진입 + 혜택가 추출
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=20000)
        page.wait_for_timeout(1500)
    except Exception as e:
        out["error"] = f"goto: {e}"
        return out

    try:
        benefit_unit = page.evaluate("""() => {
            const all = document.body.innerText || '';
            const m = all.match(/혜택가\\s*\\n?\\s*([\\d,]+)\\s*원/);
            return m ? parseInt(m[1].replace(/,/g, '')) : null;
        }""")
    except Exception:
        benefit_unit = None
    out["benefit_unit_price"] = benefit_unit

    # 1-b) 다옵션 상품(option_index 지정): PDP '혜택가'는 기본옵션(옵션1) 값이라 지정옵션과 다름.
    #   React 드롭다운이라 옵션 클릭 자동선택이 불가(2026-07-28 실측) → 옵션 리스트에서 지정옵션 정가를
    #   스크랩해 benefit_unit = 정가 × 0.9(고정 10% 우수가)로 교체. 카드%는 옵션 무관 동일(이후 기본옵션
    #   바로구매에서 얻는 card_slides 그대로). 사용자 지시: 고정 할인율이라 옵션정가 −10% −카드% = 결제액.
    oi = prod.get("option_index")
    if oi:
        opt_list = _scrape_options_only(page)
        tgt = next((o for o in opt_list if o.get("idx") == oi and o.get("list_price")), None)
        if not tgt:
            out["error"] = f"옵션{oi} 정가 스크랩 실패 (옵션 {len(opt_list)}개)"
            return out
        benefit_unit = round(tgt["list_price"] * 0.9)
        out["benefit_unit_price"] = benefit_unit
        out["option_picked"] = {"idx": oi, "list_price": tgt["list_price"]}
        print(f"     ⓘ #{prod['id']} 옵션{oi} 정가 {tgt['list_price']:,}원 → 우수가 {benefit_unit:,}원 (×0.9 고정)", flush=True)
        page.goto(url, wait_until="domcontentloaded", timeout=20000)   # 패널 초기화
        page.wait_for_timeout(1200)

    # 2) optimal qty 계산 (≥10% nominal, cap 500K)
    optimal = _find_optimal_qty(benefit_unit, tiers, cap_won=500_000, min_pct=10.0, simple_ranges=simple_ranges)
    out["optimal_tier"] = optimal
    if not optimal:
        out["effective_ten_percent"] = False
        out["error"] = "≥10% tier unreachable (cap 500K)" if benefit_unit else "no benefit_unit"
        return out
    out["effective_ten_percent"] = True
    qty = optimal["qty"]
    out["qty"] = qty

    # 3) 쿠폰 + 바로구매 (optimal qty)
    _click_coupon(page)
    ok, options = _execute_buy_now(page, qty, option_keyword=prod.get("option_keyword"),
                                   option_index=prod.get("option_index"))
    out["options"] = options
    if not ok:
        out["error"] = "바로구매 실패"
        return out

    # 4) 결제 페이지 정보 추출
    info = _extract_order_page(page)
    out["card_slides"] = info.get("slides") or []
    out["list_price"] = info.get("list_price") or info.get("list_total")
    actual_qty = info.get("qty") or qty
    if benefit_unit:
        out["member_price"] = benefit_unit * actual_qty
    else:
        out["member_price"] = info.get("member_price")
    out["qty"] = actual_qty

    # ── H열/I열 (즉시할인가 / 실비) ──
    # ★2026-08-19: 계산을 _settle() 로 단일화 (alias 경로와 같은 규칙을 쓰기 위함).
    #   H = 주문서 실제 결제금액 우선 / 청구할인 제외. 상세는 _settle docstring.
    member = out.get("member_price") or 0
    out["kakao_price"], out["kakao_reward_pt"], out["kakao_final_cost"] = _settle(
        out["card_slides"], member, tiers, simple_ranges)


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
                rw = _compute_reward(imm, tiers, simple_ranges)  # ★ H(즉시할인가) 기준
                out["paybacks"][name] = {
                    "immediate_price": imm,
                    "payback_pct": pct * 100,
                    "after_payback": after,
                    "reward_pt": rw,
                    "final_cost": after - rw,
                }
                break
    return out


def write_to_sheet(results: list[dict], date_str: str, acc_idx: int = 1) -> bool:
    """Step 1과 동일 시트의 "{M.DD}" 탭에 Hmall 10% 결과 추가 입력.
    기존 데이터 마지막 행에서 2행 띄운 후 헤더+상품 행들 입력.
    계정 #2 이상은 탭명에 " (계정N)" suffix — 사용자 수동 수정 보존용.
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
    # acc_idx > 1 이면 " (계정N)" suffix 로 분리 — 계정 #1 의 수동 수정 보존
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    suffix = f" (계정{acc_idx})" if acc_idx > 1 else ""
    tab_candidates = [
        f"{dt.month}.{dt.day:02d} 식품{suffix}",
        f"{dt.month}.{dt.day} 식품{suffix}",
    ]

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

    # 당일 카드할인 라벨(예 '삼성 5%') — 전 상품 공통이라 J1(카드할인가 열 머리) 에 1회 표기.
    _pairs = []
    for r in results:
        for c in ((r.get("payment") or {}).get("card_slides") or []):
            if c.get("percent"):
                _pairs.append(((c.get("text") or "").split()[0], c["percent"]))
    card_label = ""
    if _pairs:
        from collections import Counter
        (nm, pct), _ = Counter(_pairs).most_common(1)[0]
        card_label = f"{nm} {pct}%"
    section_title = [f"현대Hmall 10% 적립 체크 ({tab_candidates[0]}) — {len(results)}개 상품"]
    section_title += [""] * (9 - len(section_title)) + [card_label]   # J1 = 당일 카드할인
    headers = ["#", "제품명", "URL", "10%적립", "적립 문구", "쿠폰",
               "수량", "혜택가",
               "즉시할인가", "카드할인가", "5만↑최소수량", "실비(= 즉시할인가 × 카드페이백계수 − 적립금)"]
    headers += ["행사종료"]
    headers += [f"구간{i+1}" for i in range(max_tiers)]

    def _fmt(v):
        return f"{v:,}" if isinstance(v, (int, float)) and v else ""

    rows = []
    for r in results:
        if r.get("error"):
            ten = "ERR"
            phrase = r.get("error", "")
            coupon = ""
            qty_s = lp_s = imm_s = card_s = min_qty_s = real_s = ""
            event_end_s = ""
            tier_cells = [""] * max_tiers
        else:
            ten = "✓" if r["ten_percent"] else "✗"
            phrase = r.get("phrase") or ""
            coupon = "🎟️ 보유" if r.get("has_coupon") else ""
            p = r.get("payment") or {}
            qty_s = str(p.get("qty")) if p.get("qty") else ""
            # G열 = 우수고객 혜택가 (있으면) → 없으면 소비자가(list_price)
            lp_s = _fmt(p.get("benefit_unit_price") or p.get("list_price"))
            imm_s = _fmt(p.get("kakao_price"))         # 즉시할인가
            # 당일 카드할인(5%/7%) 적용가 = 혜택가에 카드할인 한 번 더. 금액만 기입(카드명/%는 J1).
            # 최소수량 = 카드할인 '단가' 기준 5만원 이상이 되는 최소 수량 (적립 판정이 최종결제가 기준).
            card_s = min_qty_s = ""
            _cs = [c for c in (p.get("card_slides") or []) if c.get("percent") and c.get("price")]
            if _cs:
                best = max(_cs, key=lambda c: c.get("percent") or 0)
                card_s = _fmt(best["price"])
                _bu = p.get("benefit_unit_price")
                if _bu:
                    unit_card = int(round(_bu * (1 - (best["percent"] or 0) / 100)))
                    if unit_card and unit_card < 50000:
                        min_qty_s = str(math.ceil(50000 / unit_card))
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
        rows.append([str(r["id"]), r["name"], r.get("url", ""), ten, phrase, coupon,
                     qty_s, lp_s, imm_s, card_s, min_qty_s, real_s, event_end_s] + tier_cells)

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
    hdr = (f"{'#':>3} | {'제품명':30s} | {'쿠폰':4s} | {'qty':>3} | {'정가':>9} | {'우수가':>9} | "
           f"{'즉시할인가':>10} | {'실비':>9} | 페이백카드")
    print(hdr)
    print("-" * 130)
    for r in results:
        name = (r["name"][:28] + "…") if len(r["name"]) > 29 else r["name"]
        coupon = "🎟️" if r.get("has_coupon") else "—"
        if r.get("error"):
            print(f"{r['id']:>3} | {name:30s} | {coupon:4s} | ERR {r['error'][:30]}")
            continue
        p = r.get("payment") or {}
        qty = str(p.get("qty") or "—")
        lp = f"{p['list_price']:,}" if p.get("list_price") else "—"
        mp = f"{p['member_price']:,}" if p.get("member_price") else "—"
        kk = f"{p['kakao_price']:,}" if p.get("kakao_price") else "—"
        kkf = f"{p['kakao_final_cost']:,}" if p.get("kakao_final_cost") is not None else "—"
        pb = p.get("paybacks") or {}
        pb_str = ", ".join(f"{k}={v['final_cost']:,}" for k, v in pb.items()) if pb else "—"
        print(f"{r['id']:>3} | {name:30s} | {coupon:4s} | {qty:>3} | {lp:>9} | {mp:>9} | "
              f"{kk:>9} | {kkf:>9} | {pb_str}")


def _cdp_alive(port: str) -> bool:
    """해당 포트의 Chrome CDP 가 응답하는지 (붙기 전에 살아있는 포트만 후보로)."""
    import urllib.request
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=2).read()
        return True
    except Exception:
        return False


def _hmall_logged_in(context) -> bool:
    """loginForm 진입 후 '로그아웃' 노출 여부로 Hmall 로그인 세션 판정 (raw 로그인은 하지 않음).
    로그인 상태면 loginForm 이 메인으로 리다이렉트되어 body 에 '로그아웃' 존재."""
    pg = context.pages[-1] if context.pages else context.new_page()   # 기존 탭 재사용(포커스 강탈 방지) — close 안 함
    try:
        pg.goto(LOGIN_URL, wait_until="domcontentloaded")
        pg.wait_for_timeout(1200)
        return "로그아웃" in pg.inner_text("body")
    except Exception:
        return False


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

    # ── CDP 포트: 로그인된 CFT 9222 단일 (2026-07-16 사용자 지시 — 다른 창 절대 띄우지 말 것) ──
    #   fresh 프로필 생짜 로그인은 Hmall reCAPTCHA v3 로 차단됨 → "이미 로그인된 CfT 세션 재사용"이 정답.
    #   CHECK10_CDP_PORT 로 강제 지정 가능. 로그인 안 돼 있으면 같은 9222 CFT 에서 로그인 시도(fallback).
    #   connect 는 plain playwright — patchright 는 Chrome 148 connect 시 크래시(setCacheDisabled). CDP attach 라 스텔스 무관.
    from playwright.sync_api import sync_playwright as sync_pw_plain
    forced = os.environ.get("CHECK10_CDP_PORT")
    candidates = list(dict.fromkeys([forced] * bool(forced) + [CDP_PORT] + [str(p) for p in PORT_CHAIN]))
    alive = [pt for pt in candidates if _cdp_alive(pt)]
    print(f"[INFO] CDP 후보 포트 {candidates} / 살아있음 {alive}")

    with sync_pw_plain() as p:
        # 1) 로그인된 CfT 우선 재사용
        for port in alive:
            try:
                browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{port}", slow_mo=300)
            except Exception as e:
                print(f"[WARN] 포트 {port} 연결 실패: {str(e)[:90]}")
                continue
            context = browser.contexts[0] if browser.contexts else browser.new_context()
            if _hmall_logged_in(context):
                print(f"[OK] 로그인된 CfT 재사용 → 포트 {port}")
                return _run(context, acc, acc_idx)
            print(f"[INFO] 포트 {port} — Hmall 로그인 안 됨, 다음 후보")

        # 2) 로그인 안 됨 → CFT 에서 raw 로그인 (reCAPTCHA 차단 가능성 있음). 포트는 체인 폴백.
        _port = resolve_cdp_port(int(CDP_PORT))
        _endpoint = f"http://127.0.0.1:{_port}"
        print(f"[INFO] 로그인된 세션 없음 — {_port} CFT 에서 로그인 시도")
        try:
            browser = p.chromium.connect_over_cdp(_endpoint, slow_mo=300)
        except Exception as e:
            print(f"[FATAL] CDP {CDP_PORT} 연결 실패: {e}")
            print(f"  수동 실행: bash ~/bin/launch-hmall-chrome.sh")
            return 1
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        return _run(context, acc, acc_idx)


def _is_crash_error(err: str | None) -> bool:
    """렌더러 크래시/페이지 소실 에러인지 — 메모리 압박 등으로 탭이 죽으면 발생."""
    if not err:
        return False
    return any(k in err for k in (
        "crashed", "Target closed", "has been closed",
        "Target page, context or browser",
    ))


def _fresh_page(context, old):
    """죽은 page 닫고 같은 context의 다른 탭 재사용(없으면 새 탭). 쿠키 유지 → 재로그인 불필요.
    새 탭 생성은 macOS 창 포커스 강탈이라 가급적 기존 탭 재사용(2026-07-10)."""
    try:
        old.close()
    except Exception:
        pass
    return context.pages[-1] if context.pages else context.new_page()


def _run(context, acc, acc_idx: int = 1) -> int:
    # ★기존 탭 재사용 — 새 탭=macOS Chrome 창 포커스 강탈(2026-07-10). close 안 함.
    page = context.pages[-1] if context.pages else context.new_page()
    if not login(page, acc["id"], acc["pw"]):
        print(f"[FATAL] 로그인 실패")
        return 1
    print(f"[OK] 로그인")

    # 중복 URL은 base 한 번만 페이지 방문, alias는 base의 benefit_ratio + 자기 옵션 unit_list_price로 derive
    cache: dict[str, dict] = {}
    results: list[dict] = []
    # CHECK10_ONLY="34,35" → 해당 id 만 검사(부분 실행). 나머지는 기존 today.json 결과를 병합해
    #   전체를 다시 기록한다(write_to_sheet 가 ws.clear() 하므로 부분만 쓰면 기존 행이 날아감 — 2026-07-23).
    only_ids = {x for x in os.environ.get("CHECK10_ONLY", "").replace(" ", "").split(",") if x}
    if os.environ.get("DEBUG_ORDER"):
        products_to_run = PRODUCTS[:1]
    elif only_ids:
        products_to_run = [p for p in PRODUCTS if p["id"] in only_ids]
        print(f"[INFO] 부분 실행 CHECK10_ONLY={sorted(only_ids)} → {len(products_to_run)}개 상품만 검사 "
              f"(나머지는 기존 {TODAY_OUT.name} 결과 병합)", flush=True)
    else:
        products_to_run = PRODUCTS
    for prod in products_to_run:
        if prod.get("alias_of"):
            base = next((r for r in results if r["id"] == prod["alias_of"]), None)
            if base:
                derived = derive_alias_result(base, prod)
                results.append(derived)
                p = derived.get("payment") or {}
                tag = "✓" if derived.get("ten_percent") else "✗"
                print(f"  #{prod['id']:>3} (alias of #{prod['alias_of']}) derive: "
                      f"unit_list={prod.get('unit_list_price') or '?'} "
                      f"benefit_unit={p.get('benefit_unit_price') or '?'} "
                      f"qty={p.get('qty') or '?'} "
                      f"H={p.get('kakao_price') or '?'} I={p.get('kakao_final_cost') or '?'} {tag}")
                continue

        print(f"  #{prod['id']:>3} {prod['name'][:30]:30s} 검사 중...", flush=True)
        result = check_one_product(page, prod)
        if _is_crash_error(result.get("error")):
            print(f"     ⚠️ 페이지 크래시 — page 재생성 후 1회 재시도")
            page = _fresh_page(context, page)
            result = check_one_product(page, prod)
        status = "✓" if result["ten_percent"] else ("ERR" if result.get("error") else "✗")
        n_tiers = len(result.get("tiers") or [])
        print(f"     → 10%={status}  구간={n_tiers}단  phrase={(result.get('phrase') or '')[:40]}")
        # 10% 적립 상품만 결제 흐름 진행 (정가/카카오할인가/실비 추출)
        if result.get("ten_percent") and not result.get("error"):
            payment = check_payment_flow(page, prod, result.get("tiers") or [], result.get("simple_ranges") or [])
            if _is_crash_error(payment.get("error")):
                print(f"     ⚠️ 결제흐름 페이지 크래시 — page 재생성 후 1회 재시도")
                page = _fresh_page(context, page)
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

    # 부분 실행이면 기존 today.json 결과와 병합 (id 기준 신규가 승) → 전체를 다시 기록.
    if only_ids and TODAY_OUT.exists():
        try:
            prev = json.loads(TODAY_OUT.read_text(encoding="utf-8"))
            if prev.get("date") == datetime.now().strftime("%Y-%m-%d"):
                merged = {r["id"]: r for r in (prev.get("products") or [])}
                merged.update({r["id"]: r for r in results})
                # Guide.md 표 순서 유지 (표에 없는 잔여 id 는 뒤에 붙임)
                order = [p["id"] for p in PRODUCTS if p["id"] in merged]
                order += [k for k in merged if k not in set(order)]
                results = [merged[k] for k in order]
                print(f"[OK] 기존 결과 병합 → 총 {len(results)}개 (신규/갱신 {len(only_ids)}개)", flush=True)
            else:
                print(f"[WARN] {TODAY_OUT.name} 날짜가 오늘이 아님({prev.get('date')}) — 병합 skip", flush=True)
        except Exception as e:
            print(f"[WARN] 기존 결과 병합 실패({e}) — 이번 결과만 기록", flush=True)

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
    write_to_sheet(results, date_str, acc_idx=acc_idx)

    page.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
