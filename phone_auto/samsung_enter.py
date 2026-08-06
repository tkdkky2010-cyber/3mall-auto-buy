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
  python3 -m phone_auto.samsung_enter next                 # '다음' (활성 검증 후 탭)
  python3 -m phone_auto.samsung_enter pin6 "<좌표10개>"     # 일반결제 비밀번호 6자리
  python3 -m phone_auto.samsung_enter next
  python3 -m phone_auto.samsung_enter cert                 # 금융인증서 > 모니모 > 인증서 카드
  python3 -m phone_auto.samsung_enter certpw "<좌표10개>"   # 인증서 비밀번호 6자리 → '인증 성공' OK
  python3 -m phone_auto.samsung_enter finish 11 석류        # ★현대몰: 주문완료 → 대장 + H.Point 적립
  python3 -m phone_auto.samsung_enter finish_lotte 5 combo=24   # ★롯데: 대장 + 뷰티 + 구매사은

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
        # ★활성 검증 — 비활성이면 입력이 틀린 것이므로 탭하지 않는다(탭해봐야 헛수고 + 오류 누적).
        if not B.next_button_enabled():
            print("[next] ✗ '다음' 비활성 — 카드번호/CVC 입력값 오류. 지우고 재입력할 것")
            return 1
        ok = B.ocr_tap("다음", contains=True, retries=4)
        print(f"[next] {'✓' if ok else '✗'}")
        time.sleep(2.5)
        return 0 if ok else 1

    if cmd == "cert":
        r = B.samsung_cert_step()
        print(f"[cert] {r}")
        return 0 if r.get("ok") else 1

    if cmd in ("finish", "finish_lotte", "finish-lotte"):
        # 마무리(대장·적립)는 몰 단위라 NH 러너와 완전히 동일 → 중복 구현하지 않고 재사용한다.
        from phone_auto import nh_enter
        sys.argv = ["nh_enter", cmd, *args[1:]]
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
