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

CDP_PORT = 9222

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
# 11개 조합 본품 구성 (가이드 섹션 14-1 고정)
# ============================================================
COMBOS: list[list[tuple[str, int]]] = [
    [("g", 2), ("h", 1)],
    [("d", 2), ("g", 2)],
    [("d", 4), ("e", 1)],
    [("e", 2), ("h", 1)],
    [("b", 2), ("d", 2)],
    [("e", 2), ("f", 2)],
    [("c", 3), ("h", 1)],
    [("c", 3), ("d", 2)],
    [("c", 1), ("f", 4)],
    [("c", 2), ("f", 3)],
    [("f", 5)],
]

# ============================================================
# 샘플 단가 참조표 (가이드 섹션 3) — 페이지 텍스트 → 단가 + s코드
# 키는 페이지 텍스트 매칭용 (소문자/공백 제거 키), 값은 (정식이름, 단가, s코드)
# s코드 None == 재고 매핑 미정 (계산엔 영향 없음, inventory 비교에서만 의미)
# ============================================================
SAMPLE_TABLE: list[tuple[str, int, str | None]] = [
    ("자음생캡슐세럼 8ml", 4_000, "s10"),
    ("윤조에센스 6세대 8ml", 1_600, "s01"),
    ("윤조에센스 6세대 15ml", 4_200, "s02"),
    ("윤조마스크", 1_600, "s03"),
    ("자음수15ml자음유액15ml", 2_100, "s04"),
    ("탄력크림 5ml", 1_000, "s05"),
    ("탄력크림 15ml", 4_000, "s06"),
    ("자음생수25ml자음생유액25ml", 4_100, "s07"),
    ("자음생캡슐세럼 5ml", 2_000, "s09"),
    ("자음생리치크림 5ml", 2_700, "s11"),
    ("자음생리치크림 10ml", 8_000, "s12"),
    ("자음생크림 5ml", 2_200, "s13"),
    ("자음생아이크림 3ml", 1_900, "s14"),
    ("자음생브라이트닝세럼 8ml", 4_200, "s18"),
    ("자음생브라이트닝앰플 5g", 3_500, "s20"),
    ("자음생마스크", 3_400, "s17"),
    ("순행클렌징폼 50ml", 2_200, "s22"),
    ("순행클렌징오일 50ml", 2_200, "s23"),
    ("자음생아이크림 4ml", 2_000, "s15"),
    ("옥용팩 35ml", 1_900, "s24"),
    ("여윤팩 35ml", 1_900, "s25"),
    ("백삼팩 35ml", 2_500, "s26"),
    ("윤조아이세럼 4ml", 2_000, "s31"),
    ("상백톤업선크림 10ml", 1_500, "s28"),
    ("상백선크림 10ml", 1_000, "s27"),
    ("상백선플루이드 3ml", 400, "s29"),
    ("진생솝 25g", 1_500, "s30"),
    ("자음생클렌징폼 50g", 5_000, "s33"),
    ("윤빛 마사저 1ea", 2_000, "s32"),
    ("미니팩 3종", 7_300, "s34"),
    ("자정기미코렉터 3ml", 2_000, "s35"),
    ("자정수25ml자정유액25ml", 5_500, "s36"),
    ("자정앰플세럼 7ml", 4_500, "s37"),
    # SET 가격 규칙 (섹션 4) — 페이지에서 종종 SET 표기로 등장
    ("자음수유액SET", 2_100, "s04"),
    ("자음생수유액SET", 4_100, "s07"),
    # 페이지 표기 변형 alias (단가표 정식이름과 단어순서/표기 다른 것)
    ("자음생크림 리치 5ml", 2_700, "s11"),     # canonical: 자음생리치크림 5ml
    ("자음생크림 리치 10ml", 8_000, "s12"),    # canonical: 자음생리치크림 10ml
    ("자음생크림리치 5ml", 2_700, "s11"),
    ("자음생크림리치 10ml", 8_000, "s12"),
    ("탄력크림EX 15ml", 4_000, "s06"),         # canonical: 탄력크림 15ml
    ("탄력크림EX 5ml", 1_000, "s05"),
    ("자음생브라이트닝세럼 8ml (2026/02)", 4_200, "s19"),
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
}

# ============================================================
# 갤러리아 네이버구매할인 상수 (섹션 6)
# ============================================================
GALLERIA_NAVER_MULT = 0.948  # 0.978 (네이버 2.2%) - 0.030 (추가 3%)

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


def lookup_sample(page_text: str) -> tuple[str, int, str | None] | None:
    """페이지에서 읽은 샘플명 → (정식이름, 단가, s코드) or None.

    가이드 룰: '최대한 텍스트가 일치하는 제품'을 찾는다.
    매칭 실패 == 신규 제품 (단가미정).
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
# 갤러리아 계산 (네이버 ×0.948, 카드 없음)
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
    # 최종구매가 = sum(단가 × 수량 × (1 - 기본%) × (1 - 쿠폰%) × 0.948)
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


def rank_by_rate(rows: list[dict]) -> list[dict]:
    """공급률 오름차순으로 'rank' 채워서 같은 list 반환."""
    for r in rows:
        r["rank"] = 0
    sorted_rows = sorted(rows, key=lambda r: r["공급률"])
    for rk, r in enumerate(sorted_rows, start=1):
        r["rank"] = rk
    return rows


# ============================================================
# gspread helper
# ============================================================
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
