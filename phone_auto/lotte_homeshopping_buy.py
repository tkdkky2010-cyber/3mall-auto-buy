"""롯데홈쇼핑 폰앱 구매 — 1계정 end-to-end (구매~주문완료~뷰티포인트 적립). ⚠️실돈.

2026-06-01 #5(yr5326) 라이브 단계별 보정으로 **풀검증** (주문 2026-06-01-F17773, 524,795원,
뷰티포인트 5,778P). LOTTE_HOMESHOPPING_STEPMAP.md 정본 + 자동메모리 lotte-homeshopping-live-calib.
앱: com.omnitel.android.lottewebview (WebView 기반 → OCR 중심). 카드앱 구간(LOCA com.lcacApp)은
hmall pay_lotte 와 동일 → lotte_card.json flow_payment[14:22] 재사용.

범위: **구매~주문완료(A~D) + 뷰티포인트(E) + 카드등록 dismiss(F) + 구매사은 적립금(G)**.
  - D 카드: ★당일 할인카드 **자동감지**(청구할인 배너 최고% — 하드코딩 제거). buy_one 분기:
      **롯데=pay_loca(LOCA앱 137601) / 삼성=pay_lotte_samsung_general(카드번호 직접, PAYCO·ARS 회피, #12 G05038 검증)
       / KB=pay_lotte_kb(KB Pay 간편번호 137601, #13 G70658 검증)
       / 현대=pay_lotte_hyundai(앱카드→현대카드 앱 dump 셔플 pin6, 2026-06-12 #1 B87302 검증)**.
      그 외 카드=UNVERIFIED_CARD(라이브 필요). ⚠️현대 'PIN번호 결제'는 몰 첫결제 본인인증(SMS) 온보딩 요구 → 앱카드 경로 사용.
      (구 PAYCO/ARS 경로 pay_lotte_payco·handle_ars_call 은 deprecated — 금액 크면 ARS 전화의존이라 무인불가.)
  - E 뷰티포인트(★2026-06-03 #17 검증): **동의 먼저** — 적립신청 먼저 누르지 말고, 동의 박스를 박스 안 시작 800px swipe
      (claim_beauty_point._box_fling)로 끝까지 → '동의함' 왼쪽 라디오(cx-86) 탭 + **픽셀 채움검증** → 그 다음 적립신청 → 완료 폴링.
      ⚠️ 뷰티 멤버십 없는 계정(1~20 중 일부)은 정상 실패. F: 삼성/KB 경로엔 카드등록 안 뜸 → 보통 no-op.
  - G 구매사은(★2026-06-03 검증): **검색 폐기** — 주문완료의 **구매상품을 직접 탭**(상품명='(공통)…세트', 설화수 접두 없음)
      → 상품상세 → 구매사은 섹션 최대N%적립 → 광세일 게이트 → 혜택 신청하기. 랜덤 오claim 방지(못찾으면 SKIP).

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
    cap, _adb, ocr_find, ocr_tap, wait_text, screen_has, _resolve_serial, _wait_app, wake_screen,
    CARD_ALIASES, CARD_GRID_NAME,
    _card_secrets, _tap_shuffle,        # 삼성 일반결제 공용 헬퍼 (hmall=정본, 양 몰 공용)
    card_digits_on_screen, next_button_enabled,   # 2026-08-02 공통 검증 헬퍼
    pay_samsung as _pay_samsung_shared,  # ★삼성 일반결제 = 3사 공용 정본 (몰별 복제 금지)
    pay_nh_general,                     # ★NH 일반결제 = 3사 공용 정본 (몰 무관, 항상 비전 핸드세이크)
    preflight_today_files,              # ★결제 전 오늘자 데이터 확인 (3사 공용)
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


def _dump_nodes() -> list[dict]:
    """uiautomator dump → 노드 [{text,desc,cls,cx,cy,x1,y1,x2,y2}]. ★롯데 WebView 는 inner 요소를
    content-desc/text+bounds 로 노출 → 자동 리플로우(좌표 위아래 밀림) 무관한 '현재 절대좌표' 확보.
    (단발 dump — 'flow 중 동시 dump 금지'(137 SIGKILL)는 별도 모니터링 동시실행 얘기로 여기 무관.)"""
    import re as _re, xml.etree.ElementTree as _ET
    serial = hw._serial()
    subprocess.run([hw.ADB, "-s", serial, "shell", "uiautomator", "dump", "/sdcard/_lg.xml"],
                   capture_output=True)
    raw = subprocess.run([hw.ADB, "-s", serial, "exec-out", "cat", "/sdcard/_lg.xml"],
                         capture_output=True).stdout
    nodes: list[dict] = []
    try:
        root = _ET.fromstring(raw)
    except Exception:
        return nodes
    for el in root.iter("node"):
        m = _re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", el.get("bounds", ""))
        if not m:
            continue
        x1, y1, x2, y2 = map(int, m.groups())
        nodes.append({"text": el.get("text", ""), "desc": el.get("content-desc", ""),
                      "cls": el.get("class", ""), "cx": (x1 + x2) // 2, "cy": (y1 + y2) // 2,
                      "x1": x1, "y1": y1, "x2": x2, "y2": y2})
    return nodes


# 현대카드 앱 결제화면에서 **반드시 이 카드로** 결제한다 (사용자 지정 2026-08-05).
HYUNDAI_TARGET_CARD = "현대홈쇼핑 현대카드 Edition2"


def _hyundai_cards() -> list[dict]:
    """현대카드 앱 결제화면 카드 목록 → [{name, cx, cy, selected}].

    ★좌표 하드코딩 금지 — 카드 순서가 매번 바뀐다(사용자 지적 2026-08-05). 이름으로 행을 찾는다.
    선택 표시 = 행(btnRoot) 안의 **ivCheckcardRegi 존재**. 정확히 한 행에만 붙는다(2026-08-05 실측:
    CJ-M 선택 상태 → 현대홈쇼핑 탭 → 마커가 그 행으로 이동).
    ⚠️ 이 화면은 FLAG_SECURE(screencap 99.7% 검정) 라 OCR/픽셀 검증 불가 — dump 가 유일한 신호다.
    ⚠️ ivCheckSelect 는 전 행에 항상 있어 선택 신호가 **아니다**(혼동 주의)."""
    import re as _re, xml.etree.ElementTree as _ET
    serial = hw._serial()
    subprocess.run([hw.ADB, "-s", serial, "shell", "uiautomator", "dump", "/sdcard/_hc.xml"],
                   capture_output=True)
    raw = subprocess.run([hw.ADB, "-s", serial, "exec-out", "cat", "/sdcard/_hc.xml"],
                         capture_output=True).stdout
    try:
        root = _ET.fromstring(raw)
    except Exception:
        return []
    out: list[dict] = []
    for row in root.iter("node"):
        if not (row.get("resource-id") or "").endswith("btnRoot"):
            continue
        name, sel = "", False
        for d in row.iter("node"):
            rid = (d.get("resource-id") or "").split("/")[-1]
            if rid == "tvCardName":
                name = d.get("text", "")
            elif rid == "ivCheckcardRegi":
                sel = True
        m = _re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", row.get("bounds", ""))
        if name and m:
            x1, y1, x2, y2 = map(int, m.groups())
            out.append({"name": name, "cx": (x1 + x2) // 2, "cy": (y1 + y2) // 2, "selected": sel})
    return out


def _hyundai_select_card(target: str = HYUNDAI_TARGET_CARD, tries: int = 3) -> dict:
    """목표 카드 선택 + dump 재확인. **검증 실패면 ok=False** → 호출부가 결제를 중단한다
    (딴 카드로 결제되는 것보다 안 되는 게 낫다)."""
    for _ in range(tries):
        cards = _hyundai_cards()
        if not cards:
            time.sleep(1.5)
            continue
        hit = next((c for c in cards if target in c["name"]), None)
        if not hit:
            return {"ok": False, "err": f"'{target}' 없음 (목록={[c['name'] for c in cards]})"}
        if hit["selected"]:
            print(f"   [카드] '{hit['name']}' 선택 확인", flush=True)
            return {"ok": True, "card": hit["name"]}
        print(f"   [카드] '{hit['name']}' 탭 @({hit['cx']},{hit['cy']})", flush=True)
        _adb().tap(hit["cx"], hit["cy"])
        time.sleep(1.5)
    now = next((c["name"] for c in _hyundai_cards() if c["selected"]), None)
    return {"ok": False, "err": f"카드 선택 검증 실패 — 현재 선택={now!r}"}


def _dump_find(nodes: list[dict], key: str, cls: str = "") -> dict | None:
    """nodes 에서 text/desc 에 key 포함(+선택 class 필터) 첫 노드."""
    for n in nodes:
        if key in (n["text"] + " " + n["desc"]) and (not cls or cls in n["cls"]):
            return n
    return None


# (구 `_box_scroll`(작은 contained swipe) 제거 — 뷰티포인트 동의 박스 스크롤은 claim_beauty_point 의 `_box_fling`
#  (박스 안 시작 + 800px) 가 정본. 작은 거리는 느리고, 잘못 큰거리로 바꾸면 페이지스크롤로 깨짐(#15). 헷갈림 방지 위해 단일화.)


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
# ★'오늘 그만 보기' 추가 (2026-08-05): 실제 광고 버튼 문구가 이것인데 목록에 없어서 매번 '닫기'로만
#   닫혔다 → 계정 전환마다 광고가 다시 떴다. '닫기'보다 앞에 둬야 우선 매칭된다(첫 매칭 승).
POPUP_DISMISS = ("30일간 보이지 않기", "나중에 할게요", "오늘 그만 보기", "오늘 하루", "보지 않기", "다음에", "닫기")
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


def dismiss_review_prompt(tries: int = 5) -> bool:
    """리뷰작성 프롬프트('나중에 할게요') 닫기. ★마이 진입 시 / 로그인 직후 **지연 등장**(오랜만에 앱 켜고
    그 사이 리뷰가능 상품이 생긴 경우) — dismiss_popups 는 첫 OCR 없으면 바로 break 라 놓침 → 지연 렌더 대비
    tries회 재시도(2026-06-03 #13 사용자 확인). ⚠️'리뷰쓰기'(우측 액션) 아님 — '나중에 할게요'(좌)만 탭."""
    for _ in range(tries):
        it = next((x for x in _ocr_texts(cap()) if "나중에" in x["text"]), None)
        if it:
            _adb().tap(it["cx"], it["cy"]); time.sleep(1.0)
            print("   [review] '나중에 할게요' 닫기", flush=True)
            return True
        time.sleep(0.8)
    return False


def logout() -> bool:
    """마이 → 설정(우상단 톱니) → 로그아웃. (계정 전환 전 필수.)"""
    _adb().tap(*NAV_MY); time.sleep(2.0)
    dismiss_popups(2)
    dismiss_review_prompt()          # ★마이 진입 시 리뷰 프롬프트(지연 등장) 닫기 — 안 닫으면 톱니/로그아웃 막힘(#13)
    # 이미 로그아웃 상태(직전 계정 로그인 실패 등)면 로그아웃할 세션 없음 → 톱니 탭 전에 조기 skip
    if screen_has("아이디/비밀번호로 계속하기") or screen_has("통합회원"):
        print("   [logout] 이미 로그아웃 상태 — skip", flush=True)
        return True
    # ⚠️ 설정 톱니 = 헤더 아님! "고객님 반가워요!" 인사말 줄 우측(벨+톱니). (1010,150)은 장바구니라 누르면 안 됨.
    _adb().tap(1010, 336); time.sleep(1.5)   # 톱니 (6/1 실측)
    if not screen_has("로그아웃"):           # 설정화면 진입 검증
        _adb().tap(1010, 336); time.sleep(1.5)
    # 설정화면 상단 계정옆 "로그아웃"
    if not ocr_tap("로그아웃", contains=True, retries=4):
        # 이미 로그아웃 상태(직전 계정 로그인 실패 등)면 로그아웃할 세션이 없음 → 로그인 지표 확인 후 skip
        if screen_has("아이디/비밀번호로 계속하기") or screen_has("계속하기") or screen_has("통합회원"):
            print("   [logout] 이미 로그아웃 상태 — skip", flush=True)
            return True
        print("   ✗ 로그아웃 버튼 미발견", flush=True)
        return False
    time.sleep(1.0)
    # 확인 팝업 ("로그아웃 하시겠습니까?") — 안 뜨면 no-op
    ocr_tap("확인", retries=2)
    time.sleep(2.5)
    return True


def login(idx: int) -> dict:
    """마이 → '아이디/비밀번호로 계속하기' → ID/PW 입력 → 로그인. idx=1-based.
    ★2026-06-08: 앱이 자동으로 화면을 리플로우(좌표 전체 위아래로 밀림)해 OCR 탭이 어긋나
    로그인 실패(폼 잔존)했음(예: '계속하기' OCR cy748 vs 실제 dump cy897 = 150px 밀림). →
    **마이 후 + 폼 등장 후 uiautomator dump 로 절대좌표 확정**해 탭(리플로우 무관). OCR 폴백 유지.
    dump 앵커: '로그인' Button / '비밀번호 표기' 토글(PW칸 우측). ID칸=PW칸 위 한 칸(빈 input 은
    dump 에 안 잡혀 토글 앵커로 derive)."""
    acc = _accounts()[idx - 1]
    out = {"idx": idx, "id": acc["id"]}
    _adb().tap(*NAV_MY); time.sleep(1.5)
    dismiss_popups(2)
    time.sleep(1.0)   # ★마이 후 리플로우 정착 (6/2 지적)
    # ① 로그인옵션 화면 — dump 로 '아이디/비밀번호로 계속하기' 절대좌표
    cont = _dump_find(_dump_nodes(), "아이디/비밀번호로 계속하기")
    if cont:
        _adb().tap(cont["cx"], cont["cy"]); time.sleep(1.5)
    elif not (ocr_tap("아이디/비밀번호로 계속하기", contains=True, retries=4)
              or ocr_tap("계속하기", contains=True, retries=2)):
        out["err"] = "로그인 진입 버튼 미발견"; return out
    if not wait_text("아이디", timeout=10):
        out["err"] = "로그인 폼 미도달"; return out
    time.sleep(1.2)   # ★폼 리플로우 정착
    # ② 폼 — dump 앵커(로그인 Button + '비밀번호 표기' 토글)로 ID/PW/버튼 좌표 확정
    nd = _dump_nodes()
    btn = _dump_find(nd, "로그인", cls="Button")
    tog = _dump_find(nd, "비밀번호 표기")
    if btn and tog:
        gap = (tog["y2"] - tog["y1"]) + 12          # PW행 높이+여백
        id_xy = (300, tog["cy"] - gap)              # ID칸 = PW칸 위 한 칸
        pw_xy = (300, tog["cy"])                    # PW칸 (토글 왼쪽 입력영역)
        login_xy = (btn["cx"], btn["cy"])
    else:                                            # OCR 폴백 (dump 실패 시)
        id_xy, pw_xy, login_xy = (540, 585), (348, 720), (540, 889)
    # ID 입력 (검증: placeholder '통합회원' 사라짐, 안 사라지면 재시도)
    for _ in range(3):
        _adb().tap(*id_xy); time.sleep(0.8)
        _clear_field(); time.sleep(0.3)
        _input_text(acc["id"]); time.sleep(0.6)
        if not screen_has("통합회원"):
            break
    # PW 입력
    _adb().tap(*pw_xy); time.sleep(0.8)
    _clear_field(); time.sleep(0.3)        # ★오염 가드 (PW칸에 ID 잘못 들어가 있어도 제거)
    _input_text(acc["pw"]); time.sleep(0.5)
    # 로그인 — ★ID/PW 입력 후 '아이디에 대문자' 등 경고줄이 붙으면 레이아웃이 아래로 밀려(reflow)
    #   타이핑 전 dump 의 login_xy 가 빗나감(#13 Lee0128 대문자 사례) → 버튼 좌표 재-dump 후 탭.
    btn2 = _dump_find(_dump_nodes(), "로그인", cls="Button")
    _adb().tap(btn2["cx"], btn2["cy"]) if btn2 else _adb().tap(*login_xy)
    time.sleep(3.0)
    dismiss_popups()
    dismiss_review_prompt()          # ★로그인 직후에도 리뷰 프롬프트 등장 가능(#13)
    if screen_has("아이디") and screen_has("비밀번호"):
        out["err"] = "로그인 실패(폼 잔존 — 비번 오류 가능)"; return out
    out["ok"] = True
    return out


# ──────────────────────────── B. 장바구니 ────────────────────────────

def goto_cart_select_all() -> dict:
    """우상단 장바구니 → 전체선택 체크박스 → 주문하기. 주문서('결제하기' 등장) 도달까지."""
    out = {}
    # ★마이 경유 → 우상단 장바구니 아이콘 (2026-06-08 변경).
    #   홈은 데일리 프로모 takeover(예: 스와로브스키 기획전)로 진입 불안정 → '마이'는 상단바 안정.
    #   장바구니 아이콘은 마이/홈/기획전 모두 동일 (1002,148) (dump 실측). 옛 (960,150) 은 stale → 빗나감(CART_FAIL).
    _adb().tap(*NAV_MY); time.sleep(2.0)
    dismiss_popups(2)
    _adb().tap(1002, 148); time.sleep(2.5)   # 카트 아이콘 (마이 상단바, 2026-06-08 dump 실측)
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
        # ★잔액 0 계정 — '사용할 수 있는 보유금액이 없습니다' 알럿이 떠서 화면을 덮는다.
        #   안 닫으면 다음 루프의 OCR 에 '전액사용' 이 안 보여 조용히 break 하고, 알럿이 남은 채로
        #   진행돼 **청구할인 배너를 못 읽어 CARD_FAIL** 이 된다 (2026-08-05 #19 ybkim9960 적립금 0원,
        #   2회 재현). 적립금 있는 계정은 알럿이 안 떠서 여태 안 걸렸다.
        if screen_has("보유금액"):
            ocr_tap("확인", retries=2)
            out["alert"] = out.get("alert", 0) + 1
            time.sleep(0.8)
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
    # ★신용카드 버튼이 그리드 아래로 밀릴 수 있음(2026-07-08 계정1 실패) → 스크롤하며 찾아 탭.
    #   contains=False(정확일치) = 배너 '롯데카드(신용카드/L.PAY) N% 할인' 오매칭 방지(ocr_tap 기본과 동일).
    sc = _scroll_to("신용카드", contains=False, max_scroll=6)
    if not sc:
        out["err"] = "'신용카드' 버튼 탭 실패(스크롤 후 미발견)"; return out
    _adb().tap(sc["cx"], sc["cy"]); time.sleep(0.5)
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


def _agree_box(cy: int) -> tuple[int, bool]:
    """동의행(cy)의 체크박스 = 행 좌측 빈사각형. **픽셀로 박스중심 x + 체크여부 판정**(좌표 하드코딩 회피).
    WebView라 uiautomator 미노출(2026-06-02 #12) → OCR+픽셀이 유일. 박스 좌우 테두리(어두운 수직선) 검출로
    중심 x 산출, 내부 어두운픽셀로 체크(✓) 판정. 실측: 테두리 x≈45/95(중심70), 미체크 내부 dark=0 / 체크≈92.
    반환 (box_cx, checked)."""
    im = Image.open(cap("/tmp/_lt_agree.png")).convert("L"); px = im.load(); W, H = im.size
    y0, y1 = max(0, cy - 22), min(H, cy + 23)
    # 좌측 0~120px 에서 수직 테두리(어두운 컬럼) 검출 → 박스 좌우 경계
    edges = [x for x in range(0, min(120, W))
             if sum(1 for y in range(y0, y1) if px[x, y] < 110) >= 8]
    if len(edges) >= 2:
        bx = (edges[0] + edges[-1]) // 2; x0i, x1i = edges[0] + 3, edges[-1] - 2
    else:
        bx, x0i, x1i = 70, 48, 93                 # 폴백(2026-06-02 #12 실측 기본)
    dark = sum(1 for y in range(max(0, cy - 16), min(H, cy + 17))
               for x in range(x0i, min(x1i, W)) if px[x, y] < 128)
    return bx, dark >= 20


def agree_required() -> bool:
    """필수 동의 체크박스 체크. ★좌표 하드코딩 대신 **픽셀 박스검출 + 체크검증 + 재시도**
    (옛 cx-210 오프셋이 박스 빗나가 미체크→'동의하셔야 구매' 팝업으로 결제막힘, 2026-06-02 #12).
    체크 확인될 때까지 최대 5회 탭(이미 체크면 탭 안 함=토글오프 방지). ⚠️동의는 결제수단 아래 → 안 보이면 스크롤."""
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
    for _ in range(5):
        bx, checked = _agree_box(ag["cy"])
        if checked:
            return True
        _adb().tap(bx, ag["cy"]); time.sleep(1.0)
        ag = _ag() or ag                     # 리플로우 대비 cy 재확보
    return _agree_box(ag["cy"])[1]


# ──────────────────────────── D. 결제 (LOCA 재사용) ────────────────────────────

def _poll_order_complete(timeout: int) -> tuple[bool, str | None]:
    """롯데 복귀 후 주문완료 폴링. ★주문완료 화면 감지(confirmed) 후에도 주문번호가 지연렌더될 수 있어,
    번호 잡힐 때까지 계속 재OCR(첫 프레임 즉시 return 금지 — 2026-06-08/06-22 번호 None 사고).
    Returns (confirmed, order_no|None). KB·현대·LOCA 공용(중복 분기로 한쪽만 고쳐지는 회귀 방지)."""
    end = time.time() + timeout
    confirmed = False
    order = None
    while time.time() < end:
        t = _all_text()
        if not confirmed and (("주문" in t and "완료" in t) or "주문번호" in t):
            confirmed = True
        if confirmed:
            mo = re.search(r"(\d{4}-\d{2}-\d{2}-[A-Z]\d+)", t) or re.search(r"(20\d{12,})", t)
            if mo:
                order = mo.group(1); break
        time.sleep(0.8)
    return confirmed, order


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
    confirmed, order = _poll_order_complete(25)
    if confirmed:
        out["ok"] = True
        out["order"] = order
        return out
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
      → PAYCO 결제확인(금액·SAMSUNGCARD) → 결제하기 → 결제비밀번호(4x3 셔플) payco_pin6 137601(hmall 재사용, 로컬2엔진 실패→클로드 승격)
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
    confirmed, order = _poll_order_complete(180)
    if confirmed:
        out["ok"] = True; out["order"] = order; return out
    out["err"] = "주문완료 미확인(timeout — ARS 미완료/지연 가능)"
    return out


# ──────── D'. 삼성 일반결제 (카드번호 직접 — PAYCO/ARS 완전 회피) ────────

# _card_secrets / _tap_shuffle / KEYPAD_ROI_Y = hmall_hyundai_buy 에서 import (정본=hmall, 양 몰 공용). 상단 import 참조.
# (셔플키패드 로컬2엔진→클로드승격 roi 로직·card_secrets 로더는 hmall pay_samsung 과 공유 — 한 곳만 고치면 됨.)


def _tap_shuffle_retry(value: str, delay: float = 1.0, tries: int = 3) -> dict:
    """_tap_shuffle + 매핑 실패 시 '재배열'로 재셔플 후 재시도. 로컬2엔진 OCR 오독·매핑실패 대응
    (2026-07-21 #13 cert 매핑실패/#14 CVC 오독 사고). 재시도마다 force_vote(클로드 승격 포함)로 정확도↑."""
    r: dict = {}
    for i in range(tries):
        r = _tap_shuffle(value, delay=delay, force_vote=(i > 0))
        if r.get("ok"):
            return r
        if screen_has("재배열"):
            ocr_tap("재배열", contains=True); time.sleep(1.2)   # 재셔플 → 새 레이아웃 재OCR
        else:
            time.sleep(0.8)
    return r


def pay_lotte_samsung_general() -> dict:
    """롯데홈쇼핑 삼성 일반결제 — ★**3사 공용 정본 `hmall_hyundai_buy.pay_samsung` 에 위임**(2026-08-02).

    종전엔 몰별로 같은 시퀀스를 두 벌 갖고 있었다(현대몰 것은 라이브 미검증 포팅본).
    삼성 SDK 화면은 가맹점 무관 동일하므로 한 벌만 두고, **몰이 다른 '원 결제하기' 탭만 주입**한다.
    롯데는 하단 'NNN원 결제하기'(cy>2000)를 눌러야 해서 그 부분만 여기서 넘긴다.

    검증: 2026-08-02 #16·#3·#5·#15 4계정 연속 라이브 완주(주문 I20794/I21621/I22042/I22245).
    PAY_VISION_MODE=1 이면 카드번호 화면에서 정지 → 에이전트 수동입력(오늘 4/4 성공 경로).
    """
    def _lotte_pay_tap() -> bool:
        if screen_has("다음에도") or screen_has("사용할까요"):
            ocr_tap("사용할게요", contains=True, retries=2)
        pay = next((it for it in _ocr_texts(cap())
                    if "결제하기" in it["text"] and it["cy"] > 2000), None)
        if pay:
            _adb().tap(pay["cx"], pay["cy"]); return True
        return bool(ocr_tap("결제하기", contains=True))

    return _pay_samsung_shared(pay_tap=_lotte_pay_tap)

def pay_lotte_kb() -> dict:
    """KB국민카드 = KB Pay 간편결제. hmall `pay_kb` 흐름 재사용(카드앱 구간 몰 무관).
    ✅ 2026-06-03 #13 Lee0128 라이브 검증(주문 2026-06-03-G70658, KB 7%).
    경로: (원)결제하기 → KB SDK모달 'KB Pay 결제' 박스 → KB앱(com.kbcard) 결제하기(dump)
      → 간편번호6 137601(content-desc dump 자동제출; FLAG_SECURE라 screencap 검정이나 dump O)
      → 롯데 복귀 주문완료. ⚠️실 결제."""
    out = {"step": "order_sheet", "card": "KB"}
    if screen_has("다음에도") or screen_has("사용할까요"):
        ocr_tap("사용할게요", contains=True, retries=2)
    # 1) (원)결제하기 — 하단 'NNN원 결제하기'
    pay = next((it for it in _ocr_texts(cap()) if "결제하기" in it["text"] and it["cy"] > 2000), None)
    if pay:
        _adb().tap(pay["cx"], pay["cy"])
    elif not ocr_tap("결제하기", contains=True):
        out["err"] = "원결제하기 실패"; return out
    time.sleep(3.0)
    # 2) KB SDK 모달 → 'KB Pay 결제' 박스(노란 앱카드)
    out["step"] = "kb_modal"
    if not wait_text("KB Pay", timeout=12):
        out["err"] = "KB 결제 모달 미도달"; return out
    box = next((it for it in _ocr_texts(cap()) if it["text"].strip() == "KB Pay 결제"), None) or \
          next((it for it in _ocr_texts(cap()) if "KB Pay" in it["text"] and "결제" in it["text"]), None)
    if not box:
        out["err"] = "'KB Pay 결제' 박스 미발견"; return out
    _adb().tap(box["cx"], box["cy"]); time.sleep(1.0)
    # 3) KB앱(com.kbcard) 진입 → 결제하기(dump) → 간편번호6 137601(dump 자동제출)
    out["step"] = "kb_app"
    if not _wait_app("com.kbcard", timeout=15):
        out["err"] = "KB앱 미진입"; return out
    fr = FlowRunner(use_camera=False)
    try:
        fr.run_action({"action": "tap_dump_text", "text": "결제하기"})
        time.sleep(2.5)
        fr.run_action({"action": "input_pin", "value": "137601", "source": "dump"})
    except Exception as e:
        out["err"] = f"KB앱 결제 실패: {e}"; return out
    # 4) 롯데 복귀 + 주문완료 폴링
    out["step"] = "order_complete"
    if not _wait_app(PKG, timeout=20):
        out["err"] = "롯데앱 복귀 실패(결제 미확정 가능)"; return out
    time.sleep(3.0)
    confirmed, order = _poll_order_complete(30)
    if confirmed:
        out["ok"] = True; out["order"] = order; return out
    out["err"] = "주문완료 미확인(timeout)"
    return out


# ──────── D'''. 현대카드 결제 (앱카드 — 현대카드 앱 dump 셔플키패드) ────────

def pay_lotte_hyundai() -> dict:
    """현대카드 = **앱카드 결제** (현대카드 앱 com.hyundaicard.appcard).
    ✅ 2026-06-12 #1 tkdkky2002 라이브 검증(주문 2026-06-12-B87302, 현대 7%, 603,686원).
    ⚠️ 'PIN번호 결제' 금지 — 롯데 첫결제는 몰 단위 본인인증 온보딩(휴대폰SMS 등) 요구(#1 관찰).
       앱카드는 온보딩 불필요 + 카드앱 구간 몰 무관 재사용.
    경로: (원)결제하기 → XMPI 카드사 연결 → 현대 SDK 화면('앱카드 결제'/'PIN번호 결제'/무기명)
      → 앱카드 결제 → 현대카드 앱 OnlinePaymentActivity('롯데홈쇼핑에서 N원을 결제합니다',
      FLAG_SECURE=screencap 검정·dump O) → '결제 비밀번호 인증'(dump text) → 셔플키패드
      (desc='N 버튼', dump 1회 매핑) pin6 → 자동제출 → 롯데 복귀 주문완료 폴링. ⚠️실 결제."""
    pin6 = _card_secrets().get("현대", {}).get("pin6")
    if not pin6:
        return {"err": "card_secrets['현대'].pin6 없음"}
    out = {"step": "order_sheet", "card": "현대"}
    # ★이전 세션이 남은 현대카드 앱이 새 결제를 막는다 — LoadingForPayActivity 에서 카드목록이
    #   영영 안 뜨고 30초를 넘긴다(2026-08-05 #1·#15·#16 실패). 사용자가 앱을 직접 닫자 정상 동작한
    #   것이 유일한 단서 → 결제 진입 전에 항상 깨끗한 상태로 만든다. (롯데앱 reset 과 같은 패턴)
    subprocess.run([hw.ADB, "-s", hw._serial(), "shell", "am", "force-stop",
                    "com.hyundaicard.appcard"], capture_output=True)
    time.sleep(1.0)
    if screen_has("다음에도") or screen_has("사용할까요"):
        ocr_tap("사용할게요", contains=True, retries=2)
    # 1) (원)결제하기 — 하단 'NNN원 결제하기'
    pay = next((it for it in _ocr_texts(cap()) if "결제하기" in it["text"] and it["cy"] > 2000), None)
    if pay:
        _adb().tap(pay["cx"], pay["cy"])
    elif not ocr_tap("결제하기", contains=True):
        out["err"] = "원결제하기 실패"; return out
    time.sleep(3.0)
    # 2) 현대 SDK 화면 → '앱카드 결제' (XMPI 연결 로딩 포함 폴링)
    out["step"] = "hyundai_sdk"
    if not wait_text("앱카드", timeout=20):
        out["err"] = "현대 SDK 화면 미도달"; return out
    if not ocr_tap("앱카드", contains=True, retries=3):
        out["err"] = "'앱카드 결제' 선택 실패"; return out
    # 3) 현대카드 앱 진입 → '결제 비밀번호 인증' (FLAG_SECURE → dump 전용)
    out["step"] = "hyundai_app"
    if not _wait_app("com.hyundaicard.appcard", timeout=15):
        out["err"] = "현대카드 앱 미진입"; return out
    # ★카드 목록 렌더 대기 — LoadingForPayActivity 에서 오래 비어 있는다(2026-08-05 #1·#15·#16 실패:
    #   진입 직후 12초만 기다려 '결제 비밀번호 인증' 을 못 찾고 죽었다). 목록이 뜰 때까지 최대 30초.
    end = time.time() + 30
    while time.time() < end and not _hyundai_cards():
        time.sleep(1.0)
    # ★목표 카드 선택 (앱 기본값은 딴 카드다 — 실측: The CJ-M Edition2 가 선택돼 있었다)
    sel = _hyundai_select_card()
    if not sel.get("ok"):
        out["err"] = f"카드선택 실패: {sel.get('err')}"; return out
    out["card_selected"] = sel["card"]
    fr = FlowRunner(use_camera=False)
    try:
        fr.run_action({"action": "tap_dump_text", "text": "결제 비밀번호 인증", "timeout_sec": 12})
        time.sleep(2.5)
        # 4) 셔플키패드(desc='N 버튼') — dump 1회 매핑 후 연속탭, 6자리 자동제출
        fr.run_action({"action": "input_pin", "value": pin6, "source": "dump", "tap_delay_sec": 0.5})
    except Exception as e:
        out["err"] = f"현대카드 앱 결제 실패: {e}"; return out
    # 5) 롯데 복귀 + 주문완료 폴링
    out["step"] = "order_complete"
    if not _wait_app(PKG, timeout=20):
        out["err"] = "롯데앱 복귀 실패(결제 미확정 가능)"; return out
    time.sleep(3.0)
    confirmed, order = _poll_order_complete(75)
    if confirmed:
        out["ok"] = True; out["order"] = order; return out
    out["err"] = "주문완료 미확인(timeout)"
    return out


# ──────────────────────────── E. 뷰티포인트 적립신청 (nested-scroll 동의) ────────────────────────────

def claim_beauty_point(idx: int | None = None) -> dict:
    """주문완료 화면 뷰티포인트 적립신청. ★설화수(아모레퍼시픽)만, 본 주문완료 화면에서만(now-or-never).
    ★★순서(2026-06-03 #17 검증, 사용자 4회 지적): **동의 안 한 채 적립신청 먼저 누르기 금지.**
      ① 동의 박스를 **박스 안(540,1600) 잡고 800px swipe**(_box_fling)로 끝까지 → '동의함' 등장 (적립신청 누르지 않음).
      ② '동의함' 왼쪽 라디오(OCR cx-86) 탭 + **픽셀 채움검증**(미선택~0 / 선택~319 dark). ③ 그 다음 '적립신청' → 완료 폴링.
    ⚠️ 뷰티 멤버십 없는 계정은 완료문구 안 떠 정상 실패(1~20 중 일부). 박스가 안 보이면 적립신청 1회로 노출(폴백)."""
    out = {}
    if not (screen_has("뷰티포인트") or screen_has("적립신청")):
        out["skip"] = "뷰티포인트 적립 화면 아님(비설화수/미등장)"; out["ok"] = True; return out
    # ★★ 순서 (사용자 6/3 강조, 4회 지적): **동의 안 한 채 적립신청 먼저 누르기 금지.**
    #    스크롤 → '동의함' 체크 먼저 → 그 다음 적립신청. (적립신청 노출은 박스가 정말 없을 때만 폴백.)
    serial = hw._serial()
    def _blog(m):
        print(f"      [beauty] {m}", flush=True)
    def _agree_it():
        return _find("동의함", exact=True)
    def _box_fling():
        # ★동의 박스 안(540,1600) 잡고 **800px 위로(1600→800)** → 시작점이 박스 안이면 끝점이 박스 밖이어도 박스가 그만큼 스크롤.
        #   (작은 120px 여러번=느림 / 큰 swipe라도 시작점이 박스 밖이면 페이지스크롤로 깨짐[#15] → '시작점 박스 안+800px'이 핵심.)
        subprocess.run([hw.ADB, "-s", serial, "shell", "input", "swipe", "540", "1600", "540", "800", "300"])
        time.sleep(0.7)
    # 1) ★동의 먼저: **적립신청 누르지 말고** 동의 안내 박스를 스크롤해 '동의함' 찾기 (박스는 주문완료 화면에 이미 있음).
    found_at = None
    for n in range(6):
        if _agree_it():
            found_at = n; break
        _box_fling()
    _blog(f"동의함 박스스크롤: {'발견(scroll '+str(found_at)+')' if found_at is not None else '6회 스크롤 미발견 → 폴백'}")
    # 2) [폴백] 스크롤해도 동의함 없으면(박스 미노출) → 적립신청 1회로 동의안내 노출('하셔야' 팝업 확인) 후 재스크롤.
    if not _agree_it():
        _blog("폴백: 적립신청 1회 탭(동의 안내 노출용) → 확인 → 재스크롤")
        _tap_fresh("적립신청", retries=3); time.sleep(1.5)
        ocr_tap("확인", retries=2); time.sleep(1.5)
        for _ in range(6):
            if _agree_it():
                break
            _box_fling()
    if not _agree_it():
        out["err"] = "동의함 미도달(박스 스크롤 실패)"; _blog("✗ 동의함 미도달 → 적립 실패"); return out
    # 3) ★'동의함' 왼쪽 라디오 원(동의함 cx-86) 탭 + **픽셀로 선택검증**(채워지면 dark↑, 실측 미선택~0 / 선택~319). 미선택이면 재시도.
    ok = False
    for k in range(4):
        it = _agree_it()
        if not it:
            break
        rx, ry = it["cx"] - 86, it["cy"]
        _adb().tap(rx, ry); time.sleep(1.0)
        im = Image.open(cap("/tmp/_lt_agree.png")).convert("L"); px = im.load(); W, H = im.size
        dark = sum(1 for yy in range(max(0, ry - 16), min(H, ry + 16))
                   for xx in range(max(0, rx - 18), min(W, rx + 18)) if px[xx, yy] < 120)
        _blog(f"동의함 라디오 탭#{k} @({rx},{ry}) dark={dark} {'✓채움' if dark>=60 else '미채움 재시도'}")
        if dark >= 60:               # 라디오 채워짐 = 동의함 선택됨
            ok = True; break
    if not ok:
        out["err"] = "동의함 라디오 선택 실패(픽셀 미채움)"; _blog("✗ 라디오 선택 실패 → 적립 실패"); return out
    # 4) 적립신청 → '완료되었습니다' **폴링** 확인 (★완료팝업 지연렌더로 단발 체크가 false-negative[#19] → 최대 ~4s 폴링).
    sj = next((it for it in _ocr_texts(cap()) if "적립신청" in it["text"]), None)
    _blog(f"적립신청 버튼 {'발견 @('+str(sj['cx'])+','+str(sj['cy'])+')' if sj else 'OCR 미발견(_tap_fresh 재시도)'}")
    if not _tap_fresh("적립신청", retries=3):
        out["err"] = "적립신청 버튼 미발견"; _blog("✗ 적립신청 버튼 미발견 → 적립 실패"); return out
    # ★완료판정 = OCR 텍스트 1개 안에 '적립'+'완료' 동시 존재 (모달 "뷰티포인트 적립신청이 완료되었습니다").
    #   주문완료 화면의 "주문이 완료 되었습니다"는 '적립'이 없어 자동 배제 → false-positive 차단.
    done_text = None
    for p in range(6):
        time.sleep(0.7)
        its = _ocr_texts(cap())
        hit = next((t for t in its if "적립" in t["text"] and "완료" in t["text"]), None)
        if hit:
            done_text = hit["text"]; break
    shot = cap(f"/tmp/_lt_beauty_{idx if idx is not None else 'x'}.png")    # 계정별 스샷 (덮어쓰기 X)
    completed = done_text is not None
    if completed:
        _blog(f"✓ 뷰티포인트 적립완료 문구 떴음: '{done_text}'  [스샷 {shot}]")
    else:
        cand = [t["text"] for t in _ocr_texts(cap()) if "완료" in t["text"] or "적립" in t["text"]]
        _blog(f"✗ 뷰티포인트 적립완료 문구 안떴음 — 미적립 가능. 화면 '완료/적립' 텍스트={cand}  [스샷 {shot}]")
    out["completed"] = completed
    out["done_text"] = done_text
    ocr_tap("확인", retries=2); time.sleep(1.0)
    if not completed:
        out["err"] = "적립신청 완료문구 미확인(미적립 가능)"
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


def claim_lotte_reward(goods_no: str | None = None) -> dict:    # (search_term 제거 — 검색폐기로 미사용)
    """구매사은 적립금 신청 (G). ★**구매한 상품 상세로 직접 진입**(주문완료 화면의 구매상품 항목 탭)
    → '구매사은·혜택' 섹션의 '최대 N% 적립' (★ignore 제외) → '광세일' 행사페이지 → '혜택 신청하기'.

    ★검색 방식 폐기(2026-06-03 사용자 지적): 설화수 검색→랜덤상품 선택은 **구매 안 한 제품에 오claim** 위험
    (#14 실측: 자음생크림리치 구매했는데 검색결과 '자음생2종'에 적립). → 주문완료의 **그 주문 상품**만 탭해 상세 진입.
    상품 미발견/상세 미진입/광세일 게이트 실패 시 **SKIP**(오claim 방지 — 적립 못해도 잘못된 적립보다 나음).
    goods_no 지정 시(호출자 override) 번호검색=정확. ⚠️앱 직접구매 건만, 신청기간 내. '혜택 신청완료'면 idempotent."""
    out = {}
    ignore = _ignore_keywords()
    if goods_no:
        # (옵션) 상품번호(숫자) 검색 = 정확. 호출자가 명시할 때만.
        _adb().tap(*NAV_HOME); time.sleep(1.5); dismiss_popups(2); _adb().tap(888, 150); time.sleep(1.8)
        inp = next((it for it in _ocr_texts(cap()) if "검색어를 입력" in it["text"]), None)
        if inp:
            _adb().tap(inp["cx"], inp["cy"]); time.sleep(1.0)
        _input_text(str(goods_no))
        subprocess.run([hw.ADB, "-s", hw._serial(), "shell", "input", "keyevent", "66"]); time.sleep(3.0)
        out["search"] = f"번호 {goods_no}"
    else:
        # ★구매한 상품 항목을 주문완료 화면에서 직접 탭 → 상품 상세(GoodDetail). 검색/한글입력 불필요(2026-06-03 라이브검증).
        #   ⚠️ 상품명은 '(공통)…세트' / '(롯데I 단독)…' / '…ml 기획세트' — **'설화수' 접두 없음**(#15 skip 원인: 옛필터가 '설화수' 찾음).
        #   '세트'+(공통/단독/ml/기획/설화수) 매칭. 콤보면 첫 구매상품. 뷰티/적립/도착예정 텍스트 제외.
        prod = None
        for _ in range(5):
            prod = next((it for it in _ocr_texts(cap())
                         if "세트" in it["text"]
                         and any(k in it["text"] for k in ("공통", "단독", "ml", "기획", "설화수"))
                         and not any(k in it["text"] for k in ("적립", "포인트", "뷰티", "도착", "주문"))
                         and it["cy"] > 1000), None)
            if prod:
                break
            _adb().swipe(540, 1000, 540, 1500, 400); time.sleep(0.8)    # 주문완료 아래로(상품 노출)
        if not prod:
            out["skip"] = "주문완료서 구매상품 항목 미발견 — reward SKIP(오claim 방지)"; out["ok"] = True; return out
        out["product"] = prod["text"]
        _adb().tap(prod["cx"], prod["cy"]); time.sleep(3.5)
        # 게이트: 상품 상세(GoodDetail) 진입 검증 — 주문완료에 머물렀으면(상품 미진입) SKIP.
        if screen_has("주문이 완료") or screen_has("주문완료"):
            out["skip"] = "구매상품 탭 후 상품상세 미진입(주문완료 잔류) — reward SKIP"; out["ok"] = True; return out
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


def buy_one(idx: int, card: str | None = None, goods_no: str | None = None,
            combo_idx: int | None = None) -> dict:
    """idx 계정 롯데홈쇼핑 1건 구매. card=당일카드 override(미지정 시 청구할인 배너 자동감지).
    goods_no=구매사은 (옵션)상품번호 검색 override(미지정=기본: 주문완료의 구매상품 직접 탭).
    combo_idx=구매대장 기록용 조합번호(rate 시트에서 금액/조합명 조회). 미지정이면 금액 미상으로 기록."""
    res = {"idx": idx, "status": None}
    print(f"\n{'='*54}\n[#{idx}] 롯데홈쇼핑 구매 시작", flush=True)
    ws = wake_screen()                      # ★절전/잠금 preflight (2026-07-10 #11~14 검은화면 LOGOUT_FAIL 재발방지)
    if not ws["ok"]:
        res["status"] = f"SCREEN_LOCKED(awake={ws['awake']},keyguard={ws['keyguard']}) — 폰 잠금해제 필요"
        return res
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
    if not agree_required():                 # ★미체크면 결제 시 '동의하셔야' 팝업으로 막힘 → 결제 시도 전 abort
        res["status"] = "AGREE_FAIL:필수동의 체크 실패(픽셀검증)"; return res
    print(f"[#{idx}] 필수동의 체크 확인됨", flush=True)
    # 카드별 결제경로 분기: 롯데=LOCA(137601) / 삼성=일반결제(카드번호 직접, PAYCO/ARS 회피).
    #   ★PAYCO 경로(pay_lotte_payco, #10 검증)는 폐기 — ARS 전화의존이라 무인불가. 함수는 폴백/대조용 보존.
    #   그 외 카드 = 라이브 검증 필요(false-auto 금지).
    print(f"[#{idx}] ⚠️ 결제 실행 ({use_card})", flush=True)
    if use_card == "롯데":
        pay = pay_loca()
    elif use_card == "삼성":
        pay = pay_lotte_samsung_general()       # ✅ #12 G05038 라이브검증
    elif use_card == "KB":
        pay = pay_lotte_kb()                    # ✅ #13 G70658 라이브검증 (KB Pay 간편결제)
    elif use_card == "현대":
        pay = pay_lotte_hyundai()               # ✅ 2026-06-12 #1 B87302 라이브검증 (앱카드)
    elif use_card == "NH":
        # ✅ 2026-07-31 #9 wlstmdlsfk 라이브검증 (주문 2026-07-31-G83859 / 547,319원 / 뷰티 5,701P).
        # ★NH 는 **항상 에이전트 비전 핸드세이크**로 정지한다(3사 공용 정본, 환경변수 없음).
        #   pay_nh_general 이 manual=True 로 돌아오면 phone_auto/nh_enter.py 로 이어받을 것:
        #     box1(IME) → box2~4 / cvc / pin6 (칸마다 새 스크린샷 판독 — 칸 바뀌면 재셔플)
        pay = pay_nh_general()                  # 일반결제(카드번호 직접) — 사용자 지정 2026-06-25
    else:
        res["status"] = f"UNVERIFIED_CARD:{use_card}(롯데 결제경로 미검증 — 라이브 필요)"; return res
    res["pay"] = pay
    # ★NH 는 카드번호 화면에서 에이전트 비전 인계로 정지한다 = 실패 아님(정상 대기).
    #   여기서 return 해야 다음 계정이 콜드런치로 이 결제화면을 날려버리지 않는다.
    #   이어받기 = phone_auto/nh_enter.py. 완주 후 구매대장은 **수동 기록**해야 한다
    #   (아래 자동 기록 블록을 안 타므로 — record_combo 로 조합/주문번호 남길 것).
    if pay.get("manual"):
        res["status"] = "NH_HANDOFF(카드번호 화면 — 에이전트 비전 입력 대기)"; return res
    if not pay.get("ok"):
        res["status"] = f"PAY_FAIL@{pay.get('step')}:{pay.get('err')}"; return res
    res["status"] = f"DONE(주문 {pay.get('order')})"
    # 구매대장 기록 (JSON + 시트). 실패해도 결제 후처리엔 영향 없음.
    try:
        sys.path.insert(0, str(ROOT))
        import purchase_ledger as PL
        PL.record_combo("롯데홈쇼핑", res.get("id"), combo_idx,
                        order_no=pay.get("order"), card=use_card)
    except Exception as e:
        print(f"   [ledger] 기록 실패(무시): {e}", flush=True)
    # F. 카드등록 안내(있으면 dismiss). 삼성/PAYCO 경로엔 안 뜸 → 보통 no-op.
    res["cardreg"] = dismiss_card_register()
    # E. 뷰티포인트 적립신청 (설화수=아모레퍼시픽, 주문완료 화면에서만 — now-or-never).
    #    ⚠️반드시 reward 보다 먼저: reward 가 홈으로 이동하면 주문완료 화면 이탈→뷰티 소실(#6 사례).
    print(f"[#{idx}] 뷰티포인트 적립신청 (nested-scroll 동의)", flush=True)
    res["beauty"] = claim_beauty_point(idx)
    if res["beauty"].get("completed"):
        print(f"[#{idx}] ✅ 뷰티포인트 적립 완료 (문구: '{res['beauty'].get('done_text')}')", flush=True)
    elif res["beauty"].get("skip"):
        print(f"[#{idx}] ⏭️ 뷰티포인트 SKIP ({res['beauty'].get('skip')})", flush=True)
    else:
        print(f"[#{idx}] ❌ 뷰티포인트 적립 실패({res['beauty'].get('err')}) — 완료문구 안떴음, 소실 가능. 수동확인.", flush=True)
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
    if idxs and not preflight_today_files():   # ★stale 데이터로 결제 금지 (대장/적립 조용한 누락 방지)
        return 1
    card = next((x for x in a if x in CARD_GRID_NAME), None)   # 당일카드 override (예: 삼성). 미지정=자동감지
    combo_idx = next((int(x.split("=", 1)[1]) for x in a if x.startswith("combo=")), None)  # 구매대장 기록용
    results = []
    for i in idxs:
        r = buy_one(i, card=card, combo_idx=combo_idx)
        results.append(r)
        print(f"[#{i}] → {r['status']}", flush=True)
        if str(r["status"]).startswith("SCREEN_LOCKED"):   # 잠긴 폰에 나머지 계정 헛돌기 금지
            print("[STOP] 폰 잠김 — 잠금해제 후 재실행 (나머지 계정 중단)", flush=True)
            break
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
