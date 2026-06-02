"""롯데홈쇼핑 폰앱 구매 — 1계정 end-to-end (구매~주문완료~뷰티포인트 적립). ⚠️실돈.

2026-06-01 #5(yr5326) 라이브 단계별 보정으로 **풀검증** (주문 2026-06-01-F17773, 524,795원,
뷰티포인트 5,778P). LOTTE_HOMESHOPPING_STEPMAP.md 정본 + 자동메모리 lotte-homeshopping-live-calib.
앱: com.omnitel.android.lottewebview (WebView 기반 → OCR 중심). 카드앱 구간(LOCA com.lcacApp)은
hmall pay_lotte 와 동일 → lotte_card.json flow_payment[14:22] 재사용.

범위: **구매~주문완료(A~D) + 뷰티포인트(E) + 카드등록 dismiss(F) + 구매사은 적립금(G)**.
  - D 카드: ★당일 할인카드 **자동감지**(청구할인 배너 최고% — 하드코딩 제거, 2026-06-02). 롯데=LOCA / 삼성=PAYCO+ARS.
    삼성/PAYCO = 2026-06-02 #10 라이브 풀검증(주문 F71650). PAYCO PIN=payco_pin6 137601(hmall 재사용).
    ARS 본인인증 = handle_ars_call 자동응답(받기→10s→키패드→'1' 반복). ⚠️통화화면 앵커 라이브검증 필요.
  - E: nested-scroll 동의(_box_scroll + 동의함 fresh-OCR 탭) 해결. F: 삼성 경로엔 안 뜸 → 보통 no-op.
  - G: ★상품번호(숫자) 검색으로 한글입력 한계 우회(#10 검증). goods_no 없으면 최근검색어 폴백.

검증된 결제설정 순서 (★쿠폰이 적립금/L.POINT 리셋→포인트는 쿠폰 뒤 / ★카드가 현금영수증 리셋→cash 는 카드 뒤):
  주소 → 할인쿠폰(10%×n) → 플러스쿠폰(최고%, 비활성제외) → 적립금/L.POINT(전액) → 카드 → 현금영수증 → 동의 → 결제하기

CLI:
    python3 -m phone_auto.lotte_homeshopping_buy 5          # #5 라이브 (로그아웃→#5 로그인→구매)
    python3 -m phone_auto.lotte_homeshopping_buy 5 6 7      # 연속
    python3 -m phone_auto.lotte_homeshopping_buy now        # 현재화면 OCR (디버그)
"""
from __future__ import annotations
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from PIL import Image      # 쿠폰 활성/비활성 픽셀밝기 판별용

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from phone_auto import hmall_webview as hw
# 공통 헬퍼 재사용 (OCR/tap/대기 — 앱 비종속). cap/_adb 는 ANDROID_SERIAL 고정 bare adb.
from phone_auto.hmall_hyundai_buy import (
    cap, _adb, ocr_find, ocr_tap, wait_text, screen_has, _resolve_serial, _wait_app,
    CARD_ALIASES, CARD_GRID_NAME,
)
from phone_auto.flow_runner import _ocr_texts, FlowRunner
# PATH(bare adb)는 hmall_hyundai_buy import 시 이미 설정됨.

PKG = "com.omnitel.android.lottewebview"
LOTTE_CARD_FLOW = ROOT / "phone_auto" / "coords" / "apps" / "lotte_card.json"
ACCOUNTS_FILE = ROOT / "lotte.json"

# (참조) 로카페이 간편번호 = 137601 — 실입력은 lotte_card.json flow_payment[19] 의 dump 셔플로 처리.
BIZ_NO = ("507", "18", "15504")       # 현금영수증 지출증빙 사업자번호 (3칸)
ADDR_KEY = "203호"                    # 배송지 (화곡동 890 / 203호)

NAV_MY = (755, 2225)                  # 하단 네비 '마이' (1080x2400, OCR 실측)
NAV_HOME = (108, 2225)                # 하단 네비 '홈'


def _accounts() -> list[dict]:
    return json.loads(ACCOUNTS_FILE.read_text(encoding="utf-8"))["accounts"]


def _input_text(text: str) -> None:
    """adb input text (argv 전달 → 셸 미경유, @ ! 등 특수문자 안전). 검증: 6/1 _toss type."""
    serial = hw._serial()
    subprocess.run([hw.ADB, "-s", serial, "shell", "input", "text", text])
    time.sleep(0.5)


def _clear_field(n: int = 32) -> None:
    """포커스된 입력칸 내용 비우기 (오염 가드): 끝으로 이동 후 backspace n회. 빈칸이면 무해.
    로그인 슬라이드로 ID가 PW칸에 잘못 들어가도 PW 입력 전 제거 (6/2 사용자 지적 — 1초 대기 보완)."""
    serial = hw._serial()
    subprocess.run([hw.ADB, "-s", serial, "shell", "input", "keyevent", "123"])    # MOVE_END
    subprocess.run([hw.ADB, "-s", serial, "shell", "input", "keyevent", *(["67"] * n)])  # DEL ×n


def _all_text() -> str:
    return " ".join(it["text"] for it in _ocr_texts(cap()))


def _find(text: str, contains: bool = True, exact: bool = False):
    """현재 화면에서 text OCR item 반환 (없으면 None). exact=공백제거 완전일치."""
    for it in _ocr_texts(cap()):
        t = it["text"]
        if exact and t.strip() == text:
            return it
        if not exact and (text in t if contains else t == text):
            return it
    return None


def _tap_fresh(text: str, dx: int = 0, dy: int = 0, exact: bool = False,
               retries: int = 3, settle: float = 1.0) -> bool:
    """⚠️라이브 핵심: 탭 직전 OCR로 위치를 새로 읽어 (cx+dx, cy+dy) 탭.
    스크롤/리플로우로 좌표가 변하는 WebView 라디오·버튼에 stale 좌표 탭 방지(뷰티포인트 동의함 교훈)."""
    for _ in range(retries):
        it = _find(text, exact=exact)
        if it:
            _adb().tap(it["cx"] + dx, it["cy"] + dy)
            time.sleep(settle)
            return True
        time.sleep(0.6)
    return False


def _box_scroll(times: int = 1) -> None:
    """내부 스크롤 div(뷰티포인트 동의 안내 박스 등)를 박스만 스크롤. 페이지 안 밀림.
    contained swipe (540,1620→540,1500, 600ms) — #4 'adb swipe가 페이지 스크롤' 난제 해법(6/1 검증)."""
    serial = hw._serial()
    for _ in range(times):
        subprocess.run([hw.ADB, "-s", serial, "shell", "input", "swipe",
                        "540", "1620", "540", "1500", "600"])
        time.sleep(1.0)


# ──────────────────────────── A. 앱 초기화 + 로그인 ────────────────────────────

def reset_lotte_app() -> None:
    """force-stop + 콜드런치 + 안정화. (hmall reset_to_main 의 lotte 판)."""
    serial = hw._serial()
    subprocess.run([hw.ADB, "-s", serial, "shell", "am", "force-stop", PKG])
    time.sleep(1.0)
    subprocess.run([hw.ADB, "-s", serial, "shell", "monkey", "-p", PKG,
                    "-c", "android.intent.category.LAUNCHER", "1"],
                   capture_output=True)
    time.sleep(8.0)


# 비번변경 캠페인 흰버튼(좌) — ⚠️ 검정 '지금 변경하기'(우) 절대 금지
POPUP_DISMISS = ("30일간 보이지 않기", "나중에 할게요", "오늘 하루", "보지 않기", "다음에", "닫기")
POPUP_BLOCK = ("지금 변경하기",)


def dismiss_popups(max_iter: int = 5) -> int:
    """로그인 직후/주문완료 등 팝업 닫기 (비번변경/리뷰/광고). dismiss 텍스트만 탭, BLOCK 은 절대 탭 안 함."""
    closed = 0
    for _ in range(max_iter):
        its = _ocr_texts(cap())
        hit = None
        for key in POPUP_DISMISS:
            hit = next((it for it in its if key in it["text"]
                        and not any(b in it["text"] for b in POPUP_BLOCK)), None)
            if hit:
                break
        if not hit:
            break
        _adb().tap(hit["cx"], hit["cy"])
        print(f"   [popup] '{hit['text']}' @({hit['cx']},{hit['cy']}) 닫기", flush=True)
        closed += 1
        time.sleep(1.0)
    return closed


def logout() -> bool:
    """마이 → 설정(우상단 톱니) → 로그아웃. (계정 전환 전 필수.)"""
    _adb().tap(*NAV_MY); time.sleep(2.0)
    dismiss_popups(2)
    # ⚠️ 설정 톱니 = 헤더 아님! "고객님 반가워요!" 인사말 줄 우측(벨+톱니). (1010,150)은 장바구니라 누르면 안 됨.
    _adb().tap(1010, 336); time.sleep(1.5)   # 톱니 (6/1 실측)
    if not screen_has("로그아웃"):           # 설정화면 진입 검증
        _adb().tap(1010, 336); time.sleep(1.5)
    # 설정화면 상단 계정옆 "로그아웃"
    if not ocr_tap("로그아웃", contains=True, retries=4):
        print("   ✗ 로그아웃 버튼 미발견", flush=True)
        return False
    time.sleep(1.0)
    # 확인 팝업 ("로그아웃 하시겠습니까?") — 안 뜨면 no-op
    ocr_tap("확인", retries=2)
    time.sleep(2.5)
    return True


def login(idx: int) -> dict:
    """마이 → '아이디/비밀번호로 계속하기' → ID/PW 입력 → 로그인. idx=1-based."""
    acc = _accounts()[idx - 1]
    out = {"idx": idx, "id": acc["id"]}
    _adb().tap(*NAV_MY); time.sleep(1.5)
    dismiss_popups(2)
    # ★마이 탭 후 로그인옵션이 스르륵 슬라이드(6/2 사용자 지적) → 1초 정착 후 fresh OCR 탭 (슬라이드 중 오탭 방지).
    time.sleep(1.0)
    if not ocr_tap("아이디/비밀번호로 계속하기", contains=True, retries=4):
        if not ocr_tap("계속하기", contains=True, retries=2):
            out["err"] = "로그인 진입 버튼 미발견"; return out
    if not wait_text("아이디", timeout=10):
        out["err"] = "로그인 폼 미도달"; return out
    # ★레이아웃 정착 대기 (6/2 사용자 지적 버그): 폼 등장 후 화면이 스르륵 이동 → 정착 전 탭하면
    #   ID칸 좌표가 PW칸으로 밀려 ID가 PW칸에 입력(diinamic 사례). **1초 정착 + 매 시도 fresh OCR + 클리어 가드**로 해소.
    time.sleep(1.0)
    # ID 칸 = placeholder "L.POINT 통합회원 아이디 또는 이메일". 헤더/찾기 제외 + 400<cy<700.
    # ★매 시도 fresh OCR + 클리어(혹시 잘못 들어간 값 제거) → 입력. 검증 = placeholder('통합회원') 사라짐.
    for _ in range(3):
        id_it = next((it for it in _ocr_texts(cap())
                      if ("아이디" in it["text"] or "이메일" in it["text"])
                      and not any(x in it["text"] for x in ("찾기", "회원가입", "로그인"))
                      and 400 < it["cy"] < 700), None)
        if not id_it:                     # placeholder 사라짐 = 이미 입력됨
            break
        _adb().tap(id_it["cx"], id_it["cy"]); time.sleep(0.8)
        _clear_field(); time.sleep(0.3)
        _input_text(acc["id"]); time.sleep(0.6)
        if not screen_has("통합회원"):     # ID 칸 placeholder 사라짐 = 입력됨
            break
    # PW 칸. 헤더/표기/찾기 제외 + 400<cy<850. ★클리어 가드: 혹시 ID가 PW칸에 잘못 들어가 있어도 제거 후 비번 입력.
    pws = [it for it in _ocr_texts(cap()) if "비밀번호" in it["text"]
           and not any(x in it["text"] for x in ("표기", "찾기", "로그인"))
           and 400 < it["cy"] < 850]
    pws.sort(key=lambda it: it["cx"])
    px, py = (pws[0]["cx"], pws[0]["cy"]) if pws else (348, 720)
    _adb().tap(px, py); time.sleep(0.8)
    _clear_field(); time.sleep(0.3)        # ★오염 가드 (PW칸에 ID 잘못 들어가 있어도 제거)
    _input_text(acc["pw"]); time.sleep(0.5)
    # 로그인 버튼 (~540,889) = 키보드 위. ⚠️정확히 '로그인'만(간편/자동/안되시나요/찾기 제외) + 키보드 위(cy<1100).
    btn = next((it for it in _ocr_texts(cap())
                if it["text"].strip() == "로그인" and it["cy"] < 1100), None)
    bx, by = (btn["cx"], btn["cy"]) if btn else (540, 889)        # 6/1 #5 실측 폴백
    _adb().tap(bx, by); time.sleep(3.0)
    dismiss_popups()
    # 로그인 검증: 마이/홈 진입 (로그인 폼 사라짐)
    if screen_has("아이디") and screen_has("비밀번호"):
        out["err"] = "로그인 실패(폼 잔존 — 비번 오류 가능)"; return out
    out["ok"] = True
    return out


# ──────────────────────────── B. 장바구니 ────────────────────────────

def goto_cart_select_all() -> dict:
    """우상단 장바구니 → 전체선택 체크박스 → 주문하기. 주문서('결제하기' 등장) 도달까지."""
    out = {}
    # 홈으로 이동 후 우상단 장바구니 아이콘 (홈에선 OCR 미검출 → 실측 좌표)
    _adb().tap(*NAV_HOME); time.sleep(2.0)
    dismiss_popups(2)
    _adb().tap(960, 150); time.sleep(2.5)    # 카트 아이콘 (6/1 실측)
    if not (wait_text("주문하기", timeout=8) or screen_has("장바구니")):
        out["err"] = "장바구니 미도달"; return out
    # 전체선택: 헤더 "일반 (a/b)" 좌측 체크박스 (~70,cy). ⚠️체크박스는 토글 → 이미 전체선택(a==b>0)이면
    # 탭하면 해제됨 → a==b>0 일 때만 통과, 아니면 n/n 될 때까지 토글(최대 3회). (#6 '0/2' 오해 방지)
    def _gen():
        return next((it for it in _ocr_texts(cap()) if it["text"].strip().startswith("일반")
                     and "/" in it["text"]), None)
    for _ in range(3):
        g = _gen()
        if not g:
            _adb().tap(70, 303); time.sleep(1.0); continue   # 헤더 못 읽으면 기본좌표 1회
        m = re.search(r"\((\d+)\s*/\s*(\d+)\)", g["text"])
        out["selected"] = g["text"]
        if m and m.group(1) == m.group(2) and m.group(1) != "0":
            break                                            # 이미 전체선택 → 통과(탭 X)
        _adb().tap(70, g["cy"]); time.sleep(1.2)
    # 주문하기 (1회 탭 — 결제하기 등장으로 전환검증)
    if not ocr_tap("주문하기", contains=True, retries=4):
        out["err"] = "주문하기 탭 실패"; return out
    if not wait_text("결제하기", timeout=15):
        out["err"] = "주문서 미도달"; return out
    out["ok"] = True
    return out


# ──────────────────────────── C. 결제설정 ────────────────────────────

def _coupon_change_btn(sec):
    """쿠폰 섹션의 '변경 >' (⚠️'배송방법 변경'/픽업 제외, 섹션 아래 우측)."""
    chgs = [it for it in _ocr_texts(cap()) if "변경" in it["text"]
            and "배송방법" not in it["text"] and "픽업" not in it["text"]
            and it["cy"] > sec["cy"] - 60]
    return max(chgs, key=lambda it: it["cx"]) if chgs else None


def _submodal_items(shot=None):
    """열린 쿠폰 dropdown(하위모달 '할인선택') 안의 OCR 항목만 (하위 '할인선택'~'닫기' 사이).
    메인모달의 이미 적용된 상품 행을 오탭하지 않기 위함. shot=같은 스크린샷 재사용(밝기판별과 cy 정합)."""
    its = _ocr_texts(shot or cap())
    heads = [it for it in its if "할인선택" in it["text"]]
    close = next((it for it in its if it["text"].strip() == "닫기"), None)
    if not heads or not close:
        return []
    top = max(h["cy"] for h in heads)            # 아래쪽 '할인선택' = 하위모달 헤더
    return [it for it in its if top < it["cy"] < close["cy"]]


def _coupon_enabled(gimg, cy: int, x0: int = 110, x1: int = 720, band: int = 16, thr: int = 120) -> bool:
    """드롭다운 쿠폰옵션의 활성 여부 = 텍스트밴드에 '진한 글자획'(어두운 픽셀) 존재 여부.
    ★같은 쿠폰을 다른 제품이 이미 점유하면 회색(비활성) 처리됨 → OCR 텍스트는 동일, 글자색만 다름.
    실측(2026-06-02 #11 라이브): 활성 글자 최소밝기≈20, 비활성(회색)≈154 → 임계 120 분리.
    gimg=PIL Image(L모드, 옵션 OCR과 같은 스크린샷). 라디오(x<x0) 제외한 텍스트영역에 thr보다 어두운 픽셀 있으면 활성."""
    px = gimg.load(); W, H = gimg.size
    x1 = min(x1, W)
    for y in range(max(0, cy - band), min(H, cy + band + 1)):
        for x in range(x0, x1):
            if px[x, y] < thr:
                return True
    return False


def _scroll_to(text: str, contains: bool = True, max_scroll: int = 8, down: bool = True):
    """text 가 보일 때까지 스크롤하며 탐색. 찾으면 OCR item 반환, 못 찾으면 None."""
    for _ in range(max_scroll):
        it = ocr_find(text, contains=contains)
        if it:
            return it
        if down:
            _adb().swipe(540, 1700, 540, 800, 400)
        else:
            _adb().swipe(540, 800, 540, 1700, 400)
        time.sleep(0.8)
    return None


def set_discount_coupons() -> dict:
    """할인쿠폰: 섹션 라디오 → '변경' → 상품별 dropdown → 모달서 '10% 할인' 탭 → 선택완료.
    상품 수 가변 → dropdown 반복. (★쿠폰이 포인트 리셋 → 반드시 포인트보다 먼저.)"""
    out = {"applied": 0}
    sec = _scroll_to("할인쿠폰")
    if not sec:
        out["err"] = "할인쿠폰 섹션 미발견"; return out
    _adb().tap(sec["cx"], sec["cy"]); time.sleep(1.0)        # 섹션 라디오 활성
    chg = _coupon_change_btn(sec)
    if not chg:
        out["err"] = "할인쿠폰 변경 버튼 미발견"; return out
    _adb().tap(chg["cx"], chg["cy"]); time.sleep(1.8)        # 모달 '할인선택' 진입
    # 상품별 dropdown 반복: 각 상품 '쿠폰을 선택해 주세요' chevron(~985) → 하위모달 '10% 할인' 탭(자동적용+복귀)
    for _ in range(8):
        ph = next((it for it in _ocr_texts(cap()) if "선택해" in it["text"] and "주세요" in it["text"]), None)
        if not ph:
            break
        _adb().tap(985, ph["cy"]); time.sleep(1.5)           # dropdown 열기
        # ★비활성(회색) 쿠폰 제외 — 같은 쿠폰을 다른 제품이 이미 점유하면 회색 처리됨(밝기로 판별).
        shot = cap(); gimg = Image.open(shot).convert("L")
        cands = [it for it in _submodal_items(shot)
                 if "10%" in it["text"] and "할인" in it["text"] and _coupon_enabled(gimg, it["cy"])]
        if not cands:
            ocr_tap("닫기", retries=1); break
        opt = min(cands, key=lambda it: it["cy"])            # 활성 중 가장 위
        _adb().tap(opt["cx"], opt["cy"]); time.sleep(1.3)
        out["applied"] += 1
    ocr_tap("선택완료", retries=2) or ocr_tap("적용", retries=1) or ocr_tap("확인", retries=1)
    time.sleep(1.5)
    out["ok"] = True
    return out


def set_plus_coupons() -> dict:
    """플러스쿠폰: 활성(받은) 상품만, 최고 할인율 선택. 받은 게 없으면 패스."""
    out = {"applied": 0, "pcts": []}
    sec = _scroll_to("플러스쿠폰")
    if not sec:
        out["skip"] = "플러스쿠폰 섹션 없음"; out["ok"] = True; return out
    _adb().tap(sec["cx"], sec["cy"]); time.sleep(1.0)
    chg = _coupon_change_btn(sec)
    if not chg:
        out["skip"] = "플러스쿠폰 변경 없음(받은 쿠폰 X)"; out["ok"] = True; return out
    _adb().tap(chg["cx"], chg["cy"]); time.sleep(1.8)
    # 상품별 dropdown: chevron → 하위모달 옵션 '[백화점]...쿠폰 N%' 중 최고% 탭.
    # ⚠️옵션 텍스트엔 '할인' 없음('...쿠폰 N%') → '쿠폰'+'%' 로 매칭(옛 '할인' 필터 버그 수정).
    for _ in range(8):
        ph = next((it for it in _ocr_texts(cap()) if "선택해" in it["text"] and "주세요" in it["text"]), None)
        if not ph:
            break
        _adb().tap(985, ph["cy"]); time.sleep(1.5)
        # ★비활성(회색) 쿠폰 제외 후 활성 중 최고% 선택 (2026-06-02 #11: 같은 14%가 비활성+활성 2장일 때
        #   OCR 텍스트가 동일해 비활성 14%를 탭→적용실패→제품 미적용→'선택완료'서 '할인혜택 초기화' 팝업).
        shot = cap(); gimg = Image.open(shot).convert("L")
        pcts = []
        for it in _submodal_items(shot):
            m = re.search(r"(\d+)\s*%", it["text"])
            if m and "쿠폰" in it["text"] and _coupon_enabled(gimg, it["cy"]):
                pcts.append((int(m.group(1)), it))
        if not pcts:
            ocr_tap("닫기", retries=1); break
        pcts.sort(key=lambda x: (-x[0], x[1]["cy"]))      # 활성 중 최고% → 같은%면 최상단
        best = pcts[0]
        _adb().tap(best[1]["cx"], best[1]["cy"]); time.sleep(1.3)
        out["applied"] += 1; out["pcts"].append(best[0])
    ocr_tap("선택완료", retries=2) or ocr_tap("적용", retries=1) or ocr_tap("확인", retries=1)
    time.sleep(1.5)
    out["ok"] = True
    return out


def use_all_points() -> dict:
    """적립금 + L.POINT 모두 '전액사용'. ★L.POINT 무조건 사용(사용자 지시 2026-06-01: #7서 L.POINT 누락).
    라벨 스크롤 의존(옛 버그: '적립혜택 L.POINT' 오매칭으로 L.POINT 사용 누락) 대신,
    포인트사용 섹션의 **모든 '전액사용' 버튼을 탭**(적립금·L.POINT). 탭하면 '적용취소'로 바뀌어 멱등."""
    out = {"used": 0}
    # 포인트사용 섹션('전액사용' 버튼)까지 스크롤
    for _ in range(8):
        if any("전액사용" in it["text"].replace(" ", "") for it in _ocr_texts(cap())):
            break
        _adb().swipe(540, 1500, 540, 800, 450); time.sleep(0.8)
    # 보이는 '전액사용' 모두 탭 (위→아래). 탭 후 '적용취소'가 되어 다음 루프엔 남은 것(L.POINT)만 매칭.
    for _ in range(5):
        btns = [it for it in _ocr_texts(cap()) if "전액사용" in it["text"].replace(" ", "")]
        if not btns:
            break
        btns.sort(key=lambda it: it["cy"])
        _adb().tap(btns[0]["cx"], btns[0]["cy"]); time.sleep(1.2)
        out["used"] += 1
    out["ok"] = True
    return out


def set_cash_receipt() -> dict:
    """[조건부] 현금영수증 지출증빙 사업자번호 507/18/15504. L.POINT 사용 시 활성. 비활성이면 skip."""
    out = {}
    # ⚠️ 현금영수증 = 결제수단 섹션 안에 있음 (별 섹션 아님). L.POINT 사용 시 활성.
    sec = _scroll_to("현금영수증", max_scroll=8)
    if not sec:
        out["skip"] = "현금영수증 섹션 없음(L.POINT 0)"; out["ok"] = True; return out
    if not ocr_tap("지출증빙", contains=True, retries=3):
        out["skip"] = "지출증빙 비활성"; out["ok"] = True; return out
    time.sleep(1.5)
    # ★칸1 포커스 = 키패드 등장까지 보장 (6/2 #10: 단일 탭으론 포커스 실패 → entered=False + '입력해주세요' 팝업).
    #   '사업자 등록번호' 라벨 아래 빈칸 3개(테두리만, OCR 미검출). 후보 오프셋 재시도 + 팝업이 오히려 포커스 유발.
    def _keypad_up() -> bool:
        its = _ocr_texts(cap())
        return any(it["text"].strip() in ("다음", "0") for it in its) or \
               any(re.fullmatch(r"[1-9]", it["text"].strip()) for it in its)
    # ★카드 뒤 위치에선 지출증빙이 화면 하단 → 사업자 라벨이 뷰 밖. 먼저 스크롤로 노출 (2026-06-02 #11 재정렬).
    if not _scroll_to("사업자", max_scroll=4):
        out["err"] = "사업자 등록번호 라벨 미발견"; return out
    focused = False
    for dy in (122, 95, 150):
        lab = ocr_find("사업자", contains=True) or ocr_find("등록번호", contains=True)
        if not lab:
            out["err"] = "사업자 등록번호 라벨 사라짐"; return out
        _adb().tap(lab["cx"] - 14, lab["cy"] + dy); time.sleep(1.2)   # 칸1 포커스 (탭 시 자동스크롤)
        if screen_has("입력해주세요"):        # 빈칸 확인 팝업 → '확인'(이게 필드 포커스+키패드 띄움)
            ocr_tap("확인", retries=2); time.sleep(1.0)
        if _keypad_up():
            focused = True; break
    if not focused:
        out["err"] = "사업자번호 칸 포커스 실패(키패드 미등장)"; return out
    # ★칸1=3자리→자동 advance→칸2=2자리→자동 advance→칸3=5자리. 시스템 키패드라 adb input text 통함.
    for part in BIZ_NO:                       # ("507","18","15504")
        _input_text(part); time.sleep(0.6)
    # 키보드 닫기 (BACK) + 입력 검증
    serial = hw._serial()
    subprocess.run([hw.ADB, "-s", serial, "shell", "input", "keyevent", "4"]); time.sleep(1.0)
    t = _all_text()
    out["entered"] = all(p in t for p in BIZ_NO)
    out["ok"] = True
    return out


def set_address() -> dict:
    """배송지 확인/변경. 선택된 배송지가 이미 ADDR_KEY(203호)면 skip(#5). 아니면(#6=경남 창녕군 등)
    배송정보 펼치기(chevron) → '변경 >' → 주소목록서 203호 탭(자동선택+복귀).
    ⚠️배송정보는 주문서 최상단 → 먼저 스크롤업(_scroll_to 는 아래로만 탐색하므로 직접 위로)."""
    out = {}
    # 주문서 최상단(배송정보)으로 스크롤업
    for _ in range(6):
        if screen_has(ADDR_KEY) or screen_has("배송정보") or screen_has("배송지"):
            break
        _adb().swipe(540, 700, 540, 1700, 400); time.sleep(0.6)
    # 접힌 상태서 선택된 주소가 이미 203호면 변경 불필요
    if screen_has(ADDR_KEY):
        out["skip"] = f"기본배송지 이미 '{ADDR_KEY}'"; out["ok"] = True; return out
    # 배송정보 펼치기 (우측 chevron) → 주소 '변경 >' 노출 (#6 검증: 접힘 상태선 변경버튼 숨김)
    bi = _find("배송정보", contains=True)
    if bi:
        _adb().tap(1000, bi["cy"]); time.sleep(1.5)
    # 주소 '변경 >' (배송방법/픽업 제외, 우측 최대 cx)
    chgs = [it for it in _ocr_texts(cap()) if "변경" in it["text"]
            and "배송방법" not in it["text"] and "픽업" not in it["text"]]
    if not chgs:
        out["err"] = "주소 변경 버튼 미발견"; return out
    chg = max(chgs, key=lambda it: it["cx"])
    _adb().tap(chg["cx"], chg["cy"]); time.sleep(2.0)
    # 주소목록서 '203호' 포함 주소 탭 (탭=자동선택+복귀, 별도 확인버튼 없음)
    tgt = _scroll_to(ADDR_KEY, max_scroll=6)
    if not tgt:
        out["err"] = f"'{ADDR_KEY}' 주소 미발견"; return out
    _adb().tap(tgt["cx"], tgt["cy"]); time.sleep(2.0)
    ocr_tap("선택", retries=1) or ocr_tap("확인", retries=1) or ocr_tap("적용", retries=1)
    time.sleep(1.5)
    out["changed"] = screen_has(ADDR_KEY)
    out["ok"] = True
    return out


def detect_card_lotte() -> dict:
    """주문서 '청구할인' 배너에서 당일 카드별 할인율을 읽어 **최고% 카드**를 반환 (★하드코딩 금지).
    배너 형식: '<카드>카드(신용카드/L.PAY) N% 할인'. 청구할인이라 즉시금액엔 미반영 → 결제수단 아래 배너로만 식별.
    6/2 #10 라이브검증: [('삼성',7),('국민'→KB,5)] → 삼성. N은 매일 변동(숫자 하드코딩 금지).
    반환 {ok, card(키), pct, all:[(키,pct)...]}. 미발견 시 ok=False."""
    pat = re.compile(r"([가-힣A-Z]+)카드\(신용카드/L\.PAY\)\s*(\d+)\s*%\s*할인")

    def _scan():
        found = pat.findall(_all_text())
        cands = []
        for name, pct in found:
            key = CARD_ALIASES.get(name) or next(
                (v for k, v in CARD_ALIASES.items() if k in name or name in k), None)
            if key:
                cands.append((key, int(pct)))
        if cands:
            cands.sort(key=lambda x: x[1], reverse=True)       # 최고% = 당일 할인카드
            return {"ok": True, "card": cands[0][0], "pct": cands[0][1], "all": cands}
        return None

    # 청구할인 배너는 결제수단 섹션 안(현금영수증 아래). 현재 위치가 위/아래 어디든 찾도록 양방향 스캔.
    for _ in range(7):                                          # ↓ 아래로 스캔
        r = _scan()
        if r:
            return r
        _adb().swipe(540, 1700, 540, 800, 400); time.sleep(0.7)
    for _ in range(9):                                          # ↑ 못 찾으면 위로 스캔
        _adb().swipe(540, 800, 540, 1700, 400); time.sleep(0.7)
        r = _scan()
        if r:
            return r
    return {"ok": False, "err": "청구할인 배너 미발견"}


def select_card_lotte(day: str | None = None) -> dict:
    """당일 할인카드를 **자동감지(detect_card_lotte)** 후 '신용카드' 결제수단으로 선택. (당일카드 하드코딩 없음)
    ★2026-06-02 #11 라이브 매핑: 롯데 결제수단 UI가 그리드로 변경 → 검증된 **단일 루트(카드 무관, 목록 카드명만 다름)**:
      detect → '다른 결제수단'(라디오, 결제수단 그리드 펼침) → '신용카드'(그리드 버튼)
      → '카드 선택' 드롭다운(행 우측 chevron) → 카드목록 팝업서 **당일카드** 탭
      (목록: 롯데/신한/비씨/현대/삼성/KB국민/우리/하나/NH농협카드). 롯데·삼성 등 어느 카드든 동일 루트.
    ⚠️ 카드 선택이 **현금영수증(지출증빙)을 리셋** → 상위(buy_one)에서 cash 는 반드시 card 뒤에 호출.
    day 지정 시 감지 생략(상위에서 당일카드 주입 가능). 반환 {ok, card, pct, via}."""
    out = {}
    det = detect_card_lotte() if day is None else {"ok": True, "card": day, "pct": None}
    if not det.get("ok"):
        out["err"] = det.get("err", "당일카드 감지 실패"); return out
    card = det["card"]; out["card"] = card; out["pct"] = det.get("pct")
    target = CARD_GRID_NAME.get(card, card + "카드")        # 목록 카드명 ('삼성'→'삼성카드', 'KB'→'KB국민카드')
    # 1) 결제수단 영역 — '다른 결제수단' 라디오 (detect 후 위치 불정 → 아래/위 양방향 스캔) 선택
    db = None
    for _ in range(6):                                      # 아래로
        db = ocr_find("다른 결제수단", contains=True)
        if db:
            break
        _adb().swipe(540, 1700, 540, 800, 400); time.sleep(0.6)
    if not db:
        for _ in range(8):                                 # 못 찾으면 위로
            _adb().swipe(540, 800, 540, 1700, 400); time.sleep(0.6)
            db = ocr_find("다른 결제수단", contains=True)
            if db:
                break
    if not db:
        out["err"] = "'다른 결제수단' 미발견"; return out
    _adb().tap(db["cx"], db["cy"]); time.sleep(1.5)         # 라디오 선택(그리드 활성, 라디오라 멱등)
    # 2) '신용카드' 그리드 버튼 → '카드 선택'/'할부 선택' 드롭다운 노출
    if not ocr_tap("신용카드", retries=4):
        out["err"] = "'신용카드' 버튼 탭 실패"; return out
    time.sleep(1.5)
    # 3) '카드 선택' 드롭다운(행 우측 chevron x≈987) → 카드목록 팝업.
    #    ★exact 매칭 필수 — contains 면 안내문 '...비씨카드 선택 시...'의 '카드 선택' 부분문자열을 오매칭(2026-06-02 #11 버그).
    lab = _scroll_to("카드 선택", contains=False, max_scroll=4)
    if not lab:
        out["err"] = "'카드 선택' 드롭다운 미발견"; return out
    _adb().tap(987, lab["cy"]); time.sleep(1.8)
    # 4) 카드목록 팝업서 당일카드 탭 (OCR 라디오 글리프 '_'/'•' 접두 대비 contains 매칭)
    if not ocr_tap(target, contains=True, retries=4):
        out["err"] = f"카드목록 '{target}' 선택 실패"; return out
    time.sleep(2.0)
    out["via"] = "다른결제수단>신용카드>카드선택"; out["ok"] = True
    return out


def agree_required() -> bool:
    """필수 동의 체크박스 탭 ('주문 내역 확인 동의(필수)' 텍스트 좌측). ⚠️동의는 결제수단 아래 → 안 보이면 스크롤."""
    time.sleep(1.0)
    def _ag():
        return next((it for it in _ocr_texts(cap())
                     if "동의" in it["text"] and ("필수" in it["text"] or "전체" in it["text"])), None)
    ag = None
    for _ in range(6):                       # 동의가 아래에 있을 수 있어 스크롤하며 탐색
        ag = _ag()
        if ag:
            break
        _adb().swipe(540, 1500, 540, 800, 450); time.sleep(0.8)
    if not ag:
        return False
    if "✓" in ag["text"] or ag["text"].lstrip().startswith("v"):
        return True
    _adb().tap(max(60, ag["cx"] - 210), ag["cy"]); time.sleep(1.0)   # 텍스트 좌측 체크박스
    ag2 = _ag()
    return bool(ag2 and ("✓" in ag2["text"] or "체크" in ag2["text"])) or True


# ──────────────────────────── D. 결제 (LOCA 재사용) ────────────────────────────

def pay_loca() -> dict:
    """결제하기(원결제하기) → 로카페이(앱카드) 결제하기 → LOCA앱 137601 → 확인 → 롯데 복귀 주문완료.
    LOCA 구간(com.lcacApp)은 lotte_card.json flow[14:22] 재사용. ⚠️실 결제."""
    out = {"step": "order_sheet"}
    # 0) '다음에도 사용할까요?' 팝업 대비
    if screen_has("다음에도") or screen_has("사용할까요"):
        ocr_tap("사용할게요", contains=True, retries=2)
    # 1) 원 결제하기 (WebView OCR — 'NNN원 결제하기')
    if not ocr_tap("결제하기", contains=True):
        out["err"] = "원결제하기 실패"; return out
    out["step"] = "loca_method"
    # 2) 롯데 결제방식 화면(로카페이 앱카드 추천) → '결제하기'
    if not wait_text("로카페이", timeout=12):
        out["err"] = "로카페이 결제방식 화면 미도달"; return out
    if screen_has("다음에도") or screen_has("사용할까요"):
        ocr_tap("사용할게요", contains=True, retries=2)
    if not ocr_tap("결제하기"):
        out["err"] = "로카페이(앱카드) 결제하기 실패"; return out
    out["step"] = "loca_app"
    # 3) LOCA앱(com.lcacApp) → 결제하기 → 간편번호 dump 137601 → 확인 (flow[14:22] 재사용)
    flow = json.loads(LOTTE_CARD_FLOW.read_text(encoding="utf-8"))["flow_payment"]
    try:
        FlowRunner(use_camera=False).run(flow[14:22], {})
    except Exception as e:
        out["err"] = f"LOCA앱 결제 실패: {e}"; return out
    out["step"] = "return"
    # 4) 롯데 webview 복귀 + 주문완료 OCR
    if not _wait_app(PKG, timeout=20):
        out["err"] = "롯데앱 복귀 실패(결제 미확정 가능)"; return out
    time.sleep(3.0)
    end = time.time() + 20
    while time.time() < end:
        t = _all_text()
        if ("주문" in t and "완료" in t) or "주문번호" in t:
            out["ok"] = True
            mo = re.search(r"(\d{4}-\d{2}-\d{2}-[A-Z]\d+)", t) or re.search(r"(20\d{12,})", t)
            out["order"] = mo.group(1) if mo else None
            return out
        time.sleep(0.8)
    out["err"] = "주문완료 미확인(timeout)"
    return out


def handle_ars_call(intro_wait: float = 10.0, interval: float = 5.0, taps: int = 6) -> dict:
    """ARS 본인인증 전화 **자동응답** (2026-06-02 사용자 지정 방법). ⚠️통화화면 앵커는 첫 실제 ARS 라이브검증 필요.
    흐름: 인커밍 통화 '받기' → intro_wait초(ARS 안내) 대기 → 통화중 '키패드' 열기 → '1'(승인) interval초 간격 taps회.
    ★'1' = 매번 고정 승인버튼(셔플X). ARS 안내 타이밍을 몰라 여유롭게 반복 탭(승인 1회면 인증완료, 추가탭 무해).
    완료 후 통화 종료(ENDCALL) → 롯데앱 복귀."""
    out = {}
    serial = hw._serial()
    # 1) 인커밍 통화 대기 + 받기 ('받기/수락/통화' OCR, 없으면 KEYCODE_CALL=5)
    answered = False
    end = time.time() + 30
    while time.time() < end:
        its = _ocr_texts(cap())
        txt = " ".join(it["text"] for it in its)
        ans = next((it for it in its if it["text"].strip() in ("받기", "수락", "통화하기")), None)
        if ans:
            _adb().tap(ans["cx"], ans["cy"]); answered = True; break
        if any(k in txt for k in ("수신", "전화 왔", "롯데", "1599", "1600", "1577")):
            subprocess.run([hw.ADB, "-s", serial, "shell", "input", "keyevent", "5"])  # KEYCODE_CALL=받기
            answered = True; break
        time.sleep(1.0)
    out["answered"] = answered
    if not answered:
        out["err"] = "인커밍 통화 미감지/받기 실패(라이브검증 필요)"; return out
    # 2) 통화 연결 후 ARS 안내 — intro_wait 대기
    time.sleep(intro_wait)
    # 3) 통화중 '키패드' 열기 (OCR)
    kp = next((it for it in _ocr_texts(cap())
               for key in ("키패드", "Keypad", "다이얼패드", "keypad") if key in it["text"]), None)
    if kp:
        _adb().tap(kp["cx"], kp["cy"]); time.sleep(1.5)
    out["keypad_opened"] = bool(kp)
    # 4) '1'(승인) interval초 간격 taps회. 통화 다이얼패드 '1' OCR (없으면 좌상단 폴백 — ⚠️라이브검증).
    for _ in range(taps):
        one = next((it for it in _ocr_texts(cap()) if it["text"].strip() == "1"), None)
        if one:
            _adb().tap(one["cx"], one["cy"])
        else:
            _adb().tap(160, 1530)   # ⚠️ 다이얼패드 '1' 추정 폴백 — 첫 라이브 ARS 때 좌표 확정 필요
        time.sleep(interval)
    # 5) 통화 종료 → 롯데앱 복귀 (서버측 인증완료면 주문완료 렌더)
    subprocess.run([hw.ADB, "-s", serial, "shell", "input", "keyevent", "6"])   # KEYCODE_ENDCALL
    time.sleep(2.0)
    out["ok"] = True
    return out


def pay_lotte_payco(card: str = "삼성") -> dict:
    """비롯데 당일카드(삼성 등) = **PAYCO 간편결제 경유** 결제. 2026-06-02 #10 라이브 풀검증(주문 2026-06-02-F71650).
    경로: (원)결제하기 → 삼성카드 SDK 모달 '간편결제(PAYCO,삼성페이)' → '간편 결제'(PAYCO 박스, '다시 선택하기') → PAYCO 박스
      → PAYCO 결제확인(금액·SAMSUNGCARD) → 결제하기 → 결제비밀번호(4x3 셔플) payco_pin6 137601(hmall 재사용, 2엔진실패→4엔진)
      → **ARS 본인인증(전화요청 → ⚠️사용자 통화 인증, 무인 불가)** → 롯데 주문완료.
    ⚠️ ARS 빈도 미확정(계정당1회 가설) — 안 뜨면 바로 주문완료 폴링. ⚠️실 결제."""
    out = {"step": "order_sheet", "card": card}
    if screen_has("다음에도") or screen_has("사용할까요"):
        ocr_tap("사용할게요", contains=True, retries=2)
    # 1) (원)결제하기 — 하단 'NNN원 결제하기'
    pay = next((it for it in _ocr_texts(cap()) if "결제하기" in it["text"] and it["cy"] > 2000), None)
    if pay:
        _adb().tap(pay["cx"], pay["cy"])
    elif not ocr_tap("결제하기", contains=True):
        out["err"] = "원결제하기 실패"; return out
    time.sleep(3.0)
    # 2) 삼성카드 SDK 모달 → 간편결제(PAYCO,삼성페이)
    out["step"] = "samsung_modal"
    if not wait_text("간편결제", timeout=12):
        out["err"] = "삼성 SDK 간편결제 모달 미도달"; return out
    opt = next((it for it in _ocr_texts(cap()) if "PAYCO" in it["text"] and "삼성페이" in it["text"]), None)
    if opt:
        _adb().tap(opt["cx"], opt["cy"])
    elif not ocr_tap("간편결제", contains=True):
        out["err"] = "간편결제(PAYCO) 선택 실패"; return out
    time.sleep(3.0)
    # 3) '간편 결제'(PAYCO 박스) — '다시 선택하기' 화면 → PAYCO 박스 탭
    out["step"] = "payco_box"
    if not wait_text("다시 선택하기", timeout=10):
        out["err"] = "PAYCO 박스 화면 미도달"; return out
    box = next((it for it in _ocr_texts(cap()) if it["text"].strip() == "PAYCO"), None) or \
          next((it for it in _ocr_texts(cap()) if "PAYCO" in it["text"]), None)
    if box:
        _adb().tap(box["cx"], box["cy"]); time.sleep(3.5)
    # 4) PAYCO 결제확인(금액·가맹점·SAMSUNGCARD) → 결제하기
    out["step"] = "payco_confirm"
    if not wait_text("PAYCO 간편결제", timeout=12):
        out["err"] = "PAYCO 결제확인 미도달"; return out
    m = re.search(r"([\d,]{4,})\s*원", _all_text())
    out["amount"] = m.group(1) if m else None
    print(f"   [PAYCO] {out['amount']}원 ({card}카드) 결제 진행", flush=True)
    if not ocr_tap("결제하기", contains=True):
        out["err"] = "PAYCO 결제하기 실패"; return out
    # 5) 결제 비밀번호(4x3 셔플) → payco_pin6 137601
    out["step"] = "pin"
    if not wait_text("결제 비밀번호", timeout=12):
        out["err"] = "PIN 화면 미도달"; return out
    try:
        FlowRunner(use_camera=False).run_action(
            {"action": "input_pin", "preset": "payco_pin6", "value": "137601",
             "tap_delay_sec": 0.6, "use_camera": False})
    except Exception as e:
        out["err"] = f"PIN 입력 실패: {e}"; return out
    # 6) ARS 본인인증(있으면) 또는 바로 주문완료. ARS=빨강 'ARS 인증 전화 요청' 탭 → ⚠️사용자 통화.
    out["step"] = "ars_or_done"
    ars_seen = False
    end0 = time.time() + 14
    while time.time() < end0:
        t = _all_text()
        if "주문번호" in t:
            break
        if "ARS" in t or "본인인증" in t:
            ars_seen = True; break
        time.sleep(1.0)
    if ars_seen:
        out["ars"] = True
        req = next((it for it in _ocr_texts(cap()) if "전화" in it["text"] and "요청" in it["text"]), None)
        _adb().tap(req["cx"], req["cy"]) if req else _adb().tap(540, 1730)   # OCR 분할 대비 좌표 폴백
        print("   ⚠️ ARS 전화 요청 → 자동응답(handle_ars_call: 받기→10s→키패드→'1' 반복)", flush=True)
        out["ars_call"] = handle_ars_call()
    # 7) 주문완료 폴링 (ARS 통화 인증 시간 고려해 길게)
    out["step"] = "order_complete"
    end = time.time() + 180
    while time.time() < end:
        t = _all_text()
        if "주문번호" in t or ("주문" in t and "완료" in t):
            mo = re.search(r"(\d{4}-\d{2}-\d{2}-[A-Z]\d+)", t)
            out["order"] = mo.group(1) if mo else None
            out["ok"] = True; return out
        time.sleep(1.5)
    out["err"] = "주문완료 미확인(timeout — ARS 미완료/지연 가능)"
    return out


# ──────────────────────────── E. 뷰티포인트 적립신청 (nested-scroll 동의) ────────────────────────────

def claim_beauty_point() -> dict:
    """주문완료 화면 '아모레퍼시픽 뷰티포인트 적립 신청' → 동의안내 박스(내부 스크롤 div) 끝까지 →
    '동의함' 라디오 → '적립신청' → 완료. ★설화수(아모레퍼시픽)만, 본 주문완료 화면에서만(now-or-never).
    6/1 #5 라이브 검증: contained _box_scroll + 동의함 fresh-OCR 탭(stale 1680 실패→정착 ~1587)."""
    out = {}
    if not (screen_has("뷰티포인트") or screen_has("적립신청")):
        out["skip"] = "뷰티포인트 적립 화면 아님(비설화수/미등장)"; out["ok"] = True; return out
    # 1) 적립신청 → '정보제공 동의를 하셔야' 팝업 → 확인 (동의 안내 박스 노출)
    if not _tap_fresh("적립신청", retries=3):
        out["err"] = "적립신청 버튼 미발견"; return out
    time.sleep(1.5)
    ocr_tap("확인", retries=2); time.sleep(1.5)
    # 2) 동의 안내 박스(내부 스크롤) 끝까지 → '동의함' 라디오 등장 (페이지 아닌 박스만 스크롤).
    #    ⚠️#6 실패: 1회차 박스스크롤 안 먹힐 때 있음 → 다회 + 안 보이면 적립신청 재탭으로 박스 재노출.
    for attempt in range(2):
        for _ in range(12):
            if _find("동의함", exact=True):
                break
            _box_scroll(1)
        if _find("동의함", exact=True):
            break
        # 박스 미노출 → 적립신청 재탭 + 확인 후 재시도
        _tap_fresh("적립신청", retries=2); time.sleep(1.2)
        ocr_tap("확인", retries=2); time.sleep(1.5)
    if not _find("동의함", exact=True):
        out["err"] = "동의함 라디오 미도달(박스 스크롤 실패)"; return out
    # 3) ⚠️동의함 fresh-OCR 라디오 원(텍스트 cx-100) 탭 + 적립신청으로 선택검증. 미선택(팝업)이면 재정착 후 재시도.
    ok = False
    for _ in range(4):
        time.sleep(1.0)
        it = _find("동의함", exact=True)
        if not it:
            break
        _adb().tap(it["cx"] - 100, it["cy"]); time.sleep(1.2)     # 라디오 원 (stale 금지 → 매번 fresh)
        if not _tap_fresh("적립신청", retries=2):
            break
        time.sleep(1.8)
        if screen_has("하셔야"):              # '동의를 하셔야' 팝업 = 동의함 미선택
            ocr_tap("확인", retries=2); time.sleep(1.0)
            _box_scroll(1)                    # 라디오 재정착(탭 가능 위치로)
            continue
        ok = True; break
    if not ok:
        out["err"] = "동의함 선택/적립신청 실패"; return out
    # 4) '뷰티포인트 적립신청이 완료되었습니다' 명시 확인 (★느슨한 "완료" 매칭은 false ✓ 위험 → 완료문구 명시)
    time.sleep(1.2)
    completed = screen_has("적립신청이 완료") or screen_has("완료되었습니다")
    out["completed"] = completed
    ocr_tap("확인", retries=2); time.sleep(1.0)
    if not completed:
        out["err"] = "적립신청 완료문구 미확인(미적립 가능)"   # ok 안 세움 → 정직 실패
        return out
    out["ok"] = True
    return out


# ──────────────────────────── G. 적립금(구매사은) 신청 ────────────────────────────

IGNORE_KW_FILE = ROOT / "lotte_ignore_keywords.txt"


def _ignore_keywords() -> list[str]:
    """적립금 이벤트 매칭 시 제외 키워드 (페이백/L.CLUB/선물하기/무료가입/창립 등). 6/1 라이브 확인."""
    try:
        return [ln.strip() for ln in IGNORE_KW_FILE.read_text(encoding="utf-8").splitlines() if ln.strip()]
    except Exception:
        return ["페이백", "L.CLUB", "선물", "선물하기", "무료가입", "창립", "이리오십쇼", "가정의달", "게이트페이지"]


def claim_lotte_reward(goods_no: str | None = None, search_term: str = "설화수") -> dict:
    """구매사은 적립금 신청 (G). 검색 → 구매상품 상세 → '구매사은' 섹션의 '최대 N% 적립' 이벤트
    (★ignore 키워드 제외) → '광세일' 행사페이지 → '혜택 신청하기'. 6/1 #5 라이브 검증.

    ★**상품번호(goods_no) 검색** = adb 한글입력 한계 우회 (2026-06-02 사용자 지정 + #10 라이브검증):
       검색창에 상품번호(숫자) 입력 → 자동으로 해당 상품페이지 오픈 (한글 타이핑 불필요).
       goods_no 없으면 최근검색어(search_term) 탭 폴백. 둘 다 안 되면 SKIP.
    ⚠️ 본 행사 = 앱 직접 구매 건만, 신청기간 내. 이미 '혜택 신청완료'면 idempotent skip."""
    out = {}
    ignore = _ignore_keywords()
    # 1) 검색 진입 (우상단 Q)
    _adb().tap(*NAV_HOME); time.sleep(1.5)
    dismiss_popups(2)
    _adb().tap(888, 150); time.sleep(1.8)
    if goods_no:
        # 2a) ★상품번호 입력(숫자=adb 가능) → ENTER → 상품페이지 자동 오픈 (한글 우회)
        inp = next((it for it in _ocr_texts(cap()) if "검색어를 입력" in it["text"]), None)
        if inp:
            _adb().tap(inp["cx"], inp["cy"]); time.sleep(1.0)
        _input_text(str(goods_no))
        subprocess.run([hw.ADB, "-s", hw._serial(), "shell", "input", "keyevent", "66"])
        time.sleep(3.0)
        out["search"] = f"번호 {goods_no}"
    else:
        # 2b) 폴백: 최근검색어 중 search_term 포함 항목 탭 (한글입력 불가 우회)
        rec = next((it for it in _ocr_texts(cap())
                    if search_term in it["text"] and it["cy"] < 800), None)
        if not rec:
            out["skip"] = f"상품번호 미지정 + 최근검색어 '{search_term}' 없음 — 검색 SKIP"
            out["ok"] = True; return out
        _adb().tap(rec["cx"] - 40, rec["cy"]); time.sleep(2.5)   # 'X'(삭제) 왼쪽 본문 탭
        # 3) 검색결과 → ★'공통' 상품 선호(광세일 행사상품=적립배너 有). '기획' 단품엔 적립카드 없음.
        prods = [it for it in _ocr_texts(cap()) if "설화수" in it["text"] and it["cy"] > 600]
        prod = next((p for p in prods if "공통" in p["text"]), None) or (prods[0] if prods else None)
        if not prod:
            out["err"] = "검색결과 상품 미발견"; return out
        _adb().tap(prod["cx"], prod["cy"]); time.sleep(2.5)
    # 4) ★'구매사은 · 혜택' 섹션까지 스크롤 → 그 섹션 안의 '최대 N% 적립' 카드 탐색 (6/2 #9 라이브 확정).
    #    상품상세엔 "최대 N% 적립"이 3종 존재:
    #      ① 상단 프로모 배너 "'광세일' 구매시 최대 N% 적립금" (섹션 밖, 탭하면 향수 등 엉뚱한 페이지)
    #      ② '구매/리뷰 적립혜택' "최대 NNNP/N원 적립" (% 없음 → 정규식에서 자동 제외)
    #      ③ '구매사은·혜택' 섹션 카드 "최대 N% 적립" ← ★정답(탭→광세일 행사페이지→혜택 신청하기)
    #    판별 = '구매사은' 헤더(cy) **아래** + 정규식 `최대\d+%적립`(N은 10/15/20 등 매일 변동) + ignore 제외.
    #    헤더 아래로 스코프하면 ①배너(섹션 위)·②소액카드(섹션 위)가 자동 배제됨.
    card = None
    in_section = False
    for _ in range(14):
        its = _ocr_texts(cap())
        hdr = next((i for i in its if "구매사은" in i["text"]), None)
        if hdr:
            in_section = True
        if in_section:
            ymin = hdr["cy"] if hdr else 0           # 헤더 보이면 그 아래만; 헤더가 위로 밀렸으면 화면 전체(배너는 이미 위로 사라짐)
            cands = []
            for it in its:
                if it["cy"] < ymin:
                    continue
                m = re.search(r"최대\s*(\d+)\s*%\s*적립", it["text"])
                if m and not any(k in it["text"] for k in ignore):
                    cands.append((int(m.group(1)), it))
            if cands:
                cands.sort(key=lambda x: x[0], reverse=True)     # 최고 % 적립 이벤트
                card = cands[0][1]; break
        _adb().swipe(540, 1500, 540, 900, 450); time.sleep(0.9)
    if not card:
        out["err"] = "구매사은 '최대 N% 적립' 카드 미발견(광세일 행사상품 아닐 수 있음)"; return out
    out["card"] = card["text"]
    _adb().tap(card["cx"], card["cy"]); time.sleep(3.0)
    # 5) ★광세일 구매사은 행사페이지인지 게이트 검증 (선물/live 오이동 시 신청완료 오매칭 방지 — #6 교훈)
    if not (screen_has("행사안내") or screen_has("광세일")):
        out["err"] = f"광세일 적립 event 미도달(잘못된 카드: {card['text']})"; return out
    # 6) '혜택 신청하기' 스크롤 탐색 → 탭. 이미 '혜택 신청완료'면 idempotent.
    for _ in range(6):
        if any("신청완료" in it["text"] for it in _ocr_texts(cap())):
            out["already"] = True; out["ok"] = True; return out
        b = next((it for it in _ocr_texts(cap()) if "혜택" in it["text"] and "신청하기" in it["text"]), None)
        if b:
            _adb().tap(b["cx"], b["cy"]); time.sleep(2.5)
            out["completed"] = screen_has("신청이 완료") or screen_has("완료되었")
            ocr_tap("확인", retries=2)
            out["ok"] = True; return out
        _adb().swipe(540, 1500, 540, 800, 500); time.sleep(1.0)
    out["err"] = "혜택 신청하기 버튼 미발견"
    return out


# ──────────────────────────── 오케스트레이션 ────────────────────────────

def dismiss_card_register() -> dict:
    """F. 결제 후 '카드등록 안내' 화면이 뜨면 dismiss(등록 안 함). 삼성/PAYCO 경로엔 안 뜸(#10 실측).
    ⚠️ 미관측 화면 → 스크린샷에서 '카드등록' 발견 시 'X'/'나중에'/'건너뛰기'/'닫기' 탐색 dismiss, 없으면 no-op."""
    out = {"present": False}
    if not any("카드등록" in it["text"].replace(" ", "") for it in _ocr_texts(cap())):
        return out
    out["present"] = True
    for key in ("나중에", "다음에", "건너뛰기", "닫기", "취소", "안함"):
        if ocr_tap(key, contains=True, retries=1):
            out["dismissed"] = key; break
    time.sleep(1.0)
    return out


def buy_one(idx: int, card: str | None = None, goods_no: str | None = None) -> dict:
    """idx 계정 롯데홈쇼핑 1건 구매. card=당일카드 override(미지정 시 청구할인 배너 자동감지).
    goods_no=구매사은 적립 검색용 상품번호(미지정 시 최근검색어 '설화수' 폴백)."""
    res = {"idx": idx, "status": None}
    print(f"\n{'='*54}\n[#{idx}] 롯데홈쇼핑 구매 시작", flush=True)
    reset_lotte_app()
    dismiss_popups()
    if not logout():
        res["status"] = "LOGOUT_FAIL"; return res
    lr = login(idx)
    res["id"] = lr.get("id")
    if not lr.get("ok"):
        res["status"] = f"LOGIN_FAIL:{lr.get('err')}"; return res
    print(f"[#{idx} {res['id']}] 로그인 OK → 장바구니", flush=True)
    cs = goto_cart_select_all()
    if not cs.get("ok"):
        res["status"] = f"CART_FAIL:{cs.get('err')}"; return res
    # 결제설정 순서 (★쿠폰이 적립금/L.POINT 리셋 → 포인트는 쿠폰 뒤 / ★카드가 현금영수증 리셋 → cash 는 카드 뒤):
    # 주소(최상단) → 할인쿠폰 → 플러스쿠폰 → 포인트(전액) → 카드 → 현금영수증 → 동의
    res["addr"] = set_address()
    if not res["addr"].get("ok"):
        res["status"] = f"ADDR_FAIL:{res['addr'].get('err')}"; return res
    res["dc"] = set_discount_coupons()
    res["pc"] = set_plus_coupons()
    res["pts"] = use_all_points()
    # 당일 할인카드 자동감지 + 선택 (★하드코딩 제거 — 청구할인 배너 최고%). ★cash 보다 먼저(카드가 지출증빙 리셋).
    sc = select_card_lotte(day=card)
    res["card"] = sc
    if not sc.get("ok"):
        res["status"] = f"CARD_FAIL:{sc.get('err')}"; return res
    use_card = sc["card"]
    print(f"[#{idx}] 당일카드 감지/선택 = {use_card} ({sc.get('pct')}%) via {sc.get('via')}", flush=True)
    res["cash"] = set_cash_receipt()        # ★카드 선택 직후 (카드가 현금영수증 지출증빙을 리셋하므로 여기서)
    agree_required()
    # 카드별 결제경로 분기: 롯데=LOCA(137601) / 삼성=PAYCO(137601+ARS). 그 외=라이브 검증 필요(false-auto 금지).
    print(f"[#{idx}] ⚠️ 결제 실행 ({use_card})", flush=True)
    if use_card == "롯데":
        pay = pay_loca()
    elif use_card == "삼성":
        pay = pay_lotte_payco(use_card)
    else:
        res["status"] = f"UNVERIFIED_CARD:{use_card}(롯데 결제경로 미검증 — 라이브 필요)"; return res
    res["pay"] = pay
    if not pay.get("ok"):
        res["status"] = f"PAY_FAIL@{pay.get('step')}:{pay.get('err')}"; return res
    res["status"] = f"DONE(주문 {pay.get('order')})"
    # F. 카드등록 안내(있으면 dismiss). 삼성/PAYCO 경로엔 안 뜸 → 보통 no-op.
    res["cardreg"] = dismiss_card_register()
    # E. 뷰티포인트 적립신청 (설화수=아모레퍼시픽, 주문완료 화면에서만 — now-or-never).
    #    ⚠️반드시 reward 보다 먼저: reward 가 홈으로 이동하면 주문완료 화면 이탈→뷰티 소실(#6 사례).
    print(f"[#{idx}] 뷰티포인트 적립신청 (nested-scroll 동의)", flush=True)
    res["beauty"] = claim_beauty_point()
    if not res["beauty"].get("completed") and not res["beauty"].get("skip"):
        print(f"[#{idx}] ⚠️ 뷰티포인트 실패({res['beauty'].get('err')}) — 다음 단계 이동 시 소실됨. 수동 확인 권장.", flush=True)
    # G. 구매사은 적립금 신청 (★상품번호 검색=한글우회 → 구매사은 이벤트 → 혜택 신청하기).
    print(f"[#{idx}] 구매사은 적립금 신청", flush=True)
    res["reward"] = claim_lotte_reward(goods_no=goods_no)
    return res


def main() -> int:
    a = sys.argv[1:]
    _resolve_serial()
    if not a or a[0] == "now":
        for t in sorted(_ocr_texts(cap()), key=lambda z: z["cy"]):
            print(f"  ({t['cx']:4d},{t['cy']:4d})  {t['text']}")
        return 0
    idxs = [int(x) for x in a if x.isdigit()]
    card = next((x for x in a if x in CARD_GRID_NAME), None)   # 당일카드 override (예: 삼성). 미지정=자동감지
    results = []
    for i in idxs:
        r = buy_one(i, card=card)
        results.append(r)
        print(f"[#{i}] → {r['status']}", flush=True)
    print("\n===== 요약 =====")
    for r in results:
        b = r.get("beauty", {})
        g = r.get("reward", {})
        bt = "뷰티✓" if b.get("completed") else ("뷰티skip" if b.get("skip") else f"뷰티?{b.get('err','')}")
        gt = ("적립✓" if g.get("completed") or g.get("already")
              else (f"적립skip({g.get('skip','')})" if g.get("skip") else f"적립?{g.get('err','')}"))
        print(f"  #{r['idx']} {r.get('id','')}: {r['status']}  [{bt} / {gt}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
