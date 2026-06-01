"""롯데홈쇼핑 폰앱 구매 — 1계정 end-to-end (구매~주문완료). ⚠️실돈.

2026-06-01 #4(owyura) 라이브 자율주행으로 검증한 흐름을 코드화 (LOTTE_HOMESHOPPING_STEPMAP.md 정본).
앱: com.omnitel.android.lottewebview (WebView 기반 → OCR 중심). 카드앱 구간(LOCA com.lcacApp)은
hmall pay_lotte 와 동일 → lotte_card.json flow[14:22] 재사용.

⚠️ 범위: **구매~주문완료까지만**. 뷰티포인트 적립(E)·20% 적립신청(G)은 별도 코드화 (동의박스 nested-scroll 난제).

검증된 결제설정 순서 (★ 쿠폰이 적립금/L.POINT 를 리셋 → 포인트는 반드시 쿠폰 다 끝낸 뒤 마지막):
  할인쿠폰(10%×n) → 플러스쿠폰(최고%) → 적립금/L.POINT(전액) → 현금영수증 → 주소 → 카드 → 동의 → 결제하기

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

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from phone_auto import hmall_webview as hw
# 공통 헬퍼 재사용 (OCR/tap/대기 — 앱 비종속). cap/_adb 는 ANDROID_SERIAL 고정 bare adb.
from phone_auto.hmall_hyundai_buy import (
    cap, _adb, ocr_find, ocr_tap, wait_text, screen_has, _resolve_serial, _wait_app,
)
from phone_auto.flow_runner import _ocr_texts, FlowRunner
# PATH(bare adb)는 hmall_hyundai_buy import 시 이미 설정됨.

PKG = "com.omnitel.android.lottewebview"
MAIN = "com.lotteimall.common.lottewebview.MainActivity"
LOTTE_CARD_FLOW = ROOT / "phone_auto" / "coords" / "apps" / "lotte_card.json"
ACCOUNTS_FILE = ROOT / "lotte.json"

PIN6 = "137601"                       # 로카페이 간편번호 (공용)
BIZ_NO = ("507", "18", "15504")       # 현금영수증 지출증빙 사업자번호
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


def _all_text() -> str:
    return " ".join(it["text"] for it in _ocr_texts(cap()))


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
    # 설정 톱니 = 우상단. OCR 잘 안 잡혀 좌표 fallback (마이 화면 우상단).
    if not ocr_tap("설정", retries=2):
        _adb().tap(1000, 150); time.sleep(0.5)   # 우상단 톱니 fallback
    time.sleep(1.5)
    if not ocr_tap("로그아웃", contains=True, retries=4):
        print("   ✗ 로그아웃 버튼 미발견", flush=True)
        return False
    time.sleep(1.0)
    # 확인 팝업 ("로그아웃 하시겠습니까?")
    ocr_tap("확인", retries=2)
    time.sleep(2.5)
    return True


def login(idx: int) -> dict:
    """마이 → '아이디/비밀번호로 계속하기' → ID/PW 입력 → 로그인. idx=1-based."""
    acc = _accounts()[idx - 1]
    out = {"idx": idx, "id": acc["id"]}
    _adb().tap(*NAV_MY); time.sleep(2.0)
    dismiss_popups(2)
    if not ocr_tap("아이디/비밀번호로 계속하기", contains=True, retries=4):
        if not ocr_tap("계속하기", contains=True, retries=2):
            out["err"] = "로그인 진입 버튼 미발견"; return out
    if not wait_text("아이디", timeout=10):
        out["err"] = "로그인 폼 미도달"; return out
    # ID 칸 탭 + 입력
    id_it = ocr_find("아이디", contains=True) or ocr_find("아이디를", contains=True)
    if not id_it:
        out["err"] = "ID 입력칸 미발견"; return out
    _adb().tap(id_it["cx"], id_it["cy"]); time.sleep(0.8)
    _input_text(acc["id"]); time.sleep(0.5)
    # PW 칸 탭 + 입력 (ID 칸 아래)
    pw_it = ocr_find("비밀번호", contains=True)
    if not pw_it:
        out["err"] = "PW 입력칸 미발견"; return out
    _adb().tap(pw_it["cx"], pw_it["cy"]); time.sleep(0.8)
    _input_text(acc["pw"]); time.sleep(0.5)
    # 키보드 가림 → 로그인 버튼 탭 (키보드 내리고)
    _adb().tap(*NAV_HOME); time.sleep(0.3)   # 빈 곳 탭으로 키보드 내림 시도? → 아래에서 OCR
    if not ocr_tap("로그인", retries=4):
        out["err"] = "로그인 버튼 탭 실패"; return out
    time.sleep(3.0)
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
    # 홈으로 이동 후 우상단 장바구니
    _adb().tap(*NAV_HOME); time.sleep(2.0)
    dismiss_popups(2)
    cart = ocr_find("장바구니", contains=True)
    if cart:
        _adb().tap(cart["cx"], cart["cy"])
    else:
        _adb().tap(977, 150)   # 우상단 장바구니 아이콘 fallback
    time.sleep(2.5)
    if not (wait_text("주문하기", timeout=8) or screen_has("장바구니")):
        out["err"] = "장바구니 미도달"; return out
    # 전체선택: 좌상단 '일반' 왼쪽 체크박스. 헤더 '일반' 토큰 좌측 탭.
    gen = ocr_find("일반", contains=True, pick="top")
    if gen:
        _adb().tap(max(60, gen["cx"] - 200), gen["cy"]); time.sleep(1.0)
    else:
        _adb().tap(70, 360); time.sleep(1.0)   # 전체선택 체크박스 fallback
    # 주문하기 (1회 탭 — 결제하기 등장으로 전환검증)
    if not ocr_tap("주문하기", retries=4):
        out["err"] = "주문하기 탭 실패"; return out
    if not wait_text("결제하기", timeout=15):
        out["err"] = "주문서 미도달"; return out
    out["ok"] = True
    return out


# ──────────────────────────── C. 결제설정 ────────────────────────────

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
    # '변경' (안내문 '버튼' 제외 — '변경 >')
    chg = ocr_find("변경", contains=True)
    if not chg:
        out["err"] = "할인쿠폰 변경 버튼 미발견"; return out
    _adb().tap(chg["cx"], chg["cy"]); time.sleep(1.8)
    # 상품별 dropdown 반복: 모달서 '10%' 포함 항목 탭. 더이상 없으면 종료.
    for _ in range(10):
        opt = next((it for it in _ocr_texts(cap())
                    if "10%" in it["text"] and "할인" in it["text"]), None)
        if not opt:
            break
        _adb().tap(opt["cx"], opt["cy"]); time.sleep(1.2)
        out["applied"] += 1
    ocr_tap("선택완료", retries=2) or ocr_tap("적용", retries=1) or ocr_tap("확인", retries=1)
    time.sleep(1.5)
    out["ok"] = True
    return out


def set_plus_coupons() -> dict:
    """플러스쿠폰: 활성(받은) 상품만, 최고 할인율 선택. 받은 게 없으면 패스."""
    out = {"applied": 0}
    sec = _scroll_to("플러스쿠폰")
    if not sec:
        out["skip"] = "플러스쿠폰 섹션 없음"; out["ok"] = True; return out
    _adb().tap(sec["cx"], sec["cy"]); time.sleep(1.0)
    chg = ocr_find("변경", contains=True)
    if not chg:
        out["skip"] = "플러스쿠폰 변경 없음(받은 쿠폰 X)"; out["ok"] = True; return out
    _adb().tap(chg["cx"], chg["cy"]); time.sleep(1.8)
    # 모달서 최고 % 선택 (상품별). '%' 포함 항목 중 최고치.
    for _ in range(10):
        pcts = [(int(m.group(1)), it) for it in _ocr_texts(cap())
                if (m := re.search(r"(\d+)\s*%", it["text"])) and "할인" in it["text"]]
        if not pcts:
            break
        pcts.sort(key=lambda x: x[0], reverse=True)
        best = pcts[0][1]
        _adb().tap(best["cx"], best["cy"]); time.sleep(1.2)
        out["applied"] += 1
        # 모달 안 닫히면 다음 % 시도 (방어). 닫혔으면 break.
        if not any("%" in it["text"] and "할인" in it["text"] for it in _ocr_texts(cap())):
            break
    ocr_tap("선택완료", retries=2) or ocr_tap("적용", retries=1) or ocr_tap("확인", retries=1)
    time.sleep(1.5)
    out["ok"] = True
    return out


def use_all_points() -> dict:
    """적립금 '전액사용' + L.POINT '전액사용'. (★쿠폰 다 끝낸 뒤 마지막.)"""
    out = {"used": []}
    for label in ("적립금", "L.POINT", "L POINT", "엘포인트"):
        sec = _scroll_to(label, max_scroll=6)
        if not sec:
            continue
        # 섹션 근처 '전액사용' 버튼 탭
        its = _ocr_texts(cap())
        btn = next((it for it in its if "전액사용" in it["text"].replace(" ", "")
                    and abs(it["cy"] - sec["cy"]) < 200), None)
        if not btn:
            btn = next((it for it in its if "전액사용" in it["text"].replace(" ", "")), None)
        if btn:
            _adb().tap(btn["cx"], btn["cy"]); time.sleep(1.2)
            out["used"].append(label)
    out["ok"] = True
    return out


def set_cash_receipt() -> dict:
    """[조건부] 현금영수증 지출증빙 사업자번호 507/18/15504. L.POINT 사용 시 활성. 비활성이면 skip."""
    out = {}
    sec = _scroll_to("현금영수증", max_scroll=6)
    if not sec:
        out["skip"] = "현금영수증 섹션 없음(L.POINT 0)"; out["ok"] = True; return out
    if not ocr_tap("지출증빙", contains=True, retries=3):
        out["skip"] = "지출증빙 비활성"; out["ok"] = True; return out
    time.sleep(1.2)
    # 사업자번호 빈칸 3개 (테두리만, OCR 안잡힘 → 라이브 보정 필요). 첫 구현은 좌표 추정 → 검증 시 수정.
    out["todo"] = "사업자번호 3칸 좌표 라이브 보정 필요"
    out["ok"] = True
    return out


def set_address() -> dict:
    """배송지 '변경' → 주소목록 '203호' 포함 선택."""
    out = {}
    sec = _scroll_to("배송지", max_scroll=8) or _scroll_to("주소", max_scroll=4)
    if not sec:
        out["err"] = "배송지 섹션 미발견"; return out
    # '변경 >' (배송방법/픽업 제외, 우측정렬 최대 cx)
    its = _ocr_texts(cap())
    chgs = [it for it in its if it["text"].strip() in ("변경", "변경 >") or "변경" in it["text"]]
    chgs = [it for it in chgs if "배송방법" not in it["text"] and "픽업" not in it["text"]]
    if not chgs:
        out["err"] = "주소 변경 버튼 미발견"; return out
    chgs.sort(key=lambda it: it["cx"], reverse=True)
    _adb().tap(chgs[0]["cx"], chgs[0]["cy"]); time.sleep(2.0)
    # 주소목록서 '203호' 포함 주소 선택
    tgt = _scroll_to(ADDR_KEY, max_scroll=6)
    if not tgt:
        out["err"] = f"'{ADDR_KEY}' 주소 미발견"; return out
    _adb().tap(tgt["cx"], tgt["cy"]); time.sleep(1.5)
    ocr_tap("선택", retries=2) or ocr_tap("확인", retries=1) or ocr_tap("적용", retries=1)
    time.sleep(1.5)
    out["ok"] = True
    return out


def select_card_lotte() -> dict:
    """당일 할인카드(롯데카드) 선택. '최근 사용한 결제수단' 롯데카드 퀵버튼 또는 결제수단변경→신용카드→롯데카드.
    '롯데카드 N% 할인' 배너로 당일카드 확인."""
    out = {}
    # 1) 최근 사용한 결제수단 롯데카드 퀵버튼
    sec = _scroll_to("결제수단", max_scroll=8)
    if not sec:
        out["err"] = "결제수단 섹션 미발견"; return out
    its = _ocr_texts(cap())
    quick = next((it for it in its if it["text"].strip() == "롯데카드"), None)
    if quick:
        _adb().tap(quick["cx"], quick["cy"]); time.sleep(1.5)
    else:
        # 2) 결제수단변경 → 신용카드 → 롯데카드 드롭다운
        if not ocr_tap("결제수단변경", contains=True, retries=2):
            ocr_tap("신용카드", contains=True, retries=2)
        time.sleep(1.5)
        ocr_tap("신용카드 선택", contains=True, retries=2); time.sleep(1.2)
        if not ocr_tap("롯데카드", retries=4):
            out["err"] = "롯데카드 선택 실패"; return out
        time.sleep(2.0)
    # 당일카드(7% 할인 배너) 확인
    t = _all_text()
    m = re.search(r"롯데카드\s*(\d+)%\s*할인", t)
    out["discount_banner"] = m.group(0) if m else None
    out["ok"] = True
    return out


def agree_required() -> bool:
    """필수 동의 체크박스 탭 (텍스트 좌측 체크박스)."""
    its = _ocr_texts(cap())
    ag = next((it for it in its if "동의" in it["text"] and ("필수" in it["text"] or "전체" in it["text"])), None)
    if not ag:
        ag = next((it for it in its if it["text"].strip() in ("동의", "전체 동의")), None)
    if not ag:
        return False
    _adb().tap(max(60, ag["cx"] - 240), ag["cy"]); time.sleep(0.8)   # 텍스트 좌측 체크박스
    return True


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


# ──────────────────────────── 오케스트레이션 ────────────────────────────

def buy_one(idx: int) -> dict:
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
    # 결제설정 (순서 고정: 쿠폰 → 포인트 → 현금영수증 → 주소 → 카드 → 동의)
    res["dc"] = set_discount_coupons()
    res["pc"] = set_plus_coupons()
    res["pts"] = use_all_points()
    res["cash"] = set_cash_receipt()
    res["addr"] = set_address()
    if not res["addr"].get("ok"):
        res["status"] = f"ADDR_FAIL:{res['addr'].get('err')}"; return res
    res["card"] = select_card_lotte()
    if not res["card"].get("ok"):
        res["status"] = f"CARD_FAIL:{res['card'].get('err')}"; return res
    agree_required()
    print(f"[#{idx}] ⚠️ 결제 실행 (롯데카드 → LOCA 137601)", flush=True)
    pay = pay_loca()
    res["pay"] = pay
    if not pay.get("ok"):
        res["status"] = f"PAY_FAIL@{pay.get('step')}:{pay.get('err')}"; return res
    res["status"] = f"DONE(주문 {pay.get('order')})"
    # ⚠️ 뷰티포인트 적립(E)·20% 적립신청(G) = 별도 코드화 (미포함)
    return res


def main() -> int:
    a = sys.argv[1:]
    _resolve_serial()
    if not a or a[0] == "now":
        for t in sorted(_ocr_texts(cap()), key=lambda z: z["cy"]):
            print(f"  ({t['cx']:4d},{t['cy']:4d})  {t['text']}")
        return 0
    idxs = [int(x) for x in a if x.isdigit()]
    results = []
    for i in idxs:
        r = buy_one(i)
        results.append(r)
        print(f"[#{i}] → {r['status']}", flush=True)
    print("\n===== 요약 =====")
    for r in results:
        print(f"  #{r['idx']} {r.get('id','')}: {r['status']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
