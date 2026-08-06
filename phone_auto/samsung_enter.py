#!/usr/bin/env python3
"""삼성 일반결제 비전 핸드세이크 러너 — 에이전트가 판독한 키패드 좌표로 실제 입력을 수행한다.

`pay_samsung` 이 카드번호 화면에서 `manual=True` 로 정지한 뒤 이어받는 도구(3사 공용).
**값(카드번호/CVC/비번/인증서비번)은 이 스크립트가 secrets/card_secrets.json['삼성'] 에서 직접 읽는다.**
에이전트는 값을 보지 않고 **키패드 좌표만** 넘긴다 (판독=에이전트 / 탭=코드). NH `nh_enter` 와 같은 원칙.

왜 로컬 OCR 을 안 쓰나 (2026-08-06 사용자 지시 "핸드세이크로 제대로 해 좀"):
  · `_tap_shuffle` 의 로컬 2엔진(vision+easyocr)이 셔플 키패드 매핑에 자주 실패한다
    (8/6 실측: 10자리 중 5·1·2개만 매핑 → 9계정 연속 `'다음' 비활성` = 전건 미결제).
  · 실패하면 `_ocr_claude` **파일 핸드셰이크**(request.png → 45초 대기)로 승격하는데,
    스크립트를 백그라운드로 돌리면 아무도 응답을 못 줘 **무조건 타임아웃**이고,
    한 번 타임아웃하면 그 프로세스에선 클로드가 꺼져 나머지 계정까지 전부 죽는다.
  → **정지-인계**가 정본. 에이전트가 전체 화면을 직접 보고 좌표를 주면 셔플이든 뭐든 정확하다.

좌표 형식 — `d=x,y` 를 공백으로 구분해 **0~9 열 개 전부**. (값이 뭔지 모르게 하려면 전부 필요하다)
  "0=540,1904 1=180,1520 2=540,1520 3=900,1520 4=180,1648 ... 9=900,1776"

사용 (순서대로):
  python3 -m phone_auto.samsung_enter shot /tmp/kp.png     # 전체화면 캡처(판독용)
  python3 -m phone_auto.samsung_enter card "<좌표10개>"     # 카드번호 15자리 (칸 탭 + 입력 + 자릿수 검증)
  python3 -m phone_auto.samsung_enter cvc  "<좌표10개>"     # CVC 3자리  ※재셔플 대비 새로 판독할 것
  python3 -m phone_auto.samsung_enter next                 # 진행 버튼 (활성 검증 후 탭)
  python3 -m phone_auto.samsung_enter pin6 "<좌표10개>"     # 일반결제 비밀번호 6자리
  python3 -m phone_auto.samsung_enter next                 # ★현대몰은 여기가 '결제'(=승인) / 롯데는 '다음'
  python3 -m phone_auto.samsung_enter cert                 # (롯데만) 금융인증서 > 모니모 > 인증서 카드
  python3 -m phone_auto.samsung_enter certpw "<좌표10개>"   # (롯데만) 인증서 비번 6자리 → '인증 성공' OK
  python3 -m phone_auto.samsung_enter finish 11 석류        # ★현대몰: 주문완료 → 대장 + H.Point 적립
  python3 -m phone_auto.samsung_enter finish 11 combo=24    # ★현대몰 설화수는 combo= 로 (조합 기록)
  python3 -m phone_auto.samsung_enter finish_lotte 5 combo=24   # ★롯데: 대장 + 뷰티 + 구매사은

★★몰마다 **뒷부분이 갈린다** (2026-08-07 현대몰 라이브 실측 — 주문 20260807004446):
  · 현대몰 : card → cvc → next('다음') → pin6 → next(**'결제'**) → **바로 주문완료** → finish
             = **인증서 단계가 없다.** cert/certpw 를 부르려고 기다리지 말 것.
  · 롯데   : card → cvc → next('다음') → pin6 → next('다음') → cert → certpw → finish_lotte
  `next` 가 화면에 있는 버튼('다음'/'결제')을 알아서 고르므로 명령은 같다.

★★비번 화면에 카드 발급사가 **'롯데' 로 표시되는 것은 정상**이다 (사용자 확인 2026-08-07).
  `3779-89****-**897` 아래 '롯데' 라고 떠도 **실제 승인은 삼성카드**다
  (주문완료 결제정보 = `198,400원 (삼성카드 일시불)` 로 실측 확인). **롯데홈쇼핑에서도 똑같이 뜬다.**
  → 이걸 보고 "카드가 잘못 들어갔다"고 판단해 중단하지 말 것.

★★`finish` 를 빼먹지 말 것 — 핸드세이크로 빠지면 `buy_one` 이 일찍 return 해서
   구매대장·적립 자동단계를 **안 탄다**(NH 에서 8/5 에 적립 12계정을 통째로 놓친 그 구조다).

⚠️ 키패드는 **화면이 바뀌면 재셔플**된다 → 칸마다 반드시 새로 `shot` 찍어 다시 판독할 것.
⚠️ 자릿수는 화면에서 검증한다(`card_digits_on_screen`). 틀린 채 '다음'을 누르면 카드사 입력오류가
   쌓이고 3회면 카드가 잠긴다.
"""
from __future__ import annotations

import sys
import time

from phone_auto import hmall_hyundai_buy as B

_FIELDS = {"card": "card_no", "cvc": "cvc", "pin6": "pin6", "certpw": "cert_pw6"}
_DELAY = {"card": 0.5, "cvc": 0.5, "pin6": 0.8, "certpw": 0.8}   # 8/02 실측(0.35=오입력 / 1.0=느림)


def _secrets() -> dict:
    sec = B._card_secrets().get("삼성", {})
    missing = [k for k in _FIELDS.values() if not sec.get(k)]
    if missing:
        raise SystemExit(f"card_secrets['삼성'] 필드 부족: {missing}")
    return sec


def _parse_pos(spec: str) -> dict[str, tuple[int, int]]:
    """`0=x,y 1=x,y ...` → {'0': (x,y), ...}. 0~9 전부 있어야 한다(빠지면 그 숫자에서 멈춘다)."""
    pos: dict[str, tuple[int, int]] = {}
    for tok in spec.split():
        d, _, xy = tok.partition("=")
        x, _, y = xy.partition(",")
        if not (d.isdigit() and x.strip().isdigit() and y.strip().isdigit()):
            raise SystemExit(f"좌표 형식 오류: {tok!r} (형식 `d=x,y`)")
        pos[d] = (int(x), int(y))
    missing = [d for d in "0123456789" if d not in pos]
    if missing:
        raise SystemExit(f"좌표 누락: {missing} — 0~9 전부 넘길 것(값 노출 방지)")
    return pos


def _tap_value(value: str, pos: dict, delay: float) -> None:
    """값 자체는 **절대 출력하지 않는다** (자릿수만)."""
    for ch in value:
        x, y = pos[ch]
        B._adb().tap(x, y)
        time.sleep(delay)


def _tap_field(label_pred) -> bool:
    """입력칸 탭 — 키패드를 띄운다. 이미 떠 있으면 재탭이 재셔플을 유발하므로 호출측이 판단."""
    fld = next((it for it in B._ocr_texts(B.cap()) if label_pred(it["text"])), None)
    if not fld:
        return False
    B._adb().tap(fld["cx"], fld["cy"])
    time.sleep(1.5)
    return True


def _do_input(cmd: str, spec: str) -> int:
    sec = _secrets()
    pos = _parse_pos(spec)
    value = sec[_FIELDS[cmd]]
    if cmd == "card":       # 카드번호 칸 탭 (‘…없이’ 안내문구 우선, 없으면 '카드번호')
        if not (_tap_field(lambda t: "없이" in t) or _tap_field(lambda t: "카드번호" in t)):
            print("[card] ✗ 카드번호 필드 미발견"); return 1
    elif cmd == "cvc":
        if not _tap_field(lambda t: "CVC" in t.upper()):
            print("[cvc] ✗ CVC 필드 미발견"); return 1
    elif cmd == "pin6":
        _tap_field(lambda t: "6자리" in t or "숫자" in t)   # 없어도 키패드가 이미 떠 있을 수 있다
    _tap_value(value, pos, _DELAY[cmd])
    print(f"[{cmd}] ✓ {len(value)}자리 입력")
    time.sleep(1.2)
    if cmd == "card":       # ★탭 씹힘 검증 (8/02 #5: 15자리 중 9자리만 들어갔다)
        got = B.card_digits_on_screen()
        if got and got < len(value):
            print(f"[card] ✗ 화면 자릿수 {got}/{len(value)} — **탭 씹힘. 지우고 재판독·재입력할 것**")
            return 1
        print(f"[card] 화면 자릿수 {got or '판독불가'}")
    return 0


def main() -> int:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 0
    cmd = args[0]

    if cmd == "shot":
        B._resolve_serial()
        print(B.cap(args[1] if len(args) > 1 else "/tmp/samsung_kp.png"))
        return 0

    B._resolve_serial()

    if cmd == "next":
        # ★진행 버튼은 **몰마다 다르다** (2026-08-07 현대몰 라이브 실측):
        #   · 카드번호+CVC 화면 → 두 몰 모두 '다음'
        #   · 결제비번(pin6) 화면 → 현대몰은 **'결제'**(누르면 그대로 승인), 롯데는 '다음'(→인증서)
        #   화면에 있는 것을 찾아서 쓴다. 라벨을 하나로 박으면 한쪽 몰에서 그 자리에 멈춘다.
        # ★완전일치로 찾는다 — contains 로 잡으면 '결제 비밀번호'·'일반결제 비밀번호' 를 눌러
        #   결제가 엉뚱한 데로 샌다(nh_enter confirm 과 같은 교훈).
        label = next((t for t in ("다음", "결제") if B.ocr_find(t)), None)
        if not label:
            print("[next] ✗ '다음'/'결제' 버튼 미발견 — 화면 확인 필요")
            return 1
        # ★활성 검증 — 비활성이면 입력이 틀린 것이므로 탭하지 않는다(탭해봐야 헛수고 + 오류 누적).
        if not B.next_button_enabled(label=label):
            print(f"[next] ✗ '{label}' 비활성 — 카드번호/CVC/비번 입력값 오류. 지우고 재입력할 것")
            return 1
        ok = B.ocr_tap(label, contains=False, pick="bottom", retries=4)
        print(f"[next] {'✓' if ok else '✗'} ('{label}')")
        time.sleep(2.5)
        return 0 if ok else 1

    if cmd == "cert":
        r = B.samsung_cert_step()
        print(f"[cert] {r}")
        return 0 if r.get("ok") else 1

    if cmd in ("finish", "finish_lotte", "finish-lotte"):
        # 마무리(대장·적립)는 몰 단위라 NH 러너와 완전히 동일 → 중복 구현하지 않고 재사용한다.
        # ★★단 **카드명을 반드시 넘긴다** — nh_enter 의 기본값이 'NH' 라, 안 넘기면
        #   삼성으로 결제해놓고 **구매대장엔 NH 로 적힌다**(2026-08-07 발견, 그전엔 아무 신호도 없었다).
        from phone_auto import nh_enter
        rest = list(args[1:])
        if not any(a.startswith("card=") for a in rest):
            rest.append("card=삼성")
        sys.argv = ["nh_enter", cmd, *rest]
        return nh_enter.main()

    if cmd not in _FIELDS:
        print(f"unknown cmd: {cmd}")
        return 1
    if len(args) < 2:
        print(f"좌표가 필요합니다: {cmd} \"0=x,y 1=x,y ... 9=x,y\"")
        return 1
    rc = _do_input(cmd, args[1])
    if rc == 0 and cmd == "certpw":
        r = B.samsung_cert_done()          # '인증 성공' OK → 몰 복귀
        print(f"[certpw] 인증 마무리 {r}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
