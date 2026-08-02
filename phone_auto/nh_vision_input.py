#!/usr/bin/env python3
"""NH농협 일반결제 보안키패드 — Claude 비전 판독 기반 입력 헬퍼 (2026-07-31 라이브 검증).

배경: NH nppfs 셔플 키패드는 방패 아이콘이 숫자 자리에 섞여 로컬 OCR(vision/easyocr)이
      매핑에 실패한다(_tap_shuffle 사다리×3ROI 전부 실패). 반면 Claude 가 전체화면 스크린샷을
      직접 읽으면 방패가 섞여도 정확히 판독된다 → 판독은 Claude, 실행(탭)은 이 모듈이 담당.

★ 라이브 검증에서 확인된 규칙 (롯데 #9, 주문 2026-07-31-G83859 성공)
  1. **전체화면 캡처를 쓴다.** crop 후 offset 을 손으로 환산하면 좌표 오차가 생겨 오입력/넘침 발생.
  2. **한 칸(4자리) 입력 중에는 배열이 고정**이다 → 4자리 연속 탭 안전.
  3. **칸이 바뀌면 키패드가 재배열**된다 → 반드시 새 스크린샷을 다시 판독할 것.
     (재배열 후 옛 배열로 탭한 것이 2026-07-31 오입력 1/3 의 직접 원인)
  4. 탭 간격 0.8초. (0.5초에선 탭 누락 관측)
  5. 세션 만료 ~10분 → 카드16+CVC3 는 2분 내 끝내야 안전. 시행착오로 시간 쓰면 만료됨.

사용 (Claude 가 스크린샷을 읽고 배열을 넘겨준다):
    from phone_auto.nh_vision_input import tap_digits, KEY_COLS, ROW_CARD, ROW_CVC
    layout = {"1행": ['9','0','4','6','shield','1'], "2행": ['8','7','2','shield','3','5']}
    tap_digits("1672", layout_rows(layout), row_y=ROW_CARD)
"""
from __future__ import annotations
import os
import subprocess
import time

# 전체화면 1080x2400 기준 실측 좌표 (2026-07-31 라이브)
KEY_COLS = [208, 341, 473, 605, 737, 870]   # 6열 중심 x
ROW_CARD = (1088, 1222)                     # 카드번호 단계: 1행/2행 y
ROW_CVC = (1195, 1330)                      # CVC 단계 (키패드가 아래로 내려감)
ROW_PIN6 = (1157, 1291)                     # 결제비밀번호 6자리 단계
TAP_DELAY = 0.8                             # 탭 간격 (0.5s 는 누락 관측)


def _serial() -> str:
    return os.environ.get("ANDROID_SERIAL", "")


def _tap(x: int, y: int) -> None:
    cmd = ["adb"]
    if _serial():
        cmd += ["-s", _serial()]
    cmd += ["shell", "input", "tap", str(x), str(y)]
    subprocess.run(cmd, capture_output=True)


def build_pos(row1: list[str], row2: list[str], row_y: tuple[int, int]) -> dict[str, tuple[int, int]]:
    """Claude 가 읽은 두 행 라벨 → {숫자: (x, y)}. 'shield'/None 은 건너뛴다."""
    r1, r2 = row_y
    pos: dict[str, tuple[int, int]] = {}
    for lbl, x in zip(row1, KEY_COLS):
        if lbl and lbl != "shield":
            pos[str(lbl)] = (x, r1)
    for lbl, x in zip(row2, KEY_COLS):
        if lbl and lbl != "shield":
            pos[str(lbl)] = (x, r2)
    return pos


def tap_digits(value: str, pos: dict[str, tuple[int, int]], delay: float = TAP_DELAY) -> dict:
    """value 의 각 자리를 pos 매핑대로 탭. 매핑에 없는 숫자가 있으면 탭하지 않고 실패 반환
    (엉뚱한 키를 누르면 카드사 입력오류 누적 3/3 → 카드 잠김 위험)."""
    missing = sorted({d for d in value if d not in pos})
    if missing:
        return {"ok": False, "err": f"키패드에 없는 숫자 {missing} — 스크린샷 재판독 필요"}
    for d in value:
        x, y = pos[d]
        _tap(x, y)
        time.sleep(delay)
    return {"ok": True, "digits": len(value)}


def screencap(path: str) -> str:
    """전체화면 캡처 (crop 금지 — 좌표 환산 오차 방지)."""
    cmd = ["adb"]
    if _serial():
        cmd += ["-s", _serial()]
    cmd += ["exec-out", "screencap", "-p"]
    with open(path, "wb") as f:
        subprocess.run(cmd, stdout=f)
    return path
