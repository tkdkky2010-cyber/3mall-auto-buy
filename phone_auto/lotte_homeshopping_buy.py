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
      (구 PAYCO/ARS 경로 pay_lotte_payco·handle_ars_call 은 2026-08-07 삭제 — 금액 크면 ARS 전화의존이라 무인불가.)
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
    python3 -m phone_auto.lotte_homeshopping_buy resume 5 combo=3
        # ★결제는 됐는데 뒷처리(대장·뷰티·적립)를 못 끝냈을 때 — 주문완료 화면에서 실행.
        #   결제를 다시 하지 않는다. 처음부터 재실행하면 이중결제 위험(2026-08-24 신설 이유).
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
    _card_secrets,                      # 삼성 일반결제 공용 헬퍼 (hmall=정본, 양 몰 공용)
    card_digits_on_screen, next_button_enabled,   # 2026-08-02 공통 검증 헬퍼
    pay_samsung as _pay_samsung_shared,  # ★삼성 일반결제 = 3사 공용 정본 (몰별 복제 금지)
    pay_nh_general,                     # ★NH 일반결제 = 3사 공용 정본 (몰 무관, 항상 비전 핸드세이크)
    HANA_FLOW,                          # 하나앱 결제 flow (카드앱 구간 몰 무관 — pay_lotte_hana 재사용)
    preflight_today_files,              # ★결제 전 오늘자 데이터 확인 (3사 공용)
    preflight_card_app,                 # ★KB Pay 는 USB 디버깅이면 안 뜬다 (3사 공용)
    _dump_texts,                        # ★윈도우 OCR 이 놓치는 텍스트 보강 (OCR 과 겹쳐 읽기)
    ocr_or_dump_tap,                    # ★OCR 탭 실패 시 dump 탭으로 재시도 (3사 공용 정본)
)
from phone_auto.flow_runner import _ocr_texts, FlowRunner
# PATH(bare adb)는 hmall_hyundai_buy import 시 이미 설정됨.

PKG = "com.omnitel.android.lottewebview"
LOTTE_CARD_FLOW = ROOT / "phone_auto" / "coords" / "apps" / "lotte_card.json"
ACCOUNTS_FILE = ROOT / "lotte.json"

# (참조) 로카페이 간편번호 = 137601 — 실입력은 lotte_card.json flow_payment[19] 의 dump 셔플로 처리.
BIZ_NO = ("507", "18", "15504")       # 현금영수증 지출증빙 사업자번호 (3칸)
# ★★2026-08-30 이후의 판독/스크롤 보정은 **윈도우에서만** 적용한다 (사용자 지시).
#   맥은 Apple Vision OCR 이 화면을 직접 읽어 종전 경로로 잘 돌아간다. 문제는 윈도우 전용이다:
#   윈도우 OCR 은 쿠폰 '변경' 버튼을 한 건도 못 읽어(실측 0건) **항상 uiautomator dump 로 넘어가는데**,
#   롯데 webview 는 화면 밖 노드를 컨테이너 가장자리 한 점으로 접는다(위 cy≈75 / 아래 cy≈2265).
#   그래서 dump 를 주 경로로 쓰는 윈도우에서만 좌표가 왜곡되고, 맥은 이 결함을 밟지 않는다.
#   → 맥 동작을 바꾸지 않으려고 전부 이 플래그로 분기한다.
WIN_ONLY_FIX = sys.platform.startswith("win")

# ★쿠폰 구간만은 **플랫폼 무관**으로 엄격 검증한다 (2026-08-30 맥 실사고).
#   8/30 윈도우 세션이 고친 쿠폰 결함들이 WIN_ONLY_FIX 뒤에 갇혀 있어 맥에선 그대로 재발했다:
#   #2 kms3945 — 할인쿠폰(2) 라디오가 안 켜진 상태에서 `_coupon_change_btn` 이 cy 상한 없이
#   350px 아래 **플러스쿠폰 행의 '변경'** 을 잡아 그 모달을 열었고, 할인쿠폰 `applied:0` 이
#   **에러 없이** 통과해 602,000원(정상 541,800원, 70,000원 누락)이 됐다. MAX_PAY 가 막았다.
#   이 세 가지는 좌표 왜곡(dump 가장자리)과 무관한 **판독·검증 로직**이라 양 플랫폼에 동일하게 옳다.
COUPON_STRICT = True

ADDR_KEY = "203호"                    # 배송지 (화곡동 890 / 203호)

NAV_MY = (755, 2225)                  # 하단 네비 '마이' (1080x2400, OCR 실측)
NAV_HOME = (108, 2225)                # 하단 네비 '홈'


# ── 주문서(결제화면) 구간 전용 대기 ────────────────────────────────────────
# 카드앱 PIN 구간은 건드리지 않는다(오탭=카드 잠김). 여기 대상은 주소·쿠폰·포인트·
# 카드선택·동의 같은 **몰 주문서** 단계뿐이다.
# 왜: 이 sleep 들은 화면이 이미 떠 있어도 무조건 자던 고정 대기라, 계정당 롯데 ~48초 /
#     현대몰 ~23초를 그냥 흘려보냈다(2026-08-19 사용자 지시로 축소).
#     대부분 뒤에 ocr_tap/wait_text/_scroll_to 같은 **자체 재시도 폴링**이 이어져
#     중복이었다. 폴링이 없는 자리를 위해 하한 0.2s 는 남긴다.
# ⚠️ 되돌리려면 ORDER_SLEEP_SCALE=1.0 (환경변수) — 코드 수정 불필요.
_ORDER_SLEEP_SCALE = float(os.environ.get("ORDER_SLEEP_SCALE", "0.4"))


def nap(sec: float) -> None:
    """주문서 구간 고정 대기 — ORDER_SLEEP_SCALE 배율 적용(하한 0.2s)."""
    time.sleep(max(0.2, sec * _ORDER_SLEEP_SCALE))


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
    """화면 전체 텍스트 — ★OCR+dump 병합(2026-08-25). 입력칸 값(사업자번호 등)은 OCR 이 자주 놓친다."""
    return " ".join(it["text"] for it in _texts())


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

# 결제 도중 죽으면 **앞에 떠서** 다음 계정을 막는 카드앱들 (2026-08-31 실사고).
from phone_auto import fail_audit as _FA        # 실패 검수(2026-08-31)

CARD_APPS = ("com.hanaskcard.paycla", "com.kbcard.kbkookmincard", "com.hanaskcard.rocomo.potal",
             "com.lcacApp", "com.shcard.smartpay", "kvp.jjy.MispAndroid320",
             "com.samsung.android.spay", "com.nh.cashcardapp", "nh.smart.card",
             "com.hyundaicard.appcard",          # ★현대카드 (2026-08-31 #14 실측 — 빠져 있었다)
             "com.kbstar.kbbank", "com.wooricard.smartapp", "com.citibank.cardapp",
             "com.hanaskcard.jayoung", "com.lotte.lottecard")


def reset_lotte_app() -> None:
    """force-stop + 콜드런치 + 안정화. (hmall reset_to_main 의 lotte 판).

    ★카드앱도 같이 죽인다 (2026-08-31 실사고): #8 이 하나앱 '다음' 미발견으로 죽자 폰이
      `com.hanaskcard.paycla/OnlinePaymentActivity` 화면에 **갇힌 채** 남았다. 종전 코드는
      롯데앱만 force-stop 해서, 다음 계정은 롯데 화면에 닿지도 못하고 `LOGOUT_FAIL` 이 났다
      (#9·#11 연달아). 한 계정의 실패가 **뒤 계정 전부를 무너뜨리는** 연쇄였다.
      카드앱 종료는 결제 인증 전 단계에선 무해하고, 하나 보안모듈의 '앱을 다시 실행해주세요'
      경고(연속 호출 차단)도 같이 털어낸다.
    """
    serial = hw._serial()
    for pkg in CARD_APPS:
        subprocess.run([hw.ADB, "-s", serial, "shell", "am", "force-stop", pkg],
                       capture_output=True)
    subprocess.run([hw.ADB, "-s", serial, "shell", "input", "keyevent", "3"],   # HOME
                   capture_output=True)
    time.sleep(1.0)
    subprocess.run([hw.ADB, "-s", serial, "shell", "am", "force-stop", PKG])
    time.sleep(1.0)
    # ★롯데앱이 **실제로 앞에 왔는지 확인**하고 아니면 다시 띄운다 (2026-08-31 #14 실사고).
    #   패키지 목록만으로는 못 막는다 — 그날 앞을 막은 건 목록에 없던 `com.hyundaicard.appcard`
    #   였다(카드 결제 알림에서 열린 것으로 보인다). 앱은 얼마든지 새로 생기므로
    #   '무엇을 죽일까' 대신 **'롯데가 앞에 있나'** 로 판정한다. 아니면 그 앱을 접고 재시도.
    for attempt in range(3):
        subprocess.run([hw.ADB, "-s", serial, "shell", "monkey", "-p", PKG,
                        "-c", "android.intent.category.LAUNCHER", "1"],
                       capture_output=True)
        time.sleep(8.0)
        try:
            w = subprocess.run([hw.ADB, "-s", serial, "shell", "dumpsys", "window"],
                               capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=15).stdout or ""
            fg = next((ln for ln in w.splitlines() if "mCurrentFocus" in ln), "")
        except Exception:
            fg = ""
        if PKG in fg:
            return
        print(f"   [reset] 롯데앱이 앞에 없다(시도 {attempt + 1}/3) — 포그라운드: "
              f"{fg.strip()[:120]}", flush=True)
        subprocess.run([hw.ADB, "-s", serial, "shell", "input", "keyevent", "3"],
                       capture_output=True)      # HOME 으로 접고 다시 띄운다
        time.sleep(1.5)
    print("   [reset] ⚠️ 3회 시도에도 롯데앱 포그라운드 실패 — 이후 단계가 깨질 수 있다", flush=True)


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
    _adb().tap(*NAV_MY); nap(2.0)
    dismiss_popups(2)
    # ★상단 알림 배너를 걷어낸다 (2026-08-31 실사고, 검수로 확인).
    #   카트 아이콘 (1002,148) 은 화면 **맨 위**라 heads-up 알림 배너와 같은 자리다.
    #   실제 카드 결제 알림('김건엽 님, 현대 대한항공060 승인 / 8,400원 ... 뚜레쥬르')이 떠서
    #   탭이 알림에 먹혔고 `CART_FAIL:장바구니 미도달` 이 났다(#13). 알림은 언제든 뜬다.
    subprocess.run([hw.ADB, "-s", hw._serial(), "shell", "cmd", "statusbar", "collapse"],
                   capture_output=True)
    nap(0.6)
    _adb().tap(1002, 148); nap(2.5)   # 카트 아이콘 (마이 상단바, 2026-06-08 dump 실측)
    if not (wait_text("주문하기", timeout=8) or screen_has("장바구니")):
        out["err"] = "장바구니 미도달"; return out
    # 전체선택: 헤더 "일반 (a/b)" 좌측 체크박스 (~70,cy). ⚠️체크박스는 토글 → 이미 전체선택(a==b>0)이면
    # 탭하면 해제됨 → a==b>0 일 때만 통과, 아니면 n/n 될 때까지 토글(최대 4회+확정검증). (#6 '0/2' 오해 방지)
    def _gen():
        # ★OCR 단독 금지 (2026-08-25): 윈도우 OCR 이 헤더 '일반 (0/2)' 를 통째로 못 읽어
        #   '전체선택 실패(헤더=판독불가)' 가 났다. 같은 순간 dump 는 '일반 (0/2) 선택삭제' 를
        #   깨끗하게 준다(자동메모리 windows-ocr-needs-dump-merge). → 겹쳐 읽는다.
        its = _ocr_texts(cap()) + _dump_texts()
        return next((it for it in its if it["text"].strip().startswith("일반")
                     and "/" in it["text"]), None)
    # ★2026-08-25: 종전엔 3회 루프의 **마지막 탭 뒤 검증이 없어서** 토글이 뒤집힌 채로 통과했다.
    #   실제로 #8 이 '일반 (0/2)' 상태로 주문하기를 눌러 '주문하실 상품을 선택해 주세요' 팝업에 막히고
    #   그게 'CART_FAIL:주문서 미도달' 로 나타났다(원인이 이름에 안 드러남).
    #   → 탭할 때마다 다시 읽고, 끝나고 한 번 더 확정 검증. 못 맞추면 **여기서** 이름 붙여 실패시킨다.
    #   무선 adb 는 screencap 이 느려 직전 프레임을 읽는 일이 잦아 대기도 늘렸다.
    def _sel_state():
        g = _gen()
        if not g:
            return None, None
        m = re.search(r"\((\d+)\s*/\s*(\d+)\)", g["text"])
        return ((m.group(1), m.group(2)) if m else None), g

    def _all_selected(st):
        return bool(st) and st[0] == st[1] and st[0] != "0"

    ok_sel = False
    for _ in range(4):
        st, g = _sel_state()
        if _all_selected(st):
            out["selected"] = g["text"]; ok_sel = True
            break                                            # 이미 전체선택 → 통과(탭 X)
        _adb().tap(70, g["cy"] if g else 303); nap(1.8)      # 헤더 못 읽으면 기본좌표
    if not ok_sel:
        st, g = _sel_state()                                  # 마지막 탭 뒤 확정 검증
        if not _all_selected(st):
            out["err"] = f"전체선택 실패 (헤더={g['text'] if g else '판독불가'})"
            return out
        out["selected"] = g["text"]
    # 주문하기 (1회 탭 — 결제하기 등장으로 전환검증)
    if not ocr_tap("주문하기", contains=True, retries=4):
        out["err"] = "주문하기 탭 실패"; return out
    if not wait_text("결제하기", timeout=15):
        out["err"] = "주문서 미도달"; return out
    out["ok"] = True
    return out


# ──────────────────────────── C. 결제설정 ────────────────────────────

def _coupon_change_btn(sec, band: int = 220):
    """쿠폰 섹션의 '변경 >' (⚠️'배송방법 변경'/픽업 제외, 섹션 아래 우측)."""
    # ★'변경' 뿐 아니라 **'선택'** 도 받는다 (2026-08-25 실측): 쿠폰을 한 번도 고른 적 없는 행은
    #   버튼 라벨이 '선택' 이고, 그 행 안내문이 '변경 버튼을 클릭하여 할인을 선택해 주세요.' 라
    #   '변경'만 찾으면 **안내문만 잡히고 버튼은 못 잡는다**(#12 가 여기서 10% 2장을 빠뜨렸다).
    #   ⚠️ '선택완료'·'선택해 주세요' 같은 문장은 제외 — 버튼은 짧은 라벨이다.
    def _ok(t: str) -> bool:
        t = t.strip()
        if any(k in t for k in ("배송방법", "픽업", "선택완료", "선택해", "클릭")):
            return False
        return t in ("변경", "선택") or (("변경" in t or "선택" in t) and len(t) <= 4)

    # ★★cy **상한**이 없으면 아래 섹션 버튼을 가져온다 (2026-08-30 근본원인 확정).
    #   실측: 할인쿠폰(2) 행 cy=651 은 라디오가 꺼져 있어 버튼이 없는데, 상한이 없으니
    #   350px 아래 **플러스쿠폰 행의 '변경'(cy≈997)** 을 잡아 그 모달을 열었다 →
    #   할인쿠폰 `applied:0` 이 **에러 없이** 통과(#1 602,000원, 할인쿠폰 약 7만원 누락).
    #   8/25 #12 를 "이 계정만 행에 버튼이 없다"고 적어둔 것의 진짜 정체가 이것이다.
    #   선택된 행의 버튼은 라벨 바로 아래(실측 +79px)에 뜬다 → 밴드 220px 로 충분하다.
    hi = (sec["cy"] + band) if COUPON_STRICT else float("inf")    # ★상한 없으면 아래 섹션 버튼을 집는다
    chgs = [it for it in _texts()
            if _ok(it["text"]) and sec["cy"] - 60 < it["cy"] < hi]
    return max(chgs, key=lambda it: it["cx"]) if chgs else None


COUPON_RADIO_X = 100          # 쿠폰 섹션 라디오 x (1080폭 실측: 라벨은 cx≈193, 라디오는 x≈100)


def _coupon_row_amount(sec) -> int | None:
    """쿠폰 섹션 행에 **이미 적용된 금액**(예: 70,000원). 없으면 None.

    ★이게 '적용완료' 와 '라디오 꺼짐' 을 가르는 **유일한 신호**다 (2026-08-30 실사고):
      적용이 끝난 행은 금액만 남고 '변경' 버튼이 사라진다. 그런데 라디오가 꺼진 행도 버튼이 없다 —
      버튼 유무 하나로 판정하면 **적용완료를 미선택으로 오판**해 라디오를 눌러(토글) **적용을 해제**한다.
      실측: 할인쿠폰 70,000원 적용 상태에서 '라디오 활성 실패' 로 판정 → 재시도 루프 → 쿠폰 풀림.
    ⚠️ 헤더의 보유 장수 '(2)' 를 금액으로 오독하지 않도록 **천단위 콤마가 있는 수**만 인정한다.
    """
    for it in _texts():
        if abs(it["cy"] - sec["cy"]) > 45:
            continue
        m = re.search(r"(\d{1,3}(?:,\d{3})+)\s*원", it["text"])
        if m:
            return int(m.group(1).replace(",", ""))
    return None


def _anchor_key(text: str) -> str:
    """앵커 텍스트에서 **라디오 글리프를 뺀** 재탐색용 검색어. (윈도우 전용 경로에서만 쓴다)

    ★OCR 은 쿠폰 행 맨 앞의 라디오 동그라미를 **글자로 읽는다** — 그리고 그 글자는
      **라디오 상태에 따라 바뀐다.** 2026-08-30 같은 스크린샷 한 장 실측:
          라디오 ON  '㉧ 할인쿠폰(2)'      (할인쿠폰 70,000원 적용 상태)
          라디오 OFF '㉠ 플러스쿠폰(20)'   (미적용 상태)
      그래서 `sec['text'][:6]`('㉠ 할인쿠폰')을 검색어로 쓰면 **라디오를 켠 직후 그 검색어가
      화면에서 사라진다.** 게다가 이 글리프는 dump 텍스트('할인쿠폰(')에 **아예 없어서**
      dump 폴백으로도 못 찾는다 → `_scroll_to` 가 앵커도 못 찾고 가장자리 방향신호도 못 잡아
      기본값 'below' 로 **8회 무작정 아래로 스와이프**했다(tries=3 이므로 최대 24회 =
      "폰이 끝까지 내려간다"고 사용자가 지적한 동작, 2026-08-30 2차 세션 실측).
    ⚠️ 8/30 1차 수정(밴드 220 / 라디오 x=100 / min_cy=250)은 옳았고 그대로다. 이 결함은
      **라디오가 실제로 켜지게 된 뒤에야** 드러나는 후속 결함이다(그전엔 켜지질 않아 못 봤다).
    """
    m = re.search(r"[가-힣]{2,}", text)        # 글리프·괄호·숫자를 버리고 한글 낱말만
    return m.group(0) if m else text.strip()[:6]


def _select_coupon_radio(sec, tries: int = 3, key: str | None = None):
    """쿠폰 섹션 **라디오를 실제로 켠다.** 켜지면 갱신된 sec 를, 실패하면 None.

    ★라벨 중앙(`sec['cx']`)을 누르면 안 켜진다 (2026-08-30 실측): 라디오는 x≈100 인데
      OCR 앵커 '㉠ 할인쿠폰(2)' 의 중앙은 cx≈193 이라 **글자를 누르고 있었다.**
      화면상 라디오는 계속 ○ 인 채였고, 그래서 그 행에 '변경' 버튼이 안 생겼다.
    ★판정은 **'변경/선택 버튼이 그 행에 생겼는가'** 로 한다 — 라디오는 webview 라
      uiautomator dump 에 노드가 아예 안 잡혀(실측 0개) checked 속성을 쓸 수 없다.
    """
    if not COUPON_STRICT:                     # (구 맥 경로 — 라벨 중앙 1회 탭. 라디오가 안 켜져 폐기)
        _adb().tap(sec["cx"], sec["cy"]); nap(1.0)
        return _scroll_to(sec["text"][:6], max_cy=1900) or sec
    for _ in range(tries):
        # ★★**이미 켜져 있으면 절대 누르지 않는다.** 라디오는 토글이라 한 번 더 누르면 꺼진다.
        #   2026-08-30 실사고: 이 함수가 상태 확인 없이 먼저 탭해서 **이미 적용돼 있던
        #   플러스쿠폰 98,000원을 꺼버렸다** — 사용자가 폰 화면을 보고 중단시켰다.
        #   READ_FIRST 「토글 UI: 상태 확인 없이 무조건 탭 금지」를 정면으로 어긴 것이다.
        if _coupon_change_btn(sec):
            return sec
        x = COUPON_RADIO_X
        if sec.get("w"):                       # OCR 앵커면 좌측 끝에서 라디오 위치를 계산
            x = max(COUPON_RADIO_X, sec["cx"] - sec["w"] // 2 + 25)
        _adb().tap(x, sec["cy"]); nap(1.2)
        # ★검색어는 **라디오 글리프를 뺀 한글 낱말**로 (위 _anchor_key 참조). 종전 `sec['text'][:6]`
        #   은 라디오를 켠 순간 안 맞게 돼 매 회차 8회 헛스와이프를 만들었다.
        fresh = _scroll_to(key or _anchor_key(sec["text"]), max_cy=1900)
        # ★못 찾으면 **낡은 좌표로 계속 두드리지 않는다.** 종전 `or sec` 는 이미 맨 아래까지
        #   스크롤된 화면에서 진입 시점의 cy 를 그대로 눌러, 결제 페이지의 엉뚱한 행을 탭했다.
        if not fresh:
            return None
        sec = fresh
    return sec if _coupon_change_btn(sec) else None


def _submodal_items(shot=None):
    """열린 쿠폰 dropdown(하위모달 '할인선택') 안의 OCR 항목만 (하위 '할인선택'~'닫기' 사이).
    메인모달의 이미 적용된 상품 행을 오탭하지 않기 위함. shot=같은 스크린샷 재사용(밝기판별과 cy 정합)."""
    its = _texts(shot)
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


def _texts(shot=None) -> list[dict]:
    """★주문서 판독 = OCR + uiautomator dump **병합** (2026-08-25).

    윈도우 OCR 단독은 주문서 텍스트를 통째로 놓친다(자동메모리 windows-ocr-needs-dump-merge).
    실측: 같은 화면에서 OCR 8개 vs dump 40+개 — '할인쿠폰'·'플러스쿠폰'·'선택해 주세요'·쿠폰 옵션이
    전부 dump 에만 있었다. 그래서 **쿠폰이 0장 적용된 채 결제**됐다(#8 682,167원, 기대 ≈542,600원).
    ⚠️ 롯데 webview 는 **화면 밖 노드의 bounds 를 y=75 로 접는다**(실측) → 좌표를 못 쓴다.
       그래서 화면 안(100<cy<2350) 노드만 취한다. 화면 안 노드는 OCR 과 cy 가 1~2px 차이로 일치.
    """
    from collections import Counter
    its = list(_ocr_texts(shot or cap()))
    dts = [t for t in _dump_texts() if 100 < t["cy"] < 2350]
    # ★화면 밖 노드는 **컨테이너 가장자리 한 점으로 접힌다**(실측 2026-08-25: cy=2265 한 점에 58개,
    #   위쪽은 cy=75). 그 좌표로 탭하면 엉뚱한 데를 누르고, _scroll_to 는 "찾았다"며 스크롤을 멈춘다.
    #   → 같은 cy 에 8개 이상 몰린 클러스터는 버린다. (실제 보이는 행도 cy 를 2~3개 공유한다:
    #     '할인쿠폰(' + '2' + ')' 가 cy=2118 에 3개 → 임계를 3 이 아니라 8 로 둔 이유.)
    cnt = Counter(t["cy"] for t in dts)
    its += [t for t in dts if cnt[t["cy"]] < 8]
    return its


def _find_text(text: str, contains: bool = True, shot=None, pick: str = "bottom"):
    """매칭 1개 — ★**OCR 먼저, 없을 때만 dump**(ocr_or_dump_tap 과 같은 순서).

    ⚠️ OCR 과 dump 를 한 통에 섞고 pick='bottom' 으로 고르면 **dump 의 유령 노드가 이긴다.**
       (2026-08-25 실측: OCR 이 '다른 결제수단'을 cy=494 로 정확히 읽는데, 섞어 정렬하니 더 아래의
        dump 노드가 뽑혀 엉뚱한 곳을 탭 → 결제수단 그리드가 안 펴지고 '신용카드' 미발견.)
       그래서 OCR 결과가 있으면 그것만 쓰고, 하나도 없을 때만 dump 로 넘어간다.
    """
    def _pick(items):
        m = [it for it in items
             if ((text in it["text"]) if contains else (it["text"].strip() == text))]
        if not m:
            return None
        m.sort(key=lambda it: it["cy"], reverse=(pick == "bottom"))
        return m[0]

    hit = _pick(_ocr_texts(shot or cap()))
    if hit:
        return hit
    from collections import Counter
    dts = [t for t in _dump_texts() if 100 < t["cy"] < 2350]
    cnt = Counter(t["cy"] for t in dts)
    return _pick([t for t in dts if cnt[t["cy"]] < 8])


EDGE_TOP_CY = 100        # 이보다 위로 접힌 dump 노드 = 화면 **위쪽** 밖
EDGE_BOT_CY = 2250       # 이보다 아래로 접힌 dump 노드 = 화면 **아래쪽** 밖


def _offscreen_dir(text: str, contains: bool = True) -> str | None:
    """토큰이 화면 밖이면 **어느 쪽인지** 돌려준다: 'above' / 'below' / None(신호없음).

    ★★근본원인 수정 (2026-08-30, 사용자 지시 "8+8회 하지마. 못 찾는 원인을 찾아서 해결해").
      종전 `_scroll_to` 는 **방향을 몰라서** 한쪽으로 8번 훑고 반대로 8번 훑었다(최대 16 스와이프
      × 0.8s). 원인은 판독이 아니라 **정보를 버리고 있었던 것**이다 —
      롯데 webview 는 화면 밖 노드를 컨테이너 **가장자리 한 점으로 접는데**(위=cy≈75, 아래=cy≈2265),
      `_texts()` 가 그걸 '유령 노드'로 필터링해 없애 버렸다. 그 좌표는 탭에는 못 쓰지만
      **방향으로는 정확하다.**
      실측(2026-08-30 #1 주문서): 배송정보·배송방법변경 = cy 75(위), 34개 클러스터 = cy 2265(아래),
      할인쿠폰 658 / 플러스쿠폰 923 / 포인트사용 1254 / 결제하기 2171 = 화면 안 실좌표.
    ⚠️ 롯데 앱은 webview 디버깅을 안 켠다(devtools 소켓 없음 — 실측) → 현대몰처럼 CDP 로 DOM 을
       읽는 방법이 **없다**. 그래서 dump 의 이 신호가 유일한 위치 정보다.
    """
    hit = lambda t: (text in t) if contains else (t.strip() == text)
    above = below = False
    for t in _dump_texts():
        if not hit(t["text"]):
            continue
        if t["cy"] <= EDGE_TOP_CY:
            above = True
        elif t["cy"] >= EDGE_BOT_CY:
            below = True
    if above and not below:
        return "above"
    if below and not above:
        return "below"
    return None


AIM_CY = 700             # 앵커를 여기로 데려온다 (행 + 그 아래 버튼이 같이 보이는 여유 위치)


def _swipe_delta(dy: int) -> None:
    """화면 내용을 **정확히 dy 만큼** 민다 (dy>0 = 내용이 위로 = 아래쪽을 본다).

    ★고정거리(900px) 스와이프는 목표를 지나쳤다가 되돌아오는 **왕복**을 만든다 — 사용자가
      "폰에서 위아래로 계속 왔다갔다한다" 고 지적한 그 동작이다. 앵커가 보이는 순간엔
      얼마나 밀어야 하는지 **정확히 알 수 있으므로** 한 번에 맞춘다.
    """
    dy = max(-1400, min(1400, dy))
    if abs(dy) < 40:                       # 이미 충분히 가깝다 — 괜히 밀면 그게 왕복이 된다
        return
    y0 = 1750 if dy > 0 else 700
    _adb().swipe(540, y0, 540, y0 - dy, 350)
    nap(0.7)


def _swipe_toward(direction: str) -> None:
    """'below'=페이지 아래쪽을 보려고 손가락을 위로 / 'above'=그 반대."""
    if direction == "below":
        _adb().swipe(540, 1700, 540, 800, 400)
    else:
        _adb().swipe(540, 800, 540, 1700, 400)
    nap(0.8)


def _visible_dump(dts):
    """dump 노드 중 **화면 안**인 것만 (가장자리로 접힌 유령 노드 제거).
    ⚠️ 화면 밖 노드는 컨테이너 가장자리 한 점에 뭉친다(실측 cy=75 / cy=2265 에 수십 개) →
       같은 cy 에 8개 이상 몰린 클러스터는 버린다. 실제 보이는 행도 cy 를 2~3개 공유하므로 임계는 8."""
    from collections import Counter
    ins = [t for t in dts if EDGE_TOP_CY < t["cy"] < EDGE_BOT_CY]
    cnt = Counter(t["cy"] for t in ins)
    return [t for t in ins if cnt[t["cy"]] < 8]


def _scroll_to_legacy(text, contains=True, max_scroll=8, down=True, max_cy=None):
    """★맥 경로 — 2026-08-30 이전 구현 그대로 (양방향 훑기). 손대지 않는다."""
    for d in (down, not down):
        prev_cy = None
        for _ in range(max_scroll):
            it = _find_text(text, contains=contains)
            if it and (max_cy is None or it["cy"] <= max_cy):
                return it
            if it is not None:
                if prev_cy is not None and abs(it["cy"] - prev_cy) < 8:
                    return it                  # 더 안 밀린다 = 페이지 끝
                prev_cy = it["cy"]
            if d:
                _adb().swipe(540, 1700, 540, 800, 400)
            else:
                _adb().swipe(540, 800, 540, 1700, 400)
            nap(0.8)
    return None


MIN_USABLE_CY = 250      # 앵커가 이보다 위면 행이 상단 가장자리에 잘려 그 행 버튼을 못 쓴다
# 주문서 하단의 **고정** '…원 결제하기' 버튼 상단 (실측 버튼중심 cy=2170, 높이≈140).
# 이보다 아래로 밀린 행은 버튼에 덮여 OCR 목록에서 사라진다 → 앵커는 반드시 이 위에 세운다.
PAY_BTN_TOP_CY = 1950
# 사업자 등록번호 **입력칸**은 '사업자' 라벨보다 95~150px 아래에 있다(테두리만 있어 OCR 미검출).
# 라벨이 여기보다 아래면 칸 자체가 결제하기 버튼에 가려져 **탭해도 포커스가 안 잡힌다**
# (2026-08-31 #8 실측: '키패드 미등장' → entered False). 라벨을 이 위로 올려놓고 탭한다.
BIZ_LABEL_MAX_CY = 1750


def _scroll_to(text: str, contains: bool = True, max_scroll: int = 8, down: bool = True,
               max_cy: int | None = None, min_cy: int = MIN_USABLE_CY):
    """text 가 쓸 수 있는 위치에 올 때까지 **방향을 알고** 스크롤. 찾으면 item, 못 찾으면 None.

    ★max_cy: 앵커가 화면 **맨 아래 가장자리**에 걸친 상태로 멈추지 않게 하는 상한 (2026-08-25).
      쿠폰 섹션은 헤더만 보이고 그 아래 '변경' 버튼이 화면 밖이면 아무것도 못 누른다
      (실측: '할인쿠폰(' cy=2118 에서 멈춰 '할인쿠폰 변경 버튼 미발견' → 쿠폰 0장 적용).
    ★방향은 `_offscreen_dir` 과 같은 가장자리 신호로 판정 — 종전의 양방향 무작정 훑기(8+8회)를 대체.
    ★한 바퀴에 **판독은 OCR 1회 + dump 1회**만 한다: 종전엔 `_find_text` 와 `_offscreen_dir` 이
      각각 dump 를 떠서 2.8s×2 를 매 회전 버렸다(실측 3스와이프에 22초).
    """
    def _pick(items):
        m = [it for it in items
             if ((text in it["text"]) if contains else (it["text"].strip() == text))]
        if not m:
            return None
        m.sort(key=lambda it: it["cy"], reverse=True)
        return m[0]

    hit = lambda t: (text in t) if contains else (t.strip() == text)
    if not WIN_ONLY_FIX:
        return _scroll_to_legacy(text, contains, max_scroll, down, max_cy)
    for _ in range(max_scroll):          # ★상한 16 → 8 (사용자 지시: 무작정 훑기 금지)
        dts = _dump_texts()
        # ★OCR 우선 — dump 와 한 통에 섞어 정렬하면 유령 노드가 이긴다(2026-08-25 실측).
        it = _pick(_ocr_texts(cap())) or _pick(_visible_dump(dts))
        if it and it["cy"] >= min_cy and (max_cy is None or it["cy"] <= max_cy):
            return it
        if it is not None:
            # ★★위/아래 **양쪽 가장자리**를 다 피한다.
            #   `max_cy`(아래 가장자리)는 2026-08-25 에 고쳤지만 **위 가장자리는 안 고쳤다** →
            #   플러스쿠폰 앵커가 cy=102 에 걸려(유령 판정 경계 cy≤100 에서 **2px**) 실행마다
            #   되기도 하고 안 되기도 했다. 그 상태에선 행 전체가 잘려 '변경' 버튼을 못 쓴다.
            #   (실측 2026-08-30: 앵커 cy=102, 그 행 '변경' cy=184 — 조금만 더 밀리면 둘 다 유령.)
            _swipe_delta(it["cy"] - AIM_CY)      # 보이면 **정확히** 필요한 만큼만 (왕복 제거)
            continue
        above = any(hit(t["text"]) and t["cy"] <= EDGE_TOP_CY for t in dts)
        below = any(hit(t["text"]) and t["cy"] >= EDGE_BOT_CY for t in dts)
        if above and not below:
            d = "above"
        elif below and not above:
            d = "below"
        else:
            d = "below" if down else "above"     # 신호 없음 → 지정 방향
        _swipe_toward(d)
    return None


def _scroll_top(n: int = 8) -> None:
    """주문서를 맨 위로 올린다. ★쿠폰 단계 진입 전 필수 (2026-08-25).

    `_scroll_to` 는 **아래로만** 훑는다. 앵커가 현재 위치보다 위에 있으면 영영 못 찾는다 —
    #12 가 결제수단 구역까지 내려간 상태에서 쿠폰 단계에 들어가 '할인쿠폰 변경 버튼 미발견'으로
    10% 2장을 통째로 빠뜨렸다(606,181원 vs 정상 545,000원대, MAX_PAY 가드가 막았다).
    위치가 안 변하면 이미 top 이므로 조기 종료한다.
    """
    prev = None
    for _ in range(n):
        _adb().swipe(540, 800, 540, 1800, 350)
        nap(0.5)
        cur = _find_text("주문결제") or _find_text("배송정보")
        cy = cur["cy"] if cur else None
        if cy is not None and cy == prev:
            break
        prev = cy


def set_discount_coupons() -> dict:
    """할인쿠폰: 섹션 라디오 → '변경' → 상품별 dropdown → 모달서 '10% 할인' 탭 → 선택완료.
    상품 수 가변 → dropdown 반복. (★쿠폰이 포인트 리셋 → 반드시 포인트보다 먼저.)"""
    out = {"applied": 0}
    # ★윈도우: `_scroll_top()` 8스와이프 선행을 **제거**했다 — `_scroll_to` 가 dump 가장자리 신호로
    #   방향을 알아 어느 위치에서 진입해도 바로 찾아간다. 맥은 종전대로 맨 위로 되돌린다.
    if not WIN_ONLY_FIX:
        _scroll_top()
    sec = _scroll_to("할인쿠폰", max_cy=1500)   # 헤더 아래 '변경'·상품행이 같이 보여야 한다
    if not sec:
        out["err"] = "할인쿠폰 섹션 미발견"; return out
    avail = _coupon_count(sec) if COUPON_STRICT else 0   # 헤더 '할인쿠폰(N)' 의 N
    out["available"] = avail
    # ★**이미 적용된 행은 건드리지 않는다** — 라디오를 누르면 적용이 풀린다(토글).
    #   적용완료 행은 '변경' 버튼이 없어서 '라디오 꺼짐' 과 겉모습이 같다 → 금액으로 구분한다.
    already = _coupon_row_amount(sec) if COUPON_STRICT else None
    if already:
        out["already"] = already; out["applied"] = 0; out["ok"] = True
        print(f"   [쿠폰] 할인쿠폰 이미 {already:,}원 적용됨 — 그대로 둔다", flush=True)
        return out
    # ★라디오를 **실제로** 켠다 (라벨 아닌 라디오 좌표). key 는 섹션 리터럴을 그대로 넘긴다 —
    #   앵커 텍스트에서 잘라 쓰면 라디오 글리프(㉠/㉧)가 섞여 켠 직후 재탐색이 깨진다.
    sec2 = _select_coupon_radio(sec, key="할인쿠폰")
    if not sec2:
        out["err"] = ("할인쿠폰 라디오 활성 실패(변경 버튼 안 생김) — 라디오 좌표/화면 확인 필요"
                      if avail else "할인쿠폰 섹션 비활성(보유 0장)")
        out["ok"] = not avail                         # 보유 0장이면 정상 skip, 있는데 못 켜면 **실패**
        return out
    sec = sec2
    chg = _coupon_change_btn(sec)
    if not chg:
        out["err"] = "할인쿠폰 변경 버튼 미발견"; return out
    _adb().tap(chg["cx"], chg["cy"]); nap(1.8)        # 모달 '할인선택' 진입
    # 상품별 dropdown 반복: 각 상품 '쿠폰을 선택해 주세요' chevron(~985) → 하위모달 '10% 할인' 탭(자동적용+복귀)
    for _ in range(8):
        ph = next((it for it in _texts() if "선택해" in it["text"] and "주세요" in it["text"]), None)
        if not ph:
            break
        _adb().tap(985, ph["cy"]); nap(1.5)           # dropdown 열기
        # ★비활성(회색) 쿠폰 제외 — 같은 쿠폰을 다른 제품이 이미 점유하면 회색 처리됨(밝기로 판별).
        shot = cap(); gimg = Image.open(shot).convert("L")
        # ★'10%' 하드코딩을 버리고 **활성 중 최고%** 를 고른다 (플러스쿠폰과 같은 규칙).
        #   2026-08-30 #2 kms3945 실측: 이 계정의 즉석쿠폰은 10% 가 아니라
        #   '즉석쿠폰55,900원 즉석쿠폰 13% 할인' 이라 "10%" 필터가 0장을 잡고 조용히 닫았다
        #   (→ 602,000원. 정상 13% 적용 시 91,000원 할인). 즉석쿠폰 %는 계정·상품마다 다르다.
        #   ⚠️'번호쿠폰'(코드 입력 필요)·플레이스홀더는 % 가 없어 자동으로 걸러진다.
        cands = []
        for it in _submodal_items(shot):
            m = re.search(r"(\d+)\s*%", it["text"])
            if m and "할인" in it["text"] and _coupon_enabled(gimg, it["cy"]):
                cands.append((int(m.group(1)), it))
        if not cands:
            ocr_or_dump_tap("닫기", retries=1); break
        cands.sort(key=lambda x: (-x[0], x[1]["cy"]))        # 활성 중 최고% → 같은%면 최상단
        opt = cands[0][1]
        _adb().tap(opt["cx"], opt["cy"]); nap(1.3)
        out["applied"] += 1
    ocr_or_dump_tap("선택완료", retries=2) or ocr_or_dump_tap("적용", retries=1) or ocr_or_dump_tap("확인", retries=1)
    nap(1.5)
    # ★**보유 쿠폰이 있는데 0장 적용이면 실패다.** 종전엔 여기서 무조건 ok:True 라
    #   할인쿠폰 0장이 조용히 통과했다(2026-08-30 #1: 602,000원, 8/25 #12 도 같은 자리).
    #   "0을 반환하는 자리는 정상 0인지 고장난 0인지 구분해 로그로 남긴다"(8/25 교훈).
    out["ok"] = bool(out["applied"]) or bool(out.get("already")) or not avail
    if not out["ok"]:
        out["err"] = f"할인쿠폰 {avail}장 보유인데 0장 적용 — 결제 금액이 그만큼 비싸진다"
    return out


def _coupon_count(sec) -> int:
    """쿠폰 섹션 헤더 '할인쿠폰(2)' 의 보유 장수. 못 읽으면 0이 아니라 **-1**(=모름)이 아니라
    보수적으로 0 을 주면 실패를 정상 skip 으로 오인하므로, **판독 실패는 1 이상으로 본다**.
    OCR 이 '할인쿠폰(' · '2' · ')' 로 쪼개는 프레임이 있어 같은 행 숫자도 훑는다."""
    m = re.search(r"\((\d+)\)", sec.get("text", ""))
    if m:
        return int(m.group(1))
    for it in _texts():
        if abs(it["cy"] - sec["cy"]) < 30 and it["text"].strip().isdigit():
            return int(it["text"].strip())
    return 1          # 못 읽었으면 "있다"고 보고 검증을 강제한다 (조용한 skip 방지)


def set_plus_coupons() -> dict:
    """플러스쿠폰: 활성(받은) 상품만, 최고 할인율 선택. 받은 게 없으면 패스."""
    out = {"applied": 0, "pcts": []}
    if not WIN_ONLY_FIX:
        _scroll_top()
    sec = _scroll_to("플러스쿠폰", max_cy=1500)     # (윈도우) 방향은 _scroll_to 가 판정
    if not sec:
        out["skip"] = "플러스쿠폰 섹션 없음"; out["ok"] = True; return out
    already = _coupon_row_amount(sec) if COUPON_STRICT else None   # 적용된 행은 손대지 않는다
    if already:
        out["already"] = already; out["ok"] = True
        print(f"   [쿠폰] 플러스쿠폰 이미 {already:,}원 적용됨 — 그대로 둔다", flush=True)
        return out
    # ★라디오는 **라디오 좌표**로 켠다 (할인쿠폰과 같은 함정 — 라벨 중앙을 누르면 안 켜진다).
    #   2026-08-28 #5 yr5326 실측: 플러스쿠폰 26장 보유인데 '받은 쿠폰 X' 로 스킵돼
    #   627,197원(정상 543,xxx원)에 결제될 뻔했다. 리플로우로 낡은 좌표도 안에서 갱신한다.
    sec = _select_coupon_radio(sec, key="플러스쿠폰") or sec
    chg = _coupon_change_btn(sec)
    if not chg:
        out["skip"] = "플러스쿠폰 변경 없음(받은 쿠폰 X)"; out["ok"] = True; return out
    _adb().tap(chg["cx"], chg["cy"]); nap(1.8)
    # 상품별 dropdown: chevron → 하위모달 옵션 '[백화점]...쿠폰 N%' 중 최고% 탭.
    # ⚠️옵션 텍스트엔 '할인' 없음('...쿠폰 N%') → '쿠폰'+'%' 로 매칭(옛 '할인' 필터 버그 수정).
    for _ in range(8):
        ph = next((it for it in _texts() if "선택해" in it["text"] and "주세요" in it["text"]), None)
        if not ph:
            break
        _adb().tap(985, ph["cy"]); nap(1.5)
        # ★비활성(회색) 쿠폰 제외 후 활성 중 최고% 선택 (2026-06-02 #11: 같은 14%가 비활성+활성 2장일 때
        #   OCR 텍스트가 동일해 비활성 14%를 탭→적용실패→제품 미적용→'선택완료'서 '할인혜택 초기화' 팝업).
        shot = cap(); gimg = Image.open(shot).convert("L")
        pcts = []
        for it in _submodal_items(shot):
            m = re.search(r"(\d+)\s*%", it["text"])
            if m and "쿠폰" in it["text"] and _coupon_enabled(gimg, it["cy"]):
                pcts.append((int(m.group(1)), it))
        if not pcts:
            ocr_or_dump_tap("닫기", retries=1); break
        pcts.sort(key=lambda x: (-x[0], x[1]["cy"]))      # 활성 중 최고% → 같은%면 최상단
        best = pcts[0]
        _adb().tap(best[1]["cx"], best[1]["cy"]); nap(1.3)
        out["applied"] += 1; out["pcts"].append(best[0])
    ocr_or_dump_tap("선택완료", retries=2) or ocr_or_dump_tap("적용", retries=1) or ocr_or_dump_tap("확인", retries=1)
    nap(1.5)
    out["ok"] = True
    return out


def use_all_points() -> dict:
    """적립금 + L.POINT 모두 '전액사용'. ★L.POINT 무조건 사용(사용자 지시 2026-06-01: #7서 L.POINT 누락).
    라벨 스크롤 의존(옛 버그: '적립혜택 L.POINT' 오매칭으로 L.POINT 사용 누락) 대신,
    포인트사용 섹션의 **모든 '전액사용' 버튼을 탭**(적립금·L.POINT). 탭하면 '적용취소'로 바뀌어 멱등."""
    out = {"used": 0}
    # 포인트사용 섹션('전액사용' 버튼)까지 스크롤. ★_scroll_to 와 같은 이유로 양방향 —
    #   아래로만 훑으면 이미 지나친 섹션을 못 찾고 조용히 used:0 이 된다 (2026-08-28 #2·#3).
    def _points_visible() -> bool:
        return any("전액사용" in it["text"].replace(" ", "") for it in _ocr_texts(cap()))
    for dy in (-700, 700):
        for _ in range(8):
            if _points_visible():
                break
            _adb().swipe(540, 1500, 540, 1500 + dy, 450); nap(0.8)
        if _points_visible():
            break
    # 보이는 '전액사용' 모두 탭 (위→아래). 탭 후 '적용취소'가 되어 다음 루프엔 남은 것(L.POINT)만 매칭.
    for _ in range(5):
        btns = [it for it in _ocr_texts(cap()) if "전액사용" in it["text"].replace(" ", "")]
        if not btns:
            break
        btns.sort(key=lambda it: it["cy"])
        _adb().tap(btns[0]["cx"], btns[0]["cy"]); nap(1.2)
        out["used"] += 1
        # ★잔액 0 계정 — '사용할 수 있는 보유금액이 없습니다' 알럿이 떠서 화면을 덮는다.
        #   안 닫으면 다음 루프의 OCR 에 '전액사용' 이 안 보여 조용히 break 하고, 알럿이 남은 채로
        #   진행돼 **청구할인 배너를 못 읽어 CARD_FAIL** 이 된다 (2026-08-05 #19 ybkim9960 적립금 0원,
        #   2회 재현). 적립금 있는 계정은 알럿이 안 떠서 여태 안 걸렸다.
        if screen_has("보유금액"):
            ocr_or_dump_tap("확인", retries=2)
            out["alert"] = out.get("alert", 0) + 1
            nap(0.8)
    out["ok"] = True
    return out


def set_cash_receipt(points_used: int = 0) -> dict:
    """[조건부] 현금영수증 지출증빙 사업자번호 507/18/15504. L.POINT 사용 시 활성. 비활성이면 skip.

    ★points_used = 직전 `use_all_points()` 의 used (사용자 지시 2026-08-31:
      "포인트 사용/미사용에 따라 현금영수증 발부하고 안하고 차이나는 건데").
      **포인트를 썼으면 현금영수증은 필수**다 — 그때 '섹션 없음' 은 판독 실패지 부재가 아니다.
      종전엔 화면에서 '현금영수증' 글자를 못 찾으면 무조건 `L.POINT 0` 으로 단정해 skip 했고,
      윈도우에서 판독이 흔들리면 그대로 오판했다. 그 결과 결제하기에서
      '현금영수증 발급방식을 선택해 주세요' 팝업에 막혀 카드 모달이 안 뜨고
      `PAY_FAIL@hana_modal` 로 **엉뚱하게** 보고됐다(2026-08-31 #9·#10 실측:
      같은 로그에 `포인트 {'used': 1}` 과 `현금영수증 섹션 없음(L.POINT 0)` 이 나란히 찍혔다).
    """
    out = {}
    # ⚠️ 현금영수증 = 결제수단 섹션 안에 있음 (별 섹션 아님). L.POINT 사용 시 활성.
    # ★섹션을 **충분히 위로** 올려놓고 시작한다 (2026-08-31 실사고).
    #   '지출증빙' 을 누르면 그 아래로 '사업자 등록번호' 라벨(+약 406px)과 빈칸 3개(+약 524px)가
    #   펼쳐진다. 헤더가 화면 아래쪽에 있으면 그 칸들이 하단 고정 '…원 결제하기' 버튼에 가려져
    #   **탭해도 포커스가 안 잡히고**(키패드 미등장) entered=False 로 끝난다(#8 실측).
    #   실측: 헤더 cy=568 일 때 라벨 974 / 칸 1092 → 헤더를 1100 위로만 올리면 칸이 안전권.
    #   ⚠️ 여기에 max_cy 를 걸면 안 된다 (2026-08-31 회귀): 상한을 못 맞추면 _scroll_to 가 None 을
    #      돌려주고, 그걸 '섹션 없음(L.POINT 0)' 으로 **오해**해 건너뛴다. 그러면 결제하기에서
    #      '현금영수증 발급방식을 선택해 주세요' 팝업에 막혀 카드 모달이 안 뜬다
    #      (#10 실측 → PAY_FAIL@hana_modal 로 오표시). 칸 위치 보정은 아래 '사업자' 스크롤에서 한다.
    sec = _scroll_to("현금영수증", max_scroll=8)
    if not sec:
        if points_used:
            # 포인트를 썼으면 섹션은 **반드시 있다** → 판독 실패다. 조용히 넘기면 결제가 막힌다.
            out["err"] = (f"현금영수증 섹션 미발견인데 포인트 {points_used}건 사용됨 "
                          f"— 발급방식 미선택이면 결제가 막힌다(판독 실패 의심)")
            return out
        out["skip"] = "현금영수증 섹션 없음(포인트 미사용)"; out["ok"] = True; return out
    # ★라벨 글자를 누르면 라디오가 **안 켜진다** (2026-08-31 실측: '지출증빙' 텍스트를 탭해도
    #   `㉧ 소득공제` 가 그대로 남고 '휴대폰번호' 칸이 유지된다 → 사업자 라벨이 아예 안 생겨
    #   '사업자 등록번호 라벨 미발견' 으로 끝났다). 라디오 원은 라벨 **왼쪽**에 있다
    #   (실측: 라벨 cx=733 / 라디오 cx=624 → 약 -109). 누른 뒤 **'사업자' 등장으로 검증**한다.
    lab = _find_text("지출증빙")
    if not lab:
        out["skip"] = "지출증빙 비활성"; out["ok"] = True; return out
    for dx in (-109, -95, -125, 0):
        _adb().tap(lab["cx"] + dx, lab["cy"]); nap(1.5)
        if _find_text("사업자") or _find_text("등록번호"):
            break
        lab = _find_text("지출증빙") or lab
    else:
        out["err"] = "지출증빙 라디오 선택 실패(사업자 라벨 미등장)"; return out
    # ★칸1 포커스 = 키패드 등장까지 보장 (6/2 #10: 단일 탭으론 포커스 실패 → entered=False + '입력해주세요' 팝업).
    #   '사업자 등록번호' 라벨 아래 빈칸 3개(테두리만, OCR 미검출). 후보 오프셋 재시도 + 팝업이 오히려 포커스 유발.
    def _keypad_up() -> bool:
        """★IME 실제 상태로 판정 (2026-08-25 근본수정).

        종전엔 '화면에 숫자가 보이면 키패드'로 봤는데 **주문서엔 금액·수량 숫자가 널려 있어
        항상 True** 였다. 그래서 포커스가 안 된 채 사업자번호를 입력하고, 키보드를 닫으려고 누른
        **BACK 이 주문서를 닫아 장바구니로 되돌렸다.** 이후 '사업자 라벨 사라짐' →
        'AGREE_FAIL(동의행 미발견)' 이 줄줄이 났다 — 실패지점 화면덤프에 `150:장바구니` 로 찍혔다.
        """
        out = subprocess.run(["adb", "shell", "dumpsys", "input_method"],
                             capture_output=True, text=True, encoding="utf-8",
                             errors="replace", timeout=10).stdout or ""
        return "mInputShown=true" in out
    # ★카드 뒤 위치에선 지출증빙이 화면 하단 → 사업자 라벨이 뷰 밖. 먼저 스크롤로 노출 (2026-06-02 #11 재정렬).
    if not _scroll_to("사업자", max_scroll=4, max_cy=BIZ_LABEL_MAX_CY):
        out["err"] = "사업자 등록번호 라벨 미발견"; return out
    # ★2026-08-25: 입력 검증까지 하고 **실패하면 한 번 더** 시도한다. 종전엔 entered=False 여도
    #   조용히 ok 로 통과해서, 결제하기를 누른 뒤에야 '사업자 등록번호를 입력해주세요' 팝업으로
    #   막혔다(#8 resume 실측 → PAY_FAIL@kb_modal 로 엉뚱하게 표시됨).
    out["entered"] = False
    for attempt in (1, 2):
        if screen_has("입력해주세요"):        # 이전 시도의 팝업이 떠 있으면 먼저 확인
            ocr_or_dump_tap("확인", retries=2); nap(1.0)
        focused = False
        for dy in (122, 95, 150):
            lab = _find_text("사업자") or _find_text("등록번호")
            if not lab:
                if not _scroll_to("사업자", max_scroll=3, max_cy=BIZ_LABEL_MAX_CY):
                    out["err"] = "사업자 등록번호 라벨 사라짐"; return out
                lab = _find_text("사업자") or _find_text("등록번호")
                if not lab:
                    _screen_debug("사업자라벨-미발견")
                    out["err"] = "사업자 등록번호 라벨 사라짐"; return out
            _adb().tap(lab["cx"] - 14, lab["cy"] + dy); nap(1.2)   # 칸1 포커스 (탭 시 자동스크롤)
            if screen_has("입력해주세요"):    # 빈칸 확인 팝업 → '확인'(이게 필드 포커스+키패드 띄움)
                ocr_or_dump_tap("확인", retries=2); nap(1.0)
            if _keypad_up():
                focused = True; break
        if not focused:
            out["err"] = f"사업자번호 칸 포커스 실패(키패드 미등장, {attempt}차)"
            continue
        # ★칸1=3자리→자동 advance→칸2=2자리→자동 advance→칸3=5자리. 시스템 키패드라 adb input text 통함.
        for part in BIZ_NO:                   # ("507","18","15504")
            _input_text(part); nap(0.6)
        # 키보드 닫기 (BACK) — ★키보드가 실제로 떠 있을 때만.
        #   안 떠 있는데 BACK 을 누르면 **주문서가 닫히고 장바구니로 되돌아간다**(2026-08-25 실측).
        if _keypad_up():
            serial = hw._serial()
            subprocess.run([hw.ADB, "-s", serial, "shell", "input", "keyevent", "4"]); nap(1.0)
        t = _all_text()
        out["entered"] = all(p in t for p in BIZ_NO)
        if out["entered"]:
            out.pop("err", None)
            break
        out["err"] = f"사업자번호 입력 검증 실패({attempt}차)"
    # ★entered=False 를 ok:True 로 통과시키면 안 된다 (2026-08-31 실사고 — 사용자 지적
    #   "지출증빙 선택하고 사업자번호 안썼음"). 지출증빙을 **선택해 놓고** 번호가 비면
    #   결제하기가 검증에 막혀 카드 모달이 아예 안 뜬다 → `PAY_FAIL@hana_modal` 처럼
    #   **엉뚱한 지점**의 실패로 보고돼 원인을 못 찾는다(#8 실측: entered False → hana_modal 미도달).
    #   위 964/977/981 의 조기 return 들은 ok 를 안 넣으므로 그대로 실패로 잡힌다.
    #   (섹션 없음/지출증빙 비활성 = 정상 skip 은 앞에서 ok:True 로 이미 return 된다.)
    out["ok"] = bool(out.get("entered"))
    return out


def _selected_addr_text() -> str | None:
    """주문서 최상단에 **현재 선택된** 배송지 한 줄. 못 읽으면 None.

    ★'배송정보'/수령인 줄 바로 아래 밴드만 본다 — 화면 전체를 긁으면 주소 **목록**이 섞여
      틀린 주소인데도 맞다고 오판한다(2026-08-30 실사고).
    """
    its = _texts()
    anchor = next((it for it in its if it["text"].strip() in ("배송정보", "배송지")), None)
    band = [it for it in its if (anchor["cy"] - 20 < it["cy"] < anchor["cy"] + 320)] if anchor else            [it for it in its if it["cy"] < 700]
    addr = [it["text"].strip() for it in sorted(band, key=lambda z: z["cy"])
            if any(k in it["text"] for k in ("호", "동", "로", "길", "번지"))
            and "배송방법" not in it["text"] and "변경" not in it["text"]]
    return " / ".join(addr) if addr else None


def set_address() -> dict:
    """배송지 확인/변경. 선택된 배송지가 이미 ADDR_KEY(203호)면 skip(#5). 아니면(#6=경남 창녕군 등)
    배송정보 펼치기(chevron) → '변경 >' → 주소목록서 203호 탭(자동선택+복귀).
    ⚠️배송정보는 주문서 최상단 → 먼저 스크롤업(_scroll_to 는 아래로만 탐색하므로 직접 위로)."""
    out = {}
    # 주문서 최상단(배송정보)으로 스크롤업
    # ★판독은 OCR+dump 병합 (2026-08-25): 윈도우 OCR 이 주소줄을 못 읽으면 "이미 203호"인데도
    #   변경 절차로 들어가 'ADDR_FAIL:주소 변경 버튼 미발견' 으로 죽는다(#12 resume 실측).
    for _ in range(6):
        t = _all_text()
        if ADDR_KEY in t or "배송정보" in t or "배송지" in t:
            break
        _adb().swipe(540, 700, 540, 1700, 400); nap(0.6)
    # 접힌 상태서 선택된 주소가 이미 203호면 변경 불필요
    # ★★판정 범위를 **선택된 배송지 줄**로 좁힌다 (2026-08-30 실사고).
    #   종전 `ADDR_KEY in _all_text()` 는 **화면 전체**를 봤다 — 주소 목록·최근배송지·화면 밖
    #   접힌 노드에 '203호' 가 하나라도 있으면 "이미 203호" 로 skip 했다. 그 결과 실제 선택된
    #   배송지가 **'화곡동 424-1 동선하우징 402호 선물포장'** 인데도 그대로 결제 직전까지 갔다.
    #   배송지는 틀리면 물건이 다른 데로 간다 — 화면 어딘가가 아니라 **선택된 그 줄**을 봐야 한다.
    if not WIN_ONLY_FIX:                     # ★맥: 종전 판정 그대로
        if ADDR_KEY in _all_text():
            out["skip"] = f"기본배송지 이미 '{ADDR_KEY}'"; out["ok"] = True; return out
        sel = None
    else:
        sel = _selected_addr_text()
    if WIN_ONLY_FIX and sel is None:
        print(f"   [addr] ⚠️ 선택 배송지 줄 판독 실패 — 변경 절차로 진행", flush=True)
    elif sel and ADDR_KEY in sel:
        out["skip"] = f"기본배송지 이미 '{ADDR_KEY}'"; out["selected"] = sel; out["ok"] = True
        return out
    elif sel:
        print(f"   [addr] 선택 배송지가 '{sel[:40]}' — '{ADDR_KEY}' 로 변경한다", flush=True)
        out["was"] = sel
    # 배송정보 펼치기 (우측 chevron) → 주소 '변경 >' 노출 (#6 검증: 접힘 상태선 변경버튼 숨김)
    bi = _find("배송정보", contains=True)
    if bi:
        _adb().tap(1000, bi["cy"]); nap(1.5)
    # 주소 '변경 >' (배송방법/픽업 제외, 우측 최대 cx)
    chgs = [it for it in _texts() if "변경" in it["text"]
            and "배송방법" not in it["text"] and "픽업" not in it["text"] and "클릭" not in it["text"]]
    if not chgs:
        _screen_debug("주소변경버튼-미발견")
        out["err"] = "주소 변경 버튼 미발견"; return out
    chg = max(chgs, key=lambda it: it["cx"])
    _adb().tap(chg["cx"], chg["cy"]); nap(2.0)
    # 주소목록서 '203호' 포함 주소 탭 (탭=자동선택+복귀, 별도 확인버튼 없음)
    tgt = _scroll_to(ADDR_KEY, max_scroll=6)
    if not tgt:
        out["err"] = f"'{ADDR_KEY}' 주소 미발견"; return out
    _adb().tap(tgt["cx"], tgt["cy"]); nap(2.0)
    ocr_tap("선택", retries=1) or ocr_tap("확인", retries=1) or ocr_tap("적용", retries=1)
    nap(1.5)
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
        _adb().swipe(540, 1700, 540, 800, 400); nap(0.7)
    for _ in range(9):                                          # ↑ 못 찾으면 위로 스캔
        _adb().swipe(540, 800, 540, 1700, 400); nap(0.7)
        r = _scan()
        if r:
            return r
    return {"ok": False, "err": "청구할인 배너 미발견"}


def _screen_debug(tag: str) -> None:
    """실패 지점의 화면 텍스트를 로그에 남긴다 — 추측 대신 실측으로 고치기 위해(2026-08-25)."""
    try:
        o = sorted(_ocr_texts(cap()), key=lambda z: z["cy"])
        print(f"   [screen:{tag}] OCR {len(o)}개: "
              + " | ".join(f"{t['cy']}:{t['text'][:18]}" for t in o[:18]), flush=True)
    except Exception as e:
        print(f"   [screen:{tag}] 판독 실패 {e}", flush=True)


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
    # 1)+2) '다른 결제수단' 라디오 → 그리드의 '신용카드' → '카드 선택' 드롭다운 노출
    # ★탭하고 끝내지 말고 **효과를 검증**한다 (2026-08-25). 실측 실패 2종:
    #   ㉠ OCR 이 라디오를 못 읽어 dump 좌표로 폴백 → 엉뚱한 곳을 눌러 '㉧ 최근 사용한 결제수단'이
    #      선택된 채 남았다(화면덤프로 확인). 좌표는 **OCR 만** 신뢰한다.
    #   ㉡ 스크롤 폴백은 결제수단 구역을 지나쳐 주문서 꼭대기까지 올라간다 → 탭 후에는 스크롤 금지.
    #   그래서 [찾기 → 탭 → 그리드 등장 확인] 을 한 묶음으로 최대 4회 반복한다.
    ok_grid = False
    for attempt in range(4):
        if _find_text("카드 선택", contains=False):
            out["already_credit"] = True; ok_grid = True
            break
        db = ocr_find("다른 결제수단", contains=True)     # ★OCR 전용 — 오탭 방지
        if not db:
            # ★고정방향 폴백(아래2회→위2회)을 _scroll_to 로 교체 (2026-08-30 #1 실측 CARD_FAIL).
            #   실패 원인은 **판독이 아니라 위치**였다 — OCR 은 '다른 결제수단' 을 (230,1610) 에
            #   멀쩡히 읽는다(윈도우에서 실측). 그런데 폴백이 멈춘 자리에선 그 행이 cy≈2141,
            #   즉 화면 하단에 **고정된 '…원 결제하기' 버튼 뒤에 깔려** OCR 목록에서 사라졌다.
            #   → 쿠폰 단계와 같은 처방: **max_cy 로 버튼 위에 오게** 데려온다.
            #   (플랫폼 분기 없음 — _scroll_to 가 맥=legacy / 윈도우=신규로 내부에서 갈린다.)
            if _scroll_to("다른 결제수단", contains=True, max_scroll=8, max_cy=PAY_BTN_TOP_CY):
                continue                                   # 다음 회전의 ocr_find 가 잡는다
            nap(0.8)
            continue
        _adb().tap(db["cx"], db["cy"]); nap(2.5)           # 라디오(멱등) — 그리드 렌더 대기
        for _ in range(8):                                  # 제자리 폴링 (스크롤 금지)
            if _find_text("카드 선택", contains=False):
                out["already_credit"] = True; ok_grid = True
                break
            sc = ocr_find("신용카드", contains=False)
            if sc:
                _adb().tap(sc["cx"], sc["cy"]); nap(2.0)
                ok_grid = bool(_find_text("카드 선택", contains=False)) or True
                break
            nap(0.8)
        if ok_grid:
            break
    if not ok_grid:
        _screen_debug("결제수단그리드-미도달")
        out["err"] = "'다른 결제수단'→'신용카드' 그리드 미도달(4회 재시도)"; return out
    # 3) '카드 선택' 드롭다운(행 우측 chevron x≈987) → 카드목록 팝업.
    #    ★exact 매칭 필수 — contains 면 안내문 '...비씨카드 선택 시...'의 '카드 선택' 부분문자열을 오매칭(2026-06-02 #11 버그).
    lab = _scroll_to("카드 선택", contains=False, max_scroll=4)
    if not lab:
        out["err"] = "'카드 선택' 드롭다운 미발견"; return out
    _adb().tap(987, lab["cy"]); nap(1.8)
    # 4) 카드목록 팝업서 당일카드 탭 (OCR 라디오 글리프 '_'/'•' 접두 대비 contains 매칭)
    if not ocr_or_dump_tap(target, contains=True, retries=4):
        out["err"] = f"카드목록 '{target}' 선택 실패"; return out
    nap(2.0)
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


def _blog2(m: str) -> None:
    print(f"   [pay] {m}", flush=True)


def _tap_pay_button(timeout: float = 10) -> bool:
    """하단 'NNN원 결제하기' 버튼 탭. ★버튼이 나타날 때까지 기다렸다 누른다 (2026-08-25).

    키보드를 닫은 직후엔 레이아웃이 정착하기 전이라, 그 순간 OCR 하면 하단 고정바를 못 읽고
    **화면 중간의 다른 '결제하기'(cy≈1777)를 눌러** 아무 일도 안 일어난다
    (실측: #8·#9 가 여기서 `PAY_FAIL@kb_modal` 로 죽었다 — 이름이 원인을 가렸다).
    """
    # ★좌표(cy>2000) 대신 **텍스트 패턴**으로 고른다: 진짜 버튼만 금액을 달고 있다
    #   ('544,922원 결제하기'). 키보드를 닫아도 webview 뷰포트가 복원되지 않아 하단 바가
    #   cy≈1777 로 올라와 있는 경우가 있어(실측), 좌표 기준은 조용히 빗나간다.
    pat = re.compile(r"[\d,]{4,}\s*원\s*결제하기")

    def _find_btn():
        return next((it for it in _texts() if pat.search(it["text"])), None)

    # ★탭하고 끝내지 말고 **전이를 검증**한다 (2026-08-25 실측).
    #   키보드가 접히는 중에 찍힌 프레임을 읽으면 좌표가 어긋나(같은 버튼이 cy 2170 → 1777 로 읽힘)
    #   탭이 헛돌고, 주문서가 그대로인 채 `PAY_FAIL@kb_modal` 로 죽는다.
    for attempt in range(3):
        end = time.time() + timeout
        pay = None
        while time.time() < end:
            pay = _find_btn()
            if pay:
                break
            nap(0.7)
        if not pay:
            break
        _blog2(f"결제하기 탭#{attempt} @({pay['cx']},{pay['cy']}) '{pay['text'][:24]}'")
        _adb().tap(pay["cx"], pay["cy"])
        nap(2.5)
        t = " ".join(x["text"] for x in _texts())
        if "KB Pay" in t or "사업자 등록번호를" in t or "결제수단" not in t or not _find_btn():
            return True                     # 전이 발생(모달/팝업/화면이동)
        _blog2("탭이 먹지 않음 — 화면 그대로, 재시도")
        nap(1.5)
    _screen_debug("결제버튼-전이실패")
    return ocr_or_dump_tap("결제하기", contains=True)


def _fill_biz_no_if_prompted() -> bool:
    """'사업자 등록번호를 입력해주세요.' 팝업이 떴으면 그 자리에서 채운다. 채웠으면 True.

    ★이게 이 앱의 **정상 루트**다 (2026-08-25 실측). 주문서에서 빈칸을 직접 탭해 포커스를 잡는
    건 실패율이 높은데(칸이 webview 라 dump 에 EditText 로도 안 나온다), **결제하기를 누르면
    앱이 스스로 팝업을 띄우고 '확인'을 누르는 순간 칸에 포커스 + 키보드가 뜬다**
    (`mInputShown=true` 로 확인). 그래서 여기서 입력하고 키보드를 닫은 뒤 결제하기를 다시 누른다.
    안 그러면 `PAY_FAIL@kb_modal` 처럼 **엉뚱한 이름**으로 죽는다(오늘 #8 이 그랬다).
    """
    if not screen_has("사업자 등록번호"):
        return False
    print("   [현금영수증] 사업자번호 미입력 팝업 → 확인 후 입력", flush=True)
    ocr_or_dump_tap("확인", retries=2); nap(1.5)
    if not _ime_shown():
        print("   [현금영수증] ⚠️ 확인 눌렀는데 키보드가 안 떴다 — 입력 불가", flush=True)
        return False
    for part in BIZ_NO:
        _input_text(part); nap(0.6)
    t = _all_text()
    ok = all(p in t for p in BIZ_NO)
    _close_ime()                       # ★키보드가 남으면 하단 '결제하기'가 가려진다
    nap(2.0)                           # ★webview 뷰포트 복원 대기 — 접히는 중 프레임을 읽으면 좌표가 어긋난다
    print(f"   [현금영수증] 사업자번호 입력 {'성공' if ok else '검증실패'}", flush=True)
    return ok


def _ime_shown() -> bool:
    """소프트 키보드가 실제로 떠 있는가 (dumpsys input_method)."""
    out = subprocess.run(["adb", "shell", "dumpsys", "input_method"],
                         capture_output=True, text=True, encoding="utf-8",
                         errors="replace", timeout=10).stdout or ""
    return "mInputShown=true" in out


def _close_ime() -> bool:
    """키보드가 떠 있으면 BACK 으로 닫는다. ★떠 있을 때만 — 안 떠 있으면 BACK 은 페이지 뒤로가기다.

    왜 필요한가 (2026-08-25 실측): 사업자번호 입력 후 키보드가 열린 채로 남으면 화면 하단이 가려져
    **동의행 스크롤도, 하단 '결제하기' 탭도 실패**한다('동의행 미발견' → '원결제하기 실패').
    """
    if not _ime_shown():
        return False
    subprocess.run([hw.ADB, "-s", hw._serial(), "shell", "input", "keyevent", "4"])
    nap(1.0)
    return True


def agree_required() -> bool:
    """필수 동의 체크박스 체크. ★좌표 하드코딩 대신 **픽셀 박스검출 + 체크검증 + 재시도**
    (옛 cx-210 오프셋이 박스 빗나가 미체크→'동의하셔야 구매' 팝업으로 결제막힘, 2026-06-02 #12).
    체크 확인될 때까지 최대 5회 탭(이미 체크면 탭 안 함=토글오프 방지). ⚠️동의는 결제수단 아래 → 안 보이면 스크롤."""
    nap(1.0)
    def _ag():
        return next((it for it in _ocr_texts(cap())
                     if "동의" in it["text"] and ("필수" in it["text"] or "전체" in it["text"])), None)
    ag = None
    for _ in range(6):                       # 동의가 아래에 있을 수 있어 스크롤하며 탐색
        ag = _ag()
        if ag:
            break
        _adb().swipe(540, 1500, 540, 800, 450); nap(0.8)
    if not ag:
        _screen_debug("동의행-미발견")
        return False
    for _ in range(5):
        bx, checked = _agree_box(ag["cy"])
        print(f"   [agree] cy={ag['cy']} box_x={bx} checked={checked}", flush=True)
        if checked:
            return True
        _adb().tap(bx, ag["cy"]); nap(1.0)
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


# ★롯데 PAYCO/ARS 경로도 **삭제했다** (사용자 지시 2026-08-07).
#   구 `pay_lotte_payco`(PAYCO 경유) + `handle_ars_call`(ARS 자동응답) — 호출자 0개였고,
#   ARS 는 전화 의존이라 무인 실행이 안 된다. 게다가 `handle_ars_call` 의 다이얼패드 '1' 좌표는
#   **실측이 아닌 추정값**이라(코드에 경고가 있었다) 그대로 두면 언젠가 오탭한다.
#   정본 = `pay_lotte_samsung_general` → 3사 공용 `pay_samsung`(카드번호 직접, ARS 회피).
# ──────── D'. 삼성 일반결제 (카드번호 직접 — PAYCO/ARS 완전 회피) ────────

# _card_secrets = hmall_hyundai_buy 에서 import (정본=hmall, 양 몰 공용). 상단 import 참조.
# ★로컬 OCR 셔플 입력(`_tap_shuffle_retry`)은 삭제했다 (사용자 지시 2026-08-07). 호출자는 이미 0개였고,
#   삼성 결제는 `pay_samsung`(3사 공용) → `samsung_enter` 비전 핸드세이크로만 입력한다. 되살리지 말 것.


def pay_lotte_samsung_general() -> dict:
    """롯데홈쇼핑 삼성 일반결제 — ★**3사 공용 정본 `hmall_hyundai_buy.pay_samsung` 에 위임**(2026-08-02).

    종전엔 몰별로 같은 시퀀스를 두 벌 갖고 있었다(현대몰 것은 라이브 미검증 포팅본).
    삼성 SDK 화면은 가맹점 무관 동일하므로 한 벌만 두고, **몰이 다른 '원 결제하기' 탭만 주입**한다.
    롯데는 하단 'NNN원 결제하기'(cy>2000)를 눌러야 해서 그 부분만 여기서 넘긴다.

    검증: 2026-08-02 #16·#3·#5·#15 4계정 연속 라이브 완주(주문 I20794/I21621/I22042/I22245).
    ★2026-08-06: 삼성도 NH 처럼 **항상 비전 핸드세이크**로 바뀌었다(환경변수 없음).
      카드번호 화면에서 `manual=True` 로 정지 → `python3 -m phone_auto.samsung_enter` 로 이어받고,
      마무리는 `samsung_enter finish_lotte <계정> [combo=N]` (대장 + 뷰티 + 구매사은).
      옛 `PAY_VISION_MODE=1` 게이트는 제거됐다 — 깜빡하면 로컬 OCR 경로로 조용히 떨어져 실패했다.
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
    ok, msg = preflight_card_app("KB")          # ★USB 디버깅이면 KB Pay 가 안 뜬다 (현대몰과 같은 정본)
    if not ok:
        out["err"] = f"KB_APP_BLOCKED: {msg}"; return out
    if screen_has("다음에도") or screen_has("사용할까요"):
        ocr_tap("사용할게요", contains=True, retries=2)
    # 1) (원)결제하기 — 하단 'NNN원 결제하기'
    if not _tap_pay_button():
        out["err"] = "원결제하기 실패"; return out
    time.sleep(3.0)
    # ★사업자번호 미입력 팝업이면 여기서 채우고 결제하기를 다시 누른다 (정상 루트 — 위 함수 주석 참조)
    if _fill_biz_no_if_prompted():
        _tap_pay_button()
        time.sleep(3.0)
    # 2) KB SDK 모달 → 'KB Pay 결제' 박스(노란 앱카드)
    out["step"] = "kb_modal"
    if not wait_text("KB Pay", timeout=12):
        _screen_debug("KB모달-미도달")
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


def pay_lotte_hana() -> dict:
    """하나카드 = 하나Pay 앱카드. hmall `pay_hana` 와 동일 — 카드앱 구간은 몰 무관이라 flow 재사용.
    ⚠️ 롯데에선 **미검증**(2026-08-28 첫 시도). 실패해도 카드 인증 전에 멈추므로 재시도 안전.
    경로: (원)결제하기 → 하나 SDK '하나Pay 하나카드 결제' 박스 → 하나앱(com.hanaskcard.paycla)
      → '다음' → nFilter 키패드 pin6(source=sequential_logo: 로고칸 검출 순서매핑, 숫자 OCR 안 함)
      → 롯데 복귀 주문완료 폴링. ⚠️실 결제."""
    pin6 = _card_secrets().get("하나", {}).get("pin6")
    if not pin6:
        return {"step": "secrets", "err": "card_secrets['하나'].pin6 없음"}
    # ★하나앱은 이전 결제 세션 잔재가 남으면 진입 즉시 '안정적인 이용을 위해 앱을 다시 실행해주세요'
    #   경고를 띄우고, 그게 '다음' 버튼을 덮어 flow step3 이 timeout 한다
    #   (2026-08-28 #5·#7 실측 — 연속 결제 2~3계정째부터 재현. 앞선 계정 결제 후 잔재가 원인).
    #   결제 시작 **전에** 종료해 깨끗한 상태로 진입한다. 카드 인증 전이라 안전.
    subprocess.run(["adb", "shell", "am", "force-stop", "com.hanaskcard.paycla"],
                   capture_output=True)
    time.sleep(1.0)
    out = {"step": "order_sheet", "card": "하나"}
    if screen_has("다음에도") or screen_has("사용할까요"):
        ocr_tap("사용할게요", contains=True, retries=2)
    # ★_tap_pay_button 을 쓴다 (2026-08-31). 좌표(cy>2000) 기준은 하단 고정바가 cy≈1968/1777 로
    #   올라와 있을 때 조용히 빗나가고, 폴백 `ocr_tap` 은 pick="bottom" 이라 **화면 중간의 다른
    #   '결제하기'**를 누른다 → 모달이 안 떠서 `PAY_FAIL@hana_modal` 처럼 엉뚱한 이름으로 죽는다
    #   (#8 실측: `ocr_tap '결제하기' @(540,1968)` → 하나 모달 미도달. 같은 사고가 KB 에서도 있었고
    #   그래서 _tap_pay_button 이 만들어졌는데 하나·삼성 경로만 안 쓰고 있었다).
    if not _tap_pay_button():
        out["err"] = "원결제하기 실패"; return out
    time.sleep(3.0)
    # 2) 하나 SDK 결제방식 화면 → '하나Pay 하나카드 결제' 박스 (MG+/간편결제/SMS/일반 아님)
    out["step"] = "hana_modal"
    if not wait_text("하나카드 결제", timeout=15):
        # ★실패 순간의 화면을 남긴다 (2026-08-31): '미도달'만으론 결제하기 탭이 안 먹은 건지,
        #   다른 SDK 화면이 뜬 건지, 팝업에 막힌 건지 구분이 안 돼 원인을 못 찾았다.
        _screen_debug("하나모달-미도달")
        out["err"] = "하나 결제방식 화면 미도달"; return out
    if not ocr_tap("하나카드 결제", contains=True):
        out["err"] = "'하나Pay 하나카드 결제' 박스 실패"; return out
    # 3) 하나앱 구간 = hmall 검증 flow 재사용 (16=앱대기 … 21=결제진행 대기). 22 이후는 hmall 전용이라 제외.
    out["step"] = "hana_app"
    flow = json.loads(HANA_FLOW.read_text(encoding="utf-8"))["flow_payment"]
    try:
        FlowRunner(use_camera=False).run(flow[16:22], {})
    except Exception as e:
        out["err"] = f"하나앱 SDK 실패: {e}"; return out
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
    # ★_tap_pay_button 을 쓴다 (2026-08-31). 좌표(cy>2000) 기준은 하단 고정바가 cy≈1968/1777 로
    #   올라와 있을 때 조용히 빗나가고, 폴백 `ocr_tap` 은 pick="bottom" 이라 **화면 중간의 다른
    #   '결제하기'**를 누른다 → 모달이 안 떠서 `PAY_FAIL@hana_modal` 처럼 엉뚱한 이름으로 죽는다
    #   (#8 실측: `ocr_tap '결제하기' @(540,1968)` → 하나 모달 미도달. 같은 사고가 KB 에서도 있었고
    #   그래서 _tap_pay_button 이 만들어졌는데 하나·삼성 경로만 안 쓰고 있었다).
    if not _tap_pay_button():
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
    # ★이 화면은 **OCR 이 사실상 실명**이다 (2026-08-25 실측: OCR 1개 vs dump 277개).
    #   #8 이 여기서 '적립신청 버튼 미발견'으로 죽었고, dump 로 (760,1815) 를 찾아 손으로 눌러 살렸다
    #   — 뷰티포인트는 **주문완료 화면 only(now-or-never)** 라 놓치면 그 건은 복구 불가다.
    #   → OCR 먼저, 없으면 **dump 좌표로 탭**한다.
    def _tap_claim() -> bool:
        sj = next((it for it in _ocr_texts(cap()) if "적립신청" in it["text"]), None)
        if sj and _tap_fresh("적립신청", retries=2):
            _blog(f"적립신청 OCR 탭 @({sj['cx']},{sj['cy']})")
            return True
        dj = next((t for t in _dump_texts()
                   if t["text"].strip() in ("적립신청", "적립 신청") and 100 < t["cy"] < 2350), None)
        if dj:
            _blog(f"적립신청 dump 탭 @({dj['cx']},{dj['cy']})")
            _adb().tap(dj["cx"], dj["cy"]); time.sleep(1.0)
            return True
        return False

    def _claim_done():
        return next((t for t in _texts() if "적립" in t["text"] and "완료" in t["text"]), None)

    if not _tap_claim():
        out["err"] = "적립신청 버튼 미발견(OCR+dump)"; _blog("✗ 적립신청 버튼 미발견 → 적립 실패"); return out
    # ★완료판정 = OCR 텍스트 1개 안에 '적립'+'완료' 동시 존재 (모달 "뷰티포인트 적립신청이 완료되었습니다").
    #   주문완료 화면의 "주문이 완료 되었습니다"는 '적립'이 없어 자동 배제 → false-positive 차단.
    # ★탭이 조용히 안 먹는 경우가 있다 (2026-08-25 #11: dump 탭은 나갔는데 완료 모달이 끝내 안 떴다).
    #   뷰티포인트는 **주문완료 화면 only(now-or-never)** 라 한 번 놓치면 그 건은 복구 불가 →
    #   완료문구가 안 뜨면 **다시 눌러본다**(최대 3회). 이미 신청됐으면 앱이 '이미 신청' 류로 답한다.
    done_text = None
    for attempt in range(3):
        for p in range(6):
            time.sleep(0.7)
            hit = _claim_done()             # ★OCR+dump — 완료 모달도 OCR 이 못 읽는다(실측)
            if hit:
                done_text = hit["text"]; break
        if done_text:
            break
        if attempt < 2:
            _blog(f"완료문구 미확인 → 적립신청 재탭 #{attempt+1}")
            if not _tap_claim():
                break
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
    → '구매사은·혜택' 섹션의 '최대 N% 적립'/'최대 N만 적립' (★ignore 제외) → '광세일' 행사페이지 → '혜택 신청하기'.

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
            prod = next((it for it in _texts()          # ★OCR+dump — 주문완료 화면은 OCR 이 거의 실명
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
    # 4) ★'구매사은 · 혜택' 섹션까지 스크롤 → 그 섹션 안의 '최대 N% 적립'/'최대 N만 적립' 카드 탐색 (6/2 #9 라이브 확정).
    #    상품상세엔 "최대 N% 적립"이 3종 존재:
    #      ① 상단 프로모 배너 "'광세일' 구매시 최대 N% 적립금" (섹션 밖, 탭하면 향수 등 엉뚱한 페이지)
    #      ② '구매/리뷰 적립혜택' "최대 NNNP/N원 적립" (%·만 없음 → 정규식에서 자동 제외)
    #      ③ '구매사은·혜택' 섹션 카드 "최대 N% 적립" 또는 "최대 N만(원) 적립" ← ★정답(탭→광세일 행사페이지→혜택 신청하기)
    #    판별 = '구매사은' 헤더(cy) **아래** + 정규식 `최대\d+[%만]적립`(단위·수치 매일 변동,
    #    2026-08-23 사용자: 8/18 '최대 5만 적립' 형식이라 %전용 정규식이 못 찾음) + ignore 제외.
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
                m = re.search(r"최대\s*(\d+)\s*(?:%|만\s*원?)\s*적립", it["text"])
                if m and not any(k in it["text"] for k in ignore):
                    cands.append((int(m.group(1)), it))
            if cands:
                cands.sort(key=lambda x: x[0], reverse=True)     # 최고 수치 적립 이벤트 (%·만 혼재 시 통상 카드 1장)
                card = cands[0][1]; break
        _adb().swipe(540, 1500, 540, 900, 450); time.sleep(0.9)
    if not card:
        out["err"] = "구매사은 '최대 N%/N만 적립' 카드 미발견(광세일 행사상품 아닐 수 있음)"; return out
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


def _from_order_sheet(res: dict, idx: int, card: str | None = None,
                      goods_no: str | None = None, combo_idx: int | None = None) -> dict:
    """**주문서 화면에서부터** 끝(주문완료·뷰티·구매사은)까지. buy_one 과 resume 의 공용 정본.

    ★2026-08-25 분리: 종전엔 이 흐름이 buy_one 안에만 있어서, 중간에 실패하면 resume 이
      이어붙일 수 없었다(주문완료 뒷처리만 가능). 사용자 지시 "실패한 부분부터 다시" 를 위해
      buy_one(로그인·카트) 과 분리한다. 주문서 단계는 전부 멱등이라 재진입해도 이중적용이 없다.
    """
    # 결제설정 순서 (★쿠폰이 적립금/L.POINT 리셋 → 포인트는 쿠폰 뒤 / ★카드가 현금영수증 리셋 → cash 는 카드 뒤):
    # 주소(최상단) → 할인쿠폰 → 플러스쿠폰 → 포인트(전액) → 카드 → 현금영수증 → 동의
    res["addr"] = set_address()
    if not res["addr"].get("ok"):
        res["status"] = f"ADDR_FAIL:{res['addr'].get('err')}"; return res
    if os.environ.get("SKIP_COUPONS") == "1":
        # ★사람이 폰에서 직접 쿠폰을 고른 뒤 이어받을 때 쓴다 (2026-08-25 #12).
        #   자동 단계가 모달을 다시 열면 '선택완료' 과정에서 수동 선택이 초기화될 수 있다.
        res["dc"] = {"applied": None, "skip": "SKIP_COUPONS=1 (수동 적용)", "ok": True}
        res["pc"] = {"applied": None, "skip": "SKIP_COUPONS=1 (수동 적용)", "ok": True}
        res["pts"] = use_all_points()
        print(f"[#{idx}] 혜택 — 쿠폰은 수동 적용분 유지(SKIP_COUPONS=1) / 포인트 {res['pts']}", flush=True)
        return _order_sheet_tail(res, idx, card, goods_no, combo_idx)
    res["dc"] = set_discount_coupons()
    # ★0장 + 에러면 한 번 더 (2026-08-25 #12: '할인쿠폰 변경 버튼 미발견'으로 10% 2장이 빠져
    #   606,181원이 될 뻔했다 — MAX_PAY 가드가 막았다). 이미 적용된 상태면 재실행이 무해하다
    #   (모달의 '선택해 주세요' 자리표시가 없어 루프가 즉시 끝난다).
    if res["dc"].get("applied", 0) == 0 and res["dc"].get("err"):
        print(f"[#{idx}] 할인쿠폰 0장({res['dc']['err']}) → 재시도", flush=True)
        res["dc"] = set_discount_coupons()
    res["pc"] = set_plus_coupons()
    if res["pc"].get("applied", 0) == 0 and res["pc"].get("err"):
        print(f"[#{idx}] 플러스쿠폰 0장({res['pc']['err']}) → 재시도", flush=True)
        res["pc"] = set_plus_coupons()
    res["pts"] = use_all_points()
    # ★주문서 혜택 적용 결과를 **숫자로** 남긴다 (2026-08-25 신설). 종전엔 res 에만 담고 안 찍어서,
    #   쿠폰이 안 걸린 채 결제가 시도돼도 로그만 보면 알 수 없었다(#8 승인시도 682,167원 vs 시트 기대치 괴리).
    print(f"[#{idx}] 혜택 적용 — 할인쿠폰 {res['dc']} / 플러스쿠폰 {res['pc']} / 포인트 {res['pts']}", flush=True)
    return _order_sheet_tail(res, idx, card, goods_no, combo_idx)


def _order_sheet_tail(res: dict, idx: int, card, goods_no, combo_idx) -> dict:
    """혜택 적용 이후 — 카드선택 → 현금영수증 → 동의 → 결제 → 주문완료 뒷처리. (쿠폰 단계와 분리)"""
    # 당일 할인카드 자동감지 + 선택 (★하드코딩 제거 — 청구할인 배너 최고%). ★cash 보다 먼저(카드가 지출증빙 리셋).
    sc = select_card_lotte(day=card)
    res["card"] = sc
    if not sc.get("ok"):
        res["status"] = f"CARD_FAIL:{sc.get('err')}"; return res
    use_card = sc["card"]
    print(f"[#{idx}] 당일카드 감지/선택 = {use_card} ({sc.get('pct')}%) via {sc.get('via')}", flush=True)
    # ★포인트 사용량을 넘긴다 — 현금영수증 필수 여부는 **화면 판독이 아니라 포인트 사용 여부**로 갈린다.
    res["cash"] = set_cash_receipt(int((res.get("pts") or {}).get("used") or 0))
    print(f"[#{idx}] 현금영수증 — {res['cash']}", flush=True)
    if not res["cash"].get("ok"):
        # ★결제 시도 전에 **여기서** 멈춘다 — 지출증빙만 켜지고 사업자번호가 비면 결제하기가
        #   막혀서, 그 다음 카드 모달 단계가 `PAY_FAIL@*_modal` 로 오표시된다(2026-08-31 #8).
        res["status"] = (f"CASH_RECEIPT_FAIL:{res['cash'].get('err')} — 지출증빙 선택됐는데 "
                         f"사업자번호 미입력. 이 상태로는 결제가 막힌다. 결제 안 함")
        print(f"[#{idx}] ⛔ {res['status']}", flush=True)
        return res
    if _close_ime():                         # ★키보드가 남아 있으면 동의·결제하기가 가려진다
        print(f"[#{idx}] 키보드 닫음(IME) — 동의/결제하기 노출", flush=True)
    if not agree_required():                 # ★미체크면 결제 시 '동의하셔야' 팝업으로 막힘 → 결제 시도 전 abort
        res["status"] = "AGREE_FAIL:필수동의 체크 실패(픽셀검증)"; return res
    print(f"[#{idx}] 필수동의 체크 확인됨", flush=True)
    # 카드별 결제경로 분기: 롯데=LOCA(137601) / 삼성=일반결제(카드번호 직접, PAYCO/ARS 회피).
    #   ★PAYCO 경로(구 pay_lotte_payco, #10 검증)는 **2026-08-07 삭제** — ARS 전화의존이라 무인불가.
    #   그 외 카드 = 라이브 검증 필요(false-auto 금지).
    # ★PIN 직전 금액 확인 (READ_FIRST 규칙) — 하단 'NNN원 결제하기' 버튼 텍스트를 그대로 남긴다.
    _amt = next((it["text"] for it in _ocr_texts(cap())
                 if "결제하기" in it["text"] and it["cy"] > 2000), None)
    res["amount_text"] = _amt
    print(f"[#{idx}] 결제 예정 금액: {_amt or '(판독실패)'}", flush=True)
    # ★상한 가드 (2026-08-25): 쿠폰이 한 장도 안 걸린 채 결제되는 사고를 **코드가** 막는다.
    #   실측 — 쿠폰 0장이면 700,000원, 정상 적용이면 530,247원. 사람이 로그를 봐야만 알 수 있으면
    #   무인 실행에서 조용히 15만원을 더 낸다. MAX_PAY 넘으면 결제하지 않고 그 계정을 실패시킨다.
    _max = os.environ.get("MAX_PAY")
    # ★금액을 못 읽으면 **가드가 통째로 사라진다** (2026-08-31 실측: '결제 예정 금액: (판독실패)'
    #   인데도 그대로 결제 실행됨). 종전 조건 `if _max and _amt:` 은 _amt=None 이면 검사를 건너뛰어
    #   MAX_PAY 를 준 의미가 없어진다 — 쿠폰 누락분을 막으라고 켠 가드가 정작 판독이 흔들릴 때
    #   조용히 열린다. 가드를 켰으면 **못 읽는 것도 실패**로 취급한다.
    if _max and not _amt:
        res["status"] = ("AMOUNT_UNREADABLE — 결제 예정 금액 판독 실패. MAX_PAY 가드가 "
                         "무력화되므로 결제하지 않는다")
        print(f"[#{idx}] ⛔ {res['status']}", flush=True)
        return res
    if _max and _amt:
        _n = re.sub(r"[^0-9]", "", _amt.split("원")[0])
        if _n and int(_n) > int(_max):
            res["status"] = f"AMOUNT_TOO_HIGH({_n} > MAX_PAY {_max}) — 혜택 미적용 의심, 결제 안 함"
            print(f"[#{idx}] ⛔ {res['status']}", flush=True)
            return res
    if os.environ.get("STOP_BEFORE_PAY") == "1":
        # 검증용 — 주문서까지만 만들고 결제 직전에 멈춘다(실돈 안 나감). 쿠폰/금액 확인에 쓴다.
        res["status"] = f"STOP_BEFORE_PAY(금액={_amt})"
        print(f"[#{idx}] ⏹ STOP_BEFORE_PAY — 결제 직전 정지", flush=True)
        return res
    print(f"[#{idx}] ⚠️ 결제 실행 ({use_card})", flush=True)
    if use_card == "롯데":
        pay = pay_loca()
    elif use_card == "삼성":
        pay = pay_lotte_samsung_general()       # ✅ #12 G05038 라이브검증
    elif use_card == "KB":
        pay = pay_lotte_kb()                    # ✅ #13 G70658 라이브검증 (KB Pay 간편결제)
    elif use_card == "현대":
        pay = pay_lotte_hyundai()               # ✅ 2026-06-12 #1 B87302 라이브검증 (앱카드)
    elif use_card == "하나":
        pay = pay_lotte_hana()                  # ⚠️롯데 미검증 (2026-08-28 첫 시도, hmall pay_hana 재사용)
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
        # ★NH·삼성 공통 — 카드번호 화면에서 비전 인계로 정지(실패 아님). 이어받기 러너가 다르다:
        #   NH=phone_auto/nh_enter / 삼성=phone_auto/samsung_enter. 마무리는 finish_lotte 로 동일.
        runner = "samsung_enter" if use_card == "삼성" else "nh_enter"
        res["status"] = (f"{use_card}_HANDOFF(카드번호 화면 — 에이전트 비전 입력 대기, "
                         f"이어받기: python3 -m phone_auto.{runner})")
        # ★다음에 칠 명령을 **완성해서** 찍는다(현대몰 _handoff_stop 과 같은 이유 — 사람이 다시
        #   조립하게 두면 card=/combo= 를 빠뜨려 대장이 NH·식품으로 잘못 적힌다).
        steps = ("card → cvc → next → pin6 → next('다음') → cert → certpw"
                 if use_card == "삼성" else
                 "box1 → box2 → box3 → box4 → cvc → confirm → pinfield → pin6 → confirm")
        cb = f" combo={combo_idx}" if combo_idx is not None else ""
        print(f"\n{'='*54}\n★ #{idx} {use_card} 인계 대기 — 여기서 멈춥니다\n"
              f"  1) 화면 판독:  python3 -m phone_auto.{runner} shot /tmp/kp.png\n"
              f"  2) 입력 순서:  {steps}\n"
              f"  3) 마무리:     python3 -m phone_auto.{runner} finish_lotte {idx}{cb} "
              f"card={use_card}\n"
              f"     (뷰티포인트는 주문완료 화면에서만 된다 — 화면 벗어나기 전에 실행할 것)\n"
              f"{'='*54}", flush=True)
        return res
    if not pay.get("ok"):
        res["status"] = f"PAY_FAIL@{pay.get('step')}:{pay.get('err')}"; return res
    res["status"] = f"DONE(주문 {pay.get('order')})"
    _finish_after_order(res, idx, pay.get("order"), use_card, combo_idx, goods_no)
    return res


def _finish_after_order(res: dict, idx: int, order: str | None, card: str | None,
                        combo_idx: int | None, goods_no: str | None = None) -> dict:
    """주문완료 이후 뒷정리 = 구매대장 + 카드등록 dismiss + 뷰티포인트 + 구매사은 적립.
    ★buy_one 과 resume **양쪽에서** 부른다 (2026-08-24) — 한쪽만 고쳐져 대장·적립이
      조용히 누락되는 걸 막으려고 분리했다(현대몰 _record_after_done 과 같은 이유)."""
    # 구매대장 기록 (JSON + 시트). 실패해도 결제 후처리엔 영향 없음.
    try:
        sys.path.insert(0, str(ROOT))
        import purchase_ledger as PL
        PL.record_combo("롯데홈쇼핑", res.get("id"), combo_idx, order_no=order, card=card)
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
    return _from_order_sheet(res, idx, card=card, goods_no=goods_no, combo_idx=combo_idx)


    res["reward"] = claim_lotte_reward(goods_no=goods_no)
    return res


def detect_screen() -> str:
    """현재 롯데앱 화면 판정 — resume 의 진입점 결정용 (OCR+dump 병합 판독).

    ORDER_DONE / ORDER_SHEET / CART / OTHER. 주문완료 판정은 호출측이 _poll_order_complete 로
    먼저 하므로 여기선 나머지만 가른다(같은 토큰이 겹칠 때 결제 전 화면을 완료로 오판하면 위험)."""
    txt = " ".join(t["text"] for t in _texts())
    if "결제하기" in txt and any(k in txt for k in ("배송정보", "할인정보", "결제수단", "청구할인")):
        return "ORDER_SHEET"
    if "주문하기" in txt and ("장바구니" in txt or re.search(r"일반\s*\(\d+\s*/\s*\d+\)", txt)):
        return "CART"
    if any(k in txt for k in ("주문이 완료", "주문번호")):
        return "ORDER_DONE"
    return "OTHER"


def resume(idx: int, combo_idx: int | None = None, card: str | None = None,
           goods_no: str | None = None) -> dict:
    """★결제는 됐는데 뒷처리를 못 끝낸 건을 **현재 주문완료 화면에서** 이어서 마무리 (2026-08-24).

    왜 필요한가: 롯데는 `pay_loca` 가 '주문완료 미확인(timeout)'·'롯데앱 복귀 실패' 로 죽으면
    **실제로는 결제가 된 상태일 수 있다.** 그대로 재실행하면 이중결제다(2026-08-24 사용자 지적).
    → 결제를 다시 하지 않고 대장·뷰티·구매사은만 채운다.

    ★2026-08-25 확장 — **주문완료 화면이 아니어도 실패한 그 지점부터 이어붙인다**
    (사용자 지시: "resume 기능 넣어서 실패한 부분부터 다시해". hmall `resume <idx>` 와 같은 규칙).
      · ORDER_DONE  = 주문완료 → (종전 동작) 대장·뷰티·구매사은만. **결제 재시도 안 함**
      · ORDER_SHEET = 주문서   → 주소·쿠폰·포인트·카드·동의 → 결제 → 뒷처리 (전 단계 멱등)
      · CART        = 장바구니 → 전체선택 → 주문하기 → 위와 동일
      · OTHER       = 판정 불가 → **아무것도 안 한다**(이중결제 방지). 처음부터면 buy_one.

    ⚠️ 로그인 세션은 건드리지 않는다 = **지금 앱에 로그인된 계정이 idx 여야 한다.**
       (콜드런치·로그아웃을 하면 그 화면이 날아가므로 resume 이 성립하지 않는다.)
       주문번호를 못 읽으면 대장에 번호 없이 기록되므로 화면을 벗어나기 전에 실행할 것."""
    res = {"idx": idx, "mode": "resume", "status": None}
    ws = wake_screen()
    if not ws["ok"]:
        res["status"] = f"SCREEN_LOCKED(awake={ws['awake']},keyguard={ws['keyguard']})"; return res
    confirmed, order = _poll_order_complete(8)
    if not confirmed:
        scr = detect_screen()
        print(f"[#{idx}] resume — 현재화면 판정: {scr}", flush=True)
        try:
            res["id"] = json.loads(ACCOUNTS_FILE.read_text(encoding="utf-8"))["accounts"][idx - 1]["id"]
        except Exception:
            pass
        if scr == "ORDER_SHEET":
            print(f"[#{idx}] 주문서부터 이어서 진행 (주소·쿠폰·포인트·카드는 멱등)", flush=True)
            return _from_order_sheet(res, idx, card=card, goods_no=goods_no, combo_idx=combo_idx)
        if scr == "CART":
            cs = goto_cart_select_all()
            if not cs.get("ok"):
                res["status"] = f"CART_FAIL:{cs.get('err')}"; return res
            print(f"[#{idx}] 장바구니부터 이어서 진행", flush=True)
            return _from_order_sheet(res, idx, card=card, goods_no=goods_no, combo_idx=combo_idx)
        res["status"] = ("NOT_RESUMABLE — 주문완료/주문서/장바구니 어느 화면도 아니다. 결제 전이면 "
                         "buy_one(계정번호)로, 결제됐는지 모르면 PC 주문내역부터 확인할 것(이중결제 방지)")
        return res
    try:
        res["id"] = json.loads(ACCOUNTS_FILE.read_text(encoding="utf-8"))["accounts"][idx - 1]["id"]
    except Exception:
        pass
    res["status"] = f"DONE(주문 {order})"
    print(f"[#{idx}] resume — 주문완료 확인({order}) → 대장·뷰티·적립만 진행", flush=True)
    _finish_after_order(res, idx, order, card, combo_idx, goods_no)
    return res


def main() -> int:
    a = sys.argv[1:]
    _resolve_serial()
    if a and a[0] == "resume":
        idx = next((int(x) for x in a[1:] if x.isdigit()), None)
        if idx is None:
            print("사용: python3 -m phone_auto.lotte_homeshopping_buy resume <계정번호> [combo=N] [카드]")
            return 2
        cb = next((int(x.split("=", 1)[1]) for x in a if x.startswith("combo=")), None)
        r = resume(idx, combo_idx=cb, card=next((x for x in a if x in CARD_GRID_NAME), None))
        b, g = r.get("beauty", {}), r.get("reward", {})
        print(f"\n===== 요약 =====\n  #{idx} {r.get('id','')}: {r['status']}  "
              f"[{'뷰티✓' if b.get('completed') else '뷰티?'} / "
              f"{'적립✓' if g.get('completed') or g.get('already') else '적립?'}]")
        return 0 if str(r["status"]).startswith("DONE") else 1
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
        # ★실패면 **그 자리에서** 증거를 남긴다 (사용자 지시 2026-08-31 — 윈도우는 전 카드사 최초라
        #   실패 원인 검수가 필수). 다음 계정 콜드런치가 화면을 날리기 전이어야 의미가 있다.
        if _FA.is_failure(r.get("status")):
            try:
                _FA.audit(i, r.get("status"), "롯데", serial=hw._serial(), acc_id=r.get("id"),
                          texts_fn=_texts, cap_fn=cap)
            except Exception as _e:
                print(f"   [검수] 실패(무시): {_e}", flush=True)
        if str(r["status"]).startswith("SCREEN_LOCKED"):   # 잠긴 폰에 나머지 계정 헛돌기 금지
            print("[STOP] 폰 잠김 — 잠금해제 후 재실행 (나머지 계정 중단)", flush=True)
            break
        # ★핸드세이크(NH·삼성)면 여기서 멈춘다 — 다음 계정 콜드런치가 **살아있는 결제화면을 날린다.**
        if "_HANDOFF" in str(r["status"]):
            hcard = str(r["status"]).split("_HANDOFF", 1)[0]
            runner = "samsung_enter" if hcard == "삼성" else "nh_enter"
            rest = [x for x in idxs if x > i]
            c = f" combo={combo_idx}" if combo_idx is not None else ""
            print(f"\n{'='*54}\n★ #{i} {hcard} 인계 대기 — 여기서 멈춥니다 (나머지 계정 중단)\n"
                  f"  1) 판독:   python3 -m phone_auto.{runner} shot /tmp/kp.png\n"
                  f"  2) 마무리: python3 -m phone_auto.{runner} finish_lotte {i}{c}\n"
                  f"  3) 남은 계정: "
                  + (f"python3 -u -m phone_auto.lotte_homeshopping_buy {' '.join(map(str, rest))}"
                     f"{c}" if rest else "없음")
                  + f"\n{'='*54}", flush=True)
            break
    print("\n===== 요약 =====")
    for r in results:
        b = r.get("beauty", {})
        g = r.get("reward", {})
        bt = "뷰티✓" if b.get("completed") else ("뷰티skip" if b.get("skip") else f"뷰티?{b.get('err','')}")
        gt = ("적립✓" if g.get("completed") or g.get("already")
              else (f"적립skip({g.get('skip','')})" if g.get("skip") else f"적립?{g.get('err','')}"))
        print(f"  #{r['idx']} {r.get('id','')}: {r['status']}  [{bt} / {gt}]")
    _reward_warn(results, combo_idx)
    return 0


def _reward_warn(results: list[dict], combo_idx=None) -> None:
    """★결제됐는데 뷰티/적립이 확인 안 된 계정을 끝에 크게 모아 보여준다(현대몰과 동일 규칙).
    뷰티포인트는 **주문완료 화면에서만** 되는 now-or-never 라 놓치면 그 건은 복구 불가 —
    그래서 '지나갔다'는 사실만이라도 반드시 눈에 띄게 남긴다.
    NH 는 buy_one 이 핸드세이크로 일찍 return 해 대장·뷰티·적립을 전부 안 타므로 별도 안내."""
    bad = [r for r in results if str(r.get("status", "")).startswith("DONE")
           and not ((r.get("reward") or {}).get("completed") or (r.get("reward") or {}).get("already")
                    or (r.get("beauty") or {}).get("completed"))]
    # ★NH·삼성 등 **모든 카드의 핸드세이크**를 잡는다(NH 만 보면 삼성 인계가 조용히 빠진다).
    hand = [(r, str(r["status"]).split("_HANDOFF", 1)[0])
            for r in results if "_HANDOFF" in str(r.get("status", ""))]
    if not bad and not hand:
        return
    c = f" combo={combo_idx}" if combo_idx is not None else ""
    runner = {"삼성": "samsung_enter"}
    print(f"\n{'!'*54}\n⚠️ 뷰티/적립 미완 — 확인할 것")
    for r in bad:
        print(f"   #{r['idx']} {r.get('id','')} → 뷰티는 주문완료 화면 only(복구 불가). "
              f"구매사은 적립은 신청기간 내 재진입 가능")
    for r, card in hand:
        print(f"   #{r['idx']} {r.get('id','')} ({card}) → 결제 마친 뒤 "
              f"python3 -m phone_auto.{runner.get(card, 'nh_enter')} finish_lotte {r['idx']}{c}")
    print("!" * 54)


if __name__ == "__main__":
    raise SystemExit(main())
