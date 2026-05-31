"""현대몰 앱 현대카드 결제 + 뷰티포인트 재인증 — 1계정 end-to-end (phone-only hybrid).

#3(kgi5907)로 2026-05-29 23:xx 라이브 검증한 루트를 그대로 코드화.

루트 (2026-05-30 #5 라이브 end-to-end 검증):
  0. reset_to_main = force-stop+콜드런치+8s 안정화 → close_home_popup(광고 OCR 닫기, 날마다 다름)
     → login (hmall_webview CDP, cold-launch CDP race 시 1회 재시도)
  1. cart_state — 빈 카트 = 이미 구매 → SKIP
  2. 장바구니 아이콘 직접 탭(홈 경유 X) → 보이는 카트
  3. [CDP] basktList 헤더 체크박스 = 전체선택 (n/n 검증). native flow 전에 CDP 필수
  4. [OCR] 구매하기 → 결제하기(금액) → PIN번호 결제 → PIN dot 탭 → 키패드
  5. [input_pin] hyundai_hmall_pin6=137601 (vision+gcv 2엔진, 실패시 4엔진 fallback) → 확인
  6. [OCR] 최종 결제하기 → 안전결제 팝업 확인
  7. 본인인증 — 화면 변종 분기(handle_after_pay):
       · '생년월일' 폼(첫 결제) → enter_identity_auth(이름+생년월일+성별)
       · '카드비밀번호' 화면(이후 결제) → 카드비밀번호 탭 선택 + PW4 입력
  8. 주문완료 → close_home_popup(주문완료 화면 광고도 재인증 버튼 가림) → 뷰티포인트 재인증

⚠️ 오늘(2026-05-30) 멈췄던 함정 — 재발방지:
  · 광고 팝업 2곳 모두 닫아야 함: ① 홈(콜드런치마다, 안 닫으면 로그인/네비 막힘)
    ② 주문완료 화면(재인증 버튼 가림). 둘 다 '오늘 그만 보기'/'닫기' OCR (close_home_popup).
  · 본인인증은 계정마다 화면이 다름 → 매번 OCR로 판별:
    '생년월일' 글자 있다 → 이름+생년월일+성별 입력(enter_identity_auth, 첫 결제 계정).
    없다 → '안전한 결제 위해 추가 인증' 팝업 '확인' 먼저 누르고(키패드를 덮음!) → 카드비번 4자리만.
  · 카트 진입은 홈 탭 금지(9초 타임아웃 낭비) → 장바구니 아이콘 직접 탭.
  · 앱은 reset_to_main(force-stop+콜드런치+8s)로 띄워야 CDP 안정(1s는 'Remote end closed').
  · 중단 시 '처음부터 재시작' 금지 → resume <idx> (현재 화면 감지해 그 지점부터, 이중결제 방지).

전제: adb 1대(USB/무선 무관, _resolve_serial 자동고정). 현대카드 결제수단 기본. **실돈, DRY 없음.**

멀티카드 (2026-05-31): buy_one = 디스패처. 공통(콜드런치→광고→로그인→카트→전체선택→구매하기→주문서)
  → detect_card(당일 카드할인 캐러셀) → select_card(캐러셀/그리드) → 카드별 SDK → 공통(주문완료+뷰티).
  SDK: 현대=pay_hyundai(PIN→본인인증→카드비번) / 롯데=pay_lotte(OCR+롯데앱 검증흐름 재사용). CARDS_SUPPORTED만.

CLI:
    python3 -m phone_auto.hmall_hyundai_buy 3            # 특정 계정 (앱 콜드런치부터, 당일카드 자동감지)
    python3 -m phone_auto.hmall_hyundai_buy 4 5 7        # 여러 계정 연속
    python3 -m phone_auto.hmall_hyundai_buy              # PLAN 전체(빈카트 자동 skip)
    python3 -m phone_auto.hmall_hyundai_buy 롯데 8 9     # 카드 강제(현대/롯데) — 당일 할인 아닌 카드 테스트용
    python3 -m phone_auto.hmall_hyundai_buy resume 5     # 중단 시: 현재 화면 감지 → 그 지점부터 완주
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
LOTTE_FLOW = ROOT / "phone_auto" / "coords" / "apps" / "lotte_card.json"   # 검증된 롯데 결제흐름(5/29)
KB_FLOW = ROOT / "phone_auto" / "coords" / "apps" / "kb_kbpay.json"        # KB 결제흐름(DRAFT, 라이브검증중)
HANA_FLOW = ROOT / "phone_auto" / "coords" / "apps" / "hana_card.json"     # 하나 결제흐름(5/29 nFilter검증, flow[16:]=하나앱)
BC_FLOW = ROOT / "phone_auto" / "coords" / "apps" / "bc_paybook_isp.json"  # BC(페이북) 결제흐름(드래프트, flow[6:]=KCP다음~페이북앱)
# 카드사 → '카드 선택' 그리드 표기명 (카드할인 행 토큰은 키, 그리드명은 값). 결제 SDK 있는 카드만 활성.
CARD_GRID_NAME = {"현대": "현대카드", "롯데": "롯데카드", "하나": "하나카드", "KB": "KB국민카드",
                  "삼성": "삼성카드", "NH": "NH농협카드", "BC": "비씨카드"}
CARDS_SUPPORTED = ("현대", "롯데", "KB", "하나", "BC")   # SDK 코드화 카드 (BC=포팅 후 라이브검증 대기). 나머지는 SDK 추가 시 확장

# 카드할인 캐러셀 OCR 토큰 → 카드키 별칭.
# 근거(실측): 현대 할인날 캐러셀 = "현대 5% 즉시할인" + "<금액>원"(별 item). 금액은 카트 합계에 따라 매번
#   달라지는 값이라 무의미 → 매칭은 토큰("현대")으로만. 그리드명("현대카드")이 아니라 짧은 토큰으로 뜸.
#   다른 카드사도 '브랜드 짧은이름 N% (즉시/청구)할인' 패턴으로 예상.
# ⚠️ 별칭 누락 시 detect_card=None → use_card가 '현대'로 폴백 → 오결제 위험. 그래서 변형 표기를 미리 망라.
# 예상 캐러셀 표기(첫 실제 할인날 첫 런에서 검증 필요):
#   현대="현대 N% 즉시할인"(실측) / 롯데="롯데 N% 즉시할인" / KB="KB국민 N%" 또는 "KB N%"/"국민 N%"
#   하나="하나 N%" / 삼성="삼성 N%" / NH="NH농협 N%" 또는 "농협 N%" / BC="BC N%" 또는 "비씨 N%"
CARD_ALIASES = {
    "현대": "현대",
    "롯데": "롯데", "롯데카드": "롯데",
    "KB국민": "KB", "KB": "KB", "국민": "KB", "케이비": "KB",          # KB Pay/국민/KB국민 변형
    "하나": "하나",
    "삼성": "삼성",
    "NH농협": "NH", "NH": "NH", "농협": "NH",                        # NH/농협 변형
    "BC": "BC", "비씨": "BC", "페이북": "BC",                         # BC/비씨/페이북 변형
    # 아래는 SDK 미구현(CARDS_SUPPORTED 아님) — detect는 되되 UNSUPPORTED_CARD로 안전 정지(현대 오폴백 방지)
    "신한": "신한", "우리": "우리",
}


# ──────────────────────────── 타이밍 측정 (측정 전용, 동작 변경 없음) ────────────────────────────
_T0 = None        # 계정 시작 시각
_TPREV = None     # 직전 lap 시각
_CAP_N = 0        # 계정당 screencap 호출 수


def lap_reset() -> None:
    global _T0, _TPREV, _CAP_N
    _T0 = _TPREV = time.time()
    _CAP_N = 0


def lap(label: str) -> None:
    """직전 lap 이후 경과 + 계정 누적 출력."""
    global _TPREV
    now = time.time()
    if _TPREV is None:
        _TPREV = _T0 or now
    dt = now - _TPREV
    tot = now - (_T0 or now)
    print(f"      ⏱ +{dt:5.2f}s  [누적 {tot:5.1f}s | cap {_CAP_N}]  {label}", flush=True)
    _TPREV = now


# ──────────────────────────── 기본 유틸 ────────────────────────────

def _resolve_serial() -> str:
    """연결된 기기 1개를 ANDROID_SERIAL 로 고정 → cap()/dump 등 -s 없는 bare adb 호출까지
    같은 기기 타겟. USB 우선, 없으면 무선(ip:port), 없으면 mDNS. USB·무선 무관 동작 보장."""
    if os.environ.get("ANDROID_SERIAL"):
        return os.environ["ANDROID_SERIAL"]
    out = subprocess.run([hw.ADB, "devices"], capture_output=True, text=True, timeout=10).stdout
    devs = [ln.split("\t")[0] for ln in out.splitlines()[1:] if "\tdevice" in ln]
    if not devs:
        raise RuntimeError("adb 연결 기기 없음 — USB 케이블 또는 무선 디버깅 확인")
    usb = [d for d in devs if ":" not in d and "_tcp" not in d]
    wifi = [d for d in devs if ":" in d and "_tcp" not in d]
    mdns = [d for d in devs if "_tcp" in d]
    serial = (usb or wifi or mdns)[0]
    os.environ["ANDROID_SERIAL"] = serial
    print(f"[serial] {serial} ({'USB' if serial in usb else '무선'}) 고정", flush=True)
    return serial


def _adb() -> ADB:
    return ADB()


def cap(path: str = "/tmp/_hd_buy.png") -> str:
    global _CAP_N
    _CAP_N += 1
    subprocess.run(["adb", "exec-out", "screencap", "-p"], stdout=open(path, "wb"))
    return path


def close_home_popup(max_iter: int = 4) -> int:
    """홈 광고 팝업 닫기 — cold launch 시 '오늘의 최저가' 등 모달이 떠 로그인/네비를 막음.
    '오늘 그만 보기'(당일 재등장 방지) 우선, 없으면 '닫기'. 여러 개 쌓일 수 있어 반복. 닫은 수 반환."""
    closed = 0
    for _ in range(max_iter):
        its = _ocr_texts(cap())
        hit = None
        for key in ("그만 보기", "오늘 하루", "보지 않기"):   # 당일 재등장 방지 버튼 우선
            hit = next((it for it in its if key in it["text"]), None)
            if hit:
                break
        if not hit:
            hit = next((it for it in its if it["text"].strip() == "닫기"), None)
        if not hit:
            break
        _adb().tap(hit["cx"], hit["cy"])
        print(f"   [popup] 광고 닫기 '{hit['text']}' @({hit['cx']},{hit['cy']})", flush=True)
        closed += 1
        time.sleep(0.8)
    return closed


def goto_cart(retries: int = 3) -> bool:
    """우상단 장바구니 아이콘 직접 탭 → 카트 진입 검증(폴링).
    로그인 직후엔 장바구니가 바로 보임 → 홈 경유 불필요(첫 시도는 직접 탭, 9초 낭비 제거).
    재시도 시에만 홈 강제(마이페이지 복원 등 대비). 보이는 카트여야 CDP 전체선택 가능."""
    adb = _adb()
    for i in range(retries):
        if i > 0:
            adb.tap(*HOME_NAV); time.sleep(0.4)      # 재시도 시에만 홈 경유
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
    t0 = time.time()
    end = t0 + timeout
    polls = 0
    while time.time() < end:
        polls += 1
        its = _ocr_texts(cap())
        if any((text in it["text"]) if contains else it["text"].strip() == text for it in its):
            print(f"      ⏱ wait_text({text!r}) {time.time()-t0:.2f}s / {polls}폴", flush=True)
            return True
        time.sleep(0.2)
    print(f"      ⏱ wait_text({text!r}) ✗TIMEOUT {time.time()-t0:.2f}s / {polls}폴", flush=True)
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


def _cart_count(its: list) -> tuple:
    """카트 '일반상품 (n/m)' 헤더 + (n/m) match 반환. (화면 첫 (n/m)=추천 캐러셀 오탐 회피용)."""
    gb = next((it for it in its if "일반상품" in it["text"]), None)
    if not gb:
        return None, None
    m = re.search(r"\((\d+)\s*/\s*(\d+)\)", gb["text"])           # 헤더에 합쳐진 경우
    if m:
        return gb, m
    near = next((it for it in its if abs(it["cy"] - gb["cy"]) < 30  # 분리된 경우 같은 행
                 and re.search(r"\(\d+\s*/\s*\d+\)", it["text"])), None)
    return gb, (re.search(r"\((\d+)\s*/\s*(\d+)\)", near["text"]) if near else None)


def cdp_select_all(timeout: float = 8) -> tuple[bool, str]:
    """전체선택 → (ok, '(n/n)'). **native '일반상품' 헤더 체크박스 직접 탭**.
    ⚠️ CDP basktList는 분절(stale/1개짜리 WebView 동시 visible)이라 신뢰 X(#4 실측). 검증도
    화면 첫 (n/m)이 추천 캐러셀일 수 있어 '일반상품' 행의 (n/m)로만 판정(#4: 카트1개인데 캐러셀'(1/7)' 오탐)."""
    end = time.time() + timeout
    while time.time() < end:
        gb, mm = _cart_count(_ocr_texts(cap()))
        if gb is None:
            time.sleep(0.5); continue
        if mm and mm.group(1) == mm.group(2) and int(mm.group(2)) > 0:
            return True, mm.group(0)                              # 이미 전체선택
        # 일반상품 헤더 왼쪽 체크박스 탭 (전체선택 토글) — 좌표: 헤더 좌측끝 ~50px 왼쪽
        _adb().tap(max(45, gb["cx"] - gb["w"] // 2 - 50), gb["cy"]); time.sleep(1.3)
        gb2, mm2 = _cart_count(_ocr_texts(cap()))
        if mm2 and mm2.group(1) == mm2.group(2):
            return True, mm2.group(0)
        return (False, mm2.group(0) if mm2 else "no-count")
    return False, "no-cart(일반상품 헤더 미발견)"


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
    globe_n = 0
    for _ in range(5):                       # 글로브 언어순환(EN→中文→…→한국어). 더 많아도 커버.
        if _kbd_is_korean():
            break
        adb.tap(*GLOBE); time.sleep(0.7); globe_n += 1
    if not _kbd_is_korean():
        out["err"] = "키보드 한글전환 실패"; return out
    lap(f"본인인증 이름필드+한글키보드 (글로브 {globe_n}회)")
    for j in seq:
        adb.tap(*JAMO_XY[j]); time.sleep(0.15)
    time.sleep(0.25)
    # 생년월일 + 성별 (input text). 한글키보드→숫자키패드 전환 타이밍 finicky → 1.2s 유지 + 재시도.
    birth_attempts = 0
    for attempt in range(3):
        birth_attempts += 1
        adb.tap(*ID_BIRTH_FIELD); time.sleep(1.2)   # 키패드 전환(이 지점만 넉넉히)
        subprocess.run(["adb", "shell", "input", "text", birth6]); time.sleep(0.4)
        adb.tap(*ID_KEYPAD_NEXT); time.sleep(0.5)   # 키패드 '다음' = 성별칸 (직접탭은 포커스 실패)
        subprocess.run(["adb", "shell", "input", "text", gender]); time.sleep(0.4)
        if _verify_identity_dump(name, birth6, gender):
            break
        print(f"   [identity] 생년월일/성별 미반영 — 재시도 {attempt + 1}", flush=True)
    else:
        out["err"] = "dump 검증 실패(생년월일/성별 미입력) — 확인 안 누름"; return out
    lap(f"본인인증 이름자모+생년월일+성별 (생년월일 {birth_attempts}회 시도)")
    adb.tap(*ID_CONFIRM); time.sleep(2.5)   # 인증 처리
    lap("본인인증 확인 + 2.5s 처리대기")
    out["ok"] = True
    return out


# 본인인증 2단계: 카드 비밀번호 4자리.
# 첫 결제는 이름+생년월일 후 등장, 이후 결제는 본인인증 화면이 곧장 '카드비밀번호|휴대폰' 탭.
ID_CARDPW_TAB = (303, 596)        # '카드비밀번호' 탭 — 휴대폰 탭이 기본 선택일 수 있어 명시 선택 필요
ID_CARDPW_FIELD = (540, 812)      # '카드 비밀번호 4자리' 입력란 (탭과 구분)


def enter_card_password() -> dict:
    """카드 비밀번호 4자리(secrets 현대.card_pw4) → 확인.
    PIN과 동일한 고정 키패드 → input_pin hyundai_hmall_pw4 (4엔진 OCR). 입력란(540,812) 탭이 핵심."""
    out = {"ok": False}
    pw = str((json.loads((ROOT / "secrets" / "card_secrets.json").read_text(encoding="utf-8"))
              .get("현대") or {}).get("card_pw4", ""))
    if len(pw) != 4:
        out["err"] = "card_pw4 없음"; return out
    # '안전한 결제 위해 추가 인증' 팝업이 키패드를 덮어 OCR 0개 → 먼저 닫기 (실측 #5)
    if screen_has("추가 인증") or screen_has("안전한 결제"):
        ocr_tap("확인", retries=2); time.sleep(0.9)
    # 카드비밀번호 탭 명시 선택(휴대폰 탭 기본선택 대비) → 입력란 등장 → 입력란 탭 → 키패드
    _adb().tap(*ID_CARDPW_TAB); time.sleep(0.8)
    _adb().tap(*ID_CARDPW_FIELD); time.sleep(1.3)
    lap("카드비번 탭선택 + 입력란 탭 + 렌더대기 후")
    try:
        FlowRunner(use_camera=False).run_action(
            {"action": "input_pin", "preset": "hyundai_hmall_pw4", "value": pw,
             "tap_delay_sec": 0.4, "use_camera": False})
    except Exception as e:
        out["err"] = f"카드비번 input_pin 실패: {e}"; return out
    lap("카드비번 PW4 input_pin (vote+탭4) 완료")
    time.sleep(0.8)
    ocr_tap("확인", post=0.3, retries=2)
    out["ok"] = True
    return out


# ──────────────────────────── 결제 시퀀스 ────────────────────────────

def _pick_card_from_grid(grid_name: str = "현대카드") -> bool:
    """결제수단변경/신용카드 선택 → '카드 선택' 그리드 → grid_name 탭 (등록 없이 카드+할인 설정).
    실측: opener = 등록계정 '결제수단변경' / 미등록 '신용카드 선택'. 그리드 한 단계 더(신용카드 선택)일 수 있음.
    (#7·#8 현대카드, #4 롯데카드 2026-05-31 검증.)"""
    # 결제수단 섹션(결제수단변경/신용카드 선택)이 화면 아래일 수 있음 → 스크롤하며 opener 탐색
    op = None
    for _ in range(5):
        its = _ocr_texts(cap())
        op = next((it for it in its if "결제수단변경" in it["text"]), None) or \
             next((it for it in its if "신용카드 선택" in it["text"]), None)
        if op:
            break
        _adb().swipe(540, 1700, 540, 900, 400); time.sleep(0.8)   # 결제수단 보이게 스크롤 다운
    if not op:
        return False
    _adb().tap(op["cx"], op["cy"]); time.sleep(1.8)
    its2 = _ocr_texts(cap())
    if not any(it["text"].strip().startswith(grid_name) for it in its2):   # 신용카드 선택 드롭다운 한 단계 더
        sc = next((it for it in its2 if "신용카드 선택" in it["text"]), None)
        if sc:
            _adb().tap(sc["cx"], sc["cy"]); time.sleep(1.5)
    # '카드 선택' 그리드는 카드 많아 길 수 있음 → grid_name 안 보이면 그리드 영역 스크롤하며 탐색
    hd = None
    for _ in range(5):
        # startswith: 'BC'→'비씨카드'가 그리드엔 '비씨카드(페이북)'로 뜸(접미사) 대응. 정확매칭의 상위집합.
        hd = next((it for it in _ocr_texts(cap()) if it["text"].strip().startswith(grid_name)), None)
        if hd:
            break
        _adb().swipe(540, 1750, 540, 1050, 400); time.sleep(0.8)   # 그리드 스크롤 다운
    if not hd:
        return False
    _adb().tap(hd["cx"], hd["cy"]); time.sleep(2.0)
    return True


def _pick_hyundai_from_grid() -> bool:
    return _pick_card_from_grid("현대카드")


def select_card_discount(grid_name: str = "현대카드") -> dict:
    """주문서 '카드할인'에서 당일 할인카드(오른쪽에 금액 적힌 행) 선택 → '결제수단' 자동변경.
    grid_name = 미등록 계정 그리드 fallback 카드명(당일 카드, 기본 현대카드).
    실측(#6 2026-05-31): 카드할인 행 탭 → 결제수단이 카카오페이→'현대카드'로 자동 변경 + 즉시할인 적용.
    ⚠️ 금액(원) 적힌 행만 선택. '현대카드 Ed2 7% 청구할인'처럼 '>'만 있고 금액 없는 건 이벤트 안내 → 탭 금지.
       금액 카드 2개면 위(첫번째)=당일 할인카드."""
    out = {"ok": False}
    for _ in range(6):
        its = _ocr_texts(cap())
        hdr = next((it for it in its if it["text"].strip() == "카드할인"), None)
        if hdr:
            pmt = next((it for it in its if "결제수단" in it["text"] and it["cy"] > hdr["cy"]), None)
            y_lo, y_hi = hdr["cy"], (pmt["cy"] if pmt else hdr["cy"] + 700)
            # 금액(원) 포함 행 = 선택 가능 카드할인 ('>' 이벤트 안내는 금액 없음).
            # ⚠️ OCR이 '현대 5% 즉시할인 323,190원'을 한 덩어리/별도로 들쭉날쭉 → cx 무관, 금액 패턴으로만.
            # ⚠️ 하단 'NNN원 결제하기' 버튼도 금액 포함 → '결제하기' 들어간 행 제외 (#8 오탭 버그).
            rows = [it for it in its if re.search(r"[\d,]{4,}\s*원", it["text"])
                    and y_lo < it["cy"] < y_hi and "결제하기" not in it["text"]]
            rows.sort(key=lambda it: it["cy"])          # 위(첫번째)=당일 할인카드
            if rows:
                _adb().tap(350, rows[0]["cy"]); time.sleep(1.8)   # 카드 행 좌측 탭 (즉시할인 적용)
                out["amt"] = rows[0]["text"]
                t2 = " ".join(x["text"] for x in _ocr_texts(cap()))
                # 카드 미등록 계정(#7): 결제수단 '신용카드 선택' → 카드선택 그리드에서 그 카드 선택.
                # (등록 계정은 카드할인 탭만으로 결제수단 자동 → 그리드 불필요)
                if "신용카드 선택" in t2:
                    out["grid"] = _pick_card_from_grid(grid_name)
                    t2 = " ".join(x["text"] for x in _ocr_texts(cap()))
                out["applied"] = "적용" in t2                      # '적용되었어요' 토스트
                out["ok"] = "신용카드 선택" not in t2              # placeholder 사라짐 = 현대카드 설정됨
                return out
        _adb().swipe(540, 1700, 540, 800, 400); time.sleep(0.8)   # 스크롤 다운
    out["err"] = "카드할인 섹션 못 찾음"
    return out


def pay_lotte() -> dict:
    """롯데카드 SDK — **롯데카드 선택된 주문서에서 호출**. 주문완료까지.
    hmall-side(원결제하기·로카페이 결제하기)=OCR(hmall WebView는 dump 불가, 실측), 롯데앱 네이티브=검증흐름 재사용.
    실측 2026-05-31 #4 end-to-end(주문 20260531072669):
      원결제하기(OCR) → 로카페이(앱카드) 결제하기(OCR) → 롯데앱(com.lcacApp) 자동이동
      → [lotte_card.json step14~ FlowRunner: 로카페이 결제요청 결제하기→간편번호6(137601,content-desc dump)→확인]
      → hmall 복귀 자동 주문완료(결제완료 버튼 없음=ignore_fail 정상)."""
    out = {"step": "lotte"}
    # 1) 원 결제하기 (hmall WebView → OCR)
    if not ocr_tap("결제하기", contains=True):
        out["err"] = "원결제하기 실패"; return out
    # 2) 롯데 결제방식 화면(로카페이 앱카드) → '결제하기'(유일) OCR
    if not wait_text("로카페이", timeout=12):
        out["err"] = "롯데 결제방식 화면 미도달"; return out
    if not ocr_tap("결제하기"):
        out["err"] = "로카페이(앱카드) 결제하기 실패"; return out
    lap("롯데: 로카페이 앱카드 결제하기 → 롯데앱 진입")
    # 3) 롯데앱(com.lcacApp) 자동이동 → 검증된 네이티브 흐름 (FlowRunner, step14=wait_for_activity 롯데앱부터)
    flow = json.loads(LOTTE_FLOW.read_text(encoding="utf-8"))["flow_payment"]
    try:
        FlowRunner(use_camera=False).run(flow[14:], {})   # 롯데앱 대기→결제하기→간편번호dump→확인→hmall복귀
        out["ok"] = True
    except Exception as e:
        out["err"] = f"롯데앱 SDK 실패: {e}"
    return out


def _wait_app(pkg: str, timeout: float = 15) -> bool:
    """foreground 액티비티가 pkg 가 될 때까지 대기 (카드앱 진입/hmall 복귀 판정)."""
    end = time.time() + timeout
    while time.time() < end:
        out = subprocess.run(["adb", "shell", "dumpsys", "activity", "activities"],
                             capture_output=True, text=True).stdout
        if any("topResumedActivity" in ln and pkg in ln for ln in out.splitlines()):
            return True
        time.sleep(0.5)
    return False


# 주문완료(orderComplete) / 결제거절 OCR 마커 (주문서 단계엔 없는 토큰만 → 오탐 방지)
ORDER_DONE_MARKERS = ("주문이 완료", "주문번호", "재인증")          # orderComplete 페이지에만 등장
ORDER_FAIL_MARKERS = ("승인 요청 실패", "한도초과", "승인 실패", "결제 실패", "실패하였습니다")  # KCP/카드 거절


def wait_order_complete(timeout: float = 20) -> dict:
    """결제 직후 hmall orderComplete 렌더를 폴링 (전 카드 공통). 두 목적:
      ① 주문완료 마커 확인 → beauty가 너무 일찍 돌아 '재인증' 못 찾는 타이밍버그 해결(KB #1 실측).
      ② 거절 마커 감지 → 'hmall 복귀=성공' 오보고 방지(BC 한도초과 CC61 실측).
    완료=ok:True, 거절=ok:False+reason, 타임아웃=ok:False."""
    end = time.time() + timeout
    while time.time() < end:
        txt = " ".join(it["text"] for it in _ocr_texts(cap()))
        for fm in ORDER_FAIL_MARKERS:
            if fm in txt:
                return {"ok": False, "reason": f"결제거절:{fm}"}
        for dm in ORDER_DONE_MARKERS:
            if dm in txt:
                return {"ok": True, "reason": dm}
        time.sleep(0.8)
    return {"ok": False, "reason": "주문완료 미확인(timeout — 거절/지연 가능)"}


def pay_kb() -> dict:
    """KB국민카드 SDK (라이브검증 2026-05-31 #1, 주문 20260531079448). KB국민카드 선택된 주문서에서.
    원결제하기(OCR) → 'KB Pay 결제' 박스(OCR, 노란 앱카드) → KB앱(com.kbcard.cxh.appcard) 결제하기(dump)
    → 간편번호6(137601, content-desc dump; FLAG_SECURE라 화면캡처 검정이나 dump O; **6자리 자동제출**) → hmall 복귀 주문완료.
    ⚠️ '입력완료' 불필요(자동제출). ⚠️ 지체 금지 — 주문완료가 곧 home(initApp)으로 자동이동(뷰티포인트는 buy_one이 즉시 처리)."""
    out = {"step": "kb"}
    if not ocr_tap("결제하기", contains=True):                       # 1) 원 결제하기 (hmall WebView OCR)
        out["err"] = "원결제하기 실패"; return out
    if not wait_text("KB Pay", timeout=12):
        out["err"] = "KB결제 팝업 미도달"; return out
    box = next((it for it in _ocr_texts(cap()) if it["text"].strip() == "KB Pay 결제"), None)  # 2) 노란 박스
    if not box:
        out["err"] = "KB Pay 결제 박스 미발견"; return out
    _adb().tap(box["cx"], box["cy"]); time.sleep(0.5)
    out["step"] = "kb_app"
    if not _wait_app("com.kbcard", timeout=15):                      # 3) KB앱 자동이동
        out["err"] = "KB앱 미진입"; return out
    fr = FlowRunner(use_camera=False)
    try:
        fr.run_action({"action": "tap_dump_text", "text": "결제하기"})            # KB앱 결제하기 → 비번화면
        time.sleep(2.5)
        fr.run_action({"action": "input_pin", "value": "137601", "source": "dump"})  # 간편번호6 dump 자동제출
    except Exception as e:
        out["err"] = f"KB앱 결제 실패: {e}"; return out
    if not _wait_app("com.hmallapp", timeout=15):                    # 4) hmall 복귀
        out["err"] = "hmall 복귀 실패"; return out
    out["ok"] = True
    return out


def pay_hana() -> dict:
    """하나카드 SDK — **하나카드 선택된 주문서에서 호출**. 주문완료까지. (롯데와 동일 패턴: hmall-side OCR + 카드앱 flow재사용)
    hmall-side(원결제하기·'하나Pay 하나카드 결제' 박스)=OCR. 하나앱(com.hanaskcard.paycla) 네이티브=hana_card.json flow[16:] 재사용.
    ⚠️ 하나 nFilter 키패드 = content-desc 마스킹(dump 불가)이나 **screencap 읽힘(FLAG_SECURE 아님)** → `input_pin source=sequential_logo`
       (로고 decoy칸 검출로 1~0 순서매핑, 8↔0 OCR혼동 회피). KB/롯데(dump)와 다름 — json이 알아서 처리.
    ⚠️ 방치 시 하나 보안앱 '재실행' 경고 → 빠른 완주 필수(flow에 sleep만, 추가 지체 금지)."""
    out = {"step": "hana"}
    if not ocr_tap("결제하기", contains=True):           # 1) 원 결제하기 (hmall WebView OCR)
        out["err"] = "원결제하기 실패"; return out
    if not wait_text("하나카드 결제", timeout=15):        # 2) 하나카드 결제방식 화면(로딩 느려 여유)
        out["err"] = "하나 결제방식 화면 미도달"; return out
    if not ocr_tap("하나카드 결제", contains=True):       # '하나Pay 하나카드 결제' 박스 (MG+/간편결제/SMS/일반 아님)
        out["err"] = "하나Pay 하나카드 결제 박스 실패"; return out
    lap("하나: 하나Pay 박스 → 하나앱 진입")
    # 3) 하나앱(com.hanaskcard.paycla) 자동이동 → 검증흐름 재사용 (flow[16]=wait_for_activity hanaskcard부터)
    flow = json.loads(HANA_FLOW.read_text(encoding="utf-8"))["flow_payment"]
    try:
        FlowRunner(use_camera=False).run(flow[16:], {})  # 하나앱 대기→다음→nFilter6(OCR sequential_logo)→hmall복귀
        out["ok"] = True
    except Exception as e:
        out["err"] = f"하나앱 SDK 실패: {e}"
    return out


def pay_bc() -> dict:
    """BC카드(페이북/KCP) SDK — **비씨카드 선택된 주문서에서 호출**. 주문완료까지. (롯데/하나와 동일 패턴)
    hmall-side(원결제하기)=OCR. 이후 KCP '다음'→페이북앱(kvp.jjy.MispAndroid320)=bc_paybook_isp.json **flow[6:]** 재사용.
    ⚠️⚠️ **페이북앱 기본 결제수단='페이북 머니'(현금/포인트)** — flow[10]에서 반드시 '카드 결제' 선택 + flow[12] verify_selected
       하드가드(페이북머니 selected면 FlowError로 결제중단)가 내장. **페이북머니로 결제 절대금지.**
    ⚠️ BC 결제비번 키패드 = 셔플, FLAG_SECURE 아님(screencap) → `input_pin kind=bc_pin6`(4엔진 vote_digits 매핑). dump 아님.
    ⚠️ 미검증 드래프트(2026-05-22 캡처) — 첫 라이브에서 KCP '다음'(dump vs OCR)·결제수단시트 문구 조정 가능."""
    out = {"step": "bc"}
    if not ocr_tap("결제하기", contains=True):       # 1) 원 결제하기 (hmall WebView OCR; hmall이 비씨카드 적용)
        out["err"] = "원결제하기 실패"; return out
    lap("BC: 원결제하기 → KCP/페이북")
    # 2) KCP '다음' → 페이북앱 → 카드결제(가드) → 결제하기 → PIN6 → hmall복귀 (flow[6]=sleep4000부터)
    flow = json.loads(BC_FLOW.read_text(encoding="utf-8"))["flow_payment"]
    try:
        FlowRunner(use_camera=False).run(flow[6:], {})
        out["ok"] = True
    except Exception as e:
        out["err"] = f"BC/페이북앱 SDK 실패: {e}"
    return out


def detect_card() -> str | None:
    """주문서에서 '카드할인' 섹션까지 **스크롤하며** 금액행 카드사 토큰 추출 ('현대 5% 즉시할인'→'현대').
    구매하기 직후 주문서 상단엔 상품정보뿐 → 카드할인은 아래라 스크롤 필수(#4 실측). 없으면 None."""
    for _ in range(6):
        its = _ocr_texts(cap())
        hdr = next((it for it in its if it["text"].strip() == "카드할인"), None)
        if hdr:
            pmt = next((it for it in its if "결제수단" in it["text"] and it["cy"] > hdr["cy"]), None)
            y_hi = pmt["cy"] if pmt else hdr["cy"] + 700
            region = [it for it in its if hdr["cy"] < it["cy"] < y_hi]
            # 선택 가능한 카드할인(금액행 있음) 확인 — '>' 이벤트 안내만이면 None
            has_amt = any(re.search(r"[\d,]{4,}\s*원", it["text"]) and "결제하기" not in it["text"]
                          for it in region)
            if not has_amt:
                return None
            # ⚠️ OCR이 '현대 5% 즉시할인'과 '255,968원'을 별도 item으로 쪼갤 수 있음 → 영역 전체에서
            #    카드사 토큰 검색(위=첫번째 당일카드). 금액 item엔 카드명 없음(#4 실측).
            for it in sorted(region, key=lambda x: x["cy"]):
                for alias, key in CARD_ALIASES.items():   # 별칭 매핑(현대 외 변형표기 대비, 현대 오폴백 방지)
                    if alias in it["text"]:
                        return key
            return None
        _adb().swipe(540, 1700, 540, 800, 400); time.sleep(0.8)   # 카드할인 보이게 스크롤 다운
    return None


def select_card(card: str, day: str | None = None) -> dict:
    """결제수단을 card로 설정. 당일 할인카드면 캐러셀(할인 적용), 아니면 그리드(할인 없음).
    당일카드는 캐러셀로(메모리: 굳이 그리드 우회 X — 오류↑). day=호출측이 감지한 당일카드(중복 감지 회피)."""
    if day is None:
        day = detect_card()
    grid_name = CARD_GRID_NAME.get(card, card + "카드")
    if day == card:
        return select_card_discount(grid_name)                 # 캐러셀 (당일 할인)
    ok = _pick_card_from_grid(grid_name)                        # 그리드 (오늘 할인 아님 — 강제선택)
    return {"ok": ok, "via": "grid", "err": None if ok else f"{card} 그리드 선택 실패"}


def pay_hyundai(pin: str = CARD_PIN) -> dict:
    """현대카드 SDK — **현대카드 선택된 주문서에서 호출**. 결제하기 → PIN결제 → 안전팝업까지.
    (구매하기·카드선택은 buy_one/select_card. 본인인증/주문완료는 handle_after_pay)."""
    out = {"step": "order_page"}
    # 6) 결제하기(금액) → 현대 결제방식 (SDK 로딩)
    if not ocr_tap("결제하기", contains=True):
        out["err"] = "결제하기(금액) 실패"; return out
    # 카드 미등록 계정: '카드종류를 선택해주세요' 팝업(결제수단=신용카드 선택) vs 'PIN번호 결제'(현대) 판정
    end = time.time() + 15
    while time.time() < end:
        t = " ".join(x["text"] for x in _ocr_texts(cap()))
        if "카드종류" in t or "신용카드 선택" in t:
            out["err"] = "현대카드 미등록 — 결제수단에 등록된 카드 없음(계정에 현대카드 등록 필요)"; return out
        if "PIN번호 결제" in t:
            break
        time.sleep(0.3)
    else:
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
    lap("PIN dot 탭 + 1.3s 렌더대기 후")
    # 9) PIN 6자리 입력 (OCR 4엔진 고정 키패드)
    FlowRunner(use_camera=False).run_action(
        {"action": "input_pin", "preset": "hyundai_hmall_pin6", "value": pin,
         "tap_delay_sec": 0.4, "use_camera": False})
    lap("PIN6 input_pin (vote+탭6) 완료")
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
        if ("생년월일" in txt) or ("카드비밀번호" in txt) or ("카드 비밀번호" in txt) or ("본인 인증" in txt):
            # 본인인증 변종 분기: 첫 결제만 이름+생년월일+성별, 이후 결제는 카드비번 4자리만.
            # PIN6 이후 화면이 계정마다 다름 → '생년월일' 글자 있으면 name+birth 폼, 없으면 카드비번 전용.
            if "생년월일" in txt:
                print("   [identity] name+birth 폼 감지 → 이름+생년월일+성별", flush=True)
                r = enter_identity_auth()
                print(f"   [identity] {r}", flush=True)
                if not r.get("ok"):
                    return "IDENTITY_FAIL"
            else:
                print("   [identity] 카드비번 전용 화면 (name+birth 불필요)", flush=True)
            # 카드 비밀번호 4자리 — 화면 등장까지 폴링(한방 체크 X).
            # [버그수정] 기존엔 1.0s 후 screen_has 단 1회 → 카드비번 화면이 늦게 뜨면 놓치고
            #            입력란을 못 눌러 키패드 안 뜬 채 25s 헛대기(사용자 보고 "1분+ 멈춤").
            #            → 뜨는 즉시 입력란 탭하도록 폴링. 주문완료 먼저 뜨면 카드비번 불필요.
            cpw_seen = False
            end_cpw = time.time() + 15
            while time.time() < end_cpw:
                t = " ".join(x["text"] for x in _ocr_texts(cap()))
                if "주문" in t and "완료" in t:
                    return "ORDER_COMPLETE"           # 카드비번 불필요 계정
                if "카드 비밀번호" in t or "카드비밀번호" in t:
                    lap("카드비번 화면 등장 감지(폴링)")
                    cp = enter_card_password()
                    print(f"   [cardpw] {cp}", flush=True)
                    if not cp.get("ok"):
                        return "CARDPW_MANUAL"         # 키패드 OCR 실패 → 사용자 4자리 입력 후 재개
                    cpw_seen = True
                    break
                time.sleep(0.3)
            if not cpw_seen:
                print("   [cardpw] 15s 내 카드비번 화면 미등장 — 주문완료 폴링으로", flush=True)
            end2 = time.time() + 25
            while time.time() < end2:
                if (lambda t: "주문" in t and "완료" in t)(" ".join(x["text"] for x in _ocr_texts(cap()))):
                    return "ORDER_COMPLETE"
                time.sleep(0.3)
            return "AFTER_AUTH_UNKNOWN"
        time.sleep(0.3)
    return "UNKNOWN"


# ──────────────────────────── 1계정 오케스트레이션 ────────────────────────────

def buy_one(idx: int, card: str | None = None) -> dict:
    serial = hw._serial()
    res = {"idx": idx, "status": None}
    print(f"\n{'='*54}\n[#{idx}] 앱 콜드런치 → 로그인...", flush=True)

    def _launch_and_login():
        hw.reset_to_main(serial)   # force-stop+콜드런치+8s 안정화 (CDP 준비, 꼬인 상태 원천제거)
        close_home_popup()         # 앱 켜자마자 OCR 1회 → 광고 떠 있으면 닫기(날마다 다름, 없으면 통과)
        return hw.login_account(idx, serial)

    try:
        lr = _launch_and_login()
    except Exception as e:                          # cold-launch CDP race(Remote end closed 등) → 1회 재시도
        print(f"   [login] 1차 실패 ({e}) → 콜드런치 재시도", flush=True)
        lr = _launch_and_login()
    res["id"] = lr.get("id")
    if not lr.get("success"):
        res["status"] = f"LOGIN_FAIL:{lr.get('error')}"; return res
    # 카트 확인
    cs = hw.cart_state(serial)
    if cs.get("empty"):
        res["status"] = "SKIP_EMPTY(이미구매)"; return res
    print(f"[#{idx} {res['id']}] 카트 차있음 → 보이는 카트 진입", flush=True)
    lap_reset(); lap("측정시작(로그인+카트확인 후)")
    if not goto_cart():
        res["status"] = "CART_NAV_FAIL"; return res
    # 전체선택 (CDP)
    ok, sel = cdp_select_all()
    print(f"[#{idx}] 전체선택 {sel} ok={ok}", flush=True)
    if not ok:
        res["status"] = f"SELECT_ALL_FAIL:{sel}"; return res
    lap("카트진입 + 전체선택")
    # 구매하기 → 주문서 (공통, 카드무관)
    if not ocr_tap("구매하기"):
        res["status"] = "BUY_FAIL(구매하기)"; return res
    if not wait_text("결제하기", timeout=15):
        res["status"] = "ORDER_PAGE_FAIL(주문서 미도달)"; return res
    # 카드 결정 + 선택 (공통) — card 미지정 시 카드할인 캐러셀 당일카드 자동감지(스크롤 포함)
    day_card = detect_card()
    use_card = card or day_card or "현대"
    res["card"] = use_card
    print(f"[#{idx}] 당일카드 감지={day_card}, 사용={use_card}", flush=True)
    if use_card not in CARDS_SUPPORTED:
        res["status"] = f"UNSUPPORTED_CARD:{use_card}(SDK 미구현)"; return res
    sc = select_card(use_card, day_card)
    if not sc.get("ok"):
        res["status"] = f"SELECT_CARD_FAIL:{use_card}:{sc.get('err')}"; return res
    lap(f"카드 선택 ({use_card})")
    # 카드별 SDK ⚠️실돈
    print(f"[#{idx}] ⚠️ {use_card}카드 결제 실행", flush=True)
    if use_card == "현대":
        pay = pay_hyundai()
        res["pay"] = pay
        if pay.get("err"):
            res["status"] = f"PAY_FAIL@{pay.get('step')}:{pay['err']}"; return res
        after = handle_after_pay()
        res["after"] = after
        if after != "ORDER_COMPLETE":
            res["status"] = f"AFTER_PAY_{after}"; return res
    elif use_card == "롯데":
        lp = pay_lotte()
        res["pay"] = lp
        if not lp.get("ok"):
            res["status"] = f"LOTTE_FAIL@{lp.get('step')}:{lp.get('err')}"; return res
    elif use_card == "KB":
        kp = pay_kb()
        res["pay"] = kp
        if not kp.get("ok"):
            res["status"] = f"KB_FAIL@{kp.get('step')}:{kp.get('err')}"; return res
    elif use_card == "하나":
        hp = pay_hana()
        res["pay"] = hp
        if not hp.get("ok"):
            res["status"] = f"HANA_FAIL@{hp.get('step')}:{hp.get('err')}"; return res
    elif use_card == "BC":
        bp_ = pay_bc()
        res["pay"] = bp_
        if not bp_.get("ok"):
            res["status"] = f"BC_FAIL@{bp_.get('step')}:{bp_.get('err')}"; return res
    lap(f"{use_card} 결제 → 주문완료")
    # 주문완료 확인 (공통, 전 카드): orderComplete 렌더 대기 = ① beauty 타이밍 확보(KB) ② 거절 감지(BC 한도초과)
    oc = wait_order_complete(timeout=20)
    res["order_complete"] = oc
    if not oc.get("ok"):
        res["status"] = f"ORDER_NOT_COMPLETE:{use_card}:{oc.get('reason')}"; return res
    close_home_popup()   # 주문완료 화면에도 광고 팝업이 떠 '재인증' 버튼을 가림 → 닫기 (실측 #5)
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
    lap(f"뷰티포인트 재인증 → ★계정 #{idx} 총소요")
    res["status"] = "DONE" + ("" if bp.get("ok") else f"(beauty_fail:{bp.get('err')})")
    return res


def _do_beauty(res: dict) -> None:
    close_home_popup()   # 주문완료 화면 광고 팝업이 재인증 버튼 가림 → 닫기
    prof_cfg = json.loads(BP_PATH.read_text(encoding="utf-8"))
    profile = prof_cfg.get("profiles", {}).get(prof_cfg.get("active_profile"), {})
    bp = beauty_reauth(profile)
    res["beauty"] = bp
    if bp.get("ok"):
        time.sleep(1.0)
        if screen_has("완료"):
            ocr_tap("확인", post=2.0, retries=2)
    res["status"] = "DONE" + ("" if bp.get("ok") else f"(beauty_fail:{bp.get('err')})")


# ──────────────────────────── 화면 감지 + 상태머신 resume ────────────────────────────
# 중단/오류 시 '처음부터 재시작' 금지 → 현재 화면을 감지해 그 지점부터 나머지 이어서 진행.
SCREEN_MARKERS = [
    ("ORDER_COMPLETE", lambda t: "주문" in t and "완료" in t),
    ("AD_POPUP",       lambda t: "그만 보기" in t or "오늘 하루" in t or "보지 않기" in t),
    ("SAFE_POPUP",     lambda t: "안전한 결제" in t or "추가 인증" in t),
    ("IDENTITY_NAME",  lambda t: "생년월일" in t),                        # 이름+생년월일 폼(첫 결제)
    ("CARD_PW",        lambda t: "카드 비밀번호" in t or "카드비밀번호" in t),
    ("PIN_SCREEN",     lambda t: "PIN번호를 입력" in t),
    # ⚠️ PAY_CONFIRM 먼저: PIN 입력 후 최종 결제확인 화면이 헤더에 'PIN번호 결제'를 그대로 가져
    #    PAY_METHOD로 오분류돼 무한루프(#6 실측). '결제합니다'(=확인화면) 우선 판정.
    ("PAY_CONFIRM",    lambda t: "결제합니다" in t),
    ("PAY_METHOD",     lambda t: "PIN번호 결제" in t),
    ("ORDER_SHEET",    lambda t: "결제하기" in t),
    ("CART",           lambda t: "구매하기" in t or "담긴 상품" in t),
]


def detect_screen() -> str:
    """현재 화면 OCR → 플로우 단계 분류 (어느 지점이든 resume 가능)."""
    t = " ".join(it["text"] for it in _ocr_texts(cap()))
    for name, pred in SCREEN_MARKERS:
        if pred(t):
            return name
    return "UNKNOWN"


def resume(idx=None, max_steps: int = 50) -> dict:
    """현재 화면부터 결제 플로우 이어서 완주 (재시작 X). 상태머신 — 감지된 화면의 행동만 수행.
    ⚠️ CARD_PW/PIN/확인 도달 시 실제 과금 (결제 진행 중 화면에서만 호출)."""
    res = {"idx": idx, "mode": "resume", "status": None}
    last, stuck = None, 0
    for step in range(max_steps):
        s = detect_screen()
        print(f"[resume] step{step} 화면={s}", flush=True)
        stuck = stuck + 1 if s == last else 0
        last = s
        if stuck >= 6:
            res["status"] = f"STUCK@{s}"; return res
        if s == "ORDER_COMPLETE":
            close_home_popup()
            _do_beauty(res)                    # status=DONE 세팅
            return res
        elif s == "AD_POPUP":
            close_home_popup()
        elif s == "SAFE_POPUP":
            ocr_tap("확인", retries=2)
        elif s == "IDENTITY_NAME":
            r = enter_identity_auth()
            if not r.get("ok"):
                res["status"] = f"IDENTITY_FAIL:{r.get('err')}"; return res
        elif s == "CARD_PW":
            cp = enter_card_password()
            if not cp.get("ok"):
                res["status"] = f"CARDPW_FAIL:{cp.get('err')}"; return res
        elif s == "PIN_SCREEN":
            _adb().tap(*PIN_DOT); time.sleep(1.3)
            FlowRunner(use_camera=False).run_action(
                {"action": "input_pin", "preset": "hyundai_hmall_pin6", "value": CARD_PIN,
                 "tap_delay_sec": 0.4, "use_camera": False})
            time.sleep(0.8); ocr_tap("확인")
        elif s == "PAY_CONFIRM":
            ocr_tap("결제하기", contains=True)
        elif s == "PAY_METHOD":
            ocr_tap("PIN번호 결제", contains=True); wait_text("PIN번호를 입력", timeout=15)
        elif s == "ORDER_SHEET":
            ocr_tap("결제하기", contains=True); wait_text("PIN번호 결제", timeout=15)
        elif s == "CART":
            cdp_select_all()
            ocr_tap("구매하기"); wait_text("결제하기", timeout=15)
        else:                                   # UNKNOWN — 잠깐 대기 후 재감지
            time.sleep(1.2)
        time.sleep(0.4)
    res["status"] = "RESUME_MAX_STEPS"; return res


def main() -> int:
    args = sys.argv[1:]
    _resolve_serial()   # USB/무선 무관 — 연결된 기기 1개 자동 고정 (무선 IP 매번 바뀌어도 자동 검출)
    if args and args[0] == "resume":
        idx = int(args[1]) if len(args) > 1 and args[1].isdigit() else None
        r = resume(idx)             # 현재 화면 감지 → 그 지점부터 완주 (재시작 X)
        print(f"[resume #{idx}] => {r.get('status')}", flush=True)
        print(json.dumps(r, ensure_ascii=False), flush=True)
        return 0
    if args and args[0] == "lotte":
        # 롯데카드 선택된 주문서('원 결제하기' 보임)에서 호출 → 롯데 SDK (검증 흐름 재사용)
        r = pay_lotte()
        print(f"[lotte] => {r}", flush=True)
        return 0
    only = [int(a) for a in args if a.isdigit()]
    card_override = next((a for a in args if a in CARD_GRID_NAME), None)   # '현대'/'롯데' 강제 (없으면 당일 자동감지)
    plan = only or PLAN
    print(f"[serial] {hw._serial()}  plan={plan}  card={card_override or '당일 자동감지'}", flush=True)
    summary = []
    for idx in plan:
        try:
            r = buy_one(idx, card=card_override)
        except Exception as e:
            r = {"idx": idx, "status": f"EXC:{e}"}
        print(f"[#{idx}] => {r.get('status')}", flush=True)
        summary.append(r)
    print(f"\n{'='*54}\nSUMMARY", flush=True)
    for r in summary:
        print(f"  #{r['idx']:2d} {r.get('id','?'):16s} [{r.get('card','?')}] {r.get('status')}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
