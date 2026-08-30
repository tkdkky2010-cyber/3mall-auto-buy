"""현대몰 앱 현대카드 결제 + 뷰티포인트 재인증 — 1계정 end-to-end (phone-only hybrid).

#3(kgi5907)로 2026-05-29 23:xx 라이브 검증한 루트를 그대로 코드화.

루트 (2026-05-30 #5 라이브 end-to-end 검증):
  0. reset_to_main = force-stop+콜드런치+8s 안정화 → close_home_popup(광고 OCR 닫기, 날마다 다름)
     → login (hmall_webview CDP, cold-launch CDP race 시 1회 재시도)
  1. cart_state — 빈 카트 = 이미 구매 → SKIP
  2. 장바구니 아이콘 직접 탭(홈 경유 X) → 보이는 카트
  3. [CDP] basktList 헤더 체크박스 = 전체선택 (n/n 검증). native flow 전에 CDP 필수
  4. [OCR] 구매하기 → **'일반 결제' 탭**(★2026-08-19 사용자 지시 "무조건 일반결제" — 앱카드 금지.
     탭 없으면 앱카드 미등록=이미 일반결제) → 결제하기(금액) → PIN번호 결제 → PIN dot 탭 → 키패드
  5. [input_pin] hyundai_hmall_pin6=137601 (로컬 vision+easyocr → 실패 시 클로드 승격) → 확인
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
  SDK: 현대=**pay_hyundai_general**(일반결제 탭→PIN→본인인증→카드비번. 앱카드 결제 금지, 2026-08-19)
       / 롯데=pay_lotte(OCR+롯데앱 검증흐름 재사용). CARDS_SUPPORTED만.

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

PLAN = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19]
CARD_PIN = "137601"          # 현대카드 PIN 6자리 (공용)
CART_ICON = (1012, 151)      # hmall 메인 우측상단 장바구니 아이콘 (1080x2400)
HOME_NAV = (106, 2218)       # 하단 네비 '홈' (앱이 마이페이지로 복원돼도 홈 강제용)
PIN_DOT = (540, 660)         # PIN 동그라미 영역 — 탭하면 고정 키패드 호출
BP_PATH = ROOT / "secrets" / "beauty_point.json"
BP_LEDGER = ROOT / "secrets" / "beauty_point_ledger.json"   # 뷰티포인트 누적 추적 (세션 릴레이, 목표 180,000P)


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


def _ledger_append(idx: int, acct_id: str | None, profile_key: str | None) -> None:
    """뷰티 재인증 성공 1건 기록 — 다음 세션이 누적치를 이어받는 SoT.
    amount/points는 실측 어려워 건수 기반(요약 시 조합가로 보강). 실패해도 결제엔 영향 없음."""
    try:
        led = json.loads(BP_LEDGER.read_text(encoding="utf-8"))
        led.setdefault("entries", []).append({
            "date": time.strftime("%Y-%m-%d %H:%M"),
            "idx": idx, "id": acct_id, "profile": profile_key,
        })
        BP_LEDGER.write_text(json.dumps(led, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"   [ledger] 기록 실패(무시): {e}", flush=True)


# ──────── 결제 전 preflight — 오늘자 데이터 확인 (3사 공용) ────────
TODAY_JSON = ROOT / "cart" / "today.json"
TODAY_CARTS = ROOT / "cart" / "today_carts.json"


def preflight_today_files() -> bool:
    """★결제 시작 전 `cart/today.json` · `cart/today_carts.json` 이 **오늘자인지** 확인.
    stale 이면 False → 호출측이 결제를 **중단**한다.

    왜 하드스톱인가 (2026-08-05 실측 사고):
      `today_carts.json` 이 7/30 자였는데 **아무 에러 없이** 결제가 다 돌았다. 그 결과
        · 구매대장 기록 0건 — record_food 가 계정을 못 찾아 빈 루프로 조용히 통과
        · H.Point 적립 0건 — 적립 대상 상품/prmo 를 못 찾음
      둘 다 '조용히' 실패해서 사후에야 발견했다(12계정). 금액도 옛 단가로 기록될 수 있다.
      → 조용한 오작동보다 **시끄러운 정지**가 낫다.

    stale 이면 step1/step2 를 다시 돌려 갱신할 것. 정말 의도한 경우에만
    `ALLOW_STALE_CART=1` 로 우회(로그에 크게 남는다)."""
    today = time.strftime("%Y-%m-%d")
    bad = []
    for p in (TODAY_JSON, TODAY_CARTS):
        try:
            d = json.loads(p.read_text(encoding="utf-8")).get("date")
        except Exception as e:
            bad.append(f"{p.name}: 읽기 실패({e})")
            continue
        if d != today:
            bad.append(f"{p.name}: {d} (오늘 {today})")
    if not bad:
        print(f"[preflight] ✓ 오늘자 데이터 확인 ({today})", flush=True)
        return True
    print(f"\n{'='*54}\n[preflight] ✗ 오늘자가 아닌 데이터로 결제하려 함:", flush=True)
    for b in bad:
        print(f"    - {b}", flush=True)
    if os.environ.get("ALLOW_STALE_CART") == "1":
        print("[preflight] ⚠️⚠️ ALLOW_STALE_CART=1 — stale 인 걸 알면서 진행한다.\n"
              "            구매대장/적립이 조용히 누락될 수 있으니 결제 후 반드시 수동 확인할 것.",
              flush=True)
        return True
    print("[preflight] → 결제 중단. step1/step2 로 갱신 후 다시 실행할 것.\n"
          f"            (의도한 경우에만 ALLOW_STALE_CART=1)\n{'='*54}", flush=True)
    return False


LOTTE_FLOW = ROOT / "phone_auto" / "coords" / "apps" / "lotte_card.json"   # 검증된 롯데 결제흐름(5/29)
KB_FLOW = ROOT / "phone_auto" / "coords" / "apps" / "kb_kbpay.json"        # KB 결제흐름(DRAFT, 라이브검증중)
HANA_FLOW = ROOT / "phone_auto" / "coords" / "apps" / "hana_card.json"     # 하나 결제흐름(5/29 nFilter검증, flow[16:]=하나앱)
BC_FLOW = ROOT / "phone_auto" / "coords" / "apps" / "bc_paybook_isp.json"  # BC(페이북) 결제흐름(드래프트, flow[6:]=KCP다음~페이북앱)
# 카드사 → '카드 선택' 그리드 표기명 (카드할인 행 토큰은 키, 그리드명은 값). 결제 SDK 있는 카드만 활성.
CARD_GRID_NAME = {"현대": "현대카드", "롯데": "롯데카드", "하나": "하나카드", "KB": "KB국민카드",
                  "삼성": "삼성카드", "NH": "NH농협카드", "BC": "비씨카드"}
CARDS_SUPPORTED = ("현대", "롯데", "KB", "하나", "BC", "삼성", "NH", "토스")   # +토스페이(2026-07-15, 토스앱 로그인 전제 PIN)

# ★현대카드 = **무조건 일반 결제**. 앱카드 결제는 쓰지 않는다.
#   (사용자 지시 2026-08-19: "앱카드결제하면안되잖아 무조건 일반결제야")
#
#   앱카드 등록 계정은 캐러셀에서 현대카드 선택 시 결제수단이 '앱카드 결제'로 **자동선택**되고,
#   앱카드는 **누적금액 임계 초과 시 '현대카드 인증이 필요합니다'** 모달이 떠 자동화가 막힌다
#   (`PAY_FAIL@order_page:현대 결제방식 화면 미도달`).
#   → 주문서 결제수단 영역 [일반 결제|앱카드 결제] 에서 '일반 결제' 선택이 정본 = `pay_hyundai_general`.
#     탭이 없는 계정은 앱카드 미등록 = 이미 일반결제 → 그 안에서 기존 pay_hyundai(PIN) 로 이어간다.
#     식품·설화수 공통(단일 진입점).
#
#   ⚠️ 종전엔 `GENERAL_PAY_IDS = {"skykow"(07-10), "Jinhwa4553"(07-16)}` 로 **계정을 골라** 적용했다.
#      임계가 계정별 누적이라 **어제 되던 계정이 오늘 막힌다** — 2026-08-19 tkdkky2002(#1)가
#      454,080원에서 처음 걸렸다. 계정 선별도, 런타임 탭 감지도 "언젠간 막히는" 구조라 **분기를 지웠다.**
# 토스페이(간편결제 채널) = pay_toss (2026-07-15 라이브 작성). ★토스앱(viva.republica.toss) 로그인 전제 —
#   미로그인이면 게스트 본인확인(휴대폰번호+SMS/PASS)이 떠 자동화 불가(pay_toss가 감지해 안전정지).
#   PIN=dump 셔플(source=dump, 137601, FLAG_SECURE). 카카오페이=타 폰 사용중이라 제외. 상세=TOSS_PAY_NOTES.md.

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
    "토스페이": "토스", "토스": "토스",                                # 토스페이(간편결제, 카드감쌈) — detect_card 특수처리도 있음
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
    out = subprocess.run([hw.ADB, "devices"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10).stdout
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


def wake_screen() -> dict:
    """★화면 preflight (2026-07-10 근본수정, 양 몰 공용): 절전으로 화면 OFF 면 screencap=완전검정
    → OCR 전맹 → 모든 버튼 '미발견'으로 롯데 #11~14 가 4계정 연속 LOGOUT_FAIL 한 사고의 재발 방지.
    WAKEUP → 키가드 있으면 dismiss-keyguard+스와이프(비보안만 풀림) → 여전히 잠김=보안잠금(adb 해제 불가)
    → ok:False. 호출측(buy_one)은 즉시 중단하고 사용자 잠금해제 요청 (검은화면 헛돌기 금지).
    판정 문자열은 SM-G9960 실측: 'mWakefulness=Awake' / KeyguardServiceDelegate 'showing=true'."""
    def _sh(*args) -> str:
        return subprocess.run(["adb", "shell", *args], capture_output=True, text=True, encoding="utf-8", errors="replace").stdout
    _sh("input", "keyevent", "224"); time.sleep(1.0)            # KEYCODE_WAKEUP
    kg = "showing=true" in _sh("dumpsys", "window", "policy")
    if kg:
        _sh("wm", "dismiss-keyguard"); time.sleep(0.8)          # 비보안 키가드만 해제됨
        _sh("input", "touchscreen", "swipe", "540", "2000", "540", "700", "250"); time.sleep(1.2)
        _sh("input", "keyevent", "224"); time.sleep(0.5)        # 스와이프 중 재doze 대비
        kg = "showing=true" in _sh("dumpsys", "window", "policy")
    awake = "mWakefulness=Awake" in _sh("dumpsys", "power")
    ok = awake and not kg
    if not ok:
        print(f"   ✗ [screen] 화면 사용불가 — awake={awake} 잠금={kg}. "
              "보안잠금은 adb 로 못 풂 → 폰 직접 잠금해제 후 재실행", flush=True)
    return {"ok": ok, "awake": awake, "keyguard": kg}


def close_home_popup(max_iter: int = 4) -> int:
    """홈 광고 팝업 닫기 — cold launch 시 '오늘의 최저가' 등 모달이 떠 로그인/네비를 막음.
    '오늘 그만 보기'(당일 재등장 방지) 우선, 없으면 '닫기'. 여러 개 쌓일 수 있어 반복. 닫은 수 반환.

    ★이건 **OCR 판독본**이다. dump 판독본은 `hmall_webview.close_ad_popup` — logout/로그인폼 nav 가
      그쪽을 쓴다(2026-08-19: 로그인 전 1회 닫기로는 부족해서 신설). 문구 목록은 `hw.POPUP_KEYS`
      **한 군데**에서 가져온다 — 갈라지면 한쪽만 새 팝업을 배우고 다른 쪽이 조용히 막힌다."""
    closed = 0
    *dismiss_keys, close_key = hw.POPUP_KEYS      # 마지막('닫기')은 최후수단 — 정확일치로만 쓴다
    for _ in range(max_iter):
        its = _ocr_texts(cap())
        hit = None
        for key in dismiss_keys:                 # 재등장 방지 버튼 우선
            hit = next((it for it in its if key in it["text"]), None)
            if hit:
                break
        if not hit:
            hit = next((it for it in its if it["text"].strip() == close_key), None)
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


def ocr_or_dump_tap(text: str, contains: bool = False, retries: int = 4, post: float = 0.2) -> bool:
    """OCR 탭 → 실패하면 **uiautomator dump** 탭으로 재시도. True=탭 성공.

    ★왜 두 판독을 겹쳐 쓰나 (2026-08-19 윈도우 라이브 실측):
      현대 PIN 화면의 '확인' 은 **검은 버튼 + 흰 글씨**인데 `Windows.Media.Ocr` 이 못 읽는다
      (그 화면 판독 7건에 '확인' 없음, PIL 반전 전처리도 결과 동일 = 반전 문제가 아니다).
      그래서 `ocr_tap('확인')` 4회 전부 실패 → **PIN 6자리를 넣어둔 채** `PAY_FAIL@pin_entered`.
      결제 직전에서 멈추니 계정마다 사람이 손으로 눌러야 했다.
      이 화면은 **네이티브 뷰**라 dump 엔 `확인 bounds center=(540,1312)` 로 정확히 잡힌다
      (CDP 는 WebView 타깃이 아니라 접근 불가 — 홈/카트 페이지만 노출된다).
    ★순서를 OCR 먼저로 두는 이유: **WebView 화면은 반대로 dump 가 빈다.**
      실측 예 — 로그인 chooser 는 node 16개에 text 속성 0개라 dump 로는 아무것도 못 찾는다.
      두 화면 종류가 섞여 있으니 한쪽만 믿으면 조용히 못 누른다.
    """
    if ocr_tap(text, contains=contains, retries=retries, post=post):
        return True
    try:
        FlowRunner(use_camera=False).run_action(
            {"action": "tap_dump_text", "text": text, "exact": not contains, "timeout_sec": 6})
        print(f"   [dump] {text!r} 탭 (OCR 미판독 폴백)", flush=True)
        time.sleep(post)
        return True
    except Exception as e:
        print(f"   ✗ {text!r} OCR·dump 둘 다 실패: {e}", flush=True)
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


def _checkbox_selected(png: str, cx: int, cy: int) -> bool:
    """'일반상품' 헤더 체크박스 픽셀색 판정 — 선택=빨강(실측 249,132,117), 미선택=회색(235,235,235).
    중심 ±14px 평균 R-G > 60 이면 선택. OCR (n/m) 숫자 판정 대체(2026-06-05 오독 사고)."""
    from PIL import Image
    im = Image.open(png).convert("RGB")
    px = [im.getpixel((x, y)) for x in range(max(0, cx - 14), cx + 15)
          for y in range(max(0, cy - 14), cy + 15)]
    r = sum(p[0] for p in px) / len(px)
    g = sum(p[1] for p in px) / len(px)
    return (r - g) > 60


def cdp_select_all(timeout: float = 25) -> tuple[bool, str]:
    """전체선택 → (ok, '(n/m)'). '일반상품' 헤더 좌측 체크박스 탭 + **픽셀색 검증**.
    ⚠️ CDP basktList는 분절(stale/1개짜리 WebView 동시 visible)이라 신뢰 X(#4 실측).
    ⚠️ (n/m) 숫자 OCR 판정 금지 — 실측 사고 2건(2026-06-05 #2/#3/#17/#18):
      ① '(1/1)' 회색 두번째 1을 7로 오독 → (1/7) → 멀쩡한 선택을 토글로 해제
      ② 렌더 직후 첫 탭 불발(핸들러 미부착) 후 즉시 False 리턴
    → 판정은 체크박스 빨강(_checkbox_selected)으로만, 숫자는 리포팅용. timeout까지 탭 재시도."""
    end = time.time() + timeout
    last = "no-cart(일반상품 헤더 미발견)"
    while time.time() < end:
        png = cap()
        gb, mm = _cart_count(_ocr_texts(png))
        if gb is None:
            time.sleep(0.5); continue
        # 체크박스 중심 = 헤더 좌측끝 ~50px 왼쪽 (실측 (60,422), 계산 (65,425))
        bx, by = max(45, gb["cx"] - gb["w"] // 2 - 50), gb["cy"]
        last = mm.group(0) if mm else "no-count"
        if _checkbox_selected(png, bx, by):
            return True, last                                     # 선택 확인(픽셀)
        _adb().tap(bx, by); time.sleep(1.3)
    return False, last


_SEL_ONLY_JS = """(function(kws, doClick){
  var cbs=document.querySelectorAll('input[type=checkbox][name=backet]');
  var res=[];
  for(var i=0;i<cbs.length;i++){
    var cb=cbs[i];
    var row=cb.closest('li')||cb.parentElement;
    var t=((row&&row.innerText)||'').replace(/\\s+/g,' ');
    var want=false;
    for(var k=0;k<kws.length;k++){ if(t.indexOf(kws[k])>=0){ want=true; break; } }
    if(doClick && cb.checked!==want){ cb.click(); }
    res.push({t:t.slice(0,60), want:want, now:cb.checked});
  }
  return JSON.stringify({n:cbs.length, items:res});
})(%s, %s)"""


def cdp_select_only(keywords: list[str], timeout: float = 25) -> tuple[bool, str]:
    """카트에서 **상품명에 keywords 중 하나가 포함된 상품만** 선택하고 나머지는 해제 → (ok, 요약).

    혼합 카트를 카드별로 나눠 주문하기 위한 것 (2026-08-05 사용자 지시: 데이즈온=NH / 나머지=KB.
    한 주문 = 카드 하나라 상품별로 카드를 다르게 하려면 주문 자체를 나눠야 한다).

    ★DOM 을 정본으로 쓴다(OCR/픽셀 아님) — 개별 상품 체크박스는 헤더와 달리 **상품 수·구성에 따라
      위치가 움직여** 좌표 추정이 위험하고, DOM 은 상품명이 같이 읽혀 '무엇을 골랐는지' 확정된다.
      실측 구조(2026-08-05 #7): 개별상품 = `input[type=checkbox][name=backet]`,
      행 innerText 에 상품명. 헤더(전체선택)는 name 이 없어 자연히 제외된다.
    ⚠️ `cb.checked = true` 직접 대입 금지 — 페이지 핸들러(합계금액 갱신)가 안 돈다. 반드시 `.click()`.
    ⚠️ 선택 0건이면 실패 처리 — 키워드 오타로 전부 해제된 채 '구매하기'로 넘어가는 사고 방지."""
    kw_js = json.dumps(keywords, ensure_ascii=False)
    end = time.time() + timeout
    last = "no-cart(basktList WebView 미부착)"
    while time.time() < end:
        c = attach_visible_url("basktList")
        if c is None:
            nap(0.8); continue
        try:
            c.ev(_SEL_ONLY_JS % (kw_js, "true"), timeout=10)      # 선택/해제 클릭
            nap(1.2)                                        # 핸들러(합계 갱신) 반영 대기
            raw = c.ev(_SEL_ONLY_JS % (kw_js, "false"), timeout=10)  # 상태 재확인(클릭 없이)
        except Exception as e:
            last = f"CDP 실패: {e}"
            nap(0.8); continue
        finally:
            c.close()
        try:
            d = json.loads(raw)
        except Exception:
            nap(0.8); continue
        items = d.get("items", [])
        bad = [i for i in items if i["now"] != i["want"]]
        picked = [i for i in items if i["now"]]
        last = f"({len(picked)}/{len(items)})"
        if items and not bad and picked:
            for i in items:
                print(f"[cart] {'✓ 선택' if i['now'] else '· 제외'}: {i['t']}", flush=True)
            return True, last
        print(f"[cart] 선택 불일치 {len(bad)}건 / 선택 {len(picked)}건 → 재시도", flush=True)
        nap(1.0)
    return False, last


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
    adb.tap(*ID_NAME_FIELD); nap(0.6)
    globe_n = 0
    for _ in range(5):                       # 글로브 언어순환(EN→中文→…→한국어). 더 많아도 커버.
        if _kbd_is_korean():
            break
        adb.tap(*GLOBE); nap(0.7); globe_n += 1
    if not _kbd_is_korean():
        out["err"] = "키보드 한글전환 실패"; return out
    lap(f"본인인증 이름필드+한글키보드 (글로브 {globe_n}회)")
    for j in seq:
        adb.tap(*JAMO_XY[j]); nap(0.15)
    nap(0.25)
    # 생년월일 + 성별 (input text). 한글키보드→숫자키패드 전환 타이밍 finicky → 1.2s 유지 + 재시도.
    birth_attempts = 0
    for attempt in range(3):
        birth_attempts += 1
        adb.tap(*ID_BIRTH_FIELD); nap(1.2)   # 키패드 전환(이 지점만 넉넉히)
        subprocess.run(["adb", "shell", "input", "text", birth6]); nap(0.4)
        adb.tap(*ID_KEYPAD_NEXT); nap(0.5)   # 키패드 '다음' = 성별칸 (직접탭은 포커스 실패)
        subprocess.run(["adb", "shell", "input", "text", gender]); nap(0.4)
        if _verify_identity_dump(name, birth6, gender):
            break
        print(f"   [identity] 생년월일/성별 미반영 — 재시도 {attempt + 1}", flush=True)
    else:
        out["err"] = "dump 검증 실패(생년월일/성별 미입력) — 확인 안 누름"; return out
    lap(f"본인인증 이름자모+생년월일+성별 (생년월일 {birth_attempts}회 시도)")
    adb.tap(*ID_CONFIRM); nap(2.5)   # 인증 처리
    lap("본인인증 확인 + 2.5s 처리대기")
    out["ok"] = True
    return out


# 본인인증 2단계: 카드 비밀번호 4자리.
# 첫 결제는 이름+생년월일 후 등장, 이후 결제는 본인인증 화면이 곧장 '카드비밀번호|휴대폰' 탭.
ID_CARDPW_TAB = (303, 596)        # '카드비밀번호' 탭 — 휴대폰 탭이 기본 선택일 수 있어 명시 선택 필요
ID_CARDPW_FIELD = (540, 812)      # '카드 비밀번호 4자리' 입력란 (탭과 구분)


def enter_card_password() -> dict:
    """카드 비밀번호 4자리(secrets 현대.card_pw4) → 확인.
    PIN과 동일한 고정 키패드 → input_pin hyundai_hmall_pw4 (엔진 voting OCR). 입력란(540,812) 탭이 핵심."""
    out = {"ok": False}
    pw = str((json.loads((ROOT / "secrets" / "card_secrets.json").read_text(encoding="utf-8"))
              .get("현대") or {}).get("card_pw4", ""))
    if len(pw) != 4:
        out["err"] = "card_pw4 없음"; return out
    # '안전한 결제 위해 추가 인증' 팝업이 키패드를 덮어 OCR 0개 → 먼저 닫기 (실측 #5)
    # ★2026-08-19: 여기 검사가 `screen_has`(=OCR 전용)라 **팝업이 떠 있는데도 못 잡았다.**
    #   그 결과 키패드가 덮인 채 판독해 뒤 배경 글자만 3개 잡혔다(need 4개 중 부족 → 실패).
    #   `_dismiss_extra_auth_popup` = OCR+dump 병합 판독 정본으로 교체.
    _dismiss_extra_auth_popup()
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
    # ★이 '확인' 도 **검은 버튼 + 흰 글씨** → Windows OCR 미판독 (2026-08-19 #3 실측).
    #   종전엔 `ocr_tap` 전용 + **결과를 안 보고 ok=True** 를 돌려줬다 → 4자리를 넣어놓고 제출을
    #   못 한 채 '성공' 으로 보고, 호출측이 주문완료를 25s 기다렸다 `AFTER_AUTH_UNKNOWN` 으로 끝났다.
    #   READ_FIRST 「성공 메시지는 검증이 아니다」. dump 폴백 + 결과 반영 둘 다 필요하다.
    if not ocr_or_dump_tap("확인", post=0.3, retries=2):
        out["err"] = "카드비번 '확인' 제출 실패 (OCR·dump 둘 다 미발견)"; return out
    out["ok"] = True
    return out


# ──────────────────────────── 결제 시퀀스 ────────────────────────────

def _fuzzy_has(text: str, name: str) -> bool:
    """OCR 1글자 오독 허용 카드명 매칭 — text(공백제거) 안에 name과 hamming<=1 윈도우 존재.
    실측: '현대카드'를 '혀대카드'로 오독(#17 2026-06-05) → 정확매칭/startswith 실패."""
    t = text.replace(" ", "")
    n = len(name)
    if name in t:
        return True
    for i in range(len(t) - n + 1):
        if sum(a != b for a, b in zip(t[i:i + n], name)) <= 1:
            return True
    return False


def _dump_texts(serial: str | None = None) -> list[dict]:
    """uiautomator dump 의 text 노드를 **OCR 항목과 같은 형식**({text,cx,cy}) 으로 돌려준다.

    ★OCR 과 겹쳐 읽기 위한 것 (2026-08-19 실측). 현대몰 주문서에서 Windows OCR 은 글자를 깨먹는다:
        '口토사OI그' · '丁 0 曰' · '그L广 1 L TTOI — 1 LQ'
      그리고 **토스트 '현대 5% 즉시할인이 적용되었어요.' 는 아예 못 읽는다.**
      같은 순간 dump 는 '결제수단'·'일반 결제'·'앱카드 결제'·'현대카드'·토스트를 전부 깨끗하게 준다.
      반대로 **dump 가 비는 화면도 있다**(로그인 chooser = node 16개·text 0개) → 그래서 합쳐 쓴다.
      cf. 같은 교훈의 탭 버전 = `ocr_or_dump_tap`.
    """
    out: list[dict] = []
    try:
        xml = hw._dump(serial or hw._serial())
    except Exception as e:
        print(f"   [dump] 판독 실패(OCR 만 사용): {e}", flush=True)
        return out
    for m in re.finditer(r'text="([^"]+)"[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', xml):
        x1, y1, x2, y2 = map(int, m.groups()[1:])
        out.append({"text": m.group(1), "cx": (x1 + x2) // 2, "cy": (y1 + y2) // 2, "src": "dump"})
    return out


def _verify_pay_method(grid_name: str, timeout: float = 14) -> bool:
    """주문서 '결제수단' 행 **아래**에 목표 카드명 표기 **양성 검증** (fuzzy, 스크롤 포함).
    select_card 성공 판정은 이걸로만 — '신용카드 선택 placeholder 부재' 같은 부재 검증 금지(#17 오진).
    · 밴드 = 결제수단 행 아래 전체 (위쪽 'Ed2 현대카드 청구할인' 이벤트 행 오매칭 방지)
    · '적용되었어요' 토스트가 카드명 행을 덮을 수 있음 → 토스트 보이면 대기 후 재확인
    · 탭형 UI(카드/페이/무통장/휴대폰)는 카드명 행이 화면 하단에 잘림(#17 실측) → 소폭 스크롤 2회 탐색
      (300px — '카드할인' 헤더가 화면에 남아 호출측 재탐색 안 깨짐)"""
    end = time.time() + timeout
    swiped_big = 0
    swiped_small = 0
    while time.time() < end:
        # ★OCR + dump 를 **합쳐서** 읽는다 (2026-08-19 #3 실사고 — _dump_texts 주석 참고).
        #   OCR 만 보던 종전 코드는 토스트('…적용되었어요')를 못 읽어 대기 가드가 안 걸리고,
        #   토스트가 카드명 행을 덮은 채 소폭스크롤 2회 → 즉시 False → `SELECT_CARD_FAIL` 안전정지.
        #   결제가 되는데도 계정마다 멈춰 사람이 재실행해야 했다.
        its = _ocr_texts(cap()) + _dump_texts()
        txt = " ".join(it["text"] for it in its)
        pm = next((it for it in its if "결제수단" in it["text"]), None)
        if pm:
            band = [it for it in its if it["cy"] > pm["cy"] - 10]
            if any(_fuzzy_has(it["text"], grid_name) for it in band):
                return True
            if "적용되" in txt:                    # 토스트가 카드명 행을 덮는 중 → 사라질 때까지 재시도
                time.sleep(1.2); continue
            if swiped_small < 2:                    # 카드명 행이 탭 아래 잘림 → 소폭 스크롤
                _adb().swipe(540, 1750, 540, 1450, 400); swiped_small += 1
                time.sleep(0.7); continue
            return False                            # 결제수단 아래 어디에도 카드명 없음 = 미설정
        if swiped_big < 4:                          # '결제수단' 자체가 화면 밖 → 스크롤 다운
            _adb().swipe(540, 1700, 540, 900, 400); swiped_big += 1
        time.sleep(0.7)
    return False


def _pick_card_from_grid(grid_name: str = "현대카드") -> bool:
    """결제수단변경/신용카드 선택 → '카드 선택' 그리드 → grid_name 탭.
    ⚠️ '미등록' 개념 없음 — 모든 카드사가 모든 계정에서 그리드로 항상 선택 가능(2026-06-06 사용자 확인).
    opener 표기는 계정/UI 상태별: '결제수단변경' / '신용카드 선택' placeholder / 탭형(페이→카드).
    (#7·#8 현대카드, #4 롯데카드 2026-05-31 검증.)"""
    # 결제수단 섹션 opener 3단계 (화면 아래일 수 있음 → 스크롤하며 탐색):
    #   ① '결제수단변경' → ② '신용카드 선택' placeholder
    #   → ③ 탭형 UI(둘 다 없음): '페이/Pay' 탭 → 다시 '카드' 탭 = '카드 선택' 전체 그리드 등장
    #     (사용자 실측 2026-06-06 #17: 탭형 주문서에서 전 카드 목록 뜨는 유일 경로)
    opened = False
    opener_was_dropdown = False        # ★opener 가 '신용카드 선택' 그 자체였는가 (아래 재탭 금지 판정용)
    for _ in range(5):
        its = _ocr_texts(cap())
        chg = next((it for it in its if "결제수단변경" in it["text"]), None)
        drop = next((it for it in its if "신용카드 선택" in it["text"]), None)
        op = chg or drop
        if op:
            opener_was_dropdown = chg is None
            print(f"[grid] opener='{'결제수단변경' if chg else '신용카드 선택'}' 탭 "
                  f"@({op['cx']},{op['cy']})", flush=True)
            # 대기 2.5s — 1.8s 로는 바텀시트가 안 떠서 아래 재탭 분기로 빠졌다(2026-08-05 수동 실측).
            _adb().tap(op["cx"], op["cy"]); nap(2.5)
            opened = True
            break
        pay_tab = next((it for it in its if "페이" in it["text"] and "Pay" in it["text"]), None)
        card_tab = next((it for it in its if it["text"].strip() == "카드"), None)
        if pay_tab and card_tab:
            print("[grid] opener=탭형(페이→카드)", flush=True)
            _adb().tap(pay_tab["cx"], pay_tab["cy"]); nap(1.5)
            _adb().tap(card_tab["cx"], card_tab["cy"]); nap(1.5)
            opened = True
            break
        _adb().swipe(540, 1700, 540, 900, 400); nap(0.8)   # 결제수단 보이게 스크롤 다운
    if not opened:
        print("[grid] ✗ opener 미발견(결제수단 섹션 못 찾음)", flush=True)
        return False
    its2 = _ocr_texts(cap())
    if not any(_fuzzy_has(it["text"].strip(), grid_name) for it in its2):  # 신용카드 선택 드롭다운 한 단계 더
        if opener_was_dropdown:
            # ★★재탭 금지 — opener 가 이미 '신용카드 선택' 이었으면 방금 연 바텀시트를 **닫아버린다**.
            #   이게 2026-08-05 NH SELECT_CARD_FAIL 의 원인(워크로그 §5-10): 재탭으로 시트가 닫혀
            #   이후 스와이프가 시트가 아니라 주문서를 긁었고, 드롭다운은 '신용카드 선택'(비어있음)으로 남았다.
            print(f"[grid] '{grid_name}' 아직 안 보임 — opener 가 드롭다운이므로 재탭 금지, 스크롤 탐색", flush=True)
        else:
            sc = next((it for it in its2 if "신용카드 선택" in it["text"]), None)
            if sc:
                print(f"[grid] '신용카드 선택' 한 단계 더 탭 @({sc['cx']},{sc['cy']})", flush=True)
                _adb().tap(sc["cx"], sc["cy"]); nap(2.5)
    # '카드 선택' 그리드는 카드 많아 길 수 있음 → grid_name 안 보이면 그리드 영역 스크롤하며 탐색
    # _fuzzy_has: '비씨카드(페이북)' 접미사(startswith 상위집합) + '혀대카드' 류 OCR 오독 대응(#17).
    hd = None
    for i in range(5):
        hd = next((it for it in _ocr_texts(cap()) if _fuzzy_has(it["text"].strip(), grid_name)), None)
        if hd:
            break
        print(f"[grid] '{grid_name}' 미발견 #{i+1} → 그리드 스크롤", flush=True)
        _adb().swipe(540, 1750, 540, 1050, 400); nap(0.8)   # 그리드 스크롤 다운
    if not hd:
        print(f"[grid] ✗ '{grid_name}' 최종 미발견 — 선택 실패", flush=True)
        return False
    print(f"[grid] ✓ '{grid_name}' 탭 @({hd['cx']},{hd['cy']})", flush=True)
    _adb().tap(hd["cx"], hd["cy"]); nap(2.0)
    return True


def _pick_hyundai_from_grid() -> bool:
    return _pick_card_from_grid("현대카드")


def select_card_discount(grid_name: str = "현대카드") -> dict:
    """⚠️ LEGACY (2026-06-06부터 미사용) — 당일카드 선택 정본은 flow_runner
    `hmall_select_card_discount`(700px + 캐러셀금액==결제버튼금액 판정). 이 OCR 버전은 카드명
    검증이라 할인적용 미보장 → 새 코드에서 호출 금지. 참고용으로만 보존.

    주문서 '카드할인'에서 당일 할인카드(오른쪽에 금액 적힌 행) 선택 → '결제수단' 자동변경.
    grid_name = 그리드 fallback 카드명(당일 카드, 기본 현대카드).
    실측(#6 2026-05-31): 카드할인 행 탭 → 결제수단이 카카오페이→'현대카드'로 자동 변경 + 즉시할인 적용.
    ⚠️ 금액(원) 적힌 행만 선택. '현대카드 Ed2 7% 청구할인'처럼 '>'만 있고 금액 없는 건 이벤트 안내 → 탭 금지.
       금액 카드 2개면 위(첫번째)=당일 할인카드."""
    out = {"ok": False}
    taps = 0
    for _ in range(8):
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
                # ⚠️ 카드할인 행 = **토글**. 이미 목표카드 설정 상태에서 또 탭하면 해제됨
                # (#17 2026-06-05: 적용된 상태 재탭 → off → 결제수단 미설정 → '미등록' 오진).
                # → 탭 전에 결제수단 양성 검증 먼저. 성공 판정도 이걸로만(부재 검증 금지).
                if _verify_pay_method(grid_name, timeout=4):
                    out["ok"] = True
                    out["pre_applied"] = (taps == 0)
                    return out
                if taps >= 2:
                    out["err"] = f"결제수단 '{grid_name}' 설정 검증 실패(탭 {taps}회)"
                    return out
                _adb().tap(350, rows[0]["cy"]); time.sleep(1.8)   # 카드 행 좌측 탭 (즉시할인 적용)
                taps += 1
                out["amt"] = rows[0]["text"]
                its2 = _ocr_texts(cap())
                t2 = " ".join(x["text"] for x in its2)
                # '~% 즉시할인이 적용되었어요' 토스트의 카드 토큰이 목표와 다르면 = 다른 카드 행을 탭함
                # (detect 오감지 등) → 즉시 실패.
                toast = next((x["text"] for x in its2 if "적용되" in x["text"]), "")
                if toast:
                    out["toast"] = toast
                    tk = next((k for a, k in CARD_ALIASES.items() if a in toast), None)
                    expect = next((k for k, v in CARD_GRID_NAME.items() if v == grid_name), grid_name[:2])
                    if tk and tk != expect:
                        out["err"] = f"카드할인 적용 카드 불일치: 토스트='{toast}' (목표 {grid_name})"
                        return out
                # 결제수단 placeholder '신용카드 선택'이 뜨면 그리드에서 그 카드 선택
                # ('미등록' 개념 없음 — 그리드는 항상 사용 가능한 경로. 평소엔 행 탭만으로 자동설정)
                if "신용카드 선택" in t2:
                    out["grid"] = _pick_card_from_grid(grid_name)
                out["applied"] = "적용" in t2                      # '적용되었어요' 토스트
                continue                                           # 검증은 루프 상단 pre-check 에서
        else:
            _adb().swipe(540, 1700, 540, 800, 400); time.sleep(0.8)   # 스크롤 다운
    out.setdefault("err", "카드할인 섹션 못 찾음/설정 실패")
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
        out.update(_diagnose_loca())      # ★실패에 **이름**을 붙인다 (아래 함수 주석 참고)
    return out


# LOCA 앱이 결제 대신 띄우는 차단 화면들 — 화면의 고유 문구로 판정한다.
_LOCA_BLOCKERS = (
    ("LOCA_APP_RESET_REQUIRED",
     ("앱 재설정", "정보가 만료", "다시 인증이 필요"),
     "LOCA 앱 등록정보가 만료됐다 — 앱에서 '초기화' → 본인인증 → 로카페이·간편번호 재등록이 필요하다. "
     "재등록은 본인인증/비밀번호라 자동화 대상이 아니다(사람이 직접). 끝난 뒤 resume 으로 이어붙일 것."),
    ("LOCA_APP_UPDATE_REQUIRED",
     ("업데이트", "최신 버전"),
     "LOCA 앱 업데이트 요구 화면 — 스토어에서 업데이트 후 재실행할 것."),
    ("LOCA_LOGIN_REQUIRED",
     ("로그인", "인증서"),
     "LOCA 앱이 로그인/인증서를 요구한다 — 앱에서 로그인 후 재실행할 것."),
)


def _diagnose_loca() -> dict:
    """롯데앱(LOCA) 결제 실패의 **진짜 원인에 이름을 붙인다.**

    2026-08-30 실사고: `tap_dump_text '결제하기' 미발견 (timeout 8s)` 로 죽었는데, 실제 화면은
      「앱 재설정 안내 — 앱에 등록된 정보가 만료되어, 안전한 이용을 위해 다시 인증이 필요합니다」
    였다. 증상 이름('결제하기 미발견')만 보면 flow 좌표/타임아웃을 의심하게 된다 — 8/25 KB Pay
    (USB 디버깅 차단이 'KB앱 미진입'으로 나타남)와 **똑같은 함정**이다.
    → 실패한 그 자리에서 화면을 읽어 원인 코드와 해결법을 남긴다. 못 알아본 화면도
      **텍스트를 그대로 첨부**해서, 다음 사람이 화면을 다시 재현하지 않아도 되게 한다.
    """
    try:
        texts = [it["text"] for it in _dump_texts()]
    except Exception as e:
        return {"diag": f"화면 판독 실패({e})"}
    joined = " ".join(texts)
    for code, needles, howto in _LOCA_BLOCKERS:
        if all(n in joined for n in needles):
            print(f"   [LOCA] ⛔ {code} — {howto}", flush=True)
            return {"blocked": code, "howto": howto}
    print(f"   [LOCA] 화면 텍스트({len(texts)}개): {texts[:20]}", flush=True)
    return {"screen_texts": texts[:20]}


# ★카드앱 실행 차단 감지 — **3사 공용**(현대몰 식품·설화수 / 롯데몰 설화수 전부).
#   KB Pay(com.kbcard.cxh.appcard) 는 **USB 디버깅이 켜져 있으면 실행 즉시 스스로 종료**한다.
#   2026-08-25 실측(윈도우 첫 KB 결제): IntroActivity 가 떴다가 isExiting → 알림만 남는다
#     "KB Pay 앱을 종료합니다. / USB 디버깅 해제 후 다시 실행해주세요."
#   증상이 'KB앱 미진입'·'KB 결제 모달 미도달' 같은 **엉뚱한 이름**으로 나타나서 원인을 찾는 데
#   오래 걸렸다 → 결제 시작 전에 먼저 보고 **이름 붙여** 막는다.
#   해결: 폰 개발자옵션에서 **무선 디버깅**을 켜고 페어링한 뒤 **USB 디버깅을 끈다**
#     (adb pair <ip:port> <코드> → adb connect <ip:port>). 실측으로 KB Pay 정상 실행 확인.
#   ⚠️ 롯데카드(LOCA)·현대카드 앱은 USB 디버깅 상태에서도 정상 실행된다 = KB 전용 제약.
CARD_APPS_BLOCKED_BY_USB_DEBUG = ("KB",)


def preflight_card_app(card: str | None) -> tuple[bool, str]:
    """당일카드가 USB 디버깅을 싫어하는 카드면 결제 전에 막는다. 반환 (ok, msg)."""
    if not card or card not in CARD_APPS_BLOCKED_BY_USB_DEBUG:
        return True, ""
    try:
        v = subprocess.run(["adb", "shell", "settings", "get", "global", "adb_enabled"],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=10).stdout or ""
    except Exception as e:
        return True, f"adb_enabled 확인 실패({e}) — 그대로 진행"
    if v.strip() == "1":
        return False, ("USB 디버깅이 켜져 있어 KB Pay 가 실행되지 않는다(앱이 즉시 자기 종료). "
                       "폰 개발자옵션 → 무선 디버깅 켜고 페어링 → USB 디버깅 끄기 후 재실행할 것. "
                       "(adb pair <ip:port> <코드> → adb connect <ip:port>)")
    return True, ""


def _wait_app(pkg: str, timeout: float = 15) -> bool:
    """foreground 액티비티가 pkg 가 될 때까지 대기 (카드앱 진입/hmall 복귀 판정)."""
    end = time.time() + timeout
    while time.time() < end:
        out = subprocess.run(["adb", "shell", "dumpsys", "activity", "activities"],
                             capture_output=True, text=True, encoding="utf-8", errors="replace").stdout or ""
        if any("topResumedActivity" in ln and pkg in ln for ln in out.splitlines()):
            return True
        time.sleep(0.5)
    return False


# 주문완료(orderComplete) / 결제거절 OCR 마커 (주문서 단계엔 없는 토큰만 → 오탐 방지)
ORDER_DONE_MARKERS = ("주문이 완료", "주문번호", "재인증")          # orderComplete 페이지에만 등장
ORDER_FAIL_MARKERS = ("승인 요청 실패", "한도초과", "승인 실패", "결제 실패", "실패하였습니다")  # KCP/카드 거절


def scan_order_no(txt: str) -> str | None:
    """주문완료 화면 텍스트에서 주문번호. hmall 형식 = `YYYYMMDD` + 6자리 = 14자리(20260805138960).
    라벨이 안 잡히는 프레임도 있어 **번호 단독 패턴**도 같이 본다."""
    m = re.search(r"주문번호\s*[:：]?\s*(\d{10,})", txt) or re.search(r"\b(20\d{12})\b", txt)
    if m:
        return m.group(1)
    # OCR 이 `20260805 138960` 처럼 두 덩어리로 끊어 읽는 프레임 대비 — 공백 1개만 허용해 이어붙인다.
    # (전체 공백 제거는 금지: 관계없는 숫자들이 붙어 가짜 14자리가 생긴다.)
    m = re.search(r"\b(20\d{6})\s(\d{6})\b", txt)
    return m.group(1) + m.group(2) if m else None


def wait_order_complete(timeout: float = 20, order_grace: float = 3.0) -> dict:
    """결제 직후 hmall orderComplete 렌더를 폴링 (전 카드 공통). 세 목적:
      ① 주문완료 마커 확인 → beauty가 너무 일찍 돌아 '재인증' 못 찾는 타이밍버그 해결(KB #1 실측).
      ② 거절 마커 감지 → 'hmall 복귀=성공' 오보고 방지(BC 한도초과 CC61 실측).
      ③ **주문번호 판독** — 2026-08-06 신설.
    완료=ok:True(+order), 거절=ok:False+reason, 타임아웃=ok:False.

    ★③ 을 왜 여기서 하나: 종전엔 `pay.get("order")` 로 대장에 넣었는데 **현대/KB/BC/삼성 경로의
      `pay_*` 는 "order" 를 아예 안 채운다**(NH 만 nh_enter finish 에서 따로 읽었다). 그래서 8/5 KB 9건,
      8/6 현대 8건이 전부 `주문 -` 로 기록됐다. 주문완료 화면을 이미 OCR 하는 이 함수가 제자리다.
    ★`order_grace`: 마커가 뜬 뒤에도 번호가 **지연 렌더**될 수 있다(롯데 `_poll_order_complete` 가
      같은 이유로 번호 잡힐 때까지 재OCR — 2026-06-08/06-22 번호 None 사고). 다만 hmall 주문완료는
      곧 home 으로 자동이동하고 뷰티 재인증이 뒤에 있어 **무한정 기다리면 안 된다** → 짧게 3초만.
    """
    def _read() -> str:
        # ★OCR + dump **병합** — OCR 단독은 이 화면을 놓친다 (2026-08-30 #14 johwajeong 실사고:
        #   실제로는 결제 성공(마이페이지 '결제완료' 확인)인데 20s 타임아웃으로 ORDER_NOT_COMPLETE
        #   판정 → 구매대장·H.Point 적립·paid 플래그가 통째로 누락됐다. 롯데 쪽엔 8/25 에 dump
        #   폴백을 넣어놓고(5e7895f) **이 파일엔 같은 모양을 안 고쳤던 것** — READ_FIRST
        #   「버그 하나를 고치면 같은 모양을 폴더 전체에서 찾는다」 위반).
        return " ".join(it["text"] for it in (_ocr_texts(cap()) + _dump_texts()))

    end = time.time() + timeout
    while time.time() < end:
        txt = _read()
        for fm in ORDER_FAIL_MARKERS:
            if fm in txt:
                return {"ok": False, "reason": f"결제거절:{fm}"}
        dm = next((d for d in ORDER_DONE_MARKERS if d in txt), None)
        if dm:
            order = scan_order_no(txt)
            g_end = time.time() + order_grace
            while not order and time.time() < g_end:      # 번호만 늦게 뜨는 프레임 대비 (짧게)
                time.sleep(0.6)
                order = scan_order_no(_read())
            print(f"   [order] {order or '✗판독실패 — 대장에 주문번호 없이 기록된다'}", flush=True)
            return {"ok": True, "reason": dm, "order": order}
        time.sleep(0.8)
    return {"ok": False, "reason": "주문완료 미확인(timeout — 거절/지연 가능)"}


def pay_kb() -> dict:
    """KB국민카드 SDK (라이브검증 2026-05-31 #1, 주문 20260531079448). KB국민카드 선택된 주문서에서.
    원결제하기(OCR) → 'KB Pay 결제' 박스(OCR, 노란 앱카드) → KB앱(com.kbcard.cxh.appcard) 결제하기(dump)
    → 간편번호6(137601, content-desc dump; FLAG_SECURE라 화면캡처 검정이나 dump O; **6자리 자동제출**) → hmall 복귀 주문완료.
    ⚠️ '입력완료' 불필요(자동제출). ⚠️ 지체 금지 — 주문완료가 곧 home(initApp)으로 자동이동(뷰티포인트는 buy_one이 즉시 처리)."""
    out = {"step": "kb"}
    ok, msg = preflight_card_app("KB")          # ★USB 디버깅이면 KB Pay 가 안 뜬다 (3사 공용 정본)
    if not ok:
        out["err"] = f"KB_APP_BLOCKED: {msg}"; return out
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


def _toss_dump() -> str:
    """토스앱(viva.republica.toss) 화면 텍스트 — FLAG_SECURE라 screencap 검정, uiautomator dump는 됨."""
    try:
        return hw._dump(hw._serial())
    except Exception:
        return ""


def pay_toss(pin: str = CARD_PIN) -> dict:
    """토스페이(삼성 등 신용카드를 감싸는 간편결제) — 토스페이 선택된 주문서에서 호출.
    ★토스앱(viva.republica.toss) 로그인 전제. 미로그인이면 게스트 본인확인(휴대폰번호+SMS/PASS)이 떠
      자동화 불가 → 감지 즉시 안전정지(err, 카드인증 전이라 미결제 = 재시도 안전).
    경로(2026-07-15 라이브): (원)결제하기(OCR) → 토스 '결제진행' 화면 '다음'(OCR) → 토스앱 진입 →
      (로그인) 결제하기(OCR) → PIN 6자리(dump 셔플, FLAG_SECURE, 137601) → hmall 복귀.
    주문완료 검증/뷰티는 buy_one(wait_order_complete) 공통. TOSS_OBSERVE=1 이면 토스앱 진입 후 dump만 찍고 정지."""
    out = {"step": "order_page"}
    if not ocr_tap("결제하기", contains=True):                   # 1) 원 결제하기 (hmall WebView OCR)
        out["err"] = "원결제하기 실패"; return out
    if not wait_text("다음", timeout=15):                        # 2) 토스 '결제진행' 화면('다음' 버튼)
        out["err"] = "토스 결제진행(다음) 화면 미도달"; return out
    if not ocr_tap("다음", contains=True):
        out["err"] = "다음 탭 실패"; return out
    out["step"] = "toss_next"
    if not _wait_app("viva.republica.toss", timeout=20):        # 3) 토스앱 진입
        out["err"] = "토스앱 미진입"; return out
    time.sleep(3.5)                                              # 토스앱 로딩(결제확인/PIN 렌더)
    scr = _toss_dump()
    # 4) 게스트 본인확인 = 토스앱 미로그인 → SMS/PASS 필요(자동화 불가). 안전정지.
    if any(k in scr for k in ("본인\xa0확인", "본인 확인", "휴대폰\xa0번호", "휴대폰 번호", "CertifyGuest")):
        out["step"] = "toss_guest"
        out["err"] = "토스 게스트 본인확인 화면 — 토스앱 로그인 필요(자동화 불가)"; return out
    out["step"] = "toss_app"
    # ★관찰 모드: 로그인 후 첫 검증 시 토스 결제/PIN 화면 구조 확인용 — dump 출력 후 정지(미결제).
    if os.environ.get("TOSS_OBSERVE") == "1":
        print(f"[TOSS_OBSERVE] toss screen dump head:\n{scr[:1800]}", flush=True)
        out["err"] = "TOSS_OBSERVE stop (관찰 전용, 미결제)"; return out
    # 5) 토스 결제확인(OnlinePayActivity, screencap O=OCR) '결제하기'(기본선택 카드 그대로 = Amex) → PIN 화면
    if not ocr_tap("결제하기", contains=True, retries=4):
        out["err"] = "토스앱 결제하기 미발견"; return out
    out["step"] = "toss_pay_clicked"
    # 6) PIN 화면(PasswordActivity, FLAG_SECURE=screencap 검정) 대기 → text_dump 셔플 137601
    #    (토스 키패드 숫자는 text="N" 노드라 content-desc dump 아닌 text_dump)
    end = time.time() + 14
    while time.time() < end and "비밀번호" not in _toss_dump():
        time.sleep(0.8)
    try:
        FlowRunner(use_camera=False).run_action(
            {"action": "input_pin", "value": pin, "source": "text_dump"})
    except Exception as e:
        out["err"] = f"토스 PIN 입력 실패: {e}"; return out
    out["step"] = "pin_entered"
    # 6b) ★토스 승인완료(OnlinePayApproveCompleteActivity) '현대Hmall에서 결제를 완료해주세요' → '완료' 탭.
    #     이 완료를 안 누르면 카드는 승인됐는데 hmall 주문이 최종 생성 안 됨(2026-07-15 #2 실측: 카드
    #     94,005원 승인됐으나 주문 미생성 → 수동 완료 필요했음). 필수 단계.
    for _ in range(22):
        if _wait_app("com.hmallapp", timeout=1):                # 이미 hmall 자동복귀했으면 완료 불요
            break
        if "완료해주세요" in _toss_dump():                        # 승인완료 화면(OCR 가능=screencap O)
            if not ocr_tap("완료", pick="bottom", retries=2):    # 하단 '완료' 버튼(제목 '완료해주세요' 아님)
                _adb().tap(540, 2130)                           # OCR 실패 폴백(고정좌표 [476,2084][604,2177])
            time.sleep(2.5)
            break
        time.sleep(1.0)
    out["step"] = "toss_complete"
    if not _wait_app("com.hmallapp", timeout=20):               # 7) hmall 복귀 = 주문 최종생성
        out["err"] = "hmall 복귀 실패(PIN/완료 확인 필요)"; return out
    out["ok"] = True
    return out


def pay_bc() -> dict:
    """BC카드(페이북/KCP) SDK (라이브검증 2026-06-01 #3 주문 20260601059538, 490,000원 비씨카드 일시불 + 뷰티 적립완료)
    — **비씨카드 선택된 주문서에서 호출**. 주문완료까지. (롯데/하나와 동일 패턴)
    hmall-side(원결제하기)=OCR. 이후 KCP '다음'→페이북앱(kvp.jjy.MispAndroid320)=bc_paybook_isp.json **flow[6:]** 재사용.
    ⚠️⚠️ **페이북앱 기본 결제수단='페이북 머니'(현금/포인트)** — flow[10]에서 반드시 '카드 결제' 선택 + flow[12] verify_selected
       하드가드(페이북머니 selected면 FlowError로 결제중단)가 내장. **페이북머니로 결제 절대금지.**
    ⚠️ BC 결제비번 키패드 = 셔플, FLAG_SECURE 아님(screencap) → `input_pin kind=bc_pin6`(vote_digits 매핑). dump 아님.
    ✅ 라이브검증(2026-06-01): KCP '다음'=tap_dump_text·페이북앱 flow[6:] 그대로 통과. 단 카트가 BC 거래한도 이하여야(이전 CC61 한도초과 거절)."""
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


# ──────── 삼성 일반결제 공용 헬퍼 (카드앱 secrets + 셔플 키패드). 롯데(lotte_homeshopping_buy)도 import. ────────

CARD_SECRETS_FILE = ROOT / "secrets" / "card_secrets.json"   # ⚠️gitignored(다른 컴퓨터 수동복사)
_CARD_SECRETS_CACHE: dict | None = None


def _card_secrets() -> dict:
    """secrets/card_secrets.json 로드(1회 캐시). 삼성 일반결제 고정값(card_no/cvc/pin6/cert_pw6) 등.
    ⚠️ gitignored → 다른 컴퓨터엔 수동 복사 필요. 김건엽 명의 1장 전계정 공용."""
    global _CARD_SECRETS_CACHE
    if _CARD_SECRETS_CACHE is None:
        _CARD_SECRETS_CACHE = json.loads(CARD_SECRETS_FILE.read_text(encoding="utf-8"))
    return _CARD_SECRETS_CACHE


# ★★셔플 키패드 **로컬 OCR 자동입력은 삭제했다** (사용자 지시 2026-08-07 "로컬 OCR 빼, 앞으로 로컬 쓸 일 없다").
#   종전 `_tap_shuffle`(로컬 2엔진 voting → 파일 핸드셰이크 승격) + `KEYPAD_ROI_*` 3종이 여기 있었다.
#   왜 지웠나: 로컬 매핑이 자주 깨지는데(8/6 실측 10자리 중 5·1·2개) 실패하면 파일 핸드셰이크로
#   승격 → 백그라운드 실행에선 무조건 45초 타임아웃 → 그 프로세스의 나머지 계정까지 전멸했다.
#   **카드번호·CVC·비번·인증서비번은 전부 에이전트 비전 핸드세이크로만 입력한다**
#   (`samsung_enter` / `nh_enter` — 값은 스크립트가 secrets 에서 읽고 에이전트는 좌표만 준다).
#   ⚠️ 새 카드 경로를 만들 때도 로컬 OCR 자동입력을 다시 만들지 말 것.



# ─── 2026-08-02 라이브 확정 공통 헬퍼 (몰·카드 무관) ────────────────────────
# 롯데 #16·#3·#5·#15 4계정 연속 검증에서 얻은 3가지. 삼성/NH 등 '일반결제(카드번호 직접)'
# 경로는 몰이 달라도 SDK 화면이 동일하므로 여기(정본=hmall)에 두고 양 몰이 import 한다.

def card_digits_on_screen() -> int:
    """화면의 '카드 번호' 필드에 실제로 들어간 자릿수. 마스킹(*)도 1자리로 센다.
    ★2026-08-02: 탭이 씹혀 15자리 중 9자리만 들어갔는데 아무도 몰랐다(#5).
      화면에 자릿수가 보이므로 검증이 가능하다 → 넣고 반드시 확인한다."""
    import re as _re
    for it in _ocr_texts(cap()):
        t = it["text"].replace(" ", "")
        if _re.fullmatch(r"[\d*\-]{8,}", t) and ("-" in t or "*" in t):
            return len(t.replace("-", ""))
    return 0


def next_button_enabled(y_hint: int | None = None, label: str = "다음") -> bool:
    """진행 버튼('다음'/'결제') 활성 여부 — 색으로 판정(연한 파랑=비활성 / 진한 파랑=활성).
    ★2026-08-02 확정: 카드번호·CVC 가 정확히 들어가야 진해진다. 비활성인데 탭하면
      아무 일도 안 일어나고 다음 화면을 기다리다 타임아웃한다(PAY_FAIL@pin6 4건의 정체).
      → 비활성이면 '입력이 틀렸다'는 신호이므로 탭하지 말고 중단한다.
    ★★2026-08-07 근본수정 — **좌표 하드코딩 제거**: 종전 `y_hint=1377` 은 롯데 화면 기준이라
      **현대몰 삼성 일반결제(버튼 y≈1173)에선 항상 흰 배경을 찍어 무조건 '비활성'** 이 나왔다.
      입력이 완벽해도 그 자리에서 결제가 막힌다(8/6 삼성 9계정 `'다음' 비활성` 이 이것이었고,
      8/7 라이브에서 정확한 입력으로도 재현 확인). → **버튼을 OCR 로 찾아 그 자리 색을 본다.**
    ★label: 현대몰 PIN 화면의 진행 버튼은 '다음'이 아니라 **'결제'** 다(8/7 실측).
      `ocr_find` 는 **완전일치**라 '결제 비밀번호'·'일반결제 비밀번호' 같은 라벨엔 안 걸린다."""
    try:
        from PIL import Image
        btn = ocr_find(label)          # 완전일치 + pick=bottom (버튼은 화면 아래쪽)
        p = cap()
        im = Image.open(p).convert("RGB")
        w, _h = im.size
        y = y_hint or (btn["cy"] if btn else 1377)
        px = [im.getpixel((x, y)) for x in range(int(w * 0.35), int(w * 0.65), 10)]
        # 진한 파랑(활성) ≈ (30,120,240) / 연한 파랑(비활성) ≈ (150,200,250)
        return sum(1 for r, g, b in px if b > 180 and r < 110) > len(px) // 2
    except Exception:
        return True   # 판정 불가 시 막지 않음(기존 동작 유지)


def pay_samsung(pay_tap=None) -> dict:
    """삼성카드 **일반결제(카드번호 직접)** — ★3사 홈쇼핑 공용 정본 (몰 무관).

    2026-08-02 롯데 #16·#3·#5·#15 **4계정 연속 라이브 검증**된 시퀀스가 이 함수다.
    삼성 SDK 화면(다른결제→일반결제→카드15+CVC3→비번6→금융인증서>모니모>인증서비번6)은
    **가맹점 무관 동일**하므로 몰별로 복제하지 않는다. 몰이 다른 건 '원 결제하기' 탭 하나뿐 →
    `pay_tap` 콜러블로 주입한다(미지정 시 ocr_tap('결제하기')).

    ✅ **현대몰 라이브 검증 완료 2026-08-07** — #1 tkdkky2002, 주문 `20260807004446`,
       `198,400원 (삼성카드 일시불)`. 캐러셀에 삼성이 없어 그리드(`_pick_card_from_grid`)로 강제선택 →
       핸드세이크 → `samsung_enter` 로 완주. (종전 docstring 의 'hmall 라이브 미검증' 경고는 이걸로 해소.)

    ★★몰에 따라 **비번 이후가 다르다** (8/7 현대몰 실측):
      · 현대몰 : 카드15+CVC3 → '다음' → 일반결제 비번6 → **'결제'** → 곧바로 주문완료. **인증서 단계 없음.**
      · 롯데   : … 비번6 → '다음' → 금융인증서>모니모>김건엽>인증서비번6 → '인증 성공'.
      → `samsung_enter next` 가 화면의 '다음'/'결제' 를 알아서 고른다. 현대몰에서 cert 를 기다리지 말 것.

    ★★비번 화면 카드 발급사가 **'롯데' 로 표시되는 것은 정상**(사용자 확인 2026-08-07, 롯데홈쇼핑도 동일).
      실제 승인은 삼성카드로 잡힌다(주문완료 결제정보로 확인). 이걸 오결제로 오인해 중단하지 말 것.

    고정값=secrets/card_secrets.json['삼성'](김건엽 명의 1장 공용). 주문완료는 buy_one wait_order_complete 가 처리.
    ⚠️실 결제."""
    sec = _card_secrets().get("삼성", {})
    card_no, cvc, pin6, cert_pw6 = (sec.get("card_no"), sec.get("cvc"), sec.get("pin6"), sec.get("cert_pw6"))
    if not all([card_no, cvc, pin6, cert_pw6]):
        return {"step": "secrets", "err": "card_secrets['삼성'] 필드 부족(card_no/cvc/pin6/cert_pw6)"}
    out = {"step": "samsung_general"}
    # 1) 원 결제하기 → 삼성 SDK 모달 (몰별 차이는 여기뿐)
    if pay_tap is not None:
        if not pay_tap():
            out["err"] = "원결제하기 실패"; return out
    elif not ocr_tap("결제하기", contains=True):
        out["err"] = "원결제하기 실패"; return out
    time.sleep(3.0)
    # 2) SDK 모달 → '다른결제'(일반/SMS) 박스 (❌간편결제=PAYCO=ARS 위험)
    out["step"] = "samsung_modal"
    if not wait_text("간편결제", timeout=12) and not screen_has("다른"):
        out["err"] = "삼성 SDK 모달 미도달"; return out
    other = next((it for it in _ocr_texts(cap())
                  if "다른" in it["text"] and "결제" in it["text"] and "간편" not in it["text"]), None)
    if other:
        _adb().tap(other["cx"], other["cy"])
    elif not ocr_tap("다른", contains=True, retries=3):
        out["err"] = "'다른결제' 선택 실패"; return out
    time.sleep(2.5)
    # 3) '다른 결제' → '일반 결제(카드번호)' (❌SMS)
    out["step"] = "general_select"
    if not wait_text("일반", timeout=10):
        out["err"] = "일반/SMS 선택 미도달"; return out
    gn = next((it for it in _ocr_texts(cap())
               if "일반" in it["text"] and "결제" in it["text"] and "SMS" not in it["text"].upper()), None)
    if gn:
        _adb().tap(gn["cx"], gn["cy"])
    elif not ocr_tap("일반", contains=True, retries=3):
        out["err"] = "'일반 결제' 선택 실패"; return out
    time.sleep(2.5)
    # 4) 카드번호 + CVC → 다음
    out["step"] = "card_cvc"
    if not wait_text("카드번호", timeout=12):
        out["err"] = "카드번호 화면 미도달"; return out
    # ★★삼성 = **항상 에이전트 비전 핸드세이크** (사용자 지시 2026-08-06, 3사 공용 정본).
    #   근거: 셔플 키패드는 로컬 2엔진(vision+easyocr) 매핑이 자주 실패한다
    #        (8/6 실측: 10자리 중 5·1·2개만 매핑 → 9계정 연속 `'다음' 비활성`).
    #        로컬이 실패하면 `_ocr_claude` **파일 핸드셰이크**로 승격하는데, 이건 스크립트가
    #        45초 동안 내 응답을 기다리는 구조라 **스크립트를 백그라운드로 돌리면 무조건 타임아웃**이고
    #        (8/5 §11 에 NH 로 똑같이 겪고 적어놨다) 한 번 타임아웃하면 그 프로세스에선 클로드가 꺼져
    #        나머지 계정까지 전부 같은 자리에서 죽는다.
    #   ⚠️ 옛 게이트 `PAY_VISION_MODE=1` 은 **제거했다** — 플래그를 깜빡하면 조용히 옛 로컬 OCR 경로로
    #      떨어지는 게 8/6 실패의 직접 원인이었다(NH 도 8/5 에 같은 이유로 게이트를 없앴다).
    #      **기본값이 곧 핸드세이크다. 되살리지 말 것.**
    #   이어받기: `python3 -m phone_auto.samsung_enter` (카드번호 → CVC → 다음 → 비번6 → 인증서 → 인증서비번).
    #            값(카드번호/CVC/비번)은 그 스크립트가 secrets 에서 직접 읽고, 에이전트는 **키패드 좌표만** 준다.
    out["step"] = "card_screen_ready"
    out["manual"] = True
    print("[삼성] ★카드번호 화면 도달 — 에이전트 비전 인계 대기 "
          "(samsung_enter: card → cvc → next → pin6 → next → cert → certpw)", flush=True)
    return out


def samsung_cert_step() -> dict:
    """삼성 일반결제 **비번6 이후** 인증서 구간 — `samsung_enter cert` 가 호출한다.
    금융인증서 아래 '모니모 앱' → 김건엽 인증서 카드 → (인증서 비번은 호출측이 핸드세이크로 입력).
    ★`pay_samsung` 에서 잘라낸 코드 그대로 — 2026-08-02 롯데 4계정 라이브 검증된 시퀀스다."""
    out = {"step": "cert_select"}
    # 6) 금융인증서 아래 '모니모 앱' (⚠️공동인증서 모니모/설치하기 아님 — 헤더 아래 최근접)
    if not wait_text("인증서", timeout=12):
        out["err"] = "인증서 선택 미도달"; return out
    its = _ocr_texts(cap())
    fin = next((it for it in its if "금융인증서" in it["text"].replace(" ", "")), None)
    monimo = [it for it in its if "모니모" in it["text"] and "설치" not in it["text"]]
    cands = [m for m in monimo if fin and m["cy"] > fin["cy"]]
    tgt = min(cands, key=lambda m: m["cy"]) if cands else None
    if not tgt:
        out["err"] = "금융인증서 아래 '모니모 앱' 미발견"; return out
    _adb().tap(tgt["cx"], tgt["cy"]); time.sleep(3.0)
    # 7) 모니모 → 김건엽 금융인증서 카드 → 인증서비번(cert_pw6)
    out["step"] = "monimo_cert"
    if not wait_text("인증서", timeout=20):
        out["err"] = "모니모 금융인증서 미도달"; return out
    if not ocr_tap("김건엽", contains=True, retries=4):
        if not ocr_tap("국민은행", contains=True, retries=2):
            out["err"] = "인증서 카드 선택 실패"; return out
    time.sleep(2.0)
    if not wait_text("비밀번호", timeout=10):
        out["err"] = "인증서 비번 화면 미도달"; return out
    time.sleep(1.0)
    # ★인증서 비번도 셔플 키패드 → 여기서 멈추고 `samsung_enter certpw` 로 인계한다.
    out["step"] = "certpw_screen_ready"
    out["manual"] = True
    out["ok"] = True
    print("[삼성] 인증서 비번 화면 도달 — `samsung_enter certpw` 로 인계", flush=True)
    return out


def samsung_cert_done() -> dict:
    """인증서 비번 입력 후 '인증 성공' OK → 몰 복귀. (주문완료는 buy_one/finish 가 폴링)"""
    out = {"step": "cert_done"}
    if wait_text("성공", timeout=20):
        if not ocr_tap("OK", contains=True, retries=2):
            ocr_tap("확인", contains=True, retries=2)
    out["ok"] = True
    return out


# ★PAYCO 경유 결제경로는 **삭제했다** (사용자 지시 2026-08-07 '폐기 경로 정리').
#   구 `pay_samsung_payco`(삼성 PAYCO) · `pay_nh`(NH PAYCO) — 둘 다 **호출자 0개**였다.
#   왜 폐기됐나: PAYCO 는 금액이 크면 **ARS 전화인증**을 요구해 무인 실행이 불가능하다.
#   정본 = `pay_samsung` / `pay_nh_general` (둘 다 일반결제=카드번호 직접 + 비전 핸드세이크).
#   되살릴 일 있으면 git 이력(2026-08-07 이전)에서 꺼낼 것. 디스패처엔 이미 연결이 없다.
def _card_no_boxes() -> list[tuple[int, int]]:
    """카드번호 4칸 중심좌표(좌→우). resource-id 'cardno1~4' 우선(2026-06-25 NH 실측 anchor) —
    상단에 다른 EditText(stray)가 끼어도 정확. 없으면 EditText 를 y로 그룹핑해 '4칸 이상인 행' 폴백."""
    import xml.etree.ElementTree as ET
    p = "/tmp/_nh_cardno.xml"
    _adb().dump_ui(p)
    by_id: dict[int, tuple[int, int]] = {}
    edits: list[tuple[int, int]] = []
    try:
        root = ET.parse(p).getroot()
        for n in root.iter():
            if "EditText" not in n.attrib.get("class", ""):
                continue
            m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", n.attrib.get("bounds", ""))
            if not m:
                continue
            x1, y1, x2, y2 = map(int, m.groups())
            c = ((x1 + x2) // 2, (y1 + y2) // 2)
            edits.append(c)
            mm = re.search(r"cardno(\d)", n.attrib.get("resource-id", ""))
            if mm:
                by_id[int(mm.group(1))] = c
    except Exception:
        return []
    if len(by_id) >= 4:
        return [by_id[i] for i in sorted(by_id)[:4]]
    # 폴백: y 근접(±30) 그룹 중 4칸 이상인 가장 위 행 (stray box 배제)
    rows: list[list[tuple[int, int]]] = []
    for b in sorted(edits, key=lambda b: (b[1], b[0])):
        for row in rows:
            if abs(row[0][1] - b[1]) < 30:
                row.append(b); break
        else:
            rows.append([b])
    cand = [r for r in rows if len(r) >= 4]
    if not cand:
        return []
    row = sorted(cand, key=lambda r: r[0][1])[0]
    row.sort(key=lambda b: b[0])
    return row[:4]


def _edit_box_by_id(substr: str) -> tuple[int, int] | None:
    """resource-id 에 substr(소문자 비교) 포함된 EditText 중심좌표 (dump). 없으면 None."""
    import xml.etree.ElementTree as ET
    p = "/tmp/_nh_edit.xml"
    _adb().dump_ui(p)
    try:
        root = ET.parse(p).getroot()
        for n in root.iter():
            if "EditText" not in n.attrib.get("class", ""):
                continue
            if substr.lower() in n.attrib.get("resource-id", "").lower():
                m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", n.attrib.get("bounds", ""))
                if m:
                    x1, y1, x2, y2 = map(int, m.groups())
                    return ((x1 + x2) // 2, (y1 + y2) // 2)
    except Exception:
        pass
    return None


def _wait_keypad(timeout: float = 6) -> bool:
    """nppfs 보안 가상키패드(content-desc='키패드' 또는 resource-id 'keypad') 등장 대기.
    칸 탭 후 렌더에 ~2.5s 걸려, 폴링으로 정확히 등장 시점까지만 대기."""
    p = "/tmp/_nh_kpwait.xml"
    end = time.time() + timeout
    while time.time() < end:
        _adb().dump_ui(p)
        try:
            t = Path(p).read_text(encoding="utf-8", errors="ignore")
        except Exception:
            t = ""
        if 'content-desc="키패드"' in t or "keypad" in t:
            return True
        time.sleep(0.3)
    return False


# ★NH nppfs 키패드의 **로컬 OCR 판독(`_kp_read`)과 그걸 쓰던 `_tap_secure_each` 는 삭제했다**
#   (사용자 지시 2026-08-07 "로컬 OCR 빼"). 둘 다 이미 **호출자 0개**였다 —
#   NH 입력 정본은 `nh_enter`(에이전트가 배열을 판독해 좌표를 주는 핸드세이크)다.
#   로컬 엔진은 nppfs 키패드 정확도가 낮아(실측) 되살리면 8/6 삼성 사고가 그대로 재현된다.


# ───── nppfs 보안키패드 고정 그리드 (에이전트=클로드 비전 정본, 2026-06-26 라이브 확정) ─────
# 키패드 셀 위치는 고정, 숫자 라벨만 셔플. 6열 x(device px) 고정 / 2 숫자행 = 컨테이너 top + 오프셋.
# 같은 칸 4자리는 동안 셔플 안 됨 → 칸마다 1회만 읽으면 됨. 칸 바뀌면(또는 CVC/비번칸) 재셔플.
#
# ⚠️★**이 블록(`KP_ROW_OFF`/`_kp_top`/`_grid_tap`)은 지금 호출자가 없다. 그래도 지우지 않는다** —
#   폐기물이 아니라 **더 튼튼한 방식**이라서다(2026-08-07 검수). 살아 있는 NH 입력 경로
#   (`nh_enter` → `nh_vision_input.ROW_CARD/ROW_CVC/ROW_PIN6`)는 **절대 y 상수**를 쓰는데,
#   그건 키패드가 조금만 움직여도 엉뚱한 키를 누른다(자릿수는 맞아 검증도 통과 → 카드 잠김 위험).
#   여기 방식은 **컨테이너 top + 오프셋**이라 화면이 밀려도 따라간다.
#   → 다음 NH 결제 때 화면 보고 `nh_enter` 를 이쪽으로 옮기는 게 근본수정이다.
#   (그전까지의 임시 안전장치 = `nh_enter._rows_plausible` — 가정이 어긋나면 탭 안 하고 중단.)
KP_COLS = (173, 320, 467, 614, 761, 908)     # 6열 중심 x
KP_ROW_OFF = (217, 362)                       # 숫자 2행 y = 컨테이너 top + 이 오프셋 (실측: 카드top893→1110/1255, CVC1005→1222/1367, 비번963→1180/1325)


def _kp_top() -> int:
    """현재 nppfs 보안키패드 컨테이너 top y (dump). 미발견 -1."""
    p = "/tmp/_nh_kptop.xml"
    _adb().dump_ui(p)
    try:
        t = Path(p).read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return -1
    m = re.search(r'(?:nppfs-keypad[^"]*"|content-desc="키패드")[^>]*bounds="\[\d+,(\d+)\]', t)
    return int(m.group(1)) if m else -1


def _grid_tap(value: str, layout: list, top: int = -1, delay: float = 0.6) -> dict:
    """★에이전트(클로드)가 스샷에서 읽은 키패드 배열로 value 입력 — 고정그리드 셀좌표 탭.
    layout = [[행1 6칸], [행2 6칸]] 각 칸 = 숫자문자 또는 None(방패/공란). top 미지정 시 dump 자동.
    같은 칸은 셔플 안 되므로 한 배열로 4(또는 3/6)자리 연속 탭."""
    if top < 0:
        top = _kp_top()
    if top < 0:
        return {"err": "키패드 컨테이너 미발견"}
    rows = (top + KP_ROW_OFF[0], top + KP_ROW_OFF[1])
    pos = {}
    for r in range(2):
        for c in range(6):
            d = layout[r][c]
            if d not in (None, "", "shield", "🛡"):
                pos[str(d)] = (KP_COLS[c], rows[r])
    for ch in value:
        if ch not in pos:
            return {"err": f"'{ch}' 가 배열에 없음 — 재읽기 필요", "pos": pos}
        _adb().tap(*pos[ch]); time.sleep(delay)
    return {"ok": True, "digits": len(value), "top": top}


def pay_nh_general() -> dict:
    """NH농협카드 **일반결제(카드번호 직접 입력)** — 사용자 지정 경로(2026-06-25). PAYCO/모니모/금융인증서 없음.
    따옴표=실제 버튼 텍스트. NH카드 선택된 주문서에서 호출:
      1) '결제하기' → 2) 우측상단 '다른 결제' → 3) '일반결제'(카드번호+결제비밀번호)
      → 4) 카드번호 4칸(4자리씩) → 5) CVC → 6) '확인' → 7) '숫자 6자리' 결제비밀번호 팝업 → 6자리.
    고정값=secrets/card_secrets.json['NH'](card_no 16 / cvc 3 / pin6 6). 입력칸=dump EditText, 키패드=스크린샷 OCR(_tap_shuffle).
    ⚠️ 실 결제 · 첫 라이브 시 화면별 screencap 관찰 필수(4칸 dump 가독성 / '확인'·'숫자 6자리' 라벨 / 비번 뒤 종료 확정)."""
    sec = _card_secrets().get("NH", {})
    card_no, cvc, pin6 = sec.get("card_no"), sec.get("cvc"), sec.get("pin6")
    if not all([card_no, cvc, pin6]):
        return {"step": "secrets", "err": "card_secrets['NH'] 필드 부족(card_no/cvc/pin6)"}
    out = {"step": "nh_general"}
    # 1) 원 결제하기
    if not ocr_tap("결제하기", contains=True):
        out["err"] = "원결제하기 실패"; return out
    lap("NH일반: 원결제하기")
    time.sleep(3.0)
    # 2) 우측상단 '다른 결제'
    out["step"] = "other_pay"
    if not wait_text("다른 결제", timeout=12):
        out["err"] = "'다른 결제' 미도달"; return out
    if not ocr_tap("다른 결제", contains=True):
        out["err"] = "'다른 결제' 탭 실패"; return out
    time.sleep(2.0)
    # 3) '일반결제'(카드번호+결제비밀번호)  ❌간편/PAYCO/SMS
    out["step"] = "general_select"
    if not wait_text("일반결제", timeout=10):
        out["err"] = "'일반결제' 미도달"; return out
    gn = next((it for it in _ocr_texts(cap())
               if "일반결제" in it["text"].replace(" ", "") and "SMS" not in it["text"].upper()), None)
    if gn:
        _adb().tap(gn["cx"], gn["cy"])
    elif not ocr_tap("일반결제", contains=True, retries=3):
        out["err"] = "'일반결제' 선택 실패"; return out
    time.sleep(2.5)
    # 4) 카드번호 4칸(4자리씩) — 각 입력란 탭 → 그 4자리 키패드 입력
    out["step"] = "card_no"
    if not wait_text("카드번호", timeout=12):
        out["err"] = "카드번호 화면 미도달"; return out
    # ★★NH = **항상 에이전트 비전 핸드세이크** (사용자 지시 2026-08-05, 3사 공용 정본).
    #   근거: nppfs 셔플 키패드는 방패 아이콘이 숫자 자리에 섞여 **로컬 OCR 이 매핑에 실패**한다
    #        (_tap_shuffle 사다리 × 3ROI 전부 실패 — 2026-07-31 실측). 반면 에이전트가 전체화면을
    #        직접 읽으면 방패가 섞여도 정확히 판독된다 → **판독=에이전트 / 탭=nh_vision_input**.
    #   파일 핸드셰이크(_ocr_claude request.png/response.json)는 왕복이 분 단위라 결제 세션(~5분)에
    #   부적합해 폐기됐다(커밋 b2f1f46: "정지-인계(PAY_VISION_MODE)를 정본으로 채택").
    #
    #   ⚠️ 옛 로컬 OCR 자동입력 경로(_card_no_boxes + _tap_secure_each 로 카드번호/CVC/비번)는
    #      **제거했다 — 되살리지 말 것.** 환경변수 게이트(NH_VISION_MODE/PAY_VISION_MODE)도 제거:
    #      플래그를 깜빡하면 조용히 옛 경로로 떨어져 실패했다(2026-08-05 #10·#5 실패 원인).
    #      **기본값이 곧 핸드세이크다.**
    #
    #   이어받기: phone_auto/nh_vision_input.py — 칸마다 새 스크린샷 판독(칸 바뀌면 재셔플),
    #            KEY_COLS/ROW_CARD/ROW_CVC/ROW_PIN6 좌표, tap_digits(값은 secrets, 배열은 판독값).
    out["step"] = "card_screen_ready"
    out["manual"] = True
    print("[NH] ★카드번호 화면 도달 — 에이전트 비전 인계 대기 "
          "(카드번호 16 → CVC 3 → '확인' → 결제비번 6)", flush=True)
    return out


def _row_amt_of(it: dict, amt_rows: list[dict]) -> int | None:
    """카드명 아이템 `it` 이 속한 **카드 박스의 금액**. 없으면 None(=금액 없는 배너 행 → 제외).
    박스 규칙은 종전 same_row 판정 그대로: 같은 아이템 안 / 같은 행 ±40px / 바로 아래 220px·같은 열."""
    m = re.search(r"(\d{1,3}(?:,\d{3})+)", it["text"])
    if m:
        return int(m.group(1).replace(",", ""))
    same = [a for a in amt_rows if abs(a["cy"] - it["cy"]) < 40] or \
           [a for a in amt_rows if 0 < a["cy"] - it["cy"] < 220 and abs(a["cx"] - it["cx"]) < 350]
    for a in sorted(same, key=lambda a: abs(a["cy"] - it["cy"])):
        m = re.search(r"(\d{1,3}(?:,\d{3})+)", a["text"])
        if m:
            return int(m.group(1).replace(",", ""))
    return None


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
            # ★토스페이(삼성 등 카드 감싸는 간편결제): 카드명 행("토스페이 삼성")과 금액 행("267,189원")이
            #   cy 40px 넘게 떨어져 아래 same-row 매칭에서 누락됨(2026-07-15 실측 DETECT_CARD_FAIL).
            #   영역에 '토스' 있으면 우선 반환 — '삼성' substring보다 먼저(토스페이는 삼성카드 직접결제
            #   pay_samsung 와 경로가 완전히 다름 → 반드시 '토스'로 분기해야 함).
            if any("토스" in it["text"] for it in region):
                return "토스"
            # ⚠️ OCR이 '현대 5% 즉시할인'과 '255,968원'을 별도 item으로 쪼갤 수 있음 → 영역 전체에서
            #    카드사 토큰 검색(위=첫번째 당일카드). 금액 item엔 카드명 없음(#4 실측).
            # ⚠️ 단, 금액 없는 행은 제외 — '현대홈쇼핑 현대카드 Ed2 7% 청구할인 >' 이벤트 안내 행에서
            #    '현대' 오감지 → 당일카드가 롯데인데 현대로 진행하는 사고(#17 2026-06-05 오진 원인 후보).
            amt_rows = [it for it in region
                        if re.search(r"[\d,]{4,}\s*원", it["text"]) and "결제하기" not in it["text"]]
            cands: list[tuple[int, str]] = []          # (그 카드로 결제 시 금액, 카드키)
            for it in sorted(region, key=lambda x: x["cy"]):
                # ★2열 그리드 레이아웃(2026-07-30 실측): 카드명('KB국민' cy1711)과 금액('77,631원' cy1845)이
                #   같은 카드 박스 안에서 위/아래로 ~135px 떨어져 ±40px 동일행 매칭이 전부 실패 → DETECT_CARD_FAIL.
                #   → 카드명 **아래** 220px 이내 + 같은 열(cx 350px 이내) 금액도 같은 박스로 인정.
                #   ('현대카드 Ed2 7% 청구할인' 배너는 금액이 배너 **위**에 있어 이 조건에 안 걸림 = 오감지 방지 유지)
                box_amt = _row_amt_of(it, amt_rows)
                if box_amt is None:
                    continue
                for alias, key in CARD_ALIASES.items():   # 별칭 매핑(현대 외 변형표기 대비, 현대 오폴백 방지)
                    if alias in it["text"]:
                        cands.append((box_amt, key))
                        break
            if not cands:
                return None
            # ★★**가장 싼 카드**를 고른다 (사용자 지시 2026-08-06 "가장 할인율 높은 카드를 선택해야지").
            #   종전엔 `sorted(cy)` 의 첫 매칭 = **화면 맨 위** 카드였다. 그런데 캐러셀은 2열 그리드라
            #   좌/우 칸의 cy 차이가 6px(OCR 흔들림 수준)밖에 안 난다 → 어느 카드가 뽑히는지가 사실상 무작위.
            #   8/6 실측: #10 은 현대(97,052) / #11 은 삼성(92,069) 로 갈렸고, 같은 상품인데 계정마다
            #   다른 카드로 결제됐다. **싼 쪽을 놓치면 그만큼 손해**다.
            best = min(cands, key=lambda c: c[0])
            if len(cands) > 1:
                s = " / ".join(f"{k} {a:,}" for a, k in sorted(cands))
                print(f"   [detect_card] 후보 {len(cands)}개 — {s} → **최저 {best[1]} {best[0]:,}원** 선택",
                      flush=True)
            return best[1]
        _adb().swipe(540, 1700, 540, 800, 400); time.sleep(0.8)   # 카드할인 보이게 스크롤 다운
    return None


_PAY_BTN_RE = re.compile(r"([\d,]{4,})\s*원\s*결제하기")


def read_pay_amount() -> int | None:
    """주문서 하단 'N원 결제하기' 버튼에서 **결제 예정 금액**을 읽는다 (OCR + dump 병합).

    ★윈도우 OCR 단독은 이 버튼(검은 배경 흰 글씨)을 놓치거나 깨먹는다 → `_dump_texts()` 와
      겹쳐 읽는다 (READ_FIRST 「판독은 OCR + dump 를 겹쳐 쓴다」).
    ★OCR 이 '170,810원' 과 '결제하기' 를 **다른 item 으로 쪼개는** 경우가 있어, 한 덩어리 매칭이
      실패하면 '결제하기' 행과 같은 줄(±60px)의 금액을 폴백으로 쓴다.
    판독 실패는 None — 호출측이 '모르는 채 결제' 를 하지 않도록 그대로 드러낸다.
    """
    its = _ocr_texts(cap()) + _dump_texts()
    for it in its:
        m = _PAY_BTN_RE.search(it["text"].replace(" ", " "))
        if m:
            return int(m.group(1).replace(",", ""))
    btn = next((it for it in its if "결제하기" in it["text"]), None)
    if btn:
        for it in its:
            if abs(it["cy"] - btn["cy"]) > 60 or "결제하기" in it["text"]:
                continue
            m = re.search(r"(\d{1,3}(?:,\d{3})+)", it["text"])
            if m:
                return int(m.group(1).replace(",", ""))
    return None


def money_guard(idx: int, res: dict) -> bool:
    """★결제 직전 금액 가드 — 통과하면 True, 막았으면 False(res['status'] 세팅됨).

    2026-08-25 롯데에서 실제로 계정당 15만원을 막아낸 가드를 **현대몰에도 그대로** 옮긴 것
    (사용자 지시: "롯데몰에서 kb카드쓰는거지만 현대몰에서도 kb카드 쓰는날있을거임, 그럼 그 때도
    이지랄 안나게 동일하게 실수없이 잘 주문되게끔 코드수정잘해줘"). 혜택(즉시할인·쿠폰)이 조용히
    0원으로 통과해도 **사람이 로그를 보는 것에 의존하지 않고 코드가** 막는다.

    · `MAX_PAY=<원>` — 결제 예정 금액이 상한을 넘으면 결제하지 않는다.
    · 금액 **판독 실패**도 MAX_PAY 가 걸려 있으면 정지 — 모르는 금액을 결제하지 않는다.
    · `STOP_BEFORE_PAY=1` — 주문서까지만 만들고 결제 직전 정지(실돈 안 나감, 검증용).
    """
    amt = read_pay_amount()
    res["pay_amount"] = amt
    print(f"[#{idx}] 결제 예정 금액: {amt:,}원" if amt is not None
          else f"[#{idx}] ⚠️ 결제 예정 금액 판독 실패", flush=True)
    _max = os.environ.get("MAX_PAY")
    if _max:
        if amt is None:
            res["status"] = (f"AMOUNT_UNREADABLE(MAX_PAY={_max} — 금액을 못 읽으면 결제 안 함)")
            print(f"[#{idx}] ⛔ {res['status']}", flush=True)
            return False
        if amt > int(_max):
            res["status"] = f"AMOUNT_TOO_HIGH({amt} > MAX_PAY {_max}) — 혜택 미적용 의심, 결제 안 함"
            print(f"[#{idx}] ⛔ {res['status']}", flush=True)
            return False
    if os.environ.get("STOP_BEFORE_PAY") == "1":
        res["status"] = f"STOP_BEFORE_PAY(금액={amt})"
        print(f"[#{idx}] ⏹ STOP_BEFORE_PAY — 결제 직전 정지", flush=True)
        return False
    return True


def _select_toss_card() -> dict:
    """주문서 '카드할인'에서 '토스페이' 카드 선택. 700px 캐러셀 정본은 토스 레이아웃 미지원
    (2026-07-15 실측: '캐러셀 None' → 검증 실패) → 토스 카드 박스 직접 탭 + 결제버튼 금액==토스카드 금액
    검증(#6/#11 실측). 이미 선택돼 있으면 탭 안 함(토글 보호). 최대 4회(스크롤 포함)."""
    def _won(t):                                    # 천단위 콤마 숫자 (OCR이 '94,005'와 '원'을 분리하므로 원 불요)
        m = re.search(r"(\d{1,3}(?:,\d{3})+)", t)
        return int(m.group(1).replace(",", "")) if m else None

    def _read(its):
        toss = next((it for it in its if "토스페이" in it["text"] and it["cy"] < 2050), None)
        btn = next((it for it in its if "결제하기" in it["text"]), None)
        btn_amt = _won(btn["text"]) if btn else None
        toss_amt = None
        if toss:                                    # 토스 카드 박스 안(아래 240px, 같은 열)의 금액
            near = [_won(it["text"]) for it in its
                    if _won(it["text"]) and toss["cx"] - 280 < it["cx"] < toss["cx"] + 320
                    and toss["cy"] < it["cy"] < toss["cy"] + 240]
            toss_amt = next((a for a in near if a), None)
        toast = any("적용되" in it["text"] for it in its)   # '…즉시할인이 적용되었어요' 토스트
        return toss, btn_amt, toss_amt, toast

    toss = btn_amt = toss_amt = None
    for attempt in range(1, 5):
        toss, btn_amt, toss_amt, toast = _read(_ocr_texts(cap()))
        if not toss:                                # 카드할인 안 보이면 스크롤 다운
            _adb().swipe(540, 1700, 540, 900, 400); time.sleep(0.8); continue
        if btn_amt and toss_amt and btn_amt == toss_amt:   # 결제금액=토스카드금액 = 선택됨
            return {"ok": True, "amt": btn_amt, "via": "already" if attempt == 1 else "tap"}
        if toast and attempt > 1:                    # 탭 후 '적용되었어요' 토스트 = 선택 성공(금액 OCR 실패 대비)
            return {"ok": True, "amt": btn_amt, "via": "toast"}
        _adb().tap(toss["cx"], toss["cy"] + 90); time.sleep(1.8)   # 카드 박스 탭(텍스트 아래 = 카드 중앙)
    return {"ok": False, "err": f"토스 카드 선택/검증 실패 (btn={btn_amt} toss={toss_amt})"}


def select_card(card: str, day: str | None = None) -> dict:
    """결제수단을 card로 설정. **1순위 = 카드할인 캐러셀에서 카드명으로 지목**(할인 적용),
    캐러셀에 그 카드가 없으면 그리드(할인 없음). day = 호출측이 감지한 당일카드(로그용).

    ★2026-08-05 변경 (워크로그 §5-10 SELECT_CARD_FAIL 근본수정)
      예전엔 `day == card` 일 때만 캐러셀을 썼다. 그런데 **즉시할인 카드가 여러 장인 날**
      (NH·BC 가 둘 다 5%) `detect_card()` 는 맨 위 1장(BC)만 반환 → NH 는 `day != card` 로
      분류돼 그리드/드롭다운으로 우회 → 거기서 이중탭 버그로 실패했다.
      카드할인은 **주문(상품)별로 다르다** (데이즈온 주문서=NH / 석류젤리 주문서=KB — 사용자 실측).
      → 당일카드 추측에 기대지 말고 **캐러셀에서 카드명으로 직접 지목**한다."""
    if card == "토스":                    # 토스페이 = 전용 셀렉터(700px 캐러셀 미지원, 카드박스 직접 탭)
        return _select_toss_card()
    grid_name = CARD_GRID_NAME.get(card, card + "카드")
    # ① 캐러셀 카드명 타깃 — 700px 정본 가드 유지(캐러셀 '즉시할인' 행 금액 == 하단 결제버튼 금액.
    #    동일=선택됨(탭 X, 토글 보호) / 다름=탭→재확인 / 불일치 지속=raise=결제 차단).
    try:
        FlowRunner(use_camera=False).run_action(
            {"action": "hmall_select_card_discount", "card": card})
        # ★금액 일치만으론 '어느 카드가' 설정됐는지 모른다 (NH·BC 처럼 같은 5% 면 금액도 같다)
        #   → 결제수단 행 아래 카드명 **양성 검증** 필수. 실패면 진행 금지(딴 카드 결제 방지).
        if _verify_pay_method(grid_name):
            return {"ok": True, "via": "캐러셀"}
        return {"ok": False, "via": "캐러셀",
                "err": f"{card} 캐러셀 탭 후 결제수단에 '{grid_name}' 미확인 — 오결제 방지 중단"}
    except Exception as e:
        print(f"[card] 캐러셀에서 {card} 미확보({e}) → 그리드 경로(할인 없음)", flush=True)
    # ② 그리드 (이 주문서 캐러셀에 없는 카드 — 강제선택, 즉시할인 없음)
    ok = _pick_card_from_grid(grid_name)
    ok = ok and _verify_pay_method(grid_name)                   # 양성 검증 (그리드 탭≠설정 보장, #17 교훈)
    return {"ok": ok, "via": "grid", "err": None if ok else f"{card} 그리드 선택/검증 실패"}


def pay_hyundai(pin: str = CARD_PIN) -> dict:
    """현대카드 SDK — **현대카드 선택된 주문서에서 호출**. 결제하기 → PIN결제 → 안전팝업까지.
    (구매하기·카드선택은 buy_one/select_card. 본인인증/주문완료는 handle_after_pay)."""
    out = {"step": "order_page"}
    # 6) 결제하기(금액) → 현대 결제방식 (SDK 로딩)
    if not ocr_tap("결제하기", contains=True):
        out["err"] = "결제하기(금액) 실패"; return out
    # 결제수단 미설정: '카드종류를 선택해주세요' 팝업 vs 'PIN번호 결제'(현대) 판정
    end = time.time() + 15
    while time.time() < end:
        t = " ".join(x["text"] for x in _ocr_texts(cap()))
        if "카드종류" in t or "신용카드 선택" in t:
            # ⚠️ '미등록이라 등록 필요' 아님 — select_card 검증을 통과 못 한 채 결제 진입한 것.
            # 미등록 계정도 그리드 탭으로 등록 없이 결제 가능. (#17 2026-06-05 오진 메시지 수정)
            out["err"] = "결제수단 미설정(카드종류 선택 팝업) — select_card 단계 카드선택 실패"; return out
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
    # 9) PIN 6자리 입력 (OCR voting, 고정 키패드)
    FlowRunner(use_camera=False).run_action(
        {"action": "input_pin", "preset": "hyundai_hmall_pin6", "value": pin,
         "tap_delay_sec": 0.4, "use_camera": False})
    lap("PIN6 input_pin (vote+탭6) 완료")
    time.sleep(0.8)
    out["step"] = "pin_entered"
    # 확인 (PIN) → 결제확인 WebView 로딩
    # ★검은 버튼+흰 글씨라 Windows OCR 이 못 읽는다 → dump 폴백 필수 (2026-08-19, ocr_or_dump_tap 주석 참고)
    if not ocr_or_dump_tap("확인"):
        out["err"] = "PIN 확인 실패"; return out
    if not wait_text("결제합니다", timeout=15) and not wait_text("결제하기", timeout=3):
        out["err"] = "결제확인 화면 미도달"; return out
    out["step"] = "pay_confirm"
    # 10) 최종 결제하기 → 안전결제 팝업
    if not ocr_tap("결제하기", contains=True):
        out["err"] = "최종 결제하기 실패"; return out
    out["step"] = "paid_clicked"
    return out


def pay_hyundai_general() -> dict:
    """현대카드 **일반 결제** 경로 — 현대카드 결제의 **유일한 진입점** (사용자 지시 2026-08-19).
    실측(probe): 카드선택 직후 주문서에 결제수단 영역 노출 — '일반 결제' / '앱카드 결제' 탭 쌍,
    앱카드 기본선택. 탭은 좌표 아닌 OCR로(레이아웃 드리프트 대비 — 2026-07-10 y593, 08-19 y1576).
    탭이 **없으면** 앱카드 미등록 = 이미 일반결제 → `pay_hyundai()`(PIN) 로 위임한다.

    라이브 검증: 2026-07-13 skykow(#17) 완주 `✓ 결제 완료`.
    ⚠️ 후속 화면은 아는 마커(PIN번호 결제/본인인증/주문완료)만 진행, 모르는 화면이면 OCR 덤프 출력 후
    안전 정지(err). 카드 인증 전 정지는 미결제라 재시도 안전."""
    out = {"step": "general_tab"}
    # 1) 결제수단 영역 '일반 결제' 탭 — 카드할인(700px) 아래에 있음 → 아래로 스크롤하며 탐색
    #    (2026-07-10 1차 시도: 위로만 폴백해 미발견. 주문서 순서 = 카드할인 → 결제수단 → 총결제)
    found = ocr_tap("일반 결제", contains=True, retries=2)
    if not found:
        adb = _adb()
        for _ in range(5):
            adb.swipe(540, 1800, 540, 900, 400); time.sleep(1.2)   # 아래로 스크롤
            if ocr_tap("일반 결제", contains=True, retries=1):
                found = True; break
    if not found:
        # ★탭이 없다 = 앱카드 **미등록** 계정 = 결제수단이 이미 일반결제다 → 에러가 아니라 기존 PIN 경로.
        #   (사용자 지시 "무조건 일반결제" 를 전 계정에 적용하면서, 비등록 계정이 여기서
        #    헛되게 PAY_FAIL 나던 것을 막는다. 2026-08-19)
        print("   [general] '일반 결제' 탭 없음 = 앱카드 미등록 → 이미 일반결제, PIN 경로로 진행", flush=True)
        r = pay_hyundai()
        r["via"] = "pay_hyundai(앱카드 탭 없음)"
        return r
    time.sleep(1.5)
    out["step"] = "order_page"
    # 2) 결제하기 (금액 버튼)
    if not ocr_tap("결제하기", contains=True):
        out["err"] = "결제하기(금액) 실패"; return out
    # 3) 후속 화면 판별 — 아는 마커만 진행, 모르면 덤프+정지.
    #    일반결제 선택 직후 재렌더로 첫 결제하기 탭이 미반영될 수 있음(2026-07-10 실측) → 주문서 그대로면 1회 재탭.
    end = time.time() + 30
    txt = ""
    retapped = False
    while time.time() < end:
        txt = " ".join(x["text"] for x in _ocr_texts(cap()))
        if any(k in txt for k in ("PIN번호 결제", "카드번호", "비밀번호", "안심", "생년월일", "본인 인증")) \
           or ("주문" in txt and "완료" in txt):
            break
        if not retapped and "결제수단" in txt and "결제하기" in txt:
            print("   [general] 주문서 그대로 — 결제하기 1회 재탭", flush=True)
            ocr_tap("결제하기", contains=True)
            retapped = True
        time.sleep(0.5)
    if "주문" in txt and "완료" in txt:
        out["step"] = "paid_clicked"; return out            # 추가인증 없이 바로 완료된 경우
    if "PIN번호 결제" in txt:
        # 일반결제인데 SDK PIN 화면이 뜨는 변종 — 기존 pay_hyundai 후속(PIN 6자리)과 동일 처리
        out["step"] = "pay_method"
        if not ocr_tap("PIN번호 결제", contains=True):
            out["err"] = "PIN번호 결제 선택 실패"; return out
        if not wait_text("PIN번호를 입력", timeout=15):
            out["err"] = "PIN 화면 미도달"; return out
        _adb().tap(*PIN_DOT); time.sleep(1.3)
        FlowRunner(use_camera=False).run_action(
            {"action": "input_pin", "preset": "hyundai_hmall_pin6", "value": CARD_PIN,
             "tap_delay_sec": 0.4, "use_camera": False})
        time.sleep(0.8)
        out["step"] = "pin_entered"
        # ★검은 버튼+흰 글씨 = Windows OCR 미판독 → dump 폴백 (2026-08-19 실측, #1 이 여기서 멈췄다)
        if not ocr_or_dump_tap("확인"):
            out["err"] = "PIN 확인 실패"; return out
        if not wait_text("결제합니다", timeout=15) and not wait_text("결제하기", timeout=3):
            out["err"] = "결제확인 화면 미도달"; return out
        if not ocr_tap("결제하기", contains=True):
            out["err"] = "최종 결제하기 실패"; return out
        out["step"] = "paid_clicked"; return out
    # 모르는 화면 — 덤프 출력 후 안전 정지 (여기서 flow 확장할 것)
    print(f"   [general] 미지원 후속 화면 — OCR 덤프:\n{txt[:600]}", flush=True)
    out["step"] = "post_pay_click"
    out["err"] = "일반결제 후속 화면 미지원(덤프 확인 후 flow 확장 필요)"
    return out


def _dismiss_extra_auth_popup() -> bool:
    """'안전한 결제를 위해 추가 인증을 진행합니다.' 팝업이 떠 있으면 '확인' 을 눌러 닫는다. True=닫음.

    ★왜 **여러 번** 확인해야 하나 (2026-08-19 #3 실사고):
      종전엔 `handle_after_pay` **맨 앞에서 한 번만** 검사했다. 그런데 이 팝업은 본인인증 화면이
      뜬 **뒤에** 늦게 올라올 수 있다 → 그 시점엔 없다가, 나중에 떠서 **카드비번 키패드를 덮는다.**
      덮인 채로 키패드를 판독하면 뒤 배경 글자가 잡혀 엉뚱한 매핑이 나온다
      (실측 #3: need ['1','3','6','7'] 인데 got ['1','4','8'] → 로컬 2엔진+클로드 3회 전부 실패
       → `AFTER_PAY_CARDPW_MANUAL` 로 결제 미완).
      모듈 docstring 이 이미 경고하던 함정이다 — "팝업 '확인' 먼저 누르고(키패드를 덮음!)".
      한 번만 보는 코드가 그 경고를 지키지 못했다.
    ★판독은 OCR+dump 병합(`_dump_texts`) — 딤 처리된 화면에서 OCR 이 흘리는 경우가 있다.
    """
    txt = " ".join(it["text"] for it in _ocr_texts(cap()) + _dump_texts())
    if ("안전한 결제" not in txt) and ("추가 인증" not in txt):
        return False
    print("   [after_pay] '추가 인증' 팝업 감지 → 확인 (키패드 가림 제거)", flush=True)
    ok = ocr_or_dump_tap("확인", retries=2)
    time.sleep(1.0)
    return ok


def handle_after_pay(timeout: float = 30) -> str:
    """결제하기 후: 안전결제 팝업 확인 → 본인인증 자동입력 → 주문완료 판정.
    반환: 'ORDER_COMPLETE' | 'IDENTITY_FAIL' | 'AFTER_AUTH_UNKNOWN' | 'UNKNOWN'."""
    _dismiss_extra_auth_popup()
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
                    # ★키패드를 덮는 '추가 인증' 팝업을 **여기서 다시** 확인 (2026-08-19 #3 실사고).
                    #   맨 앞 1회 검사만으론 늦게 뜨는 팝업을 놓쳐 키패드 판독이 배경을 읽는다.
                    _dismiss_extra_auth_popup()
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

def buy_one(idx: int, card: str | None = None, combo_idx: int | None = None,
            only: list[str] | None = None) -> dict:
    serial = hw._serial()
    res = {"idx": idx, "status": None, "combo_idx": combo_idx}
    print(f"\n{'='*54}\n[#{idx}] 앱 콜드런치 → 로그인...", flush=True)
    ws = wake_screen()                      # ★절전/잠금 preflight (2026-07-10 검은화면 사고 재발방지)
    if not ws["ok"]:
        res["status"] = f"SCREEN_LOCKED(awake={ws['awake']},keyguard={ws['keyguard']}) — 폰 잠금해제 필요"
        return res

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
    # 선택 — only 지정이면 해당 상품만(혼합 카트 카드별 분리주문), 아니면 전체선택
    if only:
        ok, sel = cdp_select_only(only)
        print(f"[#{idx}] 부분선택[{','.join(only)}] {sel} ok={ok}", flush=True)
        if not ok:
            res["status"] = f"SELECT_ONLY_FAIL:{sel}"; return res
    else:
        ok, sel = cdp_select_all()
        print(f"[#{idx}] 전체선택 {sel} ok={ok}", flush=True)
        if not ok:
            res["status"] = f"SELECT_ALL_FAIL:{sel}"; return res
    lap("카트진입 + 선택")
    # 구매하기 → 주문서 (공통, 카드무관)
    if not ocr_tap("구매하기"):
        res["status"] = "BUY_FAIL(구매하기)"; return res
    if not wait_text("결제하기", timeout=15):
        res["status"] = "ORDER_PAGE_FAIL(주문서 미도달)"; return res
    # ★H.Point 전액사용 (700px 방식, <100p skip — flow_runner 정본 재사용, 식품 flow step5와 동일.
    #   카드선택보다 먼저. 2026-06-05 결정사항인데 설화수 경로에 누락돼 있던 것 2026-06-06 연결)
    # HMALL_NO_POINTS=1 이면 skip — 포인트 차감으로 결제금액이 적립 tier(예: 15만원 구간) 아래로
    #   내려가 이벤트 적립을 놓치는 것 방지 (사용자 지시 2026-07-13).
    if os.environ.get("HMALL_NO_POINTS") == "1":
        print(f"[#{idx}] H.Point 사용 skip (HMALL_NO_POINTS=1 — 적립구간 금액 유지)", flush=True)
    else:
        try:
            FlowRunner(use_camera=False).run_action({"action": "hmall_use_all_points"})
        except Exception as e:
            print(f"[#{idx}] ⚠️ 포인트 전액사용 실패(계속 진행): {e}", flush=True)
    lap("H.Point 전액사용 (700px)")
    # 카드 결정 + 선택 (공통) — card 미지정 시 카드할인 캐러셀 당일카드 자동감지(스크롤 포함)
    day_card = detect_card()
    use_card = card or day_card
    if not use_card:
        # ⚠️ '현대' 기본 폴백 제거(2026-06-06): 감지 실패에 카드 추측 = 오결제/오SDK 위험.
        #   감지 실패는 정지가 정답 (감지=SDK 디스패치 결정이라 틀리면 카드앱 흐름 전체가 어긋남).
        res["status"] = "DETECT_CARD_FAIL(당일카드 미감지 — 추측 진행 금지)"; return res
    res["card"] = use_card
    print(f"[#{idx}] 당일카드 감지={day_card}, 사용={use_card}", flush=True)
    if use_card not in CARDS_SUPPORTED:
        res["status"] = f"UNSUPPORTED_CARD:{use_card}(SDK 미구현)"; return res
    sc = select_card(use_card, day_card)
    if not sc.get("ok"):
        res["status"] = f"SELECT_CARD_FAIL:{use_card}:{sc.get('err')}"; return res
    lap(f"카드 선택 ({use_card})")
    # ★PIN 직전 금액 확인 + 상한 가드 (READ_FIRST 「실결제는 PIN 직전 금액 확인」).
    #   여기가 마지막 되돌릴 수 있는 지점이다 — 이 아래는 카드앱이라 실돈이 나간다.
    if not money_guard(idx, res):
        return res
    # 카드별 SDK ⚠️실돈
    print(f"[#{idx}] ⚠️ {use_card}카드 결제 실행", flush=True)
    if use_card == "현대":
        # ★현대카드 = **무조건 일반 결제** (사용자 지시 2026-08-19: "앱카드결제하면안되잖아 무조건 일반결제야").
        #   앱카드는 누적금액 임계를 넘으면 '현대카드 인증이 필요합니다' 모달로 자동화가 막힌다.
        #   임계가 계정별 누적이라 계정 선별(GENERAL_PAY_IDS)·런타임 감지 둘 다 "언젠간 막히는" 구조였다.
        #   → 분기 제거. 탭이 없는 계정(앱카드 미등록)은 pay_hyundai_general 안에서 기존 PIN 경로로 이어간다.
        print(f"[#{idx}] ★{res['id']} 현대카드 = 무조건 일반결제 → pay_hyundai_general", flush=True)
        pay = pay_hyundai_general()
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
    elif use_card == "삼성":
        sp = pay_samsung()
        res["pay"] = sp
        # ★삼성도 NH 와 동일 — 카드번호 화면에서 비전 인계로 정지한다 = 실패 아님(정상 대기).
        #   여기서 return 해야 다음 계정 콜드런치가 살아있는 결제화면을 날리지 않는다.
        if sp.get("manual"):
            # ★접두어는 **카드명 그대로**(`삼성_HANDOFF`) — 요약이 이 토큰으로 러너를 고른다.
            res["status"] = f"{use_card}_HANDOFF(카드번호 화면 — 에이전트 비전 입력 대기)"; return res
        if not sp.get("ok"):
            res["status"] = f"SAMSUNG_FAIL@{sp.get('step')}:{sp.get('err')}"; return res
    elif use_card == "NH":
        np_ = pay_nh_general()          # 일반결제(카드번호 직접) — 사용자 지정 2026-06-25. (PAYCO 경로는 2026-08-07 삭제)
        res["pay"] = np_
        # ★NH 는 카드번호 화면에서 에이전트 비전 인계로 정지한다 = 실패 아님(정상 대기 상태).
        #   여기서 return 해야 다음 계정이 콜드런치로 이 화면을 날려버리지 않는다.
        if np_.get("manual"):
            res["status"] = "NH_HANDOFF(카드번호 화면 — 에이전트 비전 입력 대기)"; return res
        if not np_.get("ok"):
            res["status"] = f"NH_FAIL@{np_.get('step')}:{np_.get('err')}"; return res
    elif use_card == "토스":
        tp = pay_toss()                 # 토스페이(간편결제) — 토스앱 로그인 전제 PIN. 게스트 본인확인 뜨면 안전정지.
        res["pay"] = tp
        if not tp.get("ok"):
            res["status"] = f"TOSS_FAIL@{tp.get('step')}:{tp.get('err')}"; return res
    lap(f"{use_card} 결제 → 주문완료")
    # 주문완료 확인 (공통, 전 카드): orderComplete 렌더 대기 = ① beauty 타이밍 확보(KB) ② 거절 감지(BC 한도초과)
    oc = wait_order_complete(timeout=20)
    res["order_complete"] = oc
    if not oc.get("ok"):
        res["status"] = f"ORDER_NOT_COMPLETE:{use_card}:{oc.get('reason')}"; return res
    # ★계정 간 7분 간격의 **기산점**. 뒤따르는 뷰티·대장·적립은 이 대기시간 안에서 소비된다
    #   (사용자 지시 2026-08-06: "결제 완료후 7분, 그 쉬는 시간 사이에 해당 계정 적립").
    res["paid_at"] = time.time()
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
        _ledger_append(idx, res.get("id"), active)   # 누적 추적 (세션 릴레이)
    lap(f"뷰티포인트 재인증 → ★계정 #{idx} 총소요")
    res["status"] = "DONE" + ("" if bp.get("ok") else f"(beauty_fail:{bp.get('err')})")
    _record_after_done(res, idx, combo_idx, only)
    return res


def _record_after_done(res: dict, idx: int, combo_idx: int | None = None,
                       only: list[str] | None = None) -> dict:
    """주문완료 후 **뒷기록 정본** — 구매대장 + H.Point 적립 + 매니페스트 paid 플래그.

    ★`buy_one` 과 `resume` **양쪽에서** 부른다 (2026-08-19 신설).
      종전엔 이 블록이 buy_one 안에만 있어서, `resume` 으로 완주하면
      **구매대장 0건 + 적립 0건 + paid 미기록** 이 조용히 발생했다 (실측 #3: 사람이 손으로 메꿨다).
      사용자 지시로 resume 을 상시 경로로 쓰게 됐으므로(2026-08-19 "다른부분에서도 resume기능써"),
      한쪽에만 있는 기록은 곧 누락이 된다. READ_FIRST 「출력물은 두 군데 — 양쪽 다 검증」.
    ★paid 플래그: `buy.py` 는 자기 경로에서만 기록하므로 resume 완주분은 미결제로 남아
      **다음 실행이 같은 계정을 또 결제하려 한다**(카트가 비어 SKIP_EMPTY 로 막히긴 하나 헛돈다).
    """
    # 구매대장 기록 (JSON + 시트). 실패해도 결제엔 영향 없음.
    # combo=NN 지정(설화수) → 조합가 기록 / 미지정(식품) → cart/today_carts.json 상품·수량으로 기록
    try:
        import purchase_ledger as PL
        # ★출처 3단: pay(NH 등 자체 판독) → wait_order_complete(신설, 전 카드 공통) → 지금 화면 재판독.
        #   마지막 폴백이 있는 이유: 뷰티 재인증/팝업을 닫는 사이 번호가 뒤늦게 렌더되는 프레임이 있다.
        order_no = ((res.get("pay") or {}).get("order")
                    or (res.get("order_complete") or {}).get("order")
                    or scan_order_no(" ".join(it["text"] for it in _ocr_texts(cap()))))
        if not order_no:
            print("   [order] ⚠️ 주문번호 미판독 — 대장에 빈칸으로 들어간다(결제는 정상). "
                  "필요하면 Hmall 주문내역에서 확인할 것", flush=True)
        if combo_idx is not None:
            PL.record_combo("현대Hmall", res.get("id"), combo_idx,
                            order_no=order_no, card=res.get("card"))
        else:
            mf_path = Path(__file__).resolve().parent.parent / "cart" / "today_carts.json"
            mf = json.loads(mf_path.read_text(encoding="utf-8"))
            cart = next((c for c in mf.get("carts", [])
                         if c.get("mall") in ("현대", "hmall") and c.get("account") == idx), None)
            items = [it for it in (cart or {}).get("items", [])
                     # ★부분선택 주문이면 **이번에 결제한 상품만** 기록 (안 그러면 나머지 상품까지
                     #   중복/선결제로 대장에 올라간다). 키워드는 카트 선택과 동일한 것을 쓴다.
                     if not (only and not any(k in (it.get("name") or "") for k in only))]
            # ★금액은 **결제 직전 실측치**(money_guard 가 읽은 '원 결제하기' 버튼)를 우선한다.
            #   종전엔 무조건 `cart/today.json` 의 즉시할인가를 썼는데, 그건 **포인트 사용 전** 금액이라
            #   H.Point 를 쓰면 실제 청구액과 어긋난다 (2026-08-30 실측: #16 대장 144,229 vs 실청구
            #   120,298 — 계정마다 포인트 잔액이 달라 오차도 제각각이었다). 대장의 '최종결제금액'은
            #   카드에 실제로 청구된 값이어야 한다. 단일 상품 주문일 때만 안전하게 적용하고,
            #   혼합 카트(2건 이상)는 상품별로 쪼갤 수 없으니 종전대로 today.json 기준을 쓴다.
            amt = res.get("pay_amount") if len(items) == 1 else None
            for it in items:
                if amt is not None:
                    name, _, t_qty = PL.food_info(it.get("product"))
                    PL.record("현대Hmall", res.get("id"), name or f"식품#{it.get('product')}",
                              it.get("qty") or t_qty or 1, amt,
                              order_no=order_no, card=res.get("card"))
                else:
                    PL.record_food("현대Hmall", res.get("id"), it.get("product"), qty=it.get("qty"),
                                   order_no=order_no, card=res.get("card"))
    except Exception as e:
        print(f"   [ledger] 기록 실패(무시): {e}", flush=True)
    res["reward"] = apply_reward_now(idx, only)   # ★H.Point 적립신청 (결제 직후 자동) — 결과를 요약까지 끌고 간다
    # 매니페스트 paid 플래그 — buy.py 를 안 거치는 resume 경로에서도 중복결제 방지가 되게.
    try:
        mf_path = Path(__file__).resolve().parent.parent / "cart" / "today_carts.json"
        mf = json.loads(mf_path.read_text(encoding="utf-8"))
        cart = next((c for c in mf.get("carts", [])
                     if c.get("mall") in ("현대", "hmall") and c.get("account") == idx), None)
        if cart is not None and not cart.get("paid"):
            cart["paid"] = True
            mf_path.write_text(json.dumps(mf, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(f"   [manifest] #{idx} paid=True 기록", flush=True)
    except Exception as e:
        print(f"   [manifest] paid 기록 실패(무시): {e}", flush=True)
    return res


def apply_reward_now(idx: int, only: list[str] | None = None) -> dict:
    """★결제 직후 H.Point 적립신청 — **코드가 자동으로 한다. 사람이 기억할 일이 아니다.**

    2026-08-05 사고: 폰 결제(이 모듈)는 `buy.py` 를 안 거치는데 적립은 `buy.py apply_reward` 에만
    있었다 → **12계정을 결제하는 동안 적립이 한 건도 안 걸렸고, 에러도 안 났다.** 사용자가
    지적해서야 발견했다(전 계정 사후 신청으로 복구). 같은 실수를 막으려고 여기서 직접 호출한다.

    · prmo 는 `cart/today.json` events 가 정본 — 한 상품에 이벤트가 2개일 수 있다
      (데이즈온 = 건강식품 특별전 + 데이즈온 10% = 2군데).
    · only 지정(분리주문)이면 **이번에 결제한 상품만** 적립 대상.
    · best-effort — 실패해도 결제 결과엔 영향 없음. 단 **반드시 로그로 드러낸다**(조용한 누락 금지).
    """
    if os.environ.get("HMALL_NO_REWARD") == "1":
        print(f"[#{idx}] [적립] HMALL_NO_REWARD=1 — skip", flush=True)
        return {"ok": False, "skip": "HMALL_NO_REWARD=1"}
    cmd = [sys.executable, str(ROOT / "buy.py"), "reward", str(idx)] + list(only or [])
    try:
        r = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
    except Exception as e:
        print(f"[#{idx}] [적립] ⚠️ 호출 실패(결제는 정상): {e} — "
              f"수동: python3 buy.py reward {idx}", flush=True)
        return {"ok": False, "err": str(e)}
    # ★[적립] 줄을 **전부** 남긴다. 종전엔 마지막 한 줄(tail[-1])만 찍어서 prmo 2건 중 1건 결과가
    #   통째로 사라졌다 — 그래서 '적립을 안 했다'와 '했는데 안 보인다'를 구분할 수 없었다(2026-08-06).
    lines = [ln for ln in (r.stdout or "").splitlines() if "[적립]" in ln]
    for ln in lines:
        print(f"[#{idx}] {ln.strip()}", flush=True)
    res = next((ln for ln in lines if "RESULT" in ln), "")
    ok = "ok=True" in res or "적립단계 skip" in " ".join(lines)
    if not ok:
        print(f"[#{idx}] [적립] ⚠️ 미완 — 재실행: python3 buy.py reward {idx}"
              f"{' ' + ' '.join(only) if only else ''}", flush=True)
    return {"ok": ok, "detail": res.strip() or (lines[-1].strip() if lines else "no-output"),
            "rc": r.returncode}


def _do_beauty(res: dict) -> None:
    close_home_popup()   # 주문완료 화면 광고 팝업이 재인증 버튼 가림 → 닫기
    prof_cfg = json.loads(BP_PATH.read_text(encoding="utf-8"))
    active = prof_cfg.get("active_profile")
    profile = prof_cfg.get("profiles", {}).get(active, {})
    bp = beauty_reauth(profile)
    res["beauty"] = bp
    if bp.get("ok"):
        time.sleep(1.0)
        if screen_has("완료"):
            ocr_tap("확인", post=2.0, retries=2)
        _ledger_append(res.get("idx"), res.get("id"), active)   # 누적 추적 (세션 릴레이)
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
            # ★뒷기록을 buy_one 과 **동일하게** 남긴다 (2026-08-19 — 없으면 대장·적립·paid 조용한 누락).
            if idx is not None:
                if not res.get("id"):
                    try:
                        res["id"] = hw.load_accounts()[int(idx) - 1]["id"]
                    except Exception:
                        pass
                res.setdefault("card", "현대")
                _record_after_done(res, int(idx))
            else:
                print("   [ledger] ⚠️ resume 에 계정번호가 없어 대장·적립·paid 를 못 남긴다 — "
                      "`resume <계정번호>` 로 다시 호출할 것", flush=True)
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
            # ★주문서 재개도 정본 가드 통과 후 결제 (포인트→카드 금액일치, 2026-06-06).
            #   둘 다 멱등: 포인트 이미 사용/없음=skip, 카드 금액일치=skip — 재개 시 이중적용 없음.
            try:
                if os.environ.get("HMALL_NO_POINTS") != "1":   # 적립구간 금액 유지 (buy_one과 동일)
                    FlowRunner(use_camera=False).run_action({"action": "hmall_use_all_points"})
                FlowRunner(use_camera=False).run_action({"action": "hmall_select_card_discount"})
            except Exception as e:
                res["status"] = f"SELECT_CARD_FAIL(resume):{e}"; return res
            # ★resume 도 같은 금액 가드를 통과해야 결제한다 — 한쪽에만 있는 가드는 곧 구멍이 된다
            #   (READ_FIRST 「버그 하나를 고치면 같은 모양을 폴더 전체에서 찾는다」).
            if not money_guard(idx if idx is not None else 0, res):
                return res
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
    combo_idx = next((int(a.split("=", 1)[1]) for a in args if a.startswith("combo=")), None)  # 설화수 기록용
    # only=데이즈온 / only=석류,견과 → 카트에서 그 상품만 선택해 주문(혼합 카트 카드별 분리주문).
    only_kw = next(([s for s in a.split("=", 1)[1].split(",") if s]
                    for a in args if a.startswith("only=")), None)
    plan = only or PLAN
    if not preflight_today_files():      # ★stale 데이터로 결제 금지 (대장/적립 조용한 누락 방지)
        return 1
    print(f"[serial] {hw._serial()}  plan={plan}  card={card_override or '당일 자동감지'}"
          f"{'  only=' + ','.join(only_kw) if only_kw else ''}", flush=True)
    summary = []
    for n, idx in enumerate(plan):
        try:
            r = buy_one(idx, card=card_override, combo_idx=combo_idx, only=only_kw)
        except Exception as e:
            r = {"idx": idx, "status": f"EXC:{e}"}
        print(f"[#{idx}] => {r.get('status')}", flush=True)
        summary.append(r)
        # ★★핸드세이크면 **루프를 멈춘다.** buy_one 이 return 해도 루프가 다음 계정으로 넘어가면
        #   콜드런치가 **살아있는 결제화면을 날린다**(카드번호 입력 대기 중인 화면이 사라진다).
        #   인계 러너로 그 계정을 끝낸 뒤, 아래에 찍힌 명령으로 나머지 계정을 이어서 돌린다.
        card = _handoff_card(r)
        if card:
            _handoff_stop(card, idx, plan[n + 1:], card_override, only_kw, combo_idx)
            break
        if n < len(plan) - 1:
            _account_gap(r)
    print(f"\n{'='*54}\nSUMMARY", flush=True)
    for r in summary:
        print(f"  #{r['idx']:2d} {r.get('id','?'):16s} [{r.get('card','?')}] {r.get('status')}"
              f"  {_reward_tag(r)}", flush=True)
    _reward_warn(summary, only_kw)
    return 0


ACCOUNT_GAP_SEC = int(os.environ.get("HMALL_ACCOUNT_GAP_SEC", "420"))   # 계정 간 간격(결제완료 기산)


def _account_gap(r: dict) -> None:
    """다음 계정까지 **결제 완료 시점 기준 7분** 을 채운다 (사용자 지시 2026-08-06).

    ★기산점이 '계정 시작'이 아니라 **결제 완료(`paid_at`)** 인 이유: 그 뒤에 오는
      뷰티 재인증 · 구매대장 · **적립신청**은 어차피 이 쉬는 시간 안에서 끝난다
      ("쉬는 시간 사이에 해당 계정 적립은 바로 하면 되잖아"). 그만큼을 빼고 **남은 만큼만** 잔다.
      적립 subprocess 는 매번 쿠키 폐기 후 새로 로그인하므로 계정이 섞이지 않는다.
    ★결제가 안 된 계정(paid_at 없음)은 간격을 둘 이유가 없어 바로 다음으로 간다.
    `HMALL_ACCOUNT_GAP_SEC=0` 으로 끌 수 있다(테스트용)."""
    paid_at = r.get("paid_at")
    if not paid_at or ACCOUNT_GAP_SEC <= 0:
        return
    used = time.time() - paid_at
    remain = ACCOUNT_GAP_SEC - used
    if remain <= 0:
        print(f"[gap] 결제완료 후 이미 {used/60:.1f}분 경과(적립 등) — 대기 없이 다음 계정", flush=True)
        return
    print(f"[gap] 결제완료 +{used/60:.1f}분(적립·대장 포함) → 7분 채우려 "
          f"{remain/60:.1f}분 대기", flush=True)
    time.sleep(remain)


_RUNNER = {"삼성": "samsung_enter", "NH": "nh_enter"}
_HANDOFF_STEPS = {
    # ★현대몰 삼성은 **인증서 단계가 없다** (2026-08-07 라이브 실측, 주문 20260807004446):
    #   pin6 뒤 버튼이 '결제'고 그걸 누르면 바로 주문완료다. cert/certpw 는 롯데 전용.
    "삼성": "card → cvc → next → pin6 → next('결제') → finish   (현대몰은 인증서 없음)",
    "NH":   "box1 → box2 → box3 → box4 → cvc → confirm → pinfield → pin6 → confirm → finish",
}


def _handoff_stop(card: str, idx: int, rest: list[int], card_override, only, combo_idx=None) -> None:
    """인계 지점에서 루프를 멈추며 **다음에 칠 명령을 그대로** 찍는다.
    남은 계정을 사람이 다시 계산하게 두면 빠뜨린다 → 명령줄을 완성해서 준다.
    ★combo_idx(설화수)면 `combo=N` 을, 아니면 상품 키워드를 붙인다 — finish 가 그걸로
      record_combo / record_food 를 가른다. 안 붙이면 설화수가 식품으로 기록된다(8/7 수정)."""
    runner = _RUNNER.get(card, "nh_enter")
    kw = f" combo={combo_idx}" if combo_idx is not None else ((" " + " ".join(only)) if only else "")
    print(f"\n{'='*54}\n★ #{idx} {card} 인계 대기 — 여기서 멈춥니다 (나머지 계정 중단)\n"
          f"  1) 화면 판독:  python3 -m phone_auto.{runner} shot /tmp/kp.png\n"
          f"  2) 입력 순서:  {_HANDOFF_STEPS.get(card, '')}\n"
          f"  3) 마무리:     python3 -m phone_auto.{runner} finish {idx}{kw}", flush=True)
    if rest:
        args = " ".join(str(i) for i in rest)
        ov = f" {card_override}" if card_override else ""
        okw = f" only={','.join(only)}" if only else ""
        cb = f" combo={combo_idx}" if combo_idx is not None else ""
        print(f"  4) 남은 계정:  python3 -u -m phone_auto.hmall_hyundai_buy{ov} {args}{okw}{cb}", flush=True)
    else:
        print("  4) 남은 계정 없음 — 이 계정이 마지막", flush=True)
    print("=" * 54, flush=True)


def _handoff_card(r: dict) -> str | None:
    """`NH_HANDOFF` / `SAMSUNG_HANDOFF` … → 카드명. 핸드세이크 카드가 늘어도 자동으로 잡힌다."""
    st = str(r.get("status", ""))
    return st.split("_HANDOFF", 1)[0] if "_HANDOFF" in st else None


def _reward_tag(r: dict) -> str:
    """요약 한 줄에 붙일 적립 상태. 결제 안 된 계정은 적립 대상이 아니므로 '-'."""
    if not str(r.get("status", "")).startswith("DONE"):
        c = _handoff_card(r)
        return f"적립⚠️{c}미완" if c else "적립-"
    rw = r.get("reward") or {}
    return "적립✓" if rw.get("ok") else f"적립⚠️{rw.get('skip') or rw.get('err') or '미확정'}"


def _reward_warn(summary: list[dict], only: list[str] | None = None) -> None:
    """★결제됐는데 적립이 확인 안 된 계정을 **끝에 크게** 모아 보여준다.
    2026-08-05 에 12계정 적립이 통째로 누락됐는데 아무 신호가 없었다 → 요약에서 반드시 튀게 한다.
    NH 는 buy_one 이 핸드세이크로 일찍 return 해 자동 적립을 안 타므로 별도 안내."""
    bad = [r for r in summary
           if str(r.get("status", "")).startswith("DONE") and not (r.get("reward") or {}).get("ok")]
    # ★카드별 핸드세이크 전부 — NH 만 보던 탓에 삼성 인계 계정이 '전 계정 확인 완료 ✓' 로
    #   조용히 넘어갔다(2026-08-06). 러너 이름도 카드에 맞춰 안내한다.
    hand = [(r, _handoff_card(r)) for r in summary if _handoff_card(r)]
    if not bad and not hand:
        print("  [적립] 전 계정 확인 완료 ✓", flush=True)
        return
    kw = (" " + " ".join(only)) if only else ""
    runner = {"삼성": "samsung_enter"}
    print(f"\n{'!'*54}\n⚠️ 적립 미완 — 아래를 반드시 처리할 것", flush=True)
    for r in bad:
        print(f"   #{r['idx']} {r.get('id','?')} → python3 buy.py reward {r['idx']}{kw}", flush=True)
    for r, card in hand:
        print(f"   #{r['idx']} {r.get('id','?')} ({card}) → 결제 마친 뒤 "
              f"python3 -m phone_auto.{runner.get(card, 'nh_enter')} finish {r['idx']}{kw}", flush=True)
    print("!" * 54, flush=True)


if __name__ == "__main__":
    sys.exit(main())
