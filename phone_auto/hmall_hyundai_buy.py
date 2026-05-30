"""현대몰 앱 현대카드 결제 + 뷰티포인트 재인증 — 1계정 end-to-end (phone-only hybrid).

#3(kgi5907)로 2026-05-29 23:xx 라이브 검증한 루트를 그대로 코드화.

루트:
  1. login (hmall_webview CDP)
  2. cart_state — 빈 카트 = 이미 구매(사용자) → SKIP
  3. reset_to_main → 장바구니 아이콘 native 탭 → 보이는 카트 진입
  4. [CDP] 보이는 basktList target 헤더 체크박스 클릭 = 전체선택 (n/n 검증)
     ※ hmall은 카트 진입 시 1개만 선택 + 새로고침하면 리셋 → native flow 전에 CDP 필수
  5. [OCR] 구매하기 → 주문서
  6. [OCR] 결제하기(=금액 결제하기) → 현대 결제방식 화면
  7. [OCR] PIN번호 결제 → PIN dot 화면 (~10s 로딩)
  8. PIN dot 영역(540,660) 탭 → 고정 키패드
  9. [input_pin] hyundai_hmall_pin6 = 137601 (OCR 4엔진) → 확인
  10. [OCR] 결제하기(최종, "Hmall에서 N원 결제합니다") → 안전결제 팝업
  11. [OCR] 안전결제 팝업 확인
  12. 본인인증(이름+생년월일 6자리) — 현대카드 첫 결제 1회용. 데이터 없으면 NEEDS_IDENTITY_AUTH 로 멈춤(수동).
  13. 주문완료(orderComplete)
  14. [CDP] 뷰티포인트 재인증: 재인증 클릭 → 이름(조화정)+카드4칸 → 확인 → "적립신청 완료" 팝업 확인

전제: 폰 hmall 앱 foreground + adb 1대. 현대카드가 결제수단 기본(오늘 캐러셀). **실돈, DRY 없음.**

CLI:
    python3 -m phone_auto.hmall_hyundai_buy 3            # 특정 계정
    python3 -m phone_auto.hmall_hyundai_buy 4 5 7        # 여러 계정 연속
    python3 -m phone_auto.hmall_hyundai_buy              # PLAN 전체(빈카트 자동 skip)
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
from phone_auto.flow_runner import _ocr_texts, FlowRunner
from phone_auto.adb_input import ADB

# adb 를 PATH 에 (flow_runner/ADB 가 bare 'adb' 호출)
os.environ["PATH"] = os.path.dirname(hw.ADB) + os.pathsep + os.environ.get("PATH", "")

PLAN = [1, 2, 3, 4, 5, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19]
CARD_PIN = "137601"          # 현대카드 PIN 6자리 (공용)
CART_ICON = (1012, 151)      # hmall 메인 우측상단 장바구니 아이콘 (1080x2400)
HOME_NAV = (106, 2218)       # 하단 네비 '홈' (앱이 마이페이지로 복원돼도 홈 강제용)
PIN_DOT = (540, 660)         # PIN 동그라미 영역 — 탭하면 고정 키패드 호출
BP_PATH = ROOT / "secrets" / "beauty_point.json"


# ──────────────────────────── 기본 유틸 ────────────────────────────

def _adb() -> ADB:
    return ADB()


def cap(path: str = "/tmp/_hd_buy.png") -> str:
    subprocess.run(["adb", "exec-out", "screencap", "-p"], stdout=open(path, "wb"))
    return path


def goto_cart(retries: int = 3) -> bool:
    """하단 '홈' 강제 → 우상단 장바구니 아이콘 → 카트 진입 검증(폴링).
    reset 후 앱이 마이페이지로 복원되는 경우 대비 (홈 먼저). 보이는 카트여야 CDP 전체선택 가능."""
    adb = _adb()
    for _ in range(retries):
        adb.tap(*HOME_NAV); time.sleep(0.4)          # 홈 전환(짧게) — 카트는 wait_text로 확인
        adb.tap(*CART_ICON)
        if wait_text("장바구니", timeout=8) or screen_has("구매하기") or screen_has("담긴 상품"):
            return True
    return False


def ocr_find(text: str, contains: bool = False, pick: str = "bottom"):
    """현재 화면 OCR 에서 text 매칭 1개 반환 (없으면 None). pick=bottom/top."""
    its = _ocr_texts(cap())
    m = [it for it in its if ((text in it["text"]) if contains else (it["text"].strip() == text))]
    if not m:
        return None
    m.sort(key=lambda it: it["cy"], reverse=(pick == "bottom"))
    return m[0]


def ocr_tap(text: str, contains: bool = False, pick: str = "bottom", retries: int = 4,
            wait: float = 0.3, post: float = 0.2) -> bool:
    """OCR로 찾아 탭. post는 전환용 최소 sleep(0.2) — 다음 화면 readiness는 호출측 wait_text가 담당."""
    adb = _adb()
    for i in range(retries):
        hit = ocr_find(text, contains, pick)
        if hit:
            adb.tap(hit["cx"], hit["cy"])
            print(f"   ocr_tap {text!r} @({hit['cx']},{hit['cy']})", flush=True)
            time.sleep(post)
            return True
        time.sleep(wait)
    print(f"   ✗ ocr_tap {text!r} 미발견 ({retries}회)", flush=True)
    return False


def wait_text(text: str, timeout: float = 15, contains: bool = True) -> bool:
    """화면에 text 등장 대기 (OCR 빠른 폴링 — 각 폴은 screencap+OCR ~0.7s + 0.2s)."""
    end = time.time() + timeout
    while time.time() < end:
        its = _ocr_texts(cap())
        if any((text in it["text"]) if contains else it["text"].strip() == text for it in its):
            return True
        time.sleep(0.2)
    return False


def screen_has(text: str, contains: bool = True) -> bool:
    its = _ocr_texts(cap())
    return any((text in it["text"]) if contains else it["text"].strip() == text for it in its)


# ──────────────────────────── CDP (폰 WebView) ────────────────────────────

def attach_visible_url(url_sub: str, settle: float = 0.0):
    """url_sub 포함 + visibilityState=visible 인 폰 WebView target 에 CDP attach.
    hmall 앱은 target 13개 상존 → 반드시 url + visible 로 보이는 화면을 특정해야 함."""
    hw._forward(hw._serial())
    if settle:
        time.sleep(settle)
    for p in hw._page_targets():
        if url_sub not in (p.get("url") or ""):
            continue
        try:
            c = hw.CDP(p["webSocketDebuggerUrl"])
            c.send("Runtime.enable", timeout=5)
            if c.ev("document.visibilityState", timeout=4) == "visible":
                return c
            c.close()
        except Exception:
            pass
    return None


def cdp_select_all(timeout: float = 8) -> tuple[bool, str]:
    """보이는 카트(basktList) 헤더 체크박스 클릭 → (n/n) 전체선택. (ok, '(n/m)')."""
    end = time.time() + timeout
    while time.time() < end:
        c = attach_visible_url("basktList")
        if c:
            break
        time.sleep(0.5)
    else:
        return False, "no-cart-target"
    try:
        def sel():
            b = c.ev("document.body?document.body.innerText:''") or ""
            mm = re.search(r"\((\d+)\s*/\s*(\d+)\)", b)
            return mm
        c.ev("(function(){var x=document.querySelectorAll('input[type=checkbox]');"
             "if(x[0]&&!x[0].checked)x[0].click();})()")
        time.sleep(0.4)
        mm = sel()
        if mm and mm.group(1) == mm.group(2):
            return True, mm.group(0)
        # 1회 재시도
        c.ev("(function(){var x=document.querySelectorAll('input[type=checkbox]');"
             "if(x[0]&&!x[0].checked)x[0].click();})()")
        time.sleep(0.4)
        mm = sel()
        return (bool(mm and mm.group(1) == mm.group(2)), mm.group(0) if mm else "?")
    finally:
        c.close()


def beauty_reauth(profile: dict, timeout: float = 12) -> dict:
    """주문완료(orderComplete) 페이지 CDP 뷰티포인트 재인증.
    재인증 클릭 → 이름+카드4칸 → 확인 → '적립신청 완료'. (#3 검증 루트)"""
    out = {"ok": False, "step": None, "err": None}
    name = str(profile.get("name") or "").strip()
    parts = [str(x).strip() for x in (profile.get("card_parts") or [])]
    if not name or len(parts) != 4:
        out["err"] = "profile name/card_parts invalid"
        return out
    end = time.time() + timeout
    while time.time() < end:
        c = attach_visible_url("orderComplete")
        if c:
            break
        time.sleep(0.6)
    else:
        out["err"] = "no orderComplete target"
        return out
    try:
        # 1) 재인증 버튼
        r = c.ev(r'''(function(){var e=[].slice.call(document.querySelectorAll('button,a,[role=button],span,div'));
          function t(x){return (x.innerText||x.textContent||'').trim();}
          var b=e.filter(function(x){return t(x)==='재인증';});
          if(b.length){b[b.length-1].click();return 'OK';}return 'NO';})()''')
        out["step"] = f"reauth-btn:{r}"
        if r != "OK":
            out["err"] = "재인증 버튼 없음"
            return out
        time.sleep(0.9)
        # 2) 이름 + 카드4칸 채우기 (native value setter + events)
        fill = (
            '(function(nm,p){function setN(el,v){var d=Object.getOwnPropertyDescriptor('
            'el.constructor.prototype,"value");d.set.call(el,v);["input","change","keyup","blur"]'
            '.forEach(function(t){el.dispatchEvent(new Event(t,{bubbles:true}));});}'
            'var n=document.querySelector("input[name=name]");'
            'var c=[1,2,3,4].map(function(i){return document.querySelector("input[name=cardNo"+i+"]");});'
            'if(!n||c.some(function(x){return !x;}))return "MISSING";'
            'n.focus();setN(n,nm);c.forEach(function(el,i){el.focus();setN(el,p[i]);});'
            'return "FILLED name="+n.value+" card="+c.map(function(x){return x.value;}).join("");})'
            '(%s,%s)' % (json.dumps(name), json.dumps(parts))
        )
        fr = c.ev(fill)
        out["step"] = f"fill:{fr}"
        if not (isinstance(fr, str) and fr.startswith("FILLED")):
            out["err"] = f"폼 입력 실패: {fr}"
            return out
        time.sleep(0.3)
        # 3) 확인
        ok = c.ev(r'''(function(){var e=[].slice.call(document.querySelectorAll('button,a,[role=button]'));
          function t(x){return (x.innerText||x.textContent||'').trim();}
          var b=e.filter(function(x){var r=x.getBoundingClientRect();return t(x)==='확인'&&r.width>0&&r.height>0;});
          if(b.length){b[b.length-1].click();return 'OK';}return 'NO';})()''')
        out["step"] = f"confirm:{ok}"
        if ok != "OK":
            out["err"] = "확인 버튼 없음"
            return out
        time.sleep(1.2)
        out["ok"] = True
        return out
    finally:
        c.close()


# ──────────────────────────── 현대 본인인증 (이름+생년월일+성별) ────────────────────────────
# 계정마다 재등장. 네이티브 SDK(WebView 아님) → 키보드/키패드 직접 탭.
# secrets/card_secrets.json 의 현대.identity = {name, birth6, gender} 에서 읽음.
GLOBE = (180, 2155)               # 키보드 좌측하단 지구본(언어순환): EN→中文→한국어
ID_NAME_FIELD = (540, 643)        # 이름 입력란
ID_BIRTH_FIELD = (228, 828)       # 생년월일 6자리 입력란
ID_KEYPAD_NEXT = (940, 1710)      # 숫자키패드 '다음' = 성별칸으로 이동
ID_CONFIRM = (540, 1244)          # 본인인증 '확인' (dump bounds 중앙)
# 두벌식 자모 키좌표 (1080x2400 삼성 허니보드 실측 OCR). 영문 QWERTY 위치와 동일.
JAMO_XY = {
    "ㄱ": (380, 1629), "ㄴ": (224, 1808), "ㅁ": (120, 1808), "ㅇ": (330, 1801),
    "ㅂ": (67, 1629), "ㅓ": (745, 1808), "ㅕ": (698, 1629), "ㅣ": (959, 1805),
}
# 이름 한글 → 자모 분해 시퀀스 (필요한 명의자만 정의). '김건엽'.
NAME_JAMO = {
    "김건엽": ["ㄱ", "ㅣ", "ㅁ", "ㄱ", "ㅓ", "ㄴ", "ㅇ", "ㅕ", "ㅂ"],
}


def _load_identity() -> dict:
    sec = json.loads((ROOT / "secrets" / "card_secrets.json").read_text(encoding="utf-8"))
    return (sec.get("현대") or {}).get("identity") or {}


def _kbd_is_korean() -> bool:
    its = _ocr_texts(cap())
    return any(c in it["text"] for it in its if it["cy"] > 1550 for c in "ㅂㅈㄷㄱㅅㅁㄴㅇㄹ")


def _verify_identity_dump(name: str, birth6: str, gender: str) -> bool:
    """uiautomator dump 으로 이름/생년월일/성별 입력값 + 확인 enabled 검증 (제출 전)."""
    subprocess.run(["adb", "exec-out", "uiautomator", "dump", "--compressed", "/sdcard/_id.xml"],
                   capture_output=True)
    subprocess.run(["adb", "pull", "/sdcard/_id.xml", "/tmp/_id.xml"], capture_output=True)
    try:
        xml = Path("/tmp/_id.xml").read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return False
    has_name = ('text="%s"' % name) in xml
    has_birth = ('text="%s"' % birth6) in xml
    # 확인 버튼 노드 안에 enabled="true" (속성 순서 무관) — 폼 완성 시 활성
    confirm_ok = any('enabled="true"' in m.group(0)
                     for m in re.finditer(r'<node[^>]*text="확인"[^>]*/?>', xml))
    return bool(has_name and has_birth and confirm_ok)


def enter_identity_auth() -> dict:
    """현대 본인인증 폼 자동입력 → dump 검증 → 확인. ⚠️확인=결제 확정(과금)."""
    out = {"ok": False}
    ident = _load_identity()
    name = ident.get("name", ""); birth6 = ident.get("birth6", ""); gender = ident.get("gender", "")
    seq = NAME_JAMO.get(name)
    if not (name and birth6 and gender and seq):
        out["err"] = f"identity 데이터/자모 미정의: name={name!r}"; return out
    adb = _adb()
    # 이름 필드 focus → 한글 키보드 전환
    adb.tap(*ID_NAME_FIELD); time.sleep(0.6)
    for _ in range(5):                       # 글로브 언어순환(EN→中文→…→한국어). 더 많아도 커버.
        if _kbd_is_korean():
            break
        adb.tap(*GLOBE); time.sleep(0.7)
    if not _kbd_is_korean():
        out["err"] = "키보드 한글전환 실패"; return out
    for j in seq:
        adb.tap(*JAMO_XY[j]); time.sleep(0.15)
    time.sleep(0.25)
    # 생년월일 + 성별 (input text). 한글키보드→숫자키패드 전환 타이밍 finicky → 1.2s 유지 + 재시도.
    for attempt in range(3):
        adb.tap(*ID_BIRTH_FIELD); time.sleep(1.2)   # 키패드 전환(이 지점만 넉넉히)
        subprocess.run(["adb", "shell", "input", "text", birth6]); time.sleep(0.4)
        adb.tap(*ID_KEYPAD_NEXT); time.sleep(0.5)   # 키패드 '다음' = 성별칸 (직접탭은 포커스 실패)
        subprocess.run(["adb", "shell", "input", "text", gender]); time.sleep(0.4)
        if _verify_identity_dump(name, birth6, gender):
            break
        print(f"   [identity] 생년월일/성별 미반영 — 재시도 {attempt + 1}", flush=True)
    else:
        out["err"] = "dump 검증 실패(생년월일/성별 미입력) — 확인 안 누름"; return out
    adb.tap(*ID_CONFIRM); time.sleep(2.5)   # 인증 처리
    out["ok"] = True
    return out


# 본인인증 2단계: 카드 비밀번호 4자리 (이름/생년월일 확인 뒤 등장).
ID_CARDPW_FIELD = (540, 812)      # '카드 비밀번호 4자리' 입력란 (탭 '카드비밀번호'(303,596)와 구분)


def enter_card_password() -> dict:
    """카드 비밀번호 4자리(secrets 현대.card_pw4) → 확인.
    PIN과 동일한 고정 키패드 → input_pin hyundai_hmall_pw4 (4엔진 OCR). 입력란(540,812) 탭이 핵심."""
    out = {"ok": False}
    pw = str((json.loads((ROOT / "secrets" / "card_secrets.json").read_text(encoding="utf-8"))
              .get("현대") or {}).get("card_pw4", ""))
    if len(pw) != 4:
        out["err"] = "card_pw4 없음"; return out
    _adb().tap(*ID_CARDPW_FIELD); time.sleep(1.3)     # 입력란(탭 '카드비밀번호'(303,596) 아님) → 키패드
    try:
        FlowRunner(use_camera=False).run_action(
            {"action": "input_pin", "preset": "hyundai_hmall_pw4", "value": pw,
             "tap_delay_sec": 0.4, "use_camera": False})
    except Exception as e:
        out["err"] = f"카드비번 input_pin 실패: {e}"; return out
    time.sleep(0.8)
    ocr_tap("확인", post=0.3, retries=2)
    out["ok"] = True
    return out


# ──────────────────────────── 결제 시퀀스 ────────────────────────────

def pay_hyundai(pin: str = CARD_PIN, from_order: bool = False) -> dict:
    """[보이는 카트 전체선택] → 구매하기 → 현대카드 PIN 결제 → 안전결제 팝업까지.
    from_order=True 면 이미 주문서이므로 구매하기 생략(세션 타임아웃 재개용).
    반환 step 으로 어디까지 갔는지 추적. 본인인증/주문완료는 호출측에서 판정."""
    out = {"step": "start"}
    if not from_order:
        # 5) 구매하기 → 주문서 (할인 계산 로딩 → wait_text가 담당)
        if not ocr_tap("구매하기"):
            out["err"] = "구매하기 실패"; return out
        if not wait_text("결제하기", timeout=15):
            out["err"] = "주문서 미도달(결제하기 안보임)"; return out
    out["step"] = "order_page"
    # 6) 결제하기(금액) → 현대 결제방식 (SDK 로딩)
    if not ocr_tap("결제하기", contains=True):
        out["err"] = "결제하기(금액) 실패"; return out
    if not wait_text("PIN번호 결제", timeout=15):
        out["err"] = "현대 결제방식 화면 미도달"; return out
    out["step"] = "pay_method"
    # 7) PIN번호 결제 → PIN dot 화면 (다음 페이지 로딩 길 수 있음 → wait_text)
    if not ocr_tap("PIN번호 결제", contains=True):
        out["err"] = "PIN번호 결제 선택 실패"; return out
    if not wait_text("PIN번호를 입력", timeout=15):
        out["err"] = "PIN 화면 미도달"; return out
    out["step"] = "pin_screen"
    # 8) PIN dot 영역 탭 → 고정 키패드 (네이티브 렌더 대기)
    _adb().tap(*PIN_DOT)
    time.sleep(1.3)
    # 9) PIN 6자리 입력 (OCR 4엔진 고정 키패드)
    FlowRunner(use_camera=False).run_action(
        {"action": "input_pin", "preset": "hyundai_hmall_pin6", "value": pin,
         "tap_delay_sec": 0.4, "use_camera": False})
    time.sleep(0.8)
    out["step"] = "pin_entered"
    # 확인 (PIN) → 결제확인 WebView 로딩
    if not ocr_tap("확인"):
        out["err"] = "PIN 확인 실패"; return out
    if not wait_text("결제합니다", timeout=15) and not wait_text("결제하기", timeout=3):
        out["err"] = "결제확인 화면 미도달"; return out
    out["step"] = "pay_confirm"
    # 10) 최종 결제하기 → 안전결제 팝업
    if not ocr_tap("결제하기", contains=True):
        out["err"] = "최종 결제하기 실패"; return out
    out["step"] = "paid_clicked"
    return out


def handle_after_pay(timeout: float = 30) -> str:
    """결제하기 후: 안전결제 팝업 확인 → 본인인증 자동입력 → 주문완료 판정.
    반환: 'ORDER_COMPLETE' | 'IDENTITY_FAIL' | 'AFTER_AUTH_UNKNOWN' | 'UNKNOWN'."""
    if screen_has("안전한 결제") or screen_has("추가 인증"):
        ocr_tap("확인", retries=2)
    end = time.time() + timeout
    while time.time() < end:
        txt = " ".join(it["text"] for it in _ocr_texts(cap()))
        if "주문" in txt and "완료" in txt:
            return "ORDER_COMPLETE"
        if ("본인 인증" in txt) or ("생년월일" in txt):
            print("   [identity] 1단계 이름+생년월일+성별...", flush=True)
            r = enter_identity_auth()
            print(f"   [identity] {r}", flush=True)
            if not r.get("ok"):
                return "IDENTITY_FAIL"
            time.sleep(1.0)
            # 2단계: 카드 비밀번호 4자리 (등장 시)
            if screen_has("카드 비밀번호") or screen_has("카드비밀번호"):
                cp = enter_card_password()
                print(f"   [cardpw] {cp}", flush=True)
                if not cp.get("ok"):
                    return "CARDPW_MANUAL"   # 키패드 OCR 실패 시 → 사용자가 4자리 입력 후 재개
            end2 = time.time() + 25
            while time.time() < end2:
                if (lambda t: "주문" in t and "완료" in t)(" ".join(x["text"] for x in _ocr_texts(cap()))):
                    return "ORDER_COMPLETE"
                time.sleep(0.3)
            return "AFTER_AUTH_UNKNOWN"
        time.sleep(0.3)
    return "UNKNOWN"


# ──────────────────────────── 1계정 오케스트레이션 ────────────────────────────

def buy_one(idx: int) -> dict:
    serial = hw._serial()
    res = {"idx": idx, "status": None}
    print(f"\n{'='*54}\n[#{idx}] 로그인...", flush=True)
    lr = hw.login_account(idx, serial)
    res["id"] = lr.get("id")
    if not lr.get("success"):
        res["status"] = f"LOGIN_FAIL:{lr.get('error')}"; return res
    # 카트 확인
    cs = hw.cart_state(serial)
    if cs.get("empty"):
        res["status"] = "SKIP_EMPTY(이미구매)"; return res
    print(f"[#{idx} {res['id']}] 카트 차있음 → 보이는 카트 진입", flush=True)
    if not goto_cart():
        res["status"] = "CART_NAV_FAIL"; return res
    # 전체선택 (CDP)
    ok, sel = cdp_select_all()
    print(f"[#{idx}] 전체선택 {sel} ok={ok}", flush=True)
    if not ok:
        res["status"] = f"SELECT_ALL_FAIL:{sel}"; return res
    # 결제 ⚠️실돈
    print(f"[#{idx}] ⚠️ 현대카드 결제 실행", flush=True)
    pay = pay_hyundai()
    res["pay"] = pay
    if pay.get("err"):
        res["status"] = f"PAY_FAIL@{pay.get('step')}:{pay['err']}"; return res
    # 결제 후 판정
    after = handle_after_pay()
    res["after"] = after
    if after != "ORDER_COMPLETE":
        res["status"] = f"AFTER_PAY_{after}"; return res
    # 뷰티포인트 재인증
    prof_cfg = json.loads(BP_PATH.read_text(encoding="utf-8"))
    active = prof_cfg.get("active_profile")
    profile = prof_cfg.get("profiles", {}).get(active, {})
    bp = beauty_reauth(profile)
    res["beauty"] = bp
    # 적립완료 팝업 확인
    if bp.get("ok"):
        time.sleep(1.0)
        if screen_has("완료"):
            ocr_tap("확인", post=2.0, retries=2)
    res["status"] = "DONE" + ("" if bp.get("ok") else f"(beauty_fail:{bp.get('err')})")
    return res


def _do_beauty(res: dict) -> None:
    prof_cfg = json.loads(BP_PATH.read_text(encoding="utf-8"))
    profile = prof_cfg.get("profiles", {}).get(prof_cfg.get("active_profile"), {})
    bp = beauty_reauth(profile)
    res["beauty"] = bp
    if bp.get("ok"):
        time.sleep(1.0)
        if screen_has("완료"):
            ocr_tap("확인", post=2.0, retries=2)
    res["status"] = "DONE" + ("" if bp.get("ok") else f"(beauty_fail:{bp.get('err')})")


def finish_from_order(idx=None) -> dict:
    """세션 타임아웃 등으로 '주문서'에 있을 때 결제부터 재개 (login/cart 생략)."""
    res = {"idx": idx, "mode": "resume", "status": None}
    print(f"[resume #{idx}] 주문서에서 결제 재개 ⚠️실돈", flush=True)
    pay = pay_hyundai(from_order=True)
    res["pay"] = pay
    if pay.get("err"):
        res["status"] = f"PAY_FAIL@{pay.get('step')}:{pay['err']}"; return res
    after = handle_after_pay()
    res["after"] = after
    if after != "ORDER_COMPLETE":
        res["status"] = f"AFTER_PAY_{after}"; return res
    _do_beauty(res)
    return res


def main() -> int:
    args = sys.argv[1:]
    if args and args[0] == "resume":
        idx = int(args[1]) if len(args) > 1 and args[1].isdigit() else None
        r = finish_from_order(idx)
        print(f"[resume #{idx}] => {r.get('status')}", flush=True)
        print(json.dumps(r, ensure_ascii=False), flush=True)
        return 0
    only = [int(a) for a in args if a.isdigit()]
    plan = only or PLAN
    print(f"[serial] {hw._serial()}  plan={plan}", flush=True)
    summary = []
    for idx in plan:
        try:
            r = buy_one(idx)
        except Exception as e:
            r = {"idx": idx, "status": f"EXC:{e}"}
        print(f"[#{idx}] => {r.get('status')}", flush=True)
        summary.append(r)
    print(f"\n{'='*54}\nSUMMARY", flush=True)
    for r in summary:
        print(f"  #{r['idx']:2d} {r.get('id','?'):16s} {r.get('status')}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
