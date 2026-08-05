#!/usr/bin/env python3
"""NH 일반결제 비전 핸드세이크 러너 — 에이전트가 판독한 키패드 배열로 실제 입력을 수행한다.

`pay_nh_general` 이 카드번호 화면에서 `manual=True` 로 정지한 뒤 이어받는 도구.
**값(카드번호/CVC/비번)은 이 스크립트가 secrets/card_secrets.json['NH'] 에서 직접 읽는다.**
에이전트는 값을 보지 않고 **키패드 배열만** 넘긴다 (판독=에이전트 / 탭=코드).

배열 규칙 (nh_vision_input 실측, 2026-07-31 라이브):
  · 6열 2행. 방패 아이콘 자리는 `shield` 로 적는다. 빈 칸은 `-`.
  · **한 칸(4자리) 입력 중에는 배열 고정** → 4자리 연속 탭 안전.
  · **칸이 바뀌면 재셔플** → 칸마다 반드시 새 스크린샷을 다시 판독할 것.

사용:
  python3 -m phone_auto.nh_enter shot /tmp/kp.png          # 전체화면 캡처(판독용)
  python3 -m phone_auto.nh_enter box1                      # 1칸 = 비보안 IME (배열 불필요)
  python3 -m phone_auto.nh_enter box2 "9,0,4,6,shield,1" "8,7,2,shield,3,5"
  python3 -m phone_auto.nh_enter cvc  "..." "..."
  python3 -m phone_auto.nh_enter pin6 "..." "..."
  python3 -m phone_auto.nh_enter confirm                   # '확인' 탭

⚠️ 자릿수는 화면에서 검증한다(card_digits_on_screen). 틀린 키를 누르면 카드사 입력오류가
   누적돼 3회에 카드가 잠길 수 있으므로, 매핑에 없는 숫자가 있으면 탭하지 않고 중단한다.
"""
from __future__ import annotations

import subprocess
import sys
import time

from phone_auto import hmall_hyundai_buy as B
from phone_auto.nh_vision_input import (KEY_COLS, ROW_CARD, ROW_CVC, ROW_PIN6,
                                        build_pos, screencap, tap_digits)

_ROWS = {"card": ROW_CARD, "cvc": ROW_CVC, "pin6": ROW_PIN6}


def _secrets() -> dict:
    sec = B._card_secrets().get("NH", {})
    if not all(sec.get(k) for k in ("card_no", "cvc", "pin6")):
        raise SystemExit("card_secrets['NH'] 필드 부족(card_no/cvc/pin6)")
    return sec


def _parse(spec: str) -> list[str]:
    out = [s.strip() for s in spec.split(",")]
    if len(out) != len(KEY_COLS):
        raise SystemExit(f"열 개수 {len(out)} (기대 {len(KEY_COLS)}) — 6열로 적을 것: {spec!r}")
    return [("" if s in ("-", "") else s) for s in out]


def _keypad_up() -> bool:
    """보안키패드가 이미 떠 있는가 ('가상키패드' 헤더로 판정)."""
    return B.screen_has("가상키패드")


def _tap_box(n: int, force: bool = False) -> None:
    """카드번호 n번째 칸 탭. box1 입력 후 레이아웃이 아래로 이동하므로 매번 fresh 재검출.

    ★★키패드가 **이미 떠 있으면 탭하지 않는다.** 재탭은 키패드를 리셋(재셔플)시켜
      방금 판독한 배열이 무효가 되고 → 엉뚱한 키가 눌린다 → 카드사 입력오류 누적
      3회면 카드 잠김. (한 칸 입력이 끝나면 다음 칸이 자동 활성 + 키패드가 새로 뜬다.)"""
    if _keypad_up() and not force:
        print(f"  [box{n}] 키패드 이미 표시중 → 재탭 skip (재셔플 방지)")
        return
    boxes = B._card_no_boxes()
    if len(boxes) < n:
        raise SystemExit(f"카드번호 입력칸 {len(boxes)}개 — {n}칸 미검출 (화면 확인 필요)")
    x, y = boxes[n - 1]
    B._adb().tap(x, y)
    time.sleep(1.2)


def main() -> int:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 0
    cmd = args[0]

    if cmd == "shot":
        path = args[1] if len(args) > 1 else "/tmp/nh_keypad.png"
        B._resolve_serial()
        print(screencap(path))
        return 0

    B._resolve_serial()
    sec = _secrets()

    if cmd == "box1":
        # 1칸 = **비보안 IME** 칸 → adb input text. 이 입력 완료가 2칸 nppfs 키패드를 소환한다
        # (2026-06-25 실측: 키 탭으로 1칸을 채우면 다음 칸 키패드가 안 뜬다).
        _tap_box(1, force=True)
        subprocess.run(["adb", "shell", "input", "text", sec["card_no"][:4]],
                       capture_output=True)
        time.sleep(1.5)
        print(f"[box1] IME 입력 완료 — 화면 자릿수 {B.card_digits_on_screen()}")
        return 0

    if cmd == "confirm":
        ok = B.ocr_tap("확인", contains=True, retries=4)
        print(f"[confirm] {'✓' if ok else '✗'}")
        return 0 if ok else 1

    if cmd == "pinfield":
        # 카드 확인 후 '일반결제비밀번호(숫자 6자리)' 칸 — **탭해야 보안키패드가 뜬다.**
        pf = next((it for it in B._ocr_texts(B.cap())
                   if "6자리" in it["text"] or "숫자" in it["text"]), None)
        if not pf:
            print("[pinfield] ✗ '숫자 6자리' 칸 미발견")
            return 1
        B._adb().tap(pf["cx"], pf["cy"])
        time.sleep(1.5)
        print(f"[pinfield] ✓ 탭 @({pf['cx']},{pf['cy']}) — 키패드 {'O' if _keypad_up() else 'X'}")
        return 0

    if cmd not in ("box2", "box3", "box4", "cvc", "pin6"):
        print(f"unknown cmd: {cmd}")
        return 1
    if len(args) < 3:
        print(f"배열 2행이 필요합니다: {cmd} \"<1행 6열>\" \"<2행 6열>\"")
        return 1

    row1, row2 = _parse(args[1]), _parse(args[2])
    if cmd.startswith("box"):
        n = int(cmd[-1])
        value = sec["card_no"][(n - 1) * 4: n * 4]
        _tap_box(n)
        B._wait_keypad(timeout=6); time.sleep(0.8)
        pos = build_pos(row1, row2, _ROWS["card"])
    elif cmd == "cvc":
        value = sec["cvc"]
        pos = build_pos(row1, row2, _ROWS["cvc"])
    else:
        value = sec["pin6"]
        pos = build_pos(row1, row2, _ROWS["pin6"])

    r = tap_digits(value, pos)
    # ⚠️ 값 자체는 절대 출력하지 않는다 (자릿수만).
    print(f"[{cmd}] {'✓' if r.get('ok') else '✗'} {r.get('digits', 0)}자리"
          f"{'' if r.get('ok') else ' — ' + str(r.get('err'))}")
    if cmd.startswith("box"):
        print(f"  화면 누적 자릿수 {B.card_digits_on_screen()}")
    return 0 if r.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
