"""rate-check 공통 — 상수 + 데이터 모델 + gspread/계산 helper.

galleria.py / hmall.py / lotte.py / run.py 가 import.
"""
from __future__ import annotations
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable

import gspread

ROOT = Path(__file__).resolve().parent.parent
SERVICE_ACCOUNT = ROOT / "gen-lang-client-0553550811-4b553902b0d0.json"
RATE_SHEET_ID = "1fxB0UvLRy2iQfonCWn5U5mWnXbzSdn6l4e2XuQluhwo"
INVENTORY_SHEET_ID = "1aDJ35nebhF6kxn_Q2NqPx5Ym-w5QqPDozygiiDSZTxU"
SULWHASOO_IDS = ROOT / "hsmaster" / "config" / "sulwhasoo-ids.json"
HMALL_CONFIG = ROOT / "hmall_config.json"
TMP_DIR = ROOT / "rate-check" / "_tmp"

import os as _os
CDP_PORT = int(_os.environ.get("RATE_CHECK_CDP_PORT", "9222"))

# ============================================================
# 본품 (b~h) — 코드, 이름, 소비자가 (가이드 섹션 3 고정)
# ============================================================
PRODUCTS: dict[str, dict] = {
    "b": {"name": "윤조3종",         "price": 229_000},
    "c": {"name": "자음2종",         "price": 150_000},
    "d": {"name": "본윤2종",         "price": 125_000},
    "e": {"name": "탄력3종",         "price": 215_000},
    "f": {"name": "윤조에센스90",    "price": 140_000},
    "g": {"name": "자음생2종",       "price": 225_000},
    "h": {"name": "자음생크림리치",  "price": 270_000},
}
PRODUCT_CODES = list(PRODUCTS.keys())  # ['b', 'c', 'd', 'e', 'f', 'g', 'h']

# 시트 라벨용 짧은 별명 (예: 'g자생2 2 + h자생크림 1')
SHORT_NAME: dict[str, str] = {
    "b": "윤3",
    "c": "자음2",
    "d": "본윤2",
    "e": "탄력3",
    "f": "윤90",
    "g": "자생2",
    "h": "자생크림",
}


def combo_label_ko(combo: list[tuple[str, int]]) -> str:
    """조합 라벨 (한국어 짧은 별명 + 코드 + 수량).

    예: [('g',2), ('h',1)] → 'g자생2 2 + h자생크림 1'
    """
    return " + ".join(f"{c}{SHORT_NAME[c]} {q}" for c, q in combo)

# ============================================================
# 시트 레이아웃 (행 번호) — 3사 20조합 적용 (5/18~)
# Galleria 1~50 / Hmall 53~78 / Lotte 81~107 / 차트 J1:M21
# ============================================================
GALLERIA_DATA_END_ROW = 50
HMALL_HEADER_ROW = 53
HMALL_COMBO_START_ROW = 59
HMALL_COMBO_END_ROW = 78
LOTTE_HEADER_ROW = 81
LOTTE_COMBO_START_ROW = 88
LOTTE_COMBO_END_ROW = 107
CHART_RANGE = "J1:M21"

# ============================================================
# 본품 회전율 (월환산 판매수량 / Layer 1 정적, cart_plan 분배 가중치)
# ============================================================
PRODUCT_VELOCITY: dict[str, int] = {
    "b": 3, "c": 10, "d": 5,
    "e": 20, "f": 10, "g": 15, "h": 7,
}

# ============================================================
# 상품별 수익금 (개당 남는 금액, 원). cart_plan 정렬 기준 (5/19).
# 출처: 사용자 직접 측정 (소비자가 - 도매가 - 부대비용 등 종합)
# ★ 5/20부터 정렬은 NAVER_SETTLED 기반 동적 수익률로 전환 — PRODUCT_PROFIT 은 폴백/참조용.
# ============================================================
PRODUCT_PROFIT: dict[str, int] = {
    "b": 12_136,   # 윤조3종 (변경X)
    "c":    730,   # 자음2종 — 5/26 갱신 (네이버 80,000 무료 → 정산 72,720)
    "d":   -359,   # 본윤2종 — 5/26 갱신 (네이버 62,330 +3천 배송 → 정산 59,216) ⚠️ 적자
    "e": 17_492,   # 탄력3종 — 5/26 갱신 (네이버 121,200 무료 → 정산 111,200)
    "f": 10_892,   # 윤조에센스90 (변경X)
    "g": 12_942,   # 자음생2종 — 5/26 갱신 (네이버 121,120 +3천 배송 → 정산 114,126)
    "h": 38_000,   # 자음생크림리치 (변경X)
}

# ============================================================
# Naver 스마트스토어 정산가 (per unit, 원). cart_plan 동적 수익 계산용 (5/20~).
# 사용자가 Naver 가격비교 확인 후 수동 업데이트. URL 코드 X — 수동.
# 공식 (참고):
#   배송비 없음:  판매가 × 0.934
#   무료배송:     판매가 × 0.934 - 2,000  (셀러가 배송비 흡수)
#   3천원 배송:   판매가 × 0.934 + 1,000  (셀러 +1k 마진)
#   정산가 직접:  사용자가 net 값만 알려줌
# 매일 변동되니 사용자 갱신 알림 시 즉시 패치.
# ============================================================
NAVER_SETTLED: dict[str, int] = {
    "b": 124_222,  # 133,000 × 0.934 (고정, 배송비 없음)
    "c":  72_720,  # 80,000 × 0.934 - 2,000 (무료배송) — 5/26 갱신
    "d":  59_216,  # 62,330 × 0.934 + 1,000 (3천원 배송) — 5/26 갱신
    "e": 111_200,  # 121,200 × 0.934 - 2,000 (무료배송) — 5/26 갱신
    "f":  82_200,  # 정산가 직접
    "g": 114_126,  # 121,120 × 0.934 + 1,000 (3천원 배송) — 5/26 갱신
    "h": 165_000,  # 정산가 직접
}

# ============================================================
# 20개 조합 본품 구성 (TOP 20, Layer 1 정적)
# ============================================================
# 2026-05-27 재생성: 후보(≤3종, 70~81만) galleria+lotte 수식 공급률 min 오름차순 상위20 + h×3 강제.
# d 추증가치 하락(15,300→10,100) 반영. 생성기: rate-check/gen_combos.py (현재 20조합 재계산으로 시트 검증 후 적용).
COMBOS: list[list[tuple[str, int]]] = [
    [("d", 2), ("g", 2)],             # 1
    [("f", 5)],                       # 2
    [("d", 1), ("f", 1), ("g", 2)],   # 3
    [("f", 2), ("g", 2)],             # 4
    [("e", 1), ("f", 2), ("g", 1)],   # 5
    [("c", 1), ("f", 4)],             # 6
    [("c", 1), ("d", 1), ("g", 2)],   # 7
    [("d", 4), ("g", 1)],             # 8
    [("e", 2), ("f", 2)],             # 9
    [("c", 1), ("f", 1), ("g", 2)],   # 10
    [("d", 3), ("f", 1), ("g", 1)],   # 11
    [("d", 4), ("e", 1)],             # 12
    [("c", 2), ("d", 1), ("f", 2)],   # 13
    [("d", 2), ("f", 2), ("g", 1)],   # 14
    [("g", 2), ("h", 1)],             # 15
    [("d", 3), ("e", 1), ("f", 1)],   # 16
    [("c", 2), ("f", 3)],             # 17
    [("d", 1), ("f", 3), ("g", 1)],   # 18
    [("c", 1), ("d", 1), ("e", 2)],   # 19
    [("h", 3)],                       # 20 (3h 강제 포함)
]

# ============================================================
# 샘플 단가 참조표 (가이드 섹션 3) — 페이지 텍스트 → 단가 + s코드
# 키는 페이지 텍스트 매칭용 (소문자/공백 제거 키), 값은 (정식이름, 단가, s코드)
# s코드 None == 재고 매핑 미정 (계산엔 영향 없음, inventory 비교에서만 의미)
# ============================================================
SAMPLE_TABLE: list[tuple[str, int, str | None]] = [
    ("자음생캡슐세럼 8ml", 3_800, "s10"),
    ("자음생캡슐세럼 15ml", 11_500, None),
    ("윤조에센스 6세대 8ml", 2_000, "s01"),
    ("윤조에센스 6세대 15ml", 4_200, "s02"),
    ("윤조마스크", 1_600, "s03"),
    ("자음수15ml자음유액15ml", 3_300, "s04"),
    ("탄력크림 5ml", 1_100, "s05"),
    ("탄력크림 15ml", 3_400, "s06"),
    ("자음생수25ml자음생유액25ml", 4_900, "s07"),
    ("자음생캡슐세럼 5ml", 3_000, "s09"),
    ("자음생리치크림 5ml", 3_000, "s11"),
    ("자음생리치크림 10ml", 8_000, "s12"),
    ("자음생크림 5ml", 2_100, "s13"),
    ("자음생아이크림 3ml", 2_000, "s14"),
    ("자음생브라이트닝세럼 8ml", 4_500, "s18"),
    ("자음생브라이트닝앰플 5g", 3_000, "s20"),
    ("자음생마스크", 3_300, "s17"),
    ("순행클렌징폼 50ml", 2_100, "s22"),
    ("순행클렌징오일 50ml", 2_100, "s23"),
    ("자음생아이크림 4ml", 2_000, "s15"),
    ("자음생왕아이크림 5ml", 4_900, None),
    ("옥용팩 35ml", 2_000, "s24"),
    ("여윤팩 35ml", 2_000, "s25"),
    ("백삼팩 35ml", 2_500, "s26"),
    ("윤조아이세럼 4ml", 2_900, "s31"),
    ("상백톤업선크림 10ml", 1_700, "s28"),
    ("상백선크림 10ml", 1_200, "s27"),
    ("상백선플루이드 3ml", 400, "s29"),
    ("진생솝 25g", 1_500, "s30"),
    ("자음생클렌징폼 50g", 5_000, "s33"),
    ("윤빛 마사저 1ea", 2_000, "s32"),
    ("미니팩 3종", 7_300, "s34"),
    ("자정기미코렉터 3ml", 2_000, "s35"),
    ("자정수25ml자정유액25ml", 5_500, "s36"),
    ("자정앰플세럼 7ml", 4_500, "s37"),
    # SET 가격 규칙 (섹션 4) — 페이지에서 종종 SET 표기로 등장
    ("자음수유액SET", 3_300, "s04"),
    ("자음생수유액SET", 4_900, "s07"),
    # 페이지 표기 변형 alias (단가표 정식이름과 단어순서/표기 다른 것)
    ("자음생크림 리치 5ml", 3_000, "s11"),     # canonical: 자음생리치크림 5ml
    ("자음생크림 리치 10ml", 8_000, "s12"),    # canonical: 자음생리치크림 10ml
    ("자음생크림리치 5ml", 3_000, "s11"),
    ("자음생크림리치 10ml", 8_000, "s12"),
    ("탄력크림EX 15ml", 3_400, "s06"),         # canonical: 탄력크림 15ml
    ("탄력크림EX 5ml", 1_100, "s05"),
]

# ============================================================
# 카드 페이백 매핑 (가이드 섹션 9-1.5)
# ============================================================
CARD_PAYBACK: dict[str, float] = {
    "롯데카드": 0.02,
    "비씨카드": 0.015,
    "삼성카드": 0.01,
    "하나카드": 0.01,
    "농협카드": 0.01,
    "KB국민카드": 0.01,  # 5/20 추가 — 1% 페이백 (이전: 페이백 없음)
}

# ============================================================
# 갤러리아 네이버구매할인 상수 (섹션 6)
# ============================================================
GALLERIA_NAVER_MULT = 0.978  # 네이버 구매할인 2.2% 만 (추가 3% 제거 — 사용자 5/14 지시)

# ============================================================
# GWP tier (섹션 5 / 9-3)
# ============================================================
GWP_TIER_70 = 700_000
GWP_TIER_40 = 400_000


# ============================================================
# 유틸: 페이지 텍스트 → 단가표 매칭
# ============================================================
def _normalize(s: str) -> str:
    """공백·괄호·하이픈·SET·+·구두점 제거하고 소문자.

    "자음수 15ml + 자음유액 15ml (SET)" → "자음수15ml자음유액15ml"
    """
    s = re.sub(r"\([^)]*\)", "", s)        # () 안 제거
    s = re.sub(r"[\s\-/+,·]", "", s)      # 공백·하이픈·슬래시·플러스·콤마·중점 제거
    s = re.sub(r"(?i)set", "", s)         # SET 단어 제거
    return s.lower()


# 샘플 단가 = 재고현황 시트(SoT)에서 동적 로드 (s코드→단가). SAMPLE_TABLE 은 이름→s코드
# 매칭(별칭 포함)에만 사용하고, *단가는 시트가 우선*. (하드코딩 drift 방지 — 2026-06-02)
INVENTORY_STATUS_TAB = "재고현황"
INVENTORY_PRICE_RANGE = "A55:E91"   # A=s코드, E=단가
_SHEET_SAMPLE_PRICES: dict[str, int] | None = None


def sample_prices_from_sheet(force: bool = False) -> dict[str, int]:
    """재고현황 시트에서 {s코드: 단가} 로드 (1회 캐시). 실패 시 빈 dict → SAMPLE_TABLE 폴백."""
    global _SHEET_SAMPLE_PRICES
    if _SHEET_SAMPLE_PRICES is not None and not force:
        return _SHEET_SAMPLE_PRICES
    prices: dict[str, int] = {}
    try:
        ws = gs_client().open_by_key(INVENTORY_SHEET_ID).worksheet(INVENTORY_STATUS_TAB)
        for r in ws.get(INVENTORY_PRICE_RANGE, value_render_option="UNFORMATTED_VALUE"):
            if not r:
                continue
            code = str(r[0]).strip()
            if not re.match(r"^s\d+$", code):
                continue
            val = r[4] if len(r) > 4 else None
            if isinstance(val, (int, float)) and val:
                prices[code] = int(val)
    except Exception as e:
        print(f"[WARN] 재고현황 단가 로드 실패 ({e}) → SAMPLE_TABLE 하드코딩 단가 사용")
    _SHEET_SAMPLE_PRICES = prices
    return prices


def check_sample_price_drift() -> list[tuple[str, str, int, int]]:
    """하드코딩 SAMPLE_TABLE 단가 vs 재고현황 시트 단가 불일치 목록 (s코드 기준).
    반환: [(s코드, 정식이름, 코드단가, 시트단가), ...]. Step 1 에서 drift 가시화용."""
    sheet = sample_prices_from_sheet()
    seen: set[str] = set()
    out = []
    for name, price, code in SAMPLE_TABLE:
        if code and code in sheet and code not in seen:
            seen.add(code)
            if sheet[code] != price:
                out.append((code, name, price, sheet[code]))
    return out


def lookup_sample(page_text: str) -> tuple[str, int, str | None] | None:
    """페이지에서 읽은 샘플명 → (정식이름, 단가, s코드) or None.

    가이드 룰: '최대한 텍스트가 일치하는 제품'을 찾는다.
    매칭 실패 == 신규 제품 (단가미정).
    단가는 재고현황 시트(s코드) 우선, 없으면 SAMPLE_TABLE 폴백.
    """
    norm = _normalize(page_text)
    if not norm:
        return None
    # 1) substring 매칭 — 단가표 정식이름이 페이지 텍스트에 포함되는 경우
    candidates = []
    for name, price, code in SAMPLE_TABLE:
        n = _normalize(name)
        if n in norm or norm in n:
            candidates.append((len(n), name, price, code))
    if candidates:
        # 가장 긴 매치 우선 (구체적인 게 정확)
        candidates.sort(reverse=True)
        _, name, price, code = candidates[0]
        sheet_prices = sample_prices_from_sheet()
        if code and code in sheet_prices:
            price = sheet_prices[code]   # 시트 단가 우선
        return (name, price, code)
    return None


# ============================================================
# 데이터 모델
# ============================================================
@dataclass
class Sample:
    name: str
    qty: int
    price: int
    code: str | None = None  # s코드 (재고 매핑용)
    raw_text: str = ""       # 페이지 원문

    @property
    def value(self) -> int:
        return self.qty * self.price


@dataclass
class ProductDay:
    """하루치 상품 데이터 (b~h 각각)."""
    code: str
    basic_discount_pct: float = 10.0   # 기본할인 (보통 10)
    coupon_pct: float = 0.0            # 최대 쿠폰%
    add_gifts: list[Sample] = field(default_factory=list)
    new_items: list[str] = field(default_factory=list)  # 단가표 미매칭

    @property
    def add_gift_value(self) -> int:
        return sum(s.value for s in self.add_gifts)


@dataclass
class GwpDay:
    """40/70만 GWP 1세트 구성."""
    set_items: list[Sample] = field(default_factory=list)
    period: str = ""

    @property
    def set_value(self) -> int:
        return sum(s.value for s in self.set_items)


# ============================================================
# 갤러리아 계산 (네이버 ×0.978, 카드 없음)
# ============================================================
def galleria_combo(idx: int, combo: list[tuple[str, int]],
                   products: dict[str, ProductDay], gwp: GwpDay) -> dict:
    """1조합 → 갤러리아 공급률 계산 결과 dict."""
    소비자가 = sum(PRODUCTS[c]["price"] * q for c, q in combo)
    추가증정 = sum(products[c].add_gift_value * q for c, q in combo)
    if 소비자가 >= GWP_TIER_70:
        gwp_value = gwp.set_value * 6
        gwp_tier = "6세트"
    elif 소비자가 >= GWP_TIER_40:
        gwp_value = gwp.set_value * 3
        gwp_tier = "3세트"
    else:
        gwp_value = 0
        gwp_tier = "미적용"
    총샘플 = 추가증정 + gwp_value
    # 최종구매가 = sum(단가 × 수량 × (1 - 기본%) × (1 - 쿠폰%) × 0.978)
    final = 0.0
    for c, q in combo:
        pd = products[c]
        mult = (1 - pd.basic_discount_pct / 100) * (1 - pd.coupon_pct / 100) * GALLERIA_NAVER_MULT
        final += PRODUCTS[c]["price"] * q * mult
    final = round(final)
    순 = final - 총샘플
    공급률 = 순 / 소비자가 if 소비자가 else 0.0
    name = " + ".join(f"{PRODUCTS[c]['name']}×{q}" for c, q in combo)
    return {
        "idx": idx, "name": name, "combo": combo,
        "소비자가": 소비자가, "추가증정": 추가증정,
        "gwp_tier": gwp_tier, "gwp_value": gwp_value, "총샘플": 총샘플,
        "최종구매가": final, "순구매가": 순, "공급률": 공급률,
    }


# ============================================================
# gspread helper
# ============================================================
def _parse_won(text) -> int:
    """'16,600원' → 16600. None/빈문자열 → 0."""
    if not text:
        return 0
    digits = re.sub(r"[^\d]", "", str(text))
    return int(digits) if digits else 0


def load_galleria_composition_from_sheet(ws) -> dict:
    """galleria가 시트(통합 탭)에 쓴 결과를 직접 읽는다 — **캐시 JSON 절대 사용 X**.

    SoT 원칙: 모든 결과는 Google Sheets에만 보존. 로컬 파일/캐시는 stale 위험으로 금지.
    매 실행마다 sheet에서 fresh 읽기 — 재실행 시 옛 캐시 따라 쓰는 버그 방지.

    Returns: {"add_gift_value": {b: int, ...}, "gwp_1set": int, "gwp_6set": int}
    galleria가 미실행이면 ValueError.
    """
    # 1) 추가증정가치 행 — "추가증정가치" 라벨 검색 (갤러리아 섹션 전체, 위치 가변 안전)
    block = ws.get("A6:H50")
    add_gift_value = {}
    for row in block:
        if row and row[0].strip().startswith("추가증정가치"):
            for i, code in enumerate("bcdefgh", start=1):
                cell = row[i] if i < len(row) else ""
                add_gift_value[code] = _parse_won(cell)
            break
    if not add_gift_value or len(add_gift_value) < 7:
        raise ValueError("'추가증정가치' 행 sheet에서 못찾음 — galleria 먼저 실행 필요")

    # 2) GWP 1세트/6세트 — "1세트 합계" / "6세트 합계" 라벨 검색 (품목 수 무관, 위치 가변 안전)
    gwp_block = ws.get("A6:C50")
    gwp_1set = gwp_6set = 0
    for row in gwp_block:
        if not row:
            continue
        label = row[0].strip()
        val = _parse_won(row[2] if len(row) > 2 else "")
        if label.startswith("1세트 합계"):
            gwp_1set = val
        elif label.startswith("6세트 합계"):
            gwp_6set = val
    if not gwp_6set:
        raise ValueError("'6세트 합계' sheet에서 못찾음 — galleria 먼저 실행 필요")

    return {"add_gift_value": add_gift_value, "gwp_1set": gwp_1set, "gwp_6set": gwp_6set}


def load_galleria_samples_from_sheet(ws) -> dict:
    """galleria sheet에서 per-product per-sample composition 파싱 (inventory.py 용).

    각 cell 텍스트 형식: '{name} x{qty} ({price}원)'. s코드는 SAMPLE_TABLE lookup.
    Returns: {code: [{name, qty, price, code}, ...]}
    캐시 X — sheet가 SoT. "샘플" 행만 수집, "추가증정가치"에서 종료 → 위치 가변 안전.
    """
    block = ws.get("A6:H50")
    products = {c: [] for c in "bcdefgh"}
    for row in block[1:]:  # row 0 = 헤더
        if not row:
            continue
        label = row[0].strip()
        if label.startswith("추가증정가치"):
            break
        if not label.startswith("샘플"):
            continue
        for c_idx, code in enumerate("bcdefgh", start=1):
            cell = row[c_idx] if c_idx < len(row) else ""
            if not cell.strip():
                continue
            m = re.match(r"(.+?)\s*x(\d+)\s*\(([\d,]+)원\)", cell.strip())
            if m:
                name = m.group(1).strip()
                hit = lookup_sample(name)
                products[code].append({
                    "name": name,
                    "qty": int(m.group(2)),
                    "price": int(m.group(3).replace(",", "")),
                    "code": hit[2] if hit else None,
                })
    return products


def load_galleria_gwp_from_sheet(ws) -> list[dict]:
    """galleria sheet의 GWP 1세트 샘플 리스트 파싱 (inventory.py 용).

    col A=name, col B='x{qty}', col C='{price}원'. "1세트 합계" 라벨에서 종료 (품목 수 무관).
    Returns: [{name, qty, price, code}, ...]
    """
    block = ws.get("A7:C50")
    set_items = []
    for row in block:
        if not row or not row[0].strip():
            continue
        label = row[0].strip()
        if label.startswith("1세트 합계") or label.startswith("6세트 합계"):
            break
        qty_match = re.match(r"x(\d+)", row[1].strip() if len(row) > 1 else "")
        if not qty_match:
            continue
        price = _parse_won(row[2] if len(row) > 2 else "")
        hit = lookup_sample(label)
        set_items.append({
            "name": label,
            "qty": int(qty_match.group(1)),
            "price": price,
            "code": hit[2] if hit else None,
        })
    return set_items


def matched_chromedriver_service(port):
    """CDP(debuggerAddress) attach 시 드라이버 버전 mismatch 회피.

    문제: attach 모드에선 Selenium Manager 가 실행 중 Chrome 의 버전을 못 읽어
    최신 stable 드라이버를 고른다. CFT 가 한 단계 낮은 major 면 chromedriver 가
    "only supports Chrome version N" 으로 연결 거부 → 매 실행 첫 attach 실패.

    해결: 실행 중 Chrome 의 major 버전을 CDP `/json/version` 으로 읽고,
    selenium 캐시에서 같은 major 의 chromedriver 로 Service 를 만들어 고정한다.
    (chromedriver 는 major 만 맞으면 동작.) 일치 캐시 없으면 None →
    Selenium Manager 기본동작에 위임 (한 번 받아두면 다음부턴 캐시 매칭됨).
    """
    import glob
    import json
    import platform
    import re
    import urllib.request

    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=3) as r:
            browser = json.load(r).get("Browser", "")
    except Exception:
        return None
    m = re.search(r"/(\d+)\.", browser)  # "Chrome/148.0.7778.97" → 148
    if not m:
        return None
    major = m.group(1)
    arch = "mac-arm64" if platform.machine() == "arm64" else "mac-x64"
    base = Path.home() / ".cache" / "selenium" / "chromedriver" / arch
    cands = sorted(glob.glob(str(base / f"{major}.*" / "chromedriver")))
    if not cands:
        return None
    from selenium.webdriver.chrome.service import Service
    return Service(executable_path=cands[-1])  # 같은 major 중 최신 패치


def gs_client() -> gspread.Client:
    return gspread.service_account(filename=str(SERVICE_ACCOUNT))


def today_tab_name(now: datetime | None = None) -> str:
    now = now or datetime.now()
    return f"{now.month}.{now.day}"


def get_or_create_tab(sh: gspread.Spreadsheet, title: str, *, leftmost: bool = True) -> gspread.Worksheet:
    """오늘 탭 가져오거나 생성. leftmost=True면 가장 왼쪽으로."""
    existing = {ws.title: ws for ws in sh.worksheets()}
    if title in existing:
        return existing[title]
    ws = sh.add_worksheet(title=title, rows=400, cols=20)
    if leftmost:
        sh.reorder_worksheets([ws] + [w for w in sh.worksheets() if w.title != title])
    return ws


def col_letter(n: int) -> str:
    """1=A, 26=Z, 27=AA"""
    s = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def write_grid(ws: gspread.Worksheet, start_row: int, grid: list[list]) -> str:
    """grid 를 start_row 부터 batch update. 사용된 range 문자열 반환."""
    if not grid:
        return ""
    maxcols = max(len(r) for r in grid)
    norm = [[*r, *([""] * (maxcols - len(r)))] for r in grid]
    norm_str = [[("" if v is None else str(v)) for v in r] for r in norm]
    end_col = col_letter(maxcols)
    rng = f"A{start_row}:{end_col}{start_row + len(norm_str) - 1}"
    ws.update(values=norm_str, range_name=rng, value_input_option="USER_ENTERED")
    return rng


# ============================================================
# 외부 ID 파일
# ============================================================
def load_ids() -> dict:
    """sulwhasoo-ids.json — b~h 별 galleria/hyundai/lotte goods_no."""
    return json.loads(SULWHASOO_IDS.read_text(encoding="utf-8"))["ids"]


def load_hmall_first_account() -> dict:
    """hmall_config.json 첫 계정."""
    return json.loads(HMALL_CONFIG.read_text(encoding="utf-8"))["accounts"][0]
